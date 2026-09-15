"""The OpenAlarm integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import (
    async_get_all_descriptions,
    async_set_service_schema,
)
from homeassistant.helpers.typing import ConfigType

from .api import NotFound, OpenAlarmClient, OpenAlarmError, trace_fields
from .const import (
    APP_URL,
    ATTR_MODE,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LOCATION_ID,
    DEFAULT_BASE_URL,
    DOMAIN,
    KIND_ALARM,
    KIND_PANIC,
    MANUFACTURER,
    MODES,
    SERVICE_ARM,
    SERVICE_CLEAR,
    SERVICE_DISARM,
    SERVICE_PANIC,
    SERVICE_PANIC_CLEAR,
    SERVICE_TRIGGER,
)
from .coordinator import OpenAlarmCoordinator, OpenAlarmStateCoordinator
from .readiness import async_check_ready
from .realtime import OpenAlarmRealtime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.ALARM_CONTROL_PANEL]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class OpenAlarmData:
    """Everything one location's entry holds at runtime."""

    client: OpenAlarmClient
    inventory: OpenAlarmCoordinator
    state: OpenAlarmStateCoordinator
    realtime: OpenAlarmRealtime | None = None


type OpenAlarmConfigEntry = ConfigEntry[OpenAlarmData]

TARGET_SCHEMA = vol.Schema(
    {vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string])},
    extra=vol.ALLOW_EXTRA,
)

MODE_SCHEMA = TARGET_SCHEMA.extend({vol.Optional(ATTR_MODE): cv.string})


