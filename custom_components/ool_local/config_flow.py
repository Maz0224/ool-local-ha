"""Set up OOL entirely from Home Assistant: hub IPv4 + pasted Studio export JSON."""
from __future__ import annotations
import ipaddress
import json

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import DOMAIN
from .protocol import probe
from .settings import parse_settings

SETTINGS_JSON = "settings_json"


class OOLFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._host = None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                await self.async_set_unique_id("ool_" + host)
                self._abort_if_unique_id_configured()
                try:
                    await self.hass.async_add_executor_job(probe, host)
                except (OSError, ValueError, TimeoutError, ConnectionError):
                    errors["base"] = "cannot_connect"
                else:
                    self._host = host
                    return await self.async_step_import()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_import(self, user_input=None):
        errors = {}
        if user_input is not None:
            raw_text = user_input[SETTINGS_JSON]
            try:
                parsed = json.loads(raw_text)
                parse_settings(parsed)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                errors["base"] = "invalid_settings"
            else:
                return self.async_create_entry(
                    title="Orchestra of Lights",
                    data={CONF_HOST: self._host, SETTINGS_JSON: json.dumps(parsed)},
                )
        return self.async_show_form(
            step_id="import",
            data_schema=vol.Schema({
                vol.Required(SETTINGS_JSON): TextSelector(TextSelectorConfig(multiline=True))
            }),
            errors=errors,
        )
