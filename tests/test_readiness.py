"""Tests for the readiness gate."""

import pytest

from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openalarm.const import (
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_SENSORS,
    DEFAULT_BASE_URL,
    DOMAIN,
    SUBENTRY_ALARM,
)

DESCRIBE = f"{DEFAULT_BASE_URL}/v1/integration/describe"
STATE = f"{DEFAULT_BASE_URL}/v1/integration/state"
ARM = f"{DEFAULT_BASE_URL}/v1/alarm/a1/arm/home"
DISARM = f"{DEFAULT_BASE_URL}/v1/alarm/a1/disarm"

BODY = {
    "error": False,
    "data": {
        "version": "1.0.0",
        "locations": [
            {
                "id": "loc-home",
                "name": "Home",
                "alarms": [
                    {
                        "id": "a1",
                        "name": "Front",
                        "modes": [
                            {"id": "home", "name": "Home"},
                            {"id": "away", "name": "Away"},
                        ],
                    }
                ],
                "panicButtons": [],
            }
        ],
    },
}

STATE_BODY = {
    "error": False,
    "data": {
        "version": "1.0.0",
        "alarms": [{"id": "a1", "state": "disarmed"}],
    },
}

ENTITY = "alarm_control_panel.front"


async def setup_entry(hass, aioclient_mock):
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=STATE_BODY)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={
            CONF_API_KEY: "oa_x",
            CONF_LOCATION_ID: "loc-home",
            CONF_LOCATION_NAME: "Home",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def alarm_device(hass, entry):
    registry = dr.async_get(hass)
    return next(
        d
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if (DOMAIN, "alarm:a1") in d.identifiers
    )


def place_alarm(hass, entry, area_id=None, labels=None):
    device = alarm_device(hass, entry)
    return dr.async_get(hass).async_update_device(
        device.id, area_id=area_id, labels=labels or set()
    )


def alarm_subentry(entry):
    return next(
        s for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_ALARM and s.unique_id == "a1"
    )


def pick_sensors(hass, entry, sensors):
    hass.config_entries.async_update_subentry(
        entry, alarm_subentry(entry), data={CONF_SENSORS: sensors}
    )


def sensor(hass, object_id, name, state, device_class=None, area_id=None, labels=None, category=None):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "binary_sensor",
        "test",
        object_id,
        suggested_object_id=object_id,
        entity_category=category,
    )
    registry.async_update_entity(entry.entity_id, area_id=area_id, labels=labels or set())
    attributes = {"friendly_name": name}
    if device_class:
        attributes["device_class"] = device_class
    hass.states.async_set(entry.entity_id, state, attributes)
    return entry.entity_id


