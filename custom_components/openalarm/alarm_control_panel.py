"""Alarm control panel entities for OpenAlarm."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OpenAlarmConfigEntry, OpenAlarmData
from .api import OpenAlarmError
from .const import DOMAIN, KIND_ALARM
from .coordinator import OpenAlarmStateCoordinator
from .readiness import async_check_ready

_LOGGER = logging.getLogger(__name__)

PENDING_WINDOW = 30.0

MODE_FEATURES = {
    "home": AlarmControlPanelEntityFeature.ARM_HOME,
    "away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "night": AlarmControlPanelEntityFeature.ARM_NIGHT,
}

SERVER_TO_HA = {
    "disarmed": AlarmControlPanelState.DISARMED,
    "armed_home": AlarmControlPanelState.ARMED_HOME,
    "armed_away": AlarmControlPanelState.ARMED_AWAY,
    "armed_night": AlarmControlPanelState.ARMED_NIGHT,
    "armed_custom": AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    "triggered": AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenAlarmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a panel per alarm, including alarms that appear later."""
    data = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync() -> None:
        fresh: list[OpenAlarmPanel] = []
        for alarm in data.inventory.alarms():
            alarm_id = alarm.get("id")
            if alarm_id and alarm_id not in known:
                known.add(alarm_id)
                fresh.append(OpenAlarmPanel(data, alarm_id))
        if fresh:
            async_add_entities(fresh)

    _sync()
    entry.async_on_unload(data.inventory.async_add_listener(_sync))


class OpenAlarmPanel(
    CoordinatorEntity[OpenAlarmStateCoordinator], AlarmControlPanelEntity
):
    """One alarm, drivable from dashboards, showing the service's real state.

    State comes from the integration's state surface on a one-minute poll, so
    an arm sent by any other client shows up here within that minute, and an
    open incident reads as triggered until it clears. A command sets the state
    optimistically for instant feedback and holds it until the server confirms
    it or the pending window lapses - the arm endpoint is eventually
    consistent, so a poll racing the queue must not snap the panel back.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_code_arm_required = False

    def __init__(self, data: OpenAlarmData, alarm_id: str) -> None:
        super().__init__(data.state)
        self._data = data
        self.alarm_id = alarm_id
        self._pending: AlarmControlPanelState | None = None
        self._pending_until: float = 0.0
        self._attr_unique_id = f"{KIND_ALARM}:{alarm_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{KIND_ALARM}:{alarm_id}")}
        )

    async def async_added_to_hass(self) -> None:
        """Also re-render when the inventory changes modes or drops the alarm."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._data.inventory.async_add_listener(self.async_write_ha_state)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending is not None:
            confirmed = self._server_state() == self._pending
            if confirmed or time.monotonic() >= self._pending_until:
                self._pending = None
        super()._handle_coordinator_update()

    def _server_entry(self) -> dict[str, str | None]:
        return (self.coordinator.data or {}).get(self.alarm_id) or {}

    def _server_state(self) -> AlarmControlPanelState | None:
        return SERVER_TO_HA.get(self._server_entry().get("state"))

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """The live mode, so automations can branch on it directly."""
        entry = self._server_entry()
        return {"mode": entry.get("mode"), "mode_name": entry.get("mode_name")}

    @property
    def available(self) -> bool:
        """Unavailable once the alarm leaves the key's inventory."""
        return super().available and any(
            alarm.get("id") == self.alarm_id
            for alarm in self._data.inventory.alarms()
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if self._pending is not None and time.monotonic() < self._pending_until:
            return self._pending
        return self._server_state()

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Advertise only the seeded modes this alarm actually has.

        Custom modes have no panel affordance in Home Assistant; they stay
        reachable through the openalarm.alarm_arm action, and show here as
        armed custom when the server reports one.
        """
        features = AlarmControlPanelEntityFeature.TRIGGER
        for mode in self._data.inventory.modes_for(self.alarm_id):
            features |= MODE_FEATURES.get(
                mode.get("id"), AlarmControlPanelEntityFeature(0)
            )
        return features

    async def _act(
        self, action: str, mode: str | None, state: AlarmControlPanelState
    ) -> None:
        try:
            body: dict[str, Any] = await self._data.client.act(
                KIND_ALARM, self.alarm_id, action, mode
            )
        except OpenAlarmError as err:
            raise HomeAssistantError(str(err)) from err
        _LOGGER.debug(
            "panel %s on %s traceId=%s environment=%s",
            action,
            self.alarm_id,
            body.get("traceId"),
            (body.get("data") or {}).get("environment"),
        )
        self._pending = state
        self._pending_until = time.monotonic() + PENDING_WINDOW
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _assert_ready(self) -> None:
        name = next(
            (
                alarm.get("name")
                for alarm in self._data.inventory.alarms()
                if alarm.get("id") == self.alarm_id
            ),
            None,
        )
        async_check_ready(
            self.hass,
            self.coordinator.config_entry,
            self.alarm_id,
            name or self.alarm_id,
        )

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        self._assert_ready()
        await self._act("arm", "home", AlarmControlPanelState.ARMED_HOME)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._assert_ready()
        await self._act("arm", "away", AlarmControlPanelState.ARMED_AWAY)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        self._assert_ready()
        await self._act("arm", "night", AlarmControlPanelState.ARMED_NIGHT)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._act("disarm", None, AlarmControlPanelState.DISARMED)

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        await self._act("trigger", None, AlarmControlPanelState.TRIGGERED)
