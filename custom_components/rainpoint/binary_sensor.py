"""Binary sensors — zone-running, hub-connected, low-battery on the rain sensor."""

from __future__ import annotations

from typing import List, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homgarapi.devices import (
    RainPoint2ZoneTimer_V2,
    RainPointDisplayHubV2,
    RainPointRainSensor,
)

from .const import DOMAIN
from .coordinator import RainPointCoordinator
from .entity import hub_device_info, sub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: RainPointCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: List[BinarySensorEntity] = []
    for hub in coord.hubs:
        if isinstance(hub, RainPointDisplayHubV2):
            entities.append(HubConnectedBinarySensor(coord, hub))
        for sub in hub.subdevices:
            if isinstance(sub, RainPoint2ZoneTimer_V2):
                for port in sub.ports:
                    entities.append(ZoneRunningBinarySensor(coord, hub, sub, port))
                entities.append(TimerLowBatteryBinarySensor(coord, hub, sub))
            elif isinstance(sub, RainPointRainSensor):
                entities.append(RainSensorLowBatteryBinarySensor(coord, hub, sub))
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
        self._attr_name = f"{sub.port_label(port)} running".strip()

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    @property
    def is_on(self) -> bool:
        status = self._sub.ports.get(self._port)
        return bool(status and status.running)


class HubConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Is the hub online per HomGar's view of it (``connected`` status)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RainPointCoordinator, hub):
        super().__init__(coordinator)
        self._hub = hub
        self._attr_unique_id = f"rainpoint_hub_{hub.mid}_connected"
        self._attr_name = "Connected"

    @property
    def device_info(self):
        return hub_device_info(self._hub)

    @property
    def is_on(self) -> Optional[bool]:
        return getattr(self._hub, "connected", None)


class TimerLowBatteryBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """On when the 2-zone timer firmware reports battery as low.

    Mirrors the rain-sensor convention: ``STA_BAT`` is an enum where
    1 = normal, 3 = low. Other values are treated as unknown so a
    firmware we haven't seen doesn't fire a false positive.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RainPointCoordinator, hub, sub):
        super().__init__(coordinator)
        self._hub = hub
        self._sub = sub
        self._attr_unique_id = f"rainpoint_{sub.sid}_low_battery"
        self._attr_name = "Battery low"

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    @property
    def is_on(self) -> Optional[bool]:
        state = getattr(self._sub, "battery_state", None)
        if state is None:
            return None
        return state == 3


class RainSensorLowBatteryBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """On when the rain sensor firmware reports battery as low.

    ``STA_BAT`` (dpCode 31) is an enum per the productModel catalog:
    ``1`` = normal, ``3`` = low. Anything else we treat as unknown /
    off so we don't fire a false positive on a firmware we haven't seen.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RainPointCoordinator, hub, sub):
        super().__init__(coordinator)
        self._hub = hub
        self._sub = sub
        self._attr_unique_id = f"rainpoint_{sub.sid}_low_battery"
        self._attr_name = "Battery low"

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    @property
    def is_on(self) -> Optional[bool]:
        state = getattr(self._sub, "battery_state", None)
        if state is None:
            return None
        return state == 3
