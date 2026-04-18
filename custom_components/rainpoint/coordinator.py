"""Polling coordinator — owns the HomgarApi client + the fetched device tree."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homgarapi.api import HomgarApi, HomgarApiException
from homgarapi.devices import HomgarHubDevice, RainPoint2ZoneTimer_V2

from datetime import datetime, timedelta, timezone

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
        # Per-(sid, port) end timestamps, stamped on each observed
        # idle->running transition. Cleared when the port stops. These
        # power the ``sensor.*_runs_until`` countdown entities.
        self._runs_until: Dict[Tuple[int, int], datetime] = {}
        # Previous running-state per port. ``None`` means "we've never
        # seen this port yet" — used to suppress stamping a run_until
        # for a port that was already running when HA first came up.
        self._prev_running: Dict[Tuple[int, int], Optional[bool]] = {}
        # Grace period: while a fresh HA-initiated control command is
        # propagating through HomGar's relay (control endpoint accepts
        # fast, but valve state in /getDeviceStatus takes 10-20 s to
        # catch up), we force the poll-reported wkstate to whatever we
        # just commanded. Without this the first poll after a RUN comes
        # back ``wkstate=0`` and the switch flips back to off mid-run.
        self._grace: Dict[Tuple[int, int], Tuple[datetime, int]] = {}

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
        now = datetime.now(timezone.utc)
        for hub in self._hubs:
            self._api.get_device_status(hub)
            for sub in hub.subdevices:
                if isinstance(sub, RainPoint2ZoneTimer_V2):
                    for port_num, port in sub.ports.items():
                        self._apply_grace(sub.sid, port_num, port, now)
                        self._observe_port(sub.sid, port_num, port, now)
                        if port.running:
                            any_running = True
        # Adaptive cadence: faster while a zone runs so state flips are quick.
        active = timedelta(seconds=self.poll_active_s)
        idle = timedelta(seconds=self.poll_idle_s)
        self.update_interval = active if any_running else idle
        return self._hubs

    def _apply_grace(self, sid: int, port_num: int, port, now: datetime) -> None:
        """Force poll-reported wkstate back to what we optimistically
        commanded, for a short window after an HA-initiated control.

        HomGar accepts the command fast but takes 10-20 s to update
        ``/getDeviceStatus``. Without this, the very next poll flips
        the switch back to off mid-run (or on, for a stop).
        """
        key = (sid, port_num)
        pending = self._grace.get(key)
        if pending is None:
            return
        expires, wkstate = pending
        if now >= expires:
            self._grace.pop(key, None)
            return
        port.wkstate = wkstate

    def _observe_port(self, sid: int, port_num: int, port, now: datetime) -> None:
        """Reconcile per-port bookkeeping from a fresh poll.

        Stamps ``runs_until`` when we have no existing stamp and the port
        is reported running:

        * First poll after HA start (``prev is None``): best-effort —
          we don't know when it actually started, but ``duration_s``
          gives an upper bound that's much better than "unknown".
        * Observed idle->running transition (``prev is False``): a
          genuine fresh start (e.g. via the phone app).

        If the coordinator already has a ``runs_until`` for this key
        (e.g. stamped by an HA-initiated optimistic update), we leave
        it alone — otherwise we'd overwrite an accurate stamp with a
        device-reported duration that's usually the original run
        length, not the remaining time.
        """
        key = (sid, port_num)
        prev = self._prev_running.get(key)
        is_running = port.running
        if is_running:
            if key not in self._runs_until and port.duration_s:
                self._runs_until[key] = now + timedelta(seconds=int(port.duration_s))
        else:
            self._runs_until.pop(key, None)
        self._prev_running[key] = is_running

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

    def runs_until(self, sid: int, port_num: int) -> Optional[datetime]:
        """End-time for a currently-running zone, or ``None`` when idle or
        when we weren't watching at the moment it started."""
        return self._runs_until.get((sid, port_num))

    async def async_control(
        self, sub, port: int, mode: int, duration: int
    ) -> None:
        """Send a control-zone command.

        Updates local state optimistically *before* hitting the API so
        switch.is_on / sensor.runs_until / sensor.remaining flip
        immediately — otherwise the UI would wait up to one poll cycle
        (~5 s) for the device status refresh to reflect the change. The
        next real poll reconciles; if the API call fails it'll snap
        back on the subsequent successful poll.
        """
        if mode == MODE_MANUAL and duration < MIN_RUN_SECONDS:
            duration = MIN_RUN_SECONDS
        hub = self.find_hub_for_sub(sub.sid)
        if hub is None:
            raise UpdateFailed(f"No hub owns sub sid={sub.sid}")
        self._apply_optimistic(sub, port, mode, duration)
        # Push updated state to all listeners synchronously.
        self.async_set_updated_data(self._hubs)
        await self.hass.async_add_executor_job(
            self._api.control_zone, hub, sub.address, port, mode, duration
        )
        await self.async_request_refresh()

    def _apply_optimistic(
        self, sub, port_num: int, mode: int, duration: int
    ) -> None:
        """Mirror what we expect control_zone to do, on the local device
        tree + coordinator bookkeeping, so UI updates don't have to wait
        for the next poll.

        The grace-window entry in ``_grace`` lets ``_apply_grace`` pin
        the commanded wkstate across the following ~30 s of polls
        (HomGar's relay takes that long to reflect the new state).
        """
        port = sub.ports.get(port_num) if hasattr(sub, "ports") else None
        if port is None:
            return
        key = (getattr(sub, "sid", None), port_num)
        now = datetime.now(timezone.utc)
        grace_until = now + timedelta(seconds=30)
        if mode == MODE_MANUAL:
            # wkstate bit 0 = running, bit 5 = manual (0x21 = 33).
            port.wkstate = 0x21
            port.duration_s = int(duration)
            self._runs_until[key] = now + timedelta(seconds=int(duration))
            self._prev_running[key] = True
            self._grace[key] = (grace_until, 0x21)
        elif mode == MODE_OFF:
            port.wkstate = 0
            # Shorter grace for STOP — HomGar either accepts it quickly
            # or rejects it outright (rate-limit code 4). If rejected,
            # we'd rather the UI flip back to "running" promptly than
            # keep lying to the user.
            self._grace[key] = (now + timedelta(seconds=5), 0)

    async def async_turn_on(self, sub, port: int, duration: int) -> None:
        await self.async_control(sub, port, MODE_MANUAL, duration)

    async def async_turn_off(self, sub, port: int) -> None:
        await self.async_control(sub, port, MODE_OFF, 0)
