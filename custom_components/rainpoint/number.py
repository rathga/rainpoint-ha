"""Per-zone run-duration number entities.

One ``number.<zone>_run_minutes`` per port on each 2-zone timer. The switch
entity reads this value when it's toggled on, so the common "slide to X,
then flick the switch" flow on a Lovelace card works without touching the
integration options. State survives HA restarts via ``RestoreNumber``.
"""

from __future__ import annotations

from typing import List, Optional

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homgarapi.devices import RainPoint2ZoneTimer_V2

from .const import (
    DEFAULT_RUN_MINUTES,
    DOMAIN,
    MAX_SLIDER_MINUTES,
    MIN_RUN_MINUTES,
)
from .coordinator import RainPointCoordinator
from .entity import sub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: RainPointCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: List[NumberEntity] = []
    for hub in coord.hubs:
        for sub in hub.subdevices:
            if isinstance(sub, RainPoint2ZoneTimer_V2):
                for port in sub.ports:
                    entities.append(ZoneRunMinutesNumber(coord, hub, sub, port))
    async_add_entities(entities)


class ZoneRunMinutesNumber(RestoreNumber):
    """Default run-duration (in minutes) applied when a zone switch flips on."""

    _attr_has_entity_name = True
    _attr_native_min_value = MIN_RUN_MINUTES
    _attr_native_max_value = MAX_SLIDER_MINUTES
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        coordinator: RainPointCoordinator,
        hub,
        sub: RainPoint2ZoneTimer_V2,
        port: int,
    ) -> None:
        self._coord = coordinator
        self._hub = hub
        self._sub = sub
        self._port = port
        self._attr_unique_id = f"rainpoint_{sub.sid}_port{port}_run_minutes"
        self._attr_name = f"{sub.port_label(port)} run minutes"
        self._value: float = DEFAULT_RUN_MINUTES

    @property
    def device_info(self):
        return sub_device_info(self._hub, self._sub)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._value = float(last.native_value)

    @property
    def native_value(self) -> Optional[float]:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = float(value)
        self.async_write_ha_state()
