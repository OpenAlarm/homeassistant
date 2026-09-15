"""Vacation arms through the panel entity like every other mode."""

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
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
ARM_VACATION = f"{DEFAULT_BASE_URL}/v1/alarm/a1/arm/vacation"

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
                            {"id": "vacation", "name": "Vacation"},
                        ],
                    }
                ],
                "panicButtons": [],
            }
        ],
    },
}

ENTITY = "alarm_control_panel.front"


async def setup_entry(hass, aioclient_mock):
    aioclient_mock.get(DESCRIBE, json=BODY)
    aioclient_mock.get(
        STATE,
        json={
            "error": False,
            "data": {"version": "1.0.0", "alarms": [{"id": "a1", "state": "disarmed"}]},
        },
    )
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


async def test_vacation_is_offered_and_arms(hass, aioclient_mock):
    """An alarm with a vacation mode advertises ARM_VACATION and arming it calls the API.

    Regression: the feature flag was advertised from the mode list but the
    panel had no async_alarm_arm_vacation, so Home Assistant's base class
    raised NotImplementedError on press.
    """
    await setup_entry(hass, aioclient_mock)
    state = hass.states.get(ENTITY)
    assert state.state == "disarmed"
    assert state.attributes["supported_features"] & AlarmControlPanelEntityFeature.ARM_VACATION

    aioclient_mock.get(ARM_VACATION, json={"error": False, "traceId": "t1", "data": {}})
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_vacation",
        {"entity_id": ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "armed_vacation"
    assert any(call[1].path == "/v1/alarm/a1/arm/vacation" for call in aioclient_mock.mock_calls)
