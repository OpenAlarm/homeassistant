"""Tests for the readiness gate."""

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openalarm.const import (
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_READINESS,
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


async def setup_entry(hass, aioclient_mock, options=None):
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
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def arm_panel(hass):
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_home",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()


GATED = {CONF_READINESS: {"a1": ["binary_sensor.front_door"]}}


async def test_an_open_sensor_blocks_arming_with_its_name(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock, options=GATED)
    hass.states.async_set(
        "binary_sensor.front_door", "on", {"friendly_name": "Front Door"}
    )

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_a_clear_sensor_lets_the_arm_through(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock, options=GATED)
    hass.states.async_set(
        "binary_sensor.front_door", "off", {"friendly_name": "Front Door"}
    )
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_an_unconfigured_alarm_arms_unconditionally(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock)
    hass.states.async_set(
        "binary_sensor.front_door", "on", {"friendly_name": "Front Door"}
    )
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})

    await arm_panel(hass)

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_a_dead_member_blocks_even_when_its_group_reads_clear(
    hass, aioclient_mock
):
    await setup_entry(
        hass,
        aioclient_mock,
        options={CONF_READINESS: {"a1": ["binary_sensor.doors"]}},
    )
    hass.states.async_set(
        "binary_sensor.doors",
        "off",
        {
            "friendly_name": "Doors",
            "entity_id": ["binary_sensor.d1", "binary_sensor.d2"],
        },
    )
    hass.states.async_set("binary_sensor.d1", "off", {"friendly_name": "Main Door"})
    hass.states.async_set(
        "binary_sensor.d2", "unavailable", {"friendly_name": "Garage Door"}
    )

    with pytest.raises(ServiceValidationError, match="Garage Door is unavailable"):
        await arm_panel(hass)


async def test_nested_groups_name_the_leaf_at_fault(hass, aioclient_mock):
    await setup_entry(
        hass,
        aioclient_mock,
        options={CONF_READINESS: {"a1": ["binary_sensor.not_clear"]}},
    )
    hass.states.async_set(
        "binary_sensor.not_clear",
        "on",
        {"friendly_name": "Not Clear", "entity_id": ["binary_sensor.windows"]},
    )
    hass.states.async_set(
        "binary_sensor.windows",
        "on",
        {"friendly_name": "Windows", "entity_id": ["binary_sensor.w1"]},
    )
    hass.states.async_set(
        "binary_sensor.w1", "on", {"friendly_name": "Study Window"}
    )

    with pytest.raises(ServiceValidationError, match="Study Window is not clear"):
        await arm_panel(hass)


async def test_a_missing_entity_blocks_arming(hass, aioclient_mock):
    await setup_entry(
        hass,
        aioclient_mock,
        options={CONF_READINESS: {"a1": ["binary_sensor.gone"]}},
    )

    with pytest.raises(
        ServiceValidationError, match="binary_sensor.gone is missing"
    ):
        await arm_panel(hass)


async def test_the_custom_arm_service_is_gated_too(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock, options=GATED)
    hass.states.async_set(
        "binary_sensor.front_door", "on", {"friendly_name": "Front Door"}
    )
    registry = dr.async_get(hass)
    device = next(
        d
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if (DOMAIN, "alarm:a1") in d.identifiers
    )

    with pytest.raises(ServiceValidationError, match="Front Door is not clear"):
        await hass.services.async_call(
            DOMAIN,
            "alarm_arm",
            {"device_id": [device.id], "mode": "home"},
            blocking=True,
        )


async def test_disarm_is_never_gated(hass, aioclient_mock):
    await setup_entry(hass, aioclient_mock, options=GATED)
    hass.states.async_set(
        "binary_sensor.front_door", "on", {"friendly_name": "Front Door"}
    )
    aioclient_mock.get(DISARM, json={"error": False, "traceId": "t1", "data": {}})

    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_disarm",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_the_options_flow_stores_the_gate(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] == "form"
    assert flow["step_id"] == "entities"

    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"entities": ["binary_sensor.front_door"]}
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_READINESS] == {"a1": ["binary_sensor.front_door"]}

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"entities": []}
    )
    assert entry.options[CONF_READINESS] == {}
