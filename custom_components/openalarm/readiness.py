"""The readiness gate: an alarm refuses to arm while a sensor is not clear."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import CONF_READINESS

DOMAIN_PREFIX = "binary_sensor."

CLEARABLE = {
    None,
    BinarySensorDeviceClass.DOOR,
    BinarySensorDeviceClass.WINDOW,
    BinarySensorDeviceClass.OPENING,
    BinarySensorDeviceClass.GARAGE_DOOR,
    BinarySensorDeviceClass.TAMPER,
}


@callback
def async_check_ready(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry | None,
    alarm_id: str,
    alarm_name: str,
) -> None:
    """Raise unless every sensor the alarm watches reads clear.

    Three sources combine: the sensors picked for the alarm in the options, and
    the sensors in the area or carrying the labels set on the alarm's device
    in Home Assistant's standard device settings. Picked sensors count as
    they are, since the user chose them. Area and label expansion keeps only
    contact and tamper classes (plus unclassified sensors such as groups), so
    a motion sensor seeing the person arming does not block; diagnostic
    entities are kept, since tamper sensors usually are. Groups are expanded
    to their members recursively, so the error names the actual sensor at
    fault, and a single member that is on, unavailable, or unknown blocks
    arming even when its group reads clear - a group only goes unavailable
    when every member is, which would let one dead sensor hide.
    """
    picked = list((entry.options.get(CONF_READINESS) or {}).get(alarm_id) or [])
    problems = _problems(hass, sorted(picked), set(), strict=True)

    selection = TargetSelection(
        {
            "area_id": [device.area_id] if device and device.area_id else [],
            "label_id": sorted(device.labels) if device else [],
        }
    )
    if selection.has_any_target:
        selected = async_extract_referenced_entity_ids(
            hass, selection, primary_entities_only=False
        )
        implied = sorted(
            entity_id
            for entity_id in selected.referenced | selected.indirectly_referenced
            if entity_id.startswith(DOMAIN_PREFIX) and entity_id not in picked
        )
        problems.extend(_problems(hass, implied, set(picked), strict=False))

    if problems:
        raise ServiceValidationError(
            f"Cannot arm {alarm_name}: " + "; ".join(problems)
        )


def _problems(
    hass: HomeAssistant, entity_ids: list[str], visited: set[str], strict: bool
) -> list[str]:
    found: list[str] = []
    for entity_id in entity_ids:
        if entity_id in visited:
            continue
        visited.add(entity_id)
        state = hass.states.get(entity_id)
        if state is None:
            if strict:
                found.append(f"{entity_id} is missing")
            continue
        members = state.attributes.get(ATTR_ENTITY_ID)
        if isinstance(members, (list, tuple)) and members:
            found.extend(_problems(hass, list(members), visited, strict))
            continue
        if not strict and state.attributes.get(ATTR_DEVICE_CLASS) not in CLEARABLE:
            continue
        if state.state == STATE_ON:
            found.append(f"{state.name} is not clear")
        elif state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            found.append(f"{state.name} is {state.state}")
    return found
