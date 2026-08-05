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
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OpenAlarmConfigEntry
from .api import OpenAlarmError
from .const import DOMAIN, KIND_ALARM
from .coordinator import OpenAlarmCoordinator

_LOGGER = logging.getLogger(__name__)

MODE_FEATURES = {
    "home": AlarmControlPanelEntityFeature.ARM_HOME,
    "away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "night": AlarmControlPanelEntityFeature.ARM_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenAlarmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a panel per alarm, including alarms that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync() -> None:
        fresh = [
            OpenAlarmPanel(coordinator, alarm_id)
            for alarm in coordinator.alarms()
            if (alarm_id := alarm.get("id")) and alarm_id not in known
        ]
        known.update(panel.alarm_id for panel in fresh)
        if fresh:
            async_add_entities(fresh)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class OpenAlarmPanel(
    CoordinatorEntity[OpenAlarmCoordinator], AlarmControlPanelEntity, RestoreEntity
):
    """One alarm, drivable from dashboards.

    The state is what Home Assistant last sent through this entity. OpenAlarm
    deliberately exposes no state over the integration surface, so arming from
    the console or another client does not move this entity - which is why it
    declares assumed_state and the UI renders explicit buttons, never a toggle
    claiming knowledge it does not have.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_code_arm_required = False

    def __init__(self, coordinator: OpenAlarmCoordinator, alarm_id: str) -> None:
        super().__init__(coordinator)
        self.alarm_id = alarm_id
        self._attr_unique_id = f"{KIND_ALARM}:{alarm_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{KIND_ALARM}:{alarm_id}")}
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last optimistic state, or start disarmed.

        A brand-new panel has no history, and rendering "unknown" reads as
        broken. Disarmed is how core's manual panel and Alarmo both start,
        and it matches the state a freshly created OpenAlarm alarm holds.
        """
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_alarm_state = AlarmControlPanelState(last.state)
            except ValueError:
                self._attr_alarm_state = None
        if self._attr_alarm_state is None:
            self._attr_alarm_state = AlarmControlPanelState.DISARMED

    @property
    def available(self) -> bool:
        """Unavailable once the alarm leaves the key's inventory."""
        return super().available and any(
            alarm.get("id") == self.alarm_id for alarm in self.coordinator.alarms()
        )

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Advertise only the seeded modes this alarm actually has.

        Custom modes have no panel affordance in Home Assistant; they stay
        reachable through the openalarm.alarm_arm action.
        """
        features = AlarmControlPanelEntityFeature.TRIGGER
        for mode in self.coordinator.modes_for(self.alarm_id):
            features |= MODE_FEATURES.get(
                mode.get("id"), AlarmControlPanelEntityFeature(0)
            )
        return features

    async def _act(
        self, action: str, mode: str | None, state: AlarmControlPanelState
    ) -> None:
        try:
            body: dict[str, Any] = await self.coordinator.client.act(
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
        self._attr_alarm_state = state
        self.async_write_ha_state()

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