async def arm_panel(hass):
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_home",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_an_open_door_in_the_alarms_area_blocks_arming(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Main Floor")
    place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "front_door", "Front Door", "on", "door", area_id=area.id)

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_a_clear_area_lets_the_arm_through(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Main Floor")
    place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "front_door", "Front Door", "off", "door", area_id=area.id)
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_an_unplaced_alarm_arms_unconditionally(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "on", "door")
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_motion_in_the_area_does_not_block(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Main Floor")
    place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "hall_motion", "Hall Motion", "on", "motion", area_id=area.id)
    sensor(hass, "front_door", "Front Door", "off", "door", area_id=area.id)
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_a_label_resolves_to_its_diagnostic_tampers(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    label = lr.async_get(hass).async_create("Perimeter")
    place_alarm(hass, entry, labels={label.label_id})
    sensor(
        hass,
        "study_tamper",
        "Study Tamper",
        "on",
        "tamper",
        labels={label.label_id},
        category=EntityCategory.DIAGNOSTIC,
    )

    with pytest.raises(ServiceValidationError, match="Study Tamper is not clear"):
        await arm_panel(hass)


async def test_a_dead_sensor_blocks_arming(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    label = lr.async_get(hass).async_create("Perimeter")
    place_alarm(hass, entry, labels={label.label_id})
    sensor(hass, "garage", "Garage Door", "unavailable", "garage_door", labels={label.label_id})

    with pytest.raises(ServiceValidationError, match="Garage Door is unavailable"):
        await arm_panel(hass)


async def test_a_labelled_group_is_walked_to_the_member_at_fault(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    label = lr.async_get(hass).async_create("Perimeter")
    place_alarm(hass, entry, labels={label.label_id})
    registry = er.async_get(hass)
    group = registry.async_get_or_create(
        "binary_sensor", "group", "doors", suggested_object_id="doors"
    )
    registry.async_update_entity(group.entity_id, labels={label.label_id})
    hass.states.async_set(
        group.entity_id,
        "off",
        {"friendly_name": "Doors", "entity_id": ["binary_sensor.d1", "binary_sensor.d2"]},
    )
    hass.states.async_set("binary_sensor.d1", "off", {"friendly_name": "Main Door"})
    hass.states.async_set("binary_sensor.d2", "unknown", {"friendly_name": "Rear Door"})

    with pytest.raises(ServiceValidationError, match="Rear Door is unknown"):
        await arm_panel(hass)


async def test_the_custom_arm_service_is_gated_too(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Main Floor")
    device = place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "front_door", "Front Door", "on", "door", area_id=area.id)

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm",
            {"device_id": [device.id], "mode": "home"},
            blocking=True,
        )


async def test_disarm_is_never_gated(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Main Floor")
    place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "front_door", "Front Door", "on", "door", area_id=area.id)
    aioclient_mock.get(DISARM, json={"error": False, "traceId": "t1", "data": {}})

    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_each_alarm_gets_a_subentry_and_its_device_lives_there(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    subentry = alarm_subentry(entry)
    assert subentry.title == "Front"

    device = alarm_device(hass, entry)
    assert device.config_subentry_id == subentry.subentry_id

    panel = er.async_get(hass).async_get(ENTITY)
    assert panel.config_subentry_id == subentry.subentry_id


async def test_a_device_from_before_subentries_is_moved_keeping_its_id(hass, aioclient_mock):
    registry = dr.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={CONF_API_KEY: "oa_x", CONF_LOCATION_ID: "loc-home", CONF_LOCATION_NAME: "Home"},
    )
    entry.add_to_hass(hass)
    old = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "alarm:a1")}, name="Front"
    )
    assert old.config_subentry_id is None

    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=STATE_BODY)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    moved = registry.async_get(old.id)
    assert moved.config_subentry_id == alarm_subentry(entry).subentry_id
    assert alarm_device(hass, entry).id == old.id


async def test_picked_sensors_gate_arming(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "on", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await arm_panel(hass)


async def test_picked_sensors_count_regardless_of_class(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "hall_motion", "Hall Motion", "on", "motion")
    pick_sensors(hass, entry, ["binary_sensor.hall_motion"])

    with pytest.raises(ServiceValidationError, match="Hall Motion is not clear"):
        await arm_panel(hass)


async def test_a_picked_sensor_that_vanished_blocks(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    pick_sensors(hass, entry, ["binary_sensor.gone"])

    with pytest.raises(ServiceValidationError, match="binary_sensor.gone is missing"):
        await arm_panel(hass)


async def test_picked_sensors_and_area_combine(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    area = ar.async_get(hass).async_get_or_create("Garage")
    place_alarm(hass, entry, area_id=area.id)
    sensor(hass, "garage_door", "Garage Door", "on", "garage_door", area_id=area.id)
    sensor(hass, "side_window", "Side Window", "unavailable", "window")
    pick_sensors(hass, entry, ["binary_sensor.side_window"])

    with pytest.raises(ServiceValidationError) as raised:
        await arm_panel(hass)
    assert "Side Window is unavailable" in str(raised.value)
    assert "Garage Door is not clear" in str(raised.value)


async def test_the_reconfigure_flow_stores_the_picks(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    subentry = alarm_subentry(entry)

    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ALARM),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    assert flow["type"] == "form"
    assert flow["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {CONF_SENSORS: ["binary_sensor.front_door"]}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert alarm_subentry(entry).data[CONF_SENSORS] == ["binary_sensor.front_door"]


async def test_alarms_cannot_be_added_by_hand(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ALARM), context={"source": "user"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "alarms_come_from_the_console"
