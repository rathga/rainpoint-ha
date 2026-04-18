"""Options flow — tune default duration and polling intervals after setup.

Separate module (not nested inside ConfigFlow) so the config_flow.py file
stays focused on the auth step.
"""

from __future__ import annotations

from typing import Any, Dict

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_POLL_ACTIVE,
    CONF_POLL_IDLE,
    DEFAULT_DURATION_S,
    DEFAULT_POLL_ACTIVE_S,
    DEFAULT_POLL_IDLE_S,
    MIN_RUN_SECONDS,
)


class RainPointOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.entry.options
        schema = vol.Schema({
            vol.Optional(
                CONF_DEFAULT_DURATION,
                default=current.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_S),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_RUN_SECONDS, max=7200)),
            vol.Optional(
                CONF_POLL_IDLE,
                default=current.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE_S),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
            vol.Optional(
                CONF_POLL_ACTIVE,
                default=current.get(CONF_POLL_ACTIVE, DEFAULT_POLL_ACTIVE_S),
            ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
