"""The OpenAlarm integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import NotFound, OpenAlarmClient, OpenAlarmError
from .const import (
    APP_URL,
    ATTR_MODE,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    DEFAULT_BASE_URL,
    DOMAIN,
    KIND_ALARM,
    KIND_PANIC,
    MANUFACTURER,
    SERVICE_ARM,
    SERVICE_CLEAR,
    SERVICE_DISARM,
    SERVICE_PANIC,
    SERVICE_PANIC_CLEAR,
    SERVICE_TRIGGER,
)
from .coordinator import OpenAlarmCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type OpenAlarmConfigEntry = ConfigEntry[OpenAlarmCoordinator]

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


async def async_setup_entry(hass: HomeAssistant, entry: OpenAlarmConfigEntry) -> bool:
    """Set up one location from a config entry."""
    client = OpenAlarmClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    coordinator = OpenAlarmCoordinator(
        hass, entry, client, entry.data[CONF_LOCATION_ID]
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _async_sync_devices(hass, entry, coordinator)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_sync_devices(hass, entry, coordinator)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenAlarmConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _async_sync_devices(
    hass: HomeAssistant, entry: OpenAlarmConfigEntry, coordinator: OpenAlarmCoordinator
) -> None:
    """Mirror the inventory into the device registry.

    A trigger that leaves the inventory has been deleted, disabled, or dropped
    from the key's scope. Its device is detached rather than left behind
    offering controls that would 404.
    """
    registry = dr.async_get(hass)
    location_name = (
        coordinator.location_name
        or entry.data.get(CONF_LOCATION_NAME)
        or entry.data[CONF_LOCATION_ID]
    )

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
                suggested_area=location_name,
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

        coordinator = _coordinator_for(hass, device)
        if coordinator is None:
            raise ServiceValidationError(
                f"The OpenAlarm location holding {device.name or trigger_id} "
                "is not loaded"
            )

        targets.append(Target(coordinator, kind, trigger_id, device.name or trigger_id))

    if not targets:
        raise ServiceValidationError("No OpenAlarm device was targeted")

    return targets


def _coordinator_for(
    hass: HomeAssistant, device: dr.DeviceEntry
) -> OpenAlarmCoordinator | None:
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
        known = target.coordinator.modes_for(target.trigger_id)
        if mode and not any(m.get("id") == mode for m in known):
            names = ", ".join(str(m.get("id")) for m in known)
            raise ServiceValidationError(
                f"{target.name} has no mode {mode}. Known modes: {names or 'none'}"
            )

        try:
            body: dict[str, Any] = await target.coordinator.client.act(
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
            body.get("traceId"),
            (body.get("data") or {}).get("environment"),
        )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register every action once for the integration."""
    if hass.services.has_service(DOMAIN, SERVICE_ARM):
        return

    async def arm(call: ServiceCall) -> None:
        await _run(hass, call, KIND_ALARM, "arm", moded=True)

    async def disarm(call: ServiceCall) -> None:
        await _run(hass, call, KIND_ALARM, "disarm", moded=False)

    async def trigger(call: ServiceCall) -> None:
        await _run(hass, call, KIND_ALARM, "trigger", moded=True)

    async def clear(call: ServiceCall) -> None:
        await _run(hass, call, KIND_ALARM, "clear", moded=False)

    async def panic(call: ServiceCall) -> None:
        await _run(hass, call, KIND_PANIC, "trigger", moded=False)

    async def panic_clear(call: ServiceCall) -> None:
        await _run(hass, call, KIND_PANIC, "clear", moded=False)

    for name, handler, schema in (
        (SERVICE_ARM, arm, MODE_SCHEMA),
        (SERVICE_DISARM, disarm, TARGET_SCHEMA),
        (SERVICE_TRIGGER, trigger, MODE_SCHEMA),
        (SERVICE_CLEAR, clear, TARGET_SCHEMA),
        (SERVICE_PANIC, panic, TARGET_SCHEMA),
        (SERVICE_PANIC_CLEAR, panic_clear, TARGET_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
