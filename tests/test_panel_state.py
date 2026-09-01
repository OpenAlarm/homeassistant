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


def state_body(state, mode=None, mode_name=None):
    alarm = {"id": "a1", "state": state, "mode": mode, "modeName": mode_name}
    return {"error": False, "data": {"version": "1.0.0", "alarms": [alarm]}}


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


async def test_the_panel_knows_its_mode(hass, aioclient_mock):
    """Automations branch on the mode directly, with no from_state gymnastics.

    The state feed carries the live mode: the armed mode while armed, the
    incident's mode while triggered, nothing while disarmed. Custom modes
    keep their real identity here even though the panel state collapses
    them to armed_custom_bypass.
    """
    entry = await setup_entry(hass, aioclient_mock)
    state = hass.states.get(ENTITY)
    assert state.attributes["mode"] is None
    assert state.attributes["mode_name"] is None

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("triggered", mode="away", mode_name="Away"))
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "triggered"
    assert state.attributes["mode"] == "away"
    assert state.attributes["mode_name"] == "Away"

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(STATE, json=state_body("armed_custom", mode="vac123", mode_name="Vacation"))
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "armed_custom_bypass"
    assert state.attributes["mode"] == "vac123"
    assert state.attributes["mode_name"] == "Vacation"


async def test_a_feed_without_modes_still_works(hass, aioclient_mock):
    """A server that predates the mode field degrades to bare attributes."""
    entry = await setup_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(
        STATE,
        json={"error": False, "data": {"version": "1.0.0", "alarms": [{"id": "a1", "state": "armed_away"}]}},
    )
    await entry.runtime_data.state.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "armed_away"
    assert state.attributes["mode"] is None
