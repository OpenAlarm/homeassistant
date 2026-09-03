"""Keeps one location's inventory current."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InvalidAuth, OpenAlarmClient, OpenAlarmError
from .const import (
    DOMAIN,
    STATE_UPDATE_INTERVAL,
    STATE_UPDATE_INTERVAL_REALTIME,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class OpenAlarmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls describe so new alarms, panic buttons and modes appear on their own."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OpenAlarmClient,
        location_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.location_id = location_id
        self.realtime: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.describe()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OpenAlarmError as err:
            raise UpdateFailed(str(err)) from err

        self.realtime = data.get("realtime") if isinstance(data.get("realtime"), dict) else None

        for location in data.get("locations") or []:
            if location.get("id") == self.location_id:
                return location

        # The key can no longer reach this location: it was deleted, or the key
        # was rescoped. Report an empty location rather than failing the update,
        # so the entities go unavailable and are flagged stale instead of the
        # whole entry erroring on every poll.
        _LOGGER.warning(
            "OpenAlarm location %s is no longer in this key's inventory",
            self.location_id,
        )
        return {"id": self.location_id, "name": None, "alarms": [], "panicButtons": []}

    @property
    def location_name(self) -> str | None:
        return (self.data or {}).get("name")

    def alarms(self) -> list[dict[str, Any]]:
        return list((self.data or {}).get("alarms") or [])

    def panic_buttons(self) -> list[dict[str, Any]]:
        return list((self.data or {}).get("panicButtons") or [])

    def modes_for(self, alarm_id: str) -> list[dict[str, Any]]:
        for alarm in self.alarms():
            if alarm.get("id") == alarm_id:
                return list(alarm.get("modes") or [])
        return []


class OpenAlarmStateCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Polls alarm state at a cadence a panel can honestly show."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: OpenAlarmClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} state",
            update_interval=STATE_UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client

    @callback
    def set_realtime_connected(self, connected: bool) -> None:
        """Poll slowly while the push channel is up, at the full cadence when it is not."""
        interval = STATE_UPDATE_INTERVAL_REALTIME if connected else STATE_UPDATE_INTERVAL
        if self.update_interval == interval:
            return
        self.update_interval = interval
        if not connected:
            self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> dict[str, dict[str, str | None]]:
        try:
            data = await self.client.state()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OpenAlarmError as err:
            raise UpdateFailed(str(err)) from err
        return {
            alarm["id"]: {
                "state": str(alarm.get("state") or "disarmed"),
                "mode": alarm.get("mode") or None,
                "mode_name": alarm.get("modeName") or None,
            }
            for alarm in data.get("alarms") or []
            if alarm.get("id")
        }
