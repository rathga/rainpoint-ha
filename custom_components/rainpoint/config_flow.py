"""Config flow: simple email + password + area_code (UK '44' default)."""

from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult

from homgarapi.api import HomgarApi, HomgarApiException

from .const import CONF_AREA_CODE, DEFAULT_AREA_CODE, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_AREA_CODE, default=DEFAULT_AREA_CODE): str,
})


class RainPointConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            unique_id = email.strip().lower()
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            try:
                await self.hass.async_add_executor_job(
                    _validate_login,
                    email,
                    user_input[CONF_PASSWORD],
                    user_input.get(CONF_AREA_CODE, DEFAULT_AREA_CODE),
                )
            except HomgarApiException as e:
                _LOGGER.warning("HomGar login failed: %s", e)
                errors["base"] = "auth"
            except Exception:  # pragma: no cover — network edges
                _LOGGER.exception("Unexpected login error")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=email, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )


def _validate_login(email: str, password: str, area_code: str) -> None:
    """Throws HomgarApiException if credentials are bad."""
    api = HomgarApi()
    api.login(email, password, area_code=area_code)
    api.get_homes()  # lightweight second call to ensure token works
