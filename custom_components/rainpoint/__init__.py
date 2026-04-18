"""RainPoint Smart+ integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from homgarapi.devices import RainPoint2ZoneTimer_V2

from .const import (
    ATTR_DURATION,
    CONF_AREA_CODE,
    DEFAULT_AREA_CODE,
    DOMAIN,
    MIN_RUN_SECONDS,
    PLATFORMS,
    SERVICE_RUN_ZONE,
)
from .coordinator import RainPointCoordinator
from .entity import hub_device_info

_LOGGER = logging.getLogger(__name__)

RUN_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_RUN_SECONDS, max=7200)
        ),
    }
)


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so new polling intervals/defaults take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RainPoint Smart+ from a config entry."""
    coordinator = RainPointCoordinator(
        hass,
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        area_code=entry.data.get(CONF_AREA_CODE, DEFAULT_AREA_CODE),
        entry=entry,
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    # First refresh validates credentials and populates the device tree.
    await coordinator.async_config_entry_first_refresh()

    # Register each hub explicitly so sub-device `via_device` references
    # resolve in the HA device registry.
    device_reg = dr.async_get(hass)
    for hub in coordinator.hubs:
        info = hub_device_info(hub)
        device_reg.async_get_or_create(config_entry_id=entry.entry_id, **info)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the ``rainpoint.run_zone`` service (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_RUN_ZONE):
        return

    async def _run_zone(call: ServiceCall) -> None:
        reg = er.async_get(hass)
        duration = int(call.data[ATTR_DURATION])
        targets: list[tuple[RainPointCoordinator, RainPoint2ZoneTimer_V2, int]] = []
        for entity_id in call.data[ATTR_ENTITY_ID]:
            entry = reg.async_get(entity_id)
            if entry is None or entry.platform != DOMAIN or entry.domain != "switch":
                _LOGGER.warning("rainpoint.run_zone: %s is not a rainpoint switch", entity_id)
                continue
            # unique_id format from switch.py: rainpoint_<sid>_port<N>
            parts = (entry.unique_id or "").split("_")
            if len(parts) != 3 or parts[0] != "rainpoint" or not parts[2].startswith("port"):
                _LOGGER.warning(
                    "rainpoint.run_zone: unexpected unique_id %r on %s",
                    entry.unique_id, entity_id,
                )
                continue
            try:
                sid = int(parts[1])
                port = int(parts[2][4:])
            except ValueError:
                continue
            for coord in hass.data.get(DOMAIN, {}).values():
                for hub in coord.hubs:
                    for sub in hub.subdevices:
                        if (
                            isinstance(sub, RainPoint2ZoneTimer_V2)
                            and getattr(sub, "sid", None) == sid
                        ):
                            targets.append((coord, sub, port))
        for coord, sub, port in targets:
            await coord.async_turn_on(sub, port, duration)

    hass.services.async_register(
        DOMAIN, SERVICE_RUN_ZONE, _run_zone, schema=RUN_ZONE_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RUN_ZONE)
    return unloaded
