"""Refresh button for OpenAlarm."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OpenAlarmConfigEntry
from .const import CONF_LOCATION_ID, DOMAIN
from .coordinator import OpenAlarmCoordinator

DESCRIPTION = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenAlarmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the refresh button for this location."""
    async_add_entities([OpenAlarmRefreshButton(entry.runtime_data, entry)])


class OpenAlarmRefreshButton(CoordinatorEntity[OpenAlarmCoordinator], ButtonEntity):
    """Pulls the inventory again without waiting for the next poll."""

    _attr_has_entity_name = True
    entity_description = DESCRIPTION

    def __init__(
        self, coordinator: OpenAlarmCoordinator, entry: OpenAlarmConfigEntry
    ) -> None:
        super().__init__(coordinator)
        location_id = entry.data[CONF_LOCATION_ID]
        self._attr_unique_id = f"{location_id}:refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"location:{location_id}")}
        )

    async def async_press(self) -> None:
        """Ask for a fresh inventory.

        The coordinator keeps its own throttling, so holding this down cannot
        hammer the API.
        """
        await self.coordinator.async_request_refresh()
