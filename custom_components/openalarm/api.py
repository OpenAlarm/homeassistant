"""Thin client for the OpenAlarm API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import KIND_ALARM, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def trace_fields(body: dict[str, Any]) -> tuple[Any, Any]:
    return body.get("traceId"), (body.get("data") or {}).get("environment")


class OpenAlarmError(Exception):
    """Raised when the API cannot be reached or answers unexpectedly."""


class InvalidAuth(OpenAlarmError):
    """Raised when the API key is missing, disabled or unknown."""


class NotFound(OpenAlarmError):
    """Raised when the key cannot reach that trigger.

    The API deliberately answers 404 for a trigger that is out of the key's
    scope, deactivated, or absent, so this covers all three.
    """


class OpenAlarmClient:
    """Calls the OpenAlarm API on behalf of one config entry."""

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, base_url: str
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                headers={"X-API-Key": self._api_key, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status in (401, 403):
                    raise InvalidAuth("The OpenAlarm API rejected this key")
                if response.status == 404:
                    raise NotFound(f"OpenAlarm has nothing at {path} for this key")
                if response.status >= 400:
                    raise OpenAlarmError(
                        f"OpenAlarm answered {response.status} for {path}"
                    )
                body = await response.json()
        except asyncio.TimeoutError as err:
            raise OpenAlarmError(f"OpenAlarm timed out on {path}") from err
        except aiohttp.ClientError as err:
            raise OpenAlarmError(f"Could not reach OpenAlarm: {err}") from err

        if not isinstance(body, dict):
            raise OpenAlarmError("OpenAlarm returned an unexpected payload")

        _LOGGER.debug(
            "OpenAlarm %s traceId=%s environment=%s", path, *trace_fields(body)
        )
        return body

    async def describe(self) -> dict[str, Any]:
        """Return the inventory this key can reach."""
        return (await self._get("/v1/integration/describe")).get("data") or {}

    async def state(self) -> dict[str, Any]:
        """Return the live state of every alarm this key can reach."""
        return (await self._get("/v1/integration/state")).get("data") or {}

    async def act(
        self, kind: str, trigger_id: str, action: str, mode: str | None = None
    ) -> dict[str, Any]:
        """Run an action against one alarm or panic button."""
        root = "alarm" if kind == KIND_ALARM else "panic"
        path = f"/v1/{root}/{trigger_id}/{action}"
        if mode:
            path = f"{path}/{mode}"
        return await self._get(path)
