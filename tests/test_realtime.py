"""Tests for the realtime push channel."""

import base64
import json

import pytest

from custom_components.openalarm.realtime import (
    RealtimeProtocolError,
    auth_subprotocol,
    handle_frame,
)


def test_auth_subprotocol_round_trips_the_header():
    value = auth_subprotocol("api.example.com", "oa_secret")
    assert value.startswith("header-")
    raw = value.removeprefix("header-")
    padded = raw + "=" * (-len(raw) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    assert decoded == {"host": "api.example.com", "Authorization": "oa_secret"}
    assert "=" not in value


def test_frames_map_to_their_actions():
    assert handle_frame({"type": "connection_ack"}) == "ack"
    assert handle_frame({"type": "subscribe_success", "id": "x"}) == "subscribed"
    assert handle_frame({"type": "data", "id": "x", "event": ["{}"]}) == "refresh"
    assert handle_frame({"type": "ka"}) is None
    assert handle_frame({"type": "publish_success"}) is None


def test_error_frames_force_a_reconnect():
    for kind in ("subscribe_error", "broadcast_error", "error"):
        with pytest.raises(RealtimeProtocolError):
            handle_frame({"type": kind, "errors": [{"errorType": "x"}]})


async def test_setup_without_a_recipe_stays_on_polling(hass, aioclient_mock):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.openalarm.const import (
        CONF_API_KEY,
        CONF_LOCATION_ID,
        CONF_LOCATION_NAME,
        DEFAULT_BASE_URL,
        DOMAIN,
    )

    body = {
        "error": False,
        "data": {
            "version": "1.0.0",
            "locations": [
                {"id": "loc-home", "name": "Home", "alarms": [], "panicButtons": []}
            ],
        },
    }
    aioclient_mock.get(f"{DEFAULT_BASE_URL}/v1/integration/describe", json=body)
    aioclient_mock.get(
        f"{DEFAULT_BASE_URL}/v1/integration/state",
        json={"error": False, "data": {"version": "1.0.0", "alarms": []}},
    )
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
    assert entry.runtime_data.realtime is None


async def test_setup_with_a_recipe_starts_the_listener(hass, aioclient_mock):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.openalarm.const import (
        CONF_API_KEY,
        CONF_LOCATION_ID,
        CONF_LOCATION_NAME,
        DEFAULT_BASE_URL,
        DOMAIN,
    )

    body = {
        "error": False,
        "data": {
            "version": "1.0.0",
            "realtime": {
                "httpHost": "example.appsync-api.ca-west-1.amazonaws.com",
                "realtimeHost": "example.appsync-realtime-api.ca-west-1.amazonaws.com",
                "channel": "/account/acc1",
            },
            "locations": [
                {"id": "loc-home", "name": "Home", "alarms": [], "panicButtons": []}
            ],
        },
    }
    aioclient_mock.get(f"{DEFAULT_BASE_URL}/v1/integration/describe", json=body)
    aioclient_mock.get(
        f"{DEFAULT_BASE_URL}/v1/integration/state",
        json={"error": False, "data": {"version": "1.0.0", "alarms": []}},
    )
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

    listener = entry.runtime_data.realtime
    assert listener is not None
    assert listener._task is not None
    assert not listener._task.done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert listener._task is None
