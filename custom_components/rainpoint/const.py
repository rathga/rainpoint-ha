"""Constants for the RainPoint Smart+ integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "rainpoint"

# Keys used in config entries / options.
CONF_AREA_CODE = "area_code"          # HomGar country-code field, e.g. "44" UK, "1" US, "31" NL
CONF_DEFAULT_DURATION = "default_duration_s"
CONF_POLL_IDLE = "poll_interval_idle_s"
CONF_POLL_ACTIVE = "poll_interval_active_s"

DEFAULT_AREA_CODE = "44"
DEFAULT_DURATION_S = 300       # 5 min — sensible default when a switch is toggled on
DEFAULT_POLL_IDLE_S = 30
DEFAULT_POLL_ACTIVE_S = 5
MIN_RUN_SECONDS = 60           # server accepts lower but app enforces 60 to protect hardware

POLL_INTERVAL_IDLE = timedelta(seconds=DEFAULT_POLL_IDLE_S)
POLL_INTERVAL_ACTIVE = timedelta(seconds=DEFAULT_POLL_ACTIVE_S)

# Control modes per HomgarApi.control_zone.
MODE_OFF = 0
MODE_MANUAL = 1
MODE_SCHEDULED = 2

PLATFORMS = ["switch", "sensor", "binary_sensor"]
