"""Tests for integration setup behaviour."""

from homeassistant.helpers.service import async_get_all_descriptions
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
                            {"id": "night", "name": "Night"},
                            {"id": "k7x9m2qp4rtnb3vwj5h8c6d0efg1tvwz", "name": "Test"},
                        ],
                    }
                ],
                "panicButtons": [],
            }
        ],
    },
}
STATE_BODY = {"error": False, "data": {"version": "1.0.0", "alarms": [{"id": "a1", "state": "disarmed"}]}}


async def test_custom_modes_reach_the_dropdown_by_name(hass, aioclient_mock):
    """The arm dropdown lists live modes as value/label pairs, custom included.

    Regression: during setup the entry is SETUP_IN_PROGRESS, not LOADED, and
    the first version of the guard skipped it - so the dropdown stayed static
    until the six-hour poll. Caught on a live install, reproduced here.
    """
    aioclient_mock.get(DESCRIBE, json=BODY)
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

    descs = await async_get_all_descriptions(hass)
    for service in ("alarm_arm", "alarm_trigger"):
        desc = (descs.get(DOMAIN) or {}).get(service) or {}
        options = desc["fields"]["mode"]["selector"]["select"]["options"]
        assert {"value": "k7x9m2qp4rtnb3vwj5h8c6d0efg1tvwz", "label": "Test"} in options
        assert options[0] == {"value": "home", "label": "Home"}
        assert desc["fields"]["mode"]["selector"]["select"]["custom_value"] is True
