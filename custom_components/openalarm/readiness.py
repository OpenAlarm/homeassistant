"""The readiness gate: a location refuses to arm while a sensor is not clear."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import CONF_READINESS

DOMAIN_PREFIX = "binary_sensor."


@callback
def async_check_ready(hass: HomeAssistant, entry: ConfigEntry, alarm_name: str) -> None:
    """Raise unless every selected sensor, walked into groups, reads clear.

    The selection is a target: entities, devices, areas, floors, or labels,
    resolved the way an action target is. Diagnostic entities are kept, since
    tamper sensors usually are. Groups are expanded to their members
    recursively, so the error names the actual sensor at fault, and a single
    member that is on, unavailable, or unknown blocks arming even when its
    group reads clear - a group only goes unavailable when every member is,
    which would let one dead sensor hide.
    """
    selection = TargetSelection(entry.options.get(CONF_READINESS) or {})
    if not selection.has_any_target:
        return
    selected = async_extract_referenced_entity_ids(
        hass, selection, primary_entities_only=False
    )
    entity_ids = sorted(
        entity_id
        for entity_id in selected.referenced | selected.indirectly_referenced
        if entity_id.startswith(DOMAIN_PREFIX)
    )
    problems = _problems(hass, entity_ids, set())
    if problems:
        raise ServiceValidationError(
            f"Cannot arm {alarm_name}: " + "; ".join(problems)
        )


def _problems(
    hass: HomeAssistant, entity_ids: list[str], visited: set[str]
) -> list[str]:
    found: list[str] = []
    for entity_id in entity_ids:
        if entity_id in visited:
            continue
        visited.add(entity_id)
        state = hass.states.get(entity_id)
        if state is None:
            found.append(f"{entity_id} is missing")
            continue
        members = state.attributes.get(ATTR_ENTITY_ID)
        if isinstance(members, (list, tuple)) and members:
            found.extend(_problems(hass, list(members), visited))
            continue
        if state.state == STATE_ON:
            found.append(f"{state.name} is not clear")
        elif state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            found.append(f"{state.name} is {state.state}")
    return found