@dataclass(frozen=True)
class Target:
    """One trigger, resolved from a device the user targeted."""

    coordinator: OpenAlarmCoordinator
    kind: str
    trigger_id: str
    name: str


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions.

    These are registered here rather than per config entry so an automation
    referencing them can still be edited and validated when no location is
    loaded, and so a call against an unloaded location fails with a sentence
    rather than "action not found".
    """
    _async_register_services(hass)
    return True


def _async_start_realtime(hass: HomeAssistant, entry: OpenAlarmConfigEntry) -> None:
    """Open the push channel once the describe payload advertises one."""
    data = entry.runtime_data
    recipe = data.inventory.realtime
    if data.realtime is not None or not recipe:
        return
    try:
        listener = OpenAlarmRealtime(
            async_get_clientsession(hass),
            entry.data[CONF_API_KEY],
            recipe,
            data.state.async_request_refresh,
            data.state.set_realtime_connected,
        )
    except KeyError:
        _LOGGER.warning("realtime recipe is incomplete; staying on polling")
        return
    data.realtime = listener
    listener.start()
    entry.async_on_unload(listener.stop)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAlarmConfigEntry) -> bool:
    """Set up one location from a config entry."""
    client = OpenAlarmClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    inventory = OpenAlarmCoordinator(
        hass, entry, client, entry.data[CONF_LOCATION_ID]
    )
    await inventory.async_config_entry_first_refresh()
    state = OpenAlarmStateCoordinator(hass, entry, client)
    await state.async_config_entry_first_refresh()

    entry.runtime_data = OpenAlarmData(client=client, inventory=inventory, state=state)

    @callback
    def _inventory_changed() -> None:
        _async_start_realtime(hass, entry)
        _async_sync_devices(hass, entry, inventory)
        hass.async_create_task(_async_refresh_mode_options(hass))

    _async_start_realtime(hass, entry)
    _async_sync_devices(hass, entry, inventory)
    entry.async_on_unload(inventory.async_add_listener(_inventory_changed))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_refresh_mode_options(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenAlarmConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.async_create_task(_async_refresh_mode_options(hass))
    return unloaded


async def _async_refresh_mode_options(hass: HomeAssistant) -> None:
    """Rebuild the arm and trigger mode dropdowns from live inventory.

    The vocabulary is fixed, so this exists for the names: a mode renamed in
    the console appears here on the same cadence as everything else - the
    six-hour poll, an entry reload, or setup. Ids outside MODES are ignored,
    so a stale record cannot put an unarmable option in the dropdown, and one
    id named differently by two alarms shows both.
    """
    labels: dict[str, set[str]] = {}
    ready = (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state not in ready:
            continue
        data = getattr(entry, "runtime_data", None)
        if data is None:
            continue
        for alarm in data.inventory.alarms():
            for mode in alarm.get("modes") or []:
                mode_id = mode.get("id")
                if mode_id in MODES:
                    labels.setdefault(mode_id, set()).add(mode.get("name") or mode_id)

    options = [
        {"value": mode_id, "label": " / ".join(sorted(labels[mode_id]))}
        for mode_id in MODES
        if mode_id in labels
    ]
    if not options:
        return

    descriptions = (await async_get_all_descriptions(hass)).get(DOMAIN) or {}
    for service in (SERVICE_ARM, SERVICE_TRIGGER):
        current = descriptions.get(service)
        if not current:
            continue
        patched = deepcopy(current)
        select = (
            patched.get("fields", {})
            .get(ATTR_MODE, {})
            .get("selector", {})
            .get("select")
        )
        if select is None:
            continue
        select["options"] = options
        async_set_service_schema(hass, DOMAIN, service, patched)


def _async_sync_devices(
    hass: HomeAssistant, entry: OpenAlarmConfigEntry, coordinator: OpenAlarmCoordinator
) -> None:
    """Mirror the inventory into the device registry.

    A trigger that leaves the inventory has been deleted, disabled, or dropped
    from the key's scope. Its device is detached rather than left behind
    offering controls that would 404.
    """
    registry = dr.async_get(hass)

    live: set[str] = set()
    for kind, model, items in (
        (KIND_ALARM, "Alarm", coordinator.alarms()),
        (KIND_PANIC, "Panic Button", coordinator.panic_buttons()),
    ):
        for item in items:
            trigger_id = item.get("id")
            if not trigger_id:
                continue
            unique_id = f"{kind}:{trigger_id}"
            live.add(unique_id)
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, unique_id)},
                name=item.get("name") or trigger_id,
                manufacturer=MANUFACTURER,
                model=model,
                entry_type=dr.DeviceEntryType.SERVICE,
                configuration_url=APP_URL,
            )

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, unique_id in device.identifiers:
            if domain != DOMAIN:
                continue
            if unique_id not in live:
                registry.async_update_device(
                    device.id, remove_config_entry_id=entry.entry_id
                )


def _resolve(hass: HomeAssistant, call: ServiceCall, kind: str) -> list[Target]:
    """Turn targeted devices into triggers we can act on."""
    registry = dr.async_get(hass)
    targets: list[Target] = []

    for device_id in call.data.get("device_id", []):
        device = registry.async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unknown device {device_id}")

        identifier = next(
            (i for i in device.identifiers if i[0] == DOMAIN and ":" in i[1]), None
        )
        if identifier is None:
            raise ServiceValidationError(
                f"{device.name or device_id} is not an OpenAlarm trigger"
            )

        found_kind, _, trigger_id = identifier[1].partition(":")
        if found_kind != kind:
            wanted = "an alarm" if kind == KIND_ALARM else "a panic button"
            raise ServiceValidationError(f"{device.name or device_id} is not {wanted}")

        data = _data_for(hass, device)
        if data is None:
            raise ServiceValidationError(
                f"The OpenAlarm location holding {device.name or trigger_id} "
                "is not loaded"
            )

        targets.append(
            Target(data.inventory, kind, trigger_id, device.name or trigger_id)
        )

    if not targets:
        raise ServiceValidationError("No OpenAlarm device was targeted")

    return targets


def _data_for(hass: HomeAssistant, device: dr.DeviceEntry) -> OpenAlarmData | None:
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            continue
        return entry.runtime_data
    return None


async def _run(
    hass: HomeAssistant, call: ServiceCall, kind: str, action: str, moded: bool
) -> None:
    mode = call.data.get(ATTR_MODE) if moded else None

    for target in _resolve(hass, call, kind):
        if mode:
            known = target.coordinator.modes_for(target.trigger_id)
            if not any(m.get("id") == mode for m in known):
                names = ", ".join(str(m.get("id")) for m in known)
                raise ServiceValidationError(
                    f"{target.name} has no mode {mode}. Known modes: {names or 'none'}"
                )

        if kind == KIND_ALARM and action == "arm":
            async_check_ready(
                hass, target.coordinator.config_entry, target.trigger_id, target.name
            )

        try:
            body = await target.coordinator.client.act(
                kind, target.trigger_id, action, mode
            )
        except NotFound as err:
            raise HomeAssistantError(
                f"OpenAlarm no longer has {target.name}. It may have been deleted, "
                "disabled, or dropped from this key's scope."
            ) from err
        except OpenAlarmError as err:
            raise HomeAssistantError(str(err)) from err

        _LOGGER.debug(
            "openalarm.%s on %s traceId=%s environment=%s",
            action,
            target.name,
            *trace_fields(body),
        )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register every action once for the integration."""
    if hass.services.has_service(DOMAIN, SERVICE_ARM):
        return

    def handler(
        kind: str, action: str, moded: bool
    ) -> Callable[[ServiceCall], Awaitable[None]]:
        async def handle(call: ServiceCall) -> None:
            await _run(hass, call, kind, action, moded)

        return handle

    for name, kind, action, moded, schema in (
        (SERVICE_ARM, KIND_ALARM, "arm", True, MODE_SCHEMA),
        (SERVICE_DISARM, KIND_ALARM, "disarm", False, TARGET_SCHEMA),
        (SERVICE_TRIGGER, KIND_ALARM, "trigger", True, MODE_SCHEMA),
        (SERVICE_CLEAR, KIND_ALARM, "clear", False, TARGET_SCHEMA),
        (SERVICE_PANIC, KIND_PANIC, "trigger", False, TARGET_SCHEMA),
        (SERVICE_PANIC_CLEAR, KIND_PANIC, "clear", False, TARGET_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler(kind, action, moded), schema=schema
        )
