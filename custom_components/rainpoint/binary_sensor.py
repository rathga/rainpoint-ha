"""Binary sensors — one 'running' indicator per zone."""

from __future__ import annotations

from typing import List

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homgarapi.devices import RainPoint2ZoneTimer_V2

from .const import DOMAIN
from .coordinator import RainPointCoordinator
from .entity import sub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: RainPointCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: List[BinarySensorEntity] = []
    for hub in coord.hubs:
        for sub in hub.subdevices:
            if isinstance(sub, RainPoint2ZoneTimer_V2):
                for port in (1, 2):
                    entities.append(ZoneRunningBinarySensor(coord, hub, sub, port))
    async_add_entities(entities)


class ZoneRunningBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: RainPointCoordinator, hub, sub, port: int):
        super().__init__(coordinator)
        self._hub = hub
        self._sub = sub
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_running"
        self._attr_name = f"{sub.port_label(port)} running"

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    @property
    def is_on(self) -> bool:
        status = self._sub.ports.get(self._port)
        return bool(status and status.running)
