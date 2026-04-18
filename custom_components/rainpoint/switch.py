"""Switch entities — one per port on each RainPoint 2-zone timer.

Turning ON calls ``control_zone(mode=manual, duration=default)``. Turning OFF
cancels (``mode=off``). We clamp durations below 60 s to 60 s to match the
RainPoint Home app's minimum — protects the pump and valve batteries.
"""

from __future__ import annotations

import logging
from typing import Any, List

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homgarapi.devices import HomgarHubDevice, RainPoint2ZoneTimer_V2

from .const import DOMAIN
from .coordinator import RainPointCoordinator
from .entity import sub_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: RainPointCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: List[SwitchEntity] = []
    for hub in coord.hubs:
        for sub in hub.subdevices:
            if isinstance(sub, RainPoint2ZoneTimer_V2):
                for port in (1, 2):
                    entities.append(RainPointZoneSwitch(coord, hub, sub, port))
    async_add_entities(entities)


class RainPointZoneSwitch(CoordinatorEntity, SwitchEntity):
    """One port of a 2-zone timer."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RainPointCoordinator,
        hub: HomgarHubDevice,
        sub: RainPoint2ZoneTimer_V2,
        port: int,
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._sub = sub
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}"
        # e.g. "Sprinklers" / "Dripline" from the API's portDescribe field.
        self._attr_name = sub.port_label(port)

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    @property
    def is_on(self) -> bool:
        status = self._sub.ports.get(self._port)
        return bool(status and status.running)

    @property
    def extra_state_attributes(self) -> dict:
        s = self._sub.ports.get(self._port)
        if not s:
            return {}
        return {
            "duration_s": s.duration_s,
            "last_usage_l": (s.last_usage_dl or 0) / 10.0,
            "alarm": s.alarm,
            "wkstate": s.wkstate,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        duration = int(kwargs.get("duration", self.coordinator.default_duration_s))
        await self.coordinator.async_turn_on(self._sub, self._port, duration)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off(self._sub, self._port)
