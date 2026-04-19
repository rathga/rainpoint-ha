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
DEFAULT_DURATION_S = 300       # 5 min — fallback when no per-zone number is set yet
DEFAULT_POLL_IDLE_S = 30
DEFAULT_POLL_ACTIVE_S = 5
MIN_RUN_SECONDS = 60           # server accepts lower but app enforces 60 to protect hardware
COOLDOWN_SECONDS = 15          # match the phone app (10-15 s observed lockout after any control)

# Per-zone ``number.*_run_minutes`` slider bounds.
# Kept at 120 because a 1-720 linear slider would be unusable for
# picking the typical 5-30 min range. Long soaks go through the
# rainpoint.run_zone service instead, whose cap is MAX_RUN_MINUTES.
MIN_RUN_MINUTES = 1
MAX_SLIDER_MINUTES = 120
DEFAULT_RUN_MINUTES = 5
# Hard cap for any run (phone app also enforces this upper bound).
MAX_RUN_MINUTES = 720

# Custom service names
SERVICE_RUN_ZONE = "run_zone"
SERVICE_FORCE_OFF = "force_off"
ATTR_DURATION = "duration"

POLL_INTERVAL_IDLE = timedelta(seconds=DEFAULT_POLL_IDLE_S)
POLL_INTERVAL_ACTIVE = timedelta(seconds=DEFAULT_POLL_ACTIVE_S)

# Control modes per HomgarApi.control_zone.
MODE_OFF = 0
MODE_MANUAL = 1
MODE_SCHEDULED = 2

PLATFORMS = ["switch", "sensor", "binary_sensor", "number"]
