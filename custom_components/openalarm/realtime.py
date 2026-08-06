"""Push-to-invalidate over the OpenAlarm realtime bus.

The server's describe response carries an AppSync Events connection recipe.
This listener subscribes to the account channel and, on any change event,
asks the state coordinator to refresh. The 60 second poll stays as the
fallback: losing the socket only ever degrades freshness, never correctness.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

SUBPROTOCOL = "aws-appsync-event-ws"
SUBSCRIPTION_ID = "openalarm-state"
BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0
DEFAULT_TIMEOUT = 300.0
RECEIVE_SLACK = 30.0


def auth_subprotocol(http_host: str, api_key: str) -> str:
    """Encode the authorization header object as a websocket subprotocol."""
    payload = json.dumps({"host": http_host, "Authorization": api_key})
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"header-{encoded}"


class RealtimeProtocolError(Exception):
    """The server said something that requires a reconnect."""


def handle_frame(frame: dict[str, Any]) -> str | None:
    """Classify a server frame into the action it demands."""
    kind = frame.get("type")
    if kind == "connection_ack":
        return "ack"
    if kind == "subscribe_success":
        return "subscribed"
    if kind == "data":
        return "refresh"
    if kind == "ka":
        return None
    if kind in ("subscribe_error", "broadcast_error", "error"):
        raise RealtimeProtocolError(json.dumps(frame.get("errors") or frame))
    return None


class OpenAlarmRealtime:
    """One websocket per config entry, reconnecting forever with backoff."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        realtime: dict[str, Any],
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._http_host = realtime["httpHost"]
        self._ws_host = realtime["realtimeHost"]
        self._channel = realtime["channel"]
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = BACKOFF_MIN
        while True:
            try:
                await self._listen()
                backoff = BACKOFF_MIN
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, RealtimeProtocolError, TimeoutError) as err:
                _LOGGER.debug("realtime connection ended: %s", err)
            except Exception:
                _LOGGER.exception("unexpected realtime failure")
            delay = backoff + random.uniform(0, backoff / 2)
            backoff = min(backoff * 2, BACKOFF_MAX)
            await asyncio.sleep(delay)

    async def _listen(self) -> None:
        url = f"wss://{self._ws_host}/event/realtime"
        protocols = (auth_subprotocol(self._http_host, self._api_key), SUBPROTOCOL)
        timeout = DEFAULT_TIMEOUT
        async with self._session.ws_connect(url, protocols=protocols, heartbeat=None) as ws:
            await ws.send_json({"type": "connection_init"})
            while True:
                msg = await ws.receive(timeout=timeout + RECEIVE_SLACK)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise RealtimeProtocolError(f"socket closed: {msg.type}")
                frame = json.loads(msg.data)
                if frame.get("type") == "connection_ack":
                    timeout = (frame.get("connectionTimeoutMs") or 300_000) / 1000
                    await ws.send_json(
                        {
                            "type": "subscribe",
                            "id": SUBSCRIPTION_ID,
                            "channel": self._channel,
                            "authorization": {
                                "Authorization": self._api_key,
                                "host": self._http_host,
                            },
                        }
                    )
                    continue
                action = handle_frame(frame)
                if action in ("subscribed", "refresh"):
                    await self._on_change()
