"""Sensor entities — RF RSSI, last-usage litres, rainfall from rain sensor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfLength,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homgarapi.devices import (
    HomgarHubDevice,
    RainPoint2ZoneTimer_V2,
    RainPointRainSensor,
)

from .const import DOMAIN
from .coordinator import RainPointCoordinator
from .entity import sub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: RainPointCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: List[SensorEntity] = []
    for hub in coord.hubs:
        for sub in hub.subdevices:
            if isinstance(sub, RainPoint2ZoneTimer_V2):
                entities.append(TimerRssiSensor(coord, hub, sub))
                for port in (1, 2):
                    entities.append(TimerLastUsageSensor(coord, hub, sub, port))
                    entities.append(ZoneRunsUntilSensor(coord, hub, sub, port))
                    entities.append(ZoneRemainingSensor(coord, hub, sub, port))
                    entities.append(ZoneCooldownSensor(coord, hub, sub, port))
            elif isinstance(sub, RainPointRainSensor):
                entities.append(RainfallTotalSensor(coord, hub, sub))
                entities.append(RainfallHourSensor(coord, hub, sub))
                entities.append(RainfallDaySensor(coord, hub, sub))
    async_add_entities(entities)


class _BaseSub(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, hub, sub):
        super().__init__(coordinator)
        self._hub = hub
        self._sub = sub

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)


class TimerRssiSensor(_BaseSub):
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, hub, sub):
        super().__init__(coordinator, hub, sub)
        self._attr_unique_id = f"rainpoint_{sub.sid}_rssi"
        self._attr_name = "RF RSSI"

    @property
    def native_value(self) -> Optional[int]:
        return self._sub.rf_rssi


class TimerLastUsageSensor(_BaseSub):
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, hub, sub, port: int):
        super().__init__(coordinator, hub, sub)
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_last_usage"
        self._attr_name = f"{sub.port_label(port)} last-cycle usage"

    @property
    def native_value(self) -> Optional[float]:
        s = self._sub.ports.get(self._port)
        if not s or s.last_usage_dl is None:
            return None
        return s.last_usage_dl / 10.0


class ZoneRunsUntilSensor(_BaseSub):
    """Timestamp of when a running zone is expected to stop.

    Returns ``None`` when the zone is idle, and also when we weren't
    watching at the moment it started (e.g. HA restarted mid-run) — a
    fresh start stamp is only recorded on observed idle->running
    transitions. HA renders this as a relative countdown in any card
    that knows about ``device_class: timestamp``.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator, hub, sub, port: int):
        super().__init__(coordinator, hub, sub)
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_runs_until"
        self._attr_name = f"{sub.port_label(port)} runs until"

    @property
    def native_value(self) -> Optional[datetime]:
        return self.coordinator.runs_until(self._sub.sid, self._port)


class ZoneCooldownSensor(_BaseSub):
    """Seconds until the next RUN/STOP command is allowed for this port.

    Matches the phone app's ~10-15 s lockout after any control command.
    Ticks down every second; 0 means no cooldown in effect.
    """

    _attr_icon = "mdi:timer-sand-paused"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, hub, sub, port: int):
        super().__init__(coordinator, hub, sub)
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_cooldown"
        self._attr_name = f"{sub.port_label(port)} cooldown"
        self._unsub_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_tick = async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=1)
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        await super().async_will_remove_from_hass()

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self.coordinator.cooldown_remaining_s(self._sub.sid, self._port)


class ZoneRemainingSensor(_BaseSub):
    """Live mm:ss countdown, ticking every second while a zone runs.

    Same source-of-truth as ``ZoneRunsUntilSensor`` (the coordinator's
    ``runs_until`` stamp) but formatted as ``"MM:SS"`` so dashboards can
    show an always-visible live counter without relying on HA's native
    relative-timestamp formatting (which only shows seconds inside the
    last minute). Idle → ``None`` → renders as "unknown" in cards; the
    intended use is inside a conditional card that only appears while
    the zone is running.
    """

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, hub, sub, port: int):
        super().__init__(coordinator, hub, sub)
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_remaining"
        self._attr_name = f"{sub.port_label(port)} remaining"
        self._unsub_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_tick = async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=1)
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        await super().async_will_remove_from_hass()

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Optional[str]:
        end = self.coordinator.runs_until(self._sub.sid, self._port)
        if end is None:
            return None
        remaining = (end - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return None
        mm = int(remaining // 60)
        ss = int(remaining % 60)
        return f"{mm:02d}:{ss:02d}"


class RainfallBase(_BaseSub):
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_state_class = SensorStateClass.MEASUREMENT


class RainfallTotalSensor(RainfallBase):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, hub, sub):
        super().__init__(coordinator, hub, sub)
        self._attr_unique_id = f"rainpoint_{sub.sid}_rain_total"
        self._attr_name = "Rainfall total"

    @property
    def native_value(self) -> Optional[float]:
        return getattr(self._sub, "rainfall_mm_total", None)


class RainfallHourSensor(RainfallBase):
    def __init__(self, coordinator, hub, sub):
        super().__init__(coordinator, hub, sub)
        self._attr_unique_id = f"rainpoint_{sub.sid}_rain_1h"
        self._attr_name = "Rainfall 1h"

    @property
    def native_value(self) -> Optional[float]:
        return getattr(self._sub, "rainfall_mm_hour", None)


class RainfallDaySensor(RainfallBase):
    def __init__(self, coordinator, hub, sub):
        super().__init__(coordinator, hub, sub)
        self._attr_unique_id = f"rainpoint_{sub.sid}_rain_24h"
        self._attr_name = "Rainfall 24h"

    @property
    def native_value(self) -> Optional[float]:
        return getattr(self._sub, "rainfall_mm_daily", None)
