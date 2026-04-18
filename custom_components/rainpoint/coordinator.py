"""Polling coordinator — owns the HomgarApi client + the fetched device tree."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homgarapi.api import HomgarApi, HomgarApiException
from homgarapi.devices import HomgarHubDevice, RainPoint2ZoneTimer_V2

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_POLL_ACTIVE,
    CONF_POLL_IDLE,
    DEFAULT_DURATION_S,
    DEFAULT_POLL_ACTIVE_S,
    DEFAULT_POLL_IDLE_S,
    DOMAIN,
    MIN_RUN_SECONDS,
    MODE_MANUAL,
    MODE_OFF,
)

_LOGGER = logging.getLogger(__name__)


class RainPointCoordinator(DataUpdateCoordinator[List[HomgarHubDevice]]):
    """Pulls the device tree + status from HomGar cloud, at a variable cadence."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
        area_code: str,
        entry: ConfigEntry | None = None,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_POLL_IDLE_S),
        )
        self.email = email
        self.password = password
        self.area_code = area_code
        self.entry = entry
        self._cache: dict = {}
        self._api: HomgarApi = HomgarApi(auth_cache=self._cache)
        # Full tree, refreshed when we see device-tree-changing events.
        self._hubs: List[HomgarHubDevice] = []
        self._tree_loaded = False

    # ------------------------------------------------------------------
    # Option-backed values with safe defaults when no entry is supplied.

    @property
    def default_duration_s(self) -> int:
        if self.entry is None:
            return DEFAULT_DURATION_S
        return self.entry.options.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_S)

    @property
    def poll_idle_s(self) -> int:
        if self.entry is None:
            return DEFAULT_POLL_IDLE_S
        return self.entry.options.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE_S)

    @property
    def poll_active_s(self) -> int:
        if self.entry is None:
            return DEFAULT_POLL_ACTIVE_S
        return self.entry.options.get(CONF_POLL_ACTIVE, DEFAULT_POLL_ACTIVE_S)

    async def _async_update_data(self) -> List[HomgarHubDevice]:
        try:
            return await self.hass.async_add_executor_job(self._sync_refresh)
        except HomgarApiException as e:
            raise UpdateFailed(f"HomGar API error {e.code}: {e.msg}") from e

    def _sync_refresh(self) -> List[HomgarHubDevice]:
        self._api.ensure_logged_in(self.email, self.password, area_code=self.area_code)
        if not self._tree_loaded:
            homes = self._api.get_homes()
            hubs: List[HomgarHubDevice] = []
            for home in homes:
                hubs.extend(self._api.get_devices_for_hid(home.hid))
            self._hubs = hubs
            self._tree_loaded = True
        any_running = False
        for hub in self._hubs:
            self._api.get_device_status(hub)
            for sub in hub.subdevices:
                if isinstance(sub, RainPoint2ZoneTimer_V2):
                    for port in sub.ports.values():
                        if port.running:
                            any_running = True
        # Adaptive cadence: faster while a zone runs so state flips are quick.
        active = timedelta(seconds=self.poll_active_s)
        idle = timedelta(seconds=self.poll_idle_s)
        self.update_interval = active if any_running else idle
        return self._hubs

    # ------------------------------------------------------------------
    # Public helpers used by entities.

    @property
    def hubs(self) -> List[HomgarHubDevice]:
        return self._hubs

    def find_hub_for_sub(self, sub_sid: int) -> Optional[HomgarHubDevice]:
        for hub in self._hubs:
            for sub in hub.subdevices:
                if getattr(sub, "sid", None) == sub_sid:
                    return hub
        return None

    async def async_control(
        self, sub, port: int, mode: int, duration: int
    ) -> None:
        """Send a control-zone command and refresh once it lands."""
        if mode == MODE_MANUAL and duration < MIN_RUN_SECONDS:
            duration = MIN_RUN_SECONDS
        hub = self.find_hub_for_sub(sub.sid)
        if hub is None:
            raise UpdateFailed(f"No hub owns sub sid={sub.sid}")
        await self.hass.async_add_executor_job(
            self._api.control_zone, hub, sub.address, port, mode, duration
        )
        await self.async_request_refresh()

    async def async_turn_on(self, sub, port: int, duration: int) -> None:
        await self.async_control(sub, port, MODE_MANUAL, duration)

    async def async_turn_off(self, sub, port: int) -> None:
        await self.async_control(sub, port, MODE_OFF, 0)
