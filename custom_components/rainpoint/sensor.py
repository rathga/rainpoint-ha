"""Sensor entities — RF RSSI, last-usage litres, rainfall from rain sensor."""

from __future__ import annotations

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homgarapi.devices import (
    HomgarHubDevice,
    RainPoint2ZoneTimer_V2,
    RainPointRainSensor,
)

from .const import DOMAIN
from .coordinator import RainPointCoordinator


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
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"sub-{self._sub.sid}")},
            "name": self._sub.name,
            "model": self._sub.model,
            "via_device": (DOMAIN, f"hub-{self._hub.mid}"),
            "manufacturer": "RainPoint",
        }


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
        self._attr_name = f"Port {port} last-cycle usage"

    @property
    def native_value(self) -> Optional[float]:
        s = self._sub.ports.get(self._port)
        if not s or s.last_usage_dl is None:
            return None
        return s.last_usage_dl / 10.0


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
