"""Config flow for OpenAlarm."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import InvalidAuth, OpenAlarmClient, OpenAlarmError
from .const import (
    CONF_ALARM,
    CONF_API_KEY,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_READINESS,
    CONF_SENSORS,
    DEFAULT_BASE_URL,
    DOMAIN,
)

API_KEY_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
)

SENSORS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SENSORS): EntitySelector(
            EntitySelectorConfig(domain="binary_sensor", multiple=True)
        )
    }
)


class OpenAlarmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Take an API key, show what it can reach, and set up one location."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._base_url: str = DEFAULT_BASE_URL
        self._locations: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OpenAlarmOptionsFlow:
        return OpenAlarmOptionsFlow()

    async def _describe(self, api_key: str, base_url: str) -> list[dict[str, Any]]:
        client = OpenAlarmClient(async_get_clientsession(self.hass), api_key, base_url)
        data = await client.describe()
        return list(data.get("locations") or [])

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            try:
                locations = await self._describe(api_key, self._base_url)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except OpenAlarmError:
                errors["base"] = "cannot_connect"
            else:
                if not locations:
                    errors["base"] = "no_locations"
                else:
                    self._api_key = api_key
                    self._locations = locations
                    return await self.async_step_location()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if len(self._locations) == 1 and user_input is None:
            user_input = {CONF_LOCATION_ID: self._locations[0]["id"]}

        if user_input is not None:
            location_id = user_input[CONF_LOCATION_ID]
            await self.async_set_unique_id(location_id)
            self._abort_if_unique_id_configured()

            location = next(
                (l for l in self._locations if l["id"] == location_id),
                {"id": location_id, "name": location_id},
            )
            name = location.get("name") or location_id
            return self.async_create_entry(
                title=name,
                data={
                    CONF_API_KEY: self._api_key,
                    CONF_LOCATION_ID: location_id,
                    CONF_LOCATION_NAME: name,
                },
            )

        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=location["id"],
                                    label=self._describe_location(location),
                                )
                                for location in self._locations
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start over when the stored key stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a replacement key for the same location."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = DEFAULT_BASE_URL
            try:
                locations = await self._describe(api_key, base_url)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except OpenAlarmError:
                errors["base"] = "cannot_connect"
            else:
                wanted = entry.data[CONF_LOCATION_ID]
                if not any(l.get("id") == wanted for l in locations):
                    errors["base"] = "wrong_location"
                else:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_API_KEY: api_key}
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            description_placeholders={
                "location": entry.data.get(CONF_LOCATION_NAME) or entry.title
            },
            errors=errors,
        )

    @staticmethod
    def _describe_location(location: dict[str, Any]) -> str:
        alarms = len(location.get("alarms") or [])
        panics = len(location.get("panicButtons") or [])
        name = location.get("name") or location.get("id")
        parts = []
        if alarms:
            parts.append(f"{alarms} alarm" + ("s" if alarms != 1 else ""))
        if panics:
            parts.append(f"{panics} panic button" + ("s" if panics != 1 else ""))
        return f"{name} ({', '.join(parts)})" if parts else str(name)



class OpenAlarmOptionsFlow(OptionsFlow):
    """Per alarm, the sensors that must be clear before it arms."""

    def __init__(self) -> None:
        self._alarm_id: str | None = None
        self._alarm_name: str | None = None

    def _alarms(self) -> list[dict[str, Any]]:
        data = getattr(self.config_entry, "runtime_data", None)
        if data is None:
            return []
        return [a for a in data.inventory.alarms() if a.get("id")]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        alarms = self._alarms()
        if not alarms:
            return self.async_abort(reason="not_loaded")

        if user_input is not None:
            self._alarm_id = user_input[CONF_ALARM]
        elif len(alarms) == 1:
            self._alarm_id = alarms[0]["id"]

        if self._alarm_id is not None:
            self._alarm_name = next(
                (a.get("name") or a["id"] for a in alarms if a["id"] == self._alarm_id),
                self._alarm_id,
            )
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALARM): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=alarm["id"],
                                    label=alarm.get("name") or alarm["id"],
                                )
                                for alarm in alarms
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        readiness = dict(self.config_entry.options.get(CONF_READINESS) or {})
        if user_input is not None:
            picked = list(user_input.get(CONF_SENSORS) or [])
            if picked:
                readiness[self._alarm_id] = picked
            else:
                readiness.pop(self._alarm_id, None)
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_READINESS: readiness}
            )

        return self.async_show_form(
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(
                SENSORS_SCHEMA, {CONF_SENSORS: readiness.get(self._alarm_id) or []}
            ),
            description_placeholders={"alarm": self._alarm_name or ""},
        )
