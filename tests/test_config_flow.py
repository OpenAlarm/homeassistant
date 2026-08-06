"""Tests for the OpenAlarm config flow."""

from unittest.mock import patch

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openalarm.const import (
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)

DESCRIBE_URL = f"{DEFAULT_BASE_URL}/v1/integration/describe"

HOME = {
    "id": "loc-home",
    "name": "Home",
    "alarms": [{"id": "a1", "name": "Front", "modes": [{"id": "away", "name": "Away"}]}],
    "panicButtons": [],
}
CABIN = {
    "id": "loc-cabin",
    "name": "Cabin",
    "alarms": [],
    "panicButtons": [{"id": "p1", "name": "Bedside"}],
}


def _body(*locations):
    return {
        "error": False,
        "message": None,
        "traceId": "t1",
        "data": {"version": "1.0.0", "locations": list(locations)},
    }


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_single_location_creates_the_entry_without_asking(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """One location means no location step - the key is the whole form."""
    aioclient_mock.get(DESCRIBE_URL, json=_body(HOME))

    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.openalarm.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "oa_test_key"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_API_KEY: "oa_test_key",
        CONF_LOCATION_ID: "loc-home",
        CONF_LOCATION_NAME: "Home",
    }
    assert result["result"].unique_id == "loc-home"
    assert len(setup.mock_calls) == 1


async def test_multiple_locations_ask_which_one(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Two locations show the picker, and the choice names the entry."""
    aioclient_mock.get(DESCRIBE_URL, json=_body(HOME, CABIN))

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_test_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "location"

    with patch("custom_components.openalarm.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LOCATION_ID: "loc-cabin"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cabin"
    assert result["data"][CONF_LOCATION_ID] == "loc-cabin"


async def test_rejected_key_shows_invalid_auth_and_recovers(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A 401 is an error on the form, not a dead end."""
    aioclient_mock.get(DESCRIBE_URL, status=401)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_bad_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(DESCRIBE_URL, json=_body(HOME))
    with patch("custom_components.openalarm.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "oa_good_key"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unreachable_api_shows_cannot_connect(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(DESCRIBE_URL, exc=aiohttp.ClientError("boom"))

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_test_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_key_reaching_nothing_is_told_so(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """An empty inventory is a scope problem the user can fix, named as such."""
    aioclient_mock.get(DESCRIBE_URL, json=_body())

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_test_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_locations"}


async def test_a_location_cannot_be_added_twice(
    hass: HomeAssistant, aioclient_mock
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={CONF_API_KEY: "oa_old", CONF_LOCATION_ID: "loc-home"},
    ).add_to_hass(hass)
    aioclient_mock.get(DESCRIBE_URL, json=_body(HOME))

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_test_key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_takes_a_replacement_key(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A rotated key is swapped in place and the entry reloads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={
            CONF_API_KEY: "oa_dead_key",
            CONF_LOCATION_ID: "loc-home",
            CONF_LOCATION_NAME: "Home",
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(DESCRIBE_URL, json=_body(HOME))

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch("custom_components.openalarm.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "oa_new_key"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "oa_new_key"


async def test_reauth_refuses_a_key_for_the_wrong_location(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A key scoped elsewhere cannot silently repoint the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="loc-home",
        title="Home",
        data={
            CONF_API_KEY: "oa_dead_key",
            CONF_LOCATION_ID: "loc-home",
            CONF_LOCATION_NAME: "Home",
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(DESCRIBE_URL, json=_body(CABIN))

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "oa_other_key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_location"}
    assert entry.data[CONF_API_KEY] == "oa_dead_key"
