"""Tests for the panel's optimistic-state hold."""

from unittest.mock import patch

from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openalarm.const import (
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)

DESCRIBE = f"{DEFAULT_BASE_URL}/v1/integration/describe"
STATE = f"{DEFAULT_BASE_URL}/v1/integration/state"
ARM = f"{DEFAULT_BASE_URL}/v1/alarm/a1/arm/home"

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

ENTITY = "alarm_control_panel.front"


def state_body(state):
    return {"error": False, "data": {"version": "1.0.0", "alarms": [{"id": "a1", "state": state}]}}


async def setup_entry(hass, aioclient_mock):
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("disarmed"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={CONF_API_KEY: "oa_x", CONF_LOCATION_ID: "loc-home", CONF_LOCATION_NAME: "Home"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_stale_poll_does_not_snap_the_panel_back(hass, aioclient_mock):
    """An arm holds its optimistic state through a poll that predates it.

    Regression: the arm endpoint is a 202 into a queue, and the immediate
    refresh after arming raced it. The old code cleared the optimistic state
    on any coordinator update, so HomeKit showed the previous mode for up to
    a minute. Seen live through the HomeKit bridge.
    """
    await setup_entry(hass, aioclient_mock)
    assert hass.states.get(ENTITY).state == "disarmed"

    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_home",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "armed_home"


async def test_confirming_poll_releases_the_hold(hass, aioclient_mock):
    """Once the server reports the pending state, the hold clears."""
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_home",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("armed_home"))
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "armed_home"

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("disarmed"))
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_expired_hold_yields_to_the_server(hass, aioclient_mock):
    """After the pending window lapses, server truth wins even unconfirmed."""
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.get(ARM, json={"error": False, "traceId": "t1", "data": {}})
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_home",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "armed_home"

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("disarmed"))

    with patch(
        "custom_components.openalarm.alarm_control_panel.time.monotonic",
        return_value=10_000_000.0,
    ):
        await entry.runtime_data.state.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "disarmed"


async def test_triggered_state_still_wins_when_confirmed(hass, aioclient_mock):
    """A live incident reported by the server shows as triggered."""
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("triggered"))
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == AlarmControlPanelState.TRIGGERED
