"""Alarm control panel entities for OpenAlarm."""

from __future__ import annotations

import logging
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

_LOGGER = logging.getLogger(__name__)

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
    arming from the OpenAlarm console shows up here within that minute, and an
    open incident reads as triggered until it clears. A command sets the state
    optimistically for instant feedback, then the next poll confirms it from
    the server.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_code_arm_required = False

    def __init__(self, data: OpenAlarmData, alarm_id: str) -> None:
        super().__init__(data.state)
        self._data = data
        self.alarm_id = alarm_id
        self._pending: AlarmControlPanelState | None = None
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
        self._pending = None
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Unavailable once the alarm leaves the key's inventory."""
        return super().available and any(
            alarm.get("id") == self.alarm_id
            for alarm in self._data.inventory.alarms()
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if self._pending is not None:
            return self._pending
        return SERVER_TO_HA.get((self.coordinator.data or {}).get(self.alarm_id))

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
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._act("arm", "home", AlarmControlPanelState.ARMED_HOME)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._act("arm", "away", AlarmControlPanelState.ARMED_AWAY)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._act("arm", "night", AlarmControlPanelState.ARMED_NIGHT)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._act("disarm", None, AlarmControlPanelState.DISARMED)

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        await self._act("trigger", None, AlarmControlPanelState.TRIGGERED)
