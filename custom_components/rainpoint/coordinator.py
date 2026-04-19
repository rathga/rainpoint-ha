"""Polling coordinator — owns the HomgarApi client + the fetched device tree."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homgarapi.api import HomgarApi, HomgarApiException
from homgarapi.devices import HomgarHubDevice, RainPoint2ZoneTimer_V2

from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_POLL_ACTIVE,
    CONF_POLL_IDLE,
    COOLDOWN_SECONDS,
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
        # Cooldown: timestamp of the last control command per port.
        # Blocks further control for COOLDOWN_SECONDS — matches the
        # phone app's ~10-15 s lockout after any start/stop.
        self._last_command_at: Dict[Tuple[int, int], datetime] = {}
        # Stale-cache suppression: after a runs_until expires and the
        # cloud cache still says running, we stop believing wkstate>0
        # until the cache reports idle organically. Prevents the cache
        # from triggering a spurious idle->running transition on the
        # next poll (= invented countdown + spurious cooldown).
        self._stale_cache: Dict[Tuple[int, int], bool] = {}

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
        except Exception as e:  # noqa: BLE001
            # HomGar's HTTP endpoint occasionally drops the TCP
            # connection mid-request (RemoteDisconnected, ConnectionError,
            # ChunkedEncodingError, ...). One blip shouldn't flip every
            # rainpoint entity to "unavailable" — keep the last known
            # state and try again on the next poll. If we have no prior
            # data we still raise so the user sees the integration
            # actually failed to start.
            if self._hubs:
                _LOGGER.warning(
                    "rainpoint poll failed (%s: %s) — keeping last "
                    "known state, will retry next cycle.",
                    type(e).__name__, e,
                )
                return self._hubs
            raise UpdateFailed(f"HomGar poll failed: {e}") from e

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
                        in_grace = self._apply_grace(sub.sid, port_num, port, now)
                        # Skip transition bookkeeping while grace is forcing
                        # wkstate — port.running reflects what we commanded,
                        # not device truth. Otherwise a STOP that's still
                        # propagating gets re-detected as a fresh start when
                        # grace expires and the cache still says running.
                        if not in_grace:
                            self._observe_port(sub.sid, port_num, port, now)
                        if port.running:
                            any_running = True
        # Adaptive cadence: faster while a zone runs so state flips are quick.
        active = timedelta(seconds=self.poll_active_s)
        idle = timedelta(seconds=self.poll_idle_s)
        self.update_interval = active if any_running else idle
        return self._hubs

    def _apply_grace(self, sid: int, port_num: int, port, now: datetime) -> bool:
        """Force poll-reported wkstate back to what we optimistically
        commanded, for a short window after an HA-initiated control.

        HomGar accepts the command fast but takes 10-20 s to update
        ``/getDeviceStatus``. Without this, the very next poll flips
        the switch back to off mid-run (or on, for a stop). Returns
        ``True`` while the override is still in effect, so the caller
        can skip transition bookkeeping (the forced wkstate isn't the
        device's real state).
        """
        key = (sid, port_num)
        pending = self._grace.get(key)
        if pending is None:
            return False
        expires, wkstate = pending
        if now >= expires:
            self._grace.pop(key, None)
            return False
        port.wkstate = wkstate
        return True

    def _observe_port(self, sid: int, port_num: int, port, now: datetime) -> None:
        """Reconcile per-port bookkeeping from a fresh poll.

        Stamps ``runs_until`` only on observed idle->running transitions
        (``prev is False`` and ``is_running``). We deliberately do NOT
        stamp when ``prev is None`` (first poll after HA start) because
        the HomGar HTTP /getDeviceStatus cache can hold a stuck
        ``wkstate=33`` long after the valve has actually stopped — the
        phone app gets the real state via MQTT push, our polling
        doesn't always see the cache update. Stamping in that situation
        invents a fake countdown that just keeps re-arming.
        Trade-off: a genuine "HA restarted mid-run" scenario will read
        "Running... unknown" until the run ends and a new transition
        is observed.

        If the coordinator already has a ``runs_until`` for this key
        (e.g. stamped by an HA-initiated optimistic update), we leave
        it alone — otherwise we'd overwrite an accurate stamp with a
        device-reported duration that's usually the original run
        length, not the remaining time.
        """
        key = (sid, port_num)
        state_forced = False

        # (1) Ongoing stale-cache suppression. If we previously caught
        # the cache lagging reality, keep forcing idle locally until the
        # cache reports idle organically — that's our signal that the
        # push from the device has finally caught up.
        if self._stale_cache.get(key, False):
            if port.wkstate == 0:
                self._stale_cache[key] = False
            else:
                port.wkstate = 0
                state_forced = True

        # (2) Staleness self-correct: runs_until has passed but we still
        # see wkstate>0 (cache hasn't updated). Force idle AND raise the
        # stale-cache flag so subsequent polls within the lag window
        # don't re-trigger a fresh idle->running transition.
        scheduled_end = self._runs_until.get(key)
        if scheduled_end is not None and now > scheduled_end:
            if port.wkstate != 0:
                port.wkstate = 0
                state_forced = True
                self._stale_cache[key] = True

        prev = self._prev_running.get(key)
        is_running = port.running
        if is_running:
            if (
                prev is False
                and key not in self._runs_until
                and port.duration_s
            ):
                self._runs_until[key] = now + timedelta(seconds=int(port.duration_s))
        else:
            self._runs_until.pop(key, None)
        # Cooldown only fires for transitions we actually *observed* —
        # never for ones we forced ourselves (natural-end staleness
        # correct, or stale-cache suppression). External stops (phone
        # app, device schedule) still start a cooldown so the user
        # can't immediately slam a new RUN into the hardware.
        if (
            prev is not None
            and prev != is_running
            and not state_forced
        ):
            self._last_command_at[key] = now
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

    def cooldown_remaining_s(self, sid: int, port_num: int) -> int:
        """Seconds until the user can issue another control for this port.

        0 once the window has passed (or no command has ever fired).
        """
        last = self._last_command_at.get((sid, port_num))
        if last is None:
            return 0
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remaining = COOLDOWN_SECONDS - elapsed
        return max(0, int(remaining + 0.999))  # round up so a 0.3 s remainder still reads as 1 s

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
        try:
            await self.hass.async_add_executor_job(
                self._api.control_zone, hub, sub.address, port, mode, duration
            )
        except HomgarApiException as e:
            # Code 4 is undocumented in HomGar's error_code_* table — the
            # phone app would render it as "Unknown(4)". Empirically it's
            # returned when the device-side rejects the command (busy or
            # in transition). Surface a friendlier message and let the
            # next poll snap state back to reality.
            if getattr(e, "code", None) == 4:
                _LOGGER.warning(
                    "control_zone(sub=%s, port=%s, mode=%s, duration=%s) "
                    "rejected with HomGar code 4 (device-busy)",
                    sub.sid, port, mode, duration,
                )
                await self.async_request_refresh()
                raise HomeAssistantError(
                    "Valve was busy — wait a few seconds and try again."
                ) from e
            raise
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
        # Any control command (RUN or STOP) starts the cooldown window.
        self._last_command_at[key] = now
        grace_until = now + timedelta(seconds=30)
        if mode == MODE_MANUAL:
            # wkstate bit 0 = running, bit 5 = manual (0x21 = 33).
            port.wkstate = 0x21
            port.duration_s = int(duration)
            self._runs_until[key] = now + timedelta(seconds=int(duration))
            self._prev_running[key] = True
            self._grace[key] = (grace_until, 0x21)
            # Fresh RUN: the cache is about to show the new running
            # state; any lingering stale flag from the previous cycle
            # should be cleared.
            self._stale_cache.pop(key, None)
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

    def force_idle(self, sub, port_num: int) -> None:
        """Locally clear stuck "running" state without hitting the API.

        HomGar's HTTP /getDeviceStatus cache occasionally holds a stuck
        ``wkstate=33`` long after the valve has actually stopped (the
        phone app sees the real state via MQTT push). Calling control
        endpoint with mode=OFF in that case does nothing — the device
        is already idle. This lets HA agree with reality without an
        actual API roundtrip.
        """
        port = sub.ports.get(port_num) if hasattr(sub, "ports") else None
        if port is None:
            return
        key = (getattr(sub, "sid", None), port_num)
        port.wkstate = 0
        self._runs_until.pop(key, None)
        self._grace.pop(key, None)
        self._prev_running[key] = False
        self.async_set_updated_data(self._hubs)
