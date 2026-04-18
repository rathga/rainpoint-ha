"""RainPoint Smart+ integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_AREA_CODE, DEFAULT_AREA_CODE, DOMAIN, PLATFORMS
from .coordinator import RainPointCoordinator
from .entity import hub_device_info

_LOGGER = logging.getLogger(__name__)


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
