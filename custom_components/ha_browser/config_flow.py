"""HA Browser — config and options flows."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.selector import BooleanSelector, TextSelector

from .const import (
    CONF_ADBLOCK,
    CONF_HOMEPAGE,
    CONF_VERIFY_TLS,
    DEFAULT_ADBLOCK,
    DEFAULT_HOMEPAGE,
    DEFAULT_VERIFY_TLS,
    DOMAIN,
)


class HABrowserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance setup: homepage + adblock on/off."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="HA Browser",
                data={},
                options={
                    CONF_HOMEPAGE: user_input[CONF_HOMEPAGE],
                    CONF_ADBLOCK: user_input[CONF_ADBLOCK],
                    CONF_VERIFY_TLS: user_input[CONF_VERIFY_TLS],
                },
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOMEPAGE, default=DEFAULT_HOMEPAGE): TextSelector(),
                    vol.Required(CONF_ADBLOCK, default=DEFAULT_ADBLOCK): BooleanSelector(),
                    vol.Required(CONF_VERIFY_TLS, default=DEFAULT_VERIFY_TLS): BooleanSelector(),
                }
            ),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return HABrowserOptionsFlow()


class HABrowserOptionsFlow(OptionsFlow):
    """Change the homepage / adblock setting after setup."""

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOMEPAGE,
                        default=options.get(CONF_HOMEPAGE, DEFAULT_HOMEPAGE),
                    ): TextSelector(),
                    vol.Required(
                        CONF_ADBLOCK,
                        default=options.get(CONF_ADBLOCK, DEFAULT_ADBLOCK),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_VERIFY_TLS,
                        default=options.get(CONF_VERIFY_TLS, DEFAULT_VERIFY_TLS),
                    ): BooleanSelector(),
                }
            ),
        )
