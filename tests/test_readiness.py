"""Tests for the readiness gate."""

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openalarm.const import (
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_READINESS,
    CONF_SENSORS,
    DEFAULT_BASE_URL,
    DOMAIN,
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


def pick_sensors(hass, entry, sensors):
    hass.config_entries.async_update_entry(
        entry, options={CONF_READINESS: {"a1": sensors}}
    )


def sensor(hass, object_id, name, state, device_class=None):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "binary_sensor", "test", object_id, suggested_object_id=object_id
    )
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


async def test_a_picked_open_sensor_blocks_arming_by_name(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "on", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_a_clear_pick_lets_the_arm_through(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "off", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_nothing_picked_arms_unconditionally(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "on", "door")
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_only_picked_sensors_count_never_the_alarms_area_or_labels(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    device = alarm_device(hass, entry)
    dr.async_get(hass).async_update_device(device.id, area_id="default", labels={"perimeter"})
    er.async_get(hass).async_update_entity(
        sensor(hass, "hub_tamper", "Hub Tamper", "on", "tamper"),
        area_id="default",
        labels={"perimeter"},
    )
    sensor(hass, "front_door", "Front Door", "off", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_picked_sensors_count_regardless_of_class(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "hall_motion", "Hall Motion", "on", "motion")
    pick_sensors(hass, entry, ["binary_sensor.hall_motion"])

    with pytest.raises(ServiceValidationError, match="Hall Motion is not clear"):
        await arm_panel(hass)


async def test_a_dead_pick_blocks_arming(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "garage", "Garage Door", "unavailable", "garage_door")
    pick_sensors(hass, entry, ["binary_sensor.garage"])

    with pytest.raises(ServiceValidationError, match="Garage Door is unavailable"):
        await arm_panel(hass)


async def test_a_picked_group_is_walked_to_the_member_at_fault(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    hass.states.async_set(
        "binary_sensor.doors",
        "off",
        {"friendly_name": "Doors", "entity_id": ["binary_sensor.d1", "binary_sensor.d2"]},
    )
    hass.states.async_set("binary_sensor.d1", "off", {"friendly_name": "Main Door"})
    hass.states.async_set("binary_sensor.d2", "unknown", {"friendly_name": "Rear Door"})
    pick_sensors(hass, entry, ["binary_sensor.doors"])

    with pytest.raises(ServiceValidationError, match="Rear Door is unknown"):
        await arm_panel(hass)


async def test_a_pick_that_vanished_blocks(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    pick_sensors(hass, entry, ["binary_sensor.gone"])

    with pytest.raises(ServiceValidationError, match="binary_sensor.gone is missing"):
        await arm_panel(hass)


async def test_the_custom_arm_service_is_gated_too(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    device = alarm_device(hass, entry)
    sensor(hass, "front_door", "Front Door", "on", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm",
            {"device_id": [device.id], "mode": "home"},
            blocking=True,
        )


async def test_disarm_is_never_gated(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    sensor(hass, "front_door", "Front Door", "on", "door")
    pick_sensors(hass, entry, ["binary_sensor.front_door"])
    aioclient_mock.get(DISARM, json={"error": False, "traceId": "t1", "data": {}})

    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_the_options_flow_stores_the_picks_per_alarm(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] == "form"
    assert flow["step_id"] == "alarm"
    assert flow["description_placeholders"] == {"name": "Front"}

    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensor_check": {CONF_SENSORS: ["binary_sensor.front_door"]}}
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_READINESS] == {"a1": ["binary_sensor.front_door"]}

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensor_check": {}}
    )
    assert entry.options[CONF_READINESS] == {}


async def test_the_options_flow_asks_which_alarm_when_there_are_several(hass, aioclient_mock):
    body = {
        "error": False,
        "data": {
            "version": "1.0.0",
            "locations": [
                {
                    "id": "loc-home",
                    "name": "Home",
                    "alarms": [
                        {"id": "a1", "name": "Front", "modes": []},
                        {"id": "a2", "name": "Garage", "modes": []},
                    ],
                    "panicButtons": [],
                }
            ],
        },
    }
    aioclient_mock.get(DESCRIBE, json=body)
    aioclient_mock.get(STATE, json=STATE_BODY)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={CONF_API_KEY: "oa_x", CONF_LOCATION_ID: "loc-home", CONF_LOCATION_NAME: "Home"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["step_id"] == "init"

    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"target": "alarm:a2"})
    assert flow["step_id"] == "alarm"
    assert flow["description_placeholders"] == {"name": "Garage"}

    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensor_check": {CONF_SENSORS: ["binary_sensor.garage_door"]}}
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_READINESS] == {"a2": ["binary_sensor.garage_door"]}
