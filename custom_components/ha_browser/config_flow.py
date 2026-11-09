"""Config flow for HA Browser."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_LOOPBACK,
    CONF_ALLOW_PRIVATE,
    CONF_ALLOW_PUBLIC,
    CONF_ALLOWED_HOSTS,
    CONF_BLOCKED_HOSTS,
    CONF_HOME_URL,
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_SEARCH_URL,
    CONF_SESSION_TTL,
    CONF_TITLE,
    CONF_VERIFY_SSL,
    DEFAULTS,
    DOMAIN,
)

_MULTILINE = selector.TextSelector(selector.TextSelectorConfig(multiline=True))


def _schema(cur: dict[str, Any]) -> vol.Schema:
    d = {**DEFAULTS, **cur}
    return vol.Schema(
        {
            vol.Required(CONF_TITLE, default=d[CONF_TITLE]): str,
            vol.Required(CONF_ICON, default=d[CONF_ICON]): selector.IconSelector(),
            vol.Required(CONF_REQUIRE_ADMIN, default=d[CONF_REQUIRE_ADMIN]): bool,
            vol.Optional(CONF_HOME_URL, default=d[CONF_HOME_URL]): str,
            vol.Required(CONF_SEARCH_URL, default=d[CONF_SEARCH_URL]): str,
            vol.Required(CONF_ALLOW_PRIVATE, default=d[CONF_ALLOW_PRIVATE]): bool,
            vol.Required(CONF_ALLOW_PUBLIC, default=d[CONF_ALLOW_PUBLIC]): bool,
            vol.Required(CONF_ALLOW_LOOPBACK, default=d[CONF_ALLOW_LOOPBACK]): bool,
            vol.Optional(CONF_ALLOWED_HOSTS, default=d[CONF_ALLOWED_HOSTS]): _MULTILINE,
            vol.Optional(CONF_BLOCKED_HOSTS, default=d[CONF_BLOCKED_HOSTS]): _MULTILINE,
            vol.Required(CONF_VERIFY_SSL, default=d[CONF_VERIFY_SSL]): bool,
            vol.Required(CONF_SESSION_TTL, default=d[CONF_SESSION_TTL]): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=10080)
            ),
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    home = user_input.get(CONF_HOME_URL, "").strip()
    if home and urlsplit(home).scheme not in ("http", "https"):
        errors[CONF_HOME_URL] = "invalid_url"
    if "%s" not in user_input[CONF_SEARCH_URL]:
        errors[CONF_SEARCH_URL] = "invalid_search_url"
    return errors


class HABrowserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title="HA Browser", data={}, options=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return HABrowserOptionsFlow()


class HABrowserOptionsFlow(OptionsFlow):
    """Edit settings after setup."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or self.config_entry.options),
            errors=errors,
        )
