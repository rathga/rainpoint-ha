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
    MAX_RUN_MINUTES,
    MIN_RUN_MINUTES,
    PLATFORMS,
    SERVICE_FORCE_OFF,
    SERVICE_RUN_ZONE,
)
from .coordinator import RainPointCoordinator
from .entity import hub_device_info

_LOGGER = logging.getLogger(__name__)

RUN_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_RUN_MINUTES, max=MAX_RUN_MINUTES)
        ),
    }
)

FORCE_OFF_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
    }
)


def _resolve_targets(
    hass: HomeAssistant, entity_ids: list[str]
) -> list[tuple[RainPointCoordinator, RainPoint2ZoneTimer_V2, int]]:
    """Look up (coordinator, sub_device, port) tuples for a list of
    rainpoint switch entity ids. Skips anything that isn't one of ours."""
    reg = er.async_get(hass)
    targets = []
    for entity_id in entity_ids:
        entry = reg.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN or entry.domain != "switch":
            _LOGGER.warning("rainpoint service: %s is not a rainpoint switch", entity_id)
            continue
        parts = (entry.unique_id or "").split("_")
        if len(parts) != 3 or parts[0] != "rainpoint" or not parts[2].startswith("port"):
            _LOGGER.warning(
                "rainpoint service: unexpected unique_id %r on %s",
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
    return targets


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
    """Register custom rainpoint services (idempotent)."""

    async def _run_zone(call: ServiceCall) -> None:
        # Users configure in minutes; the HomGar control endpoint wants seconds.
        duration_s = int(call.data[ATTR_DURATION]) * 60
        for coord, sub, port in _resolve_targets(hass, call.data[ATTR_ENTITY_ID]):
            await coord.async_turn_on(sub, port, duration_s)

    async def _force_off(call: ServiceCall) -> None:
        # Local-only state clear — does NOT call the API. Use when the
        # HomGar HTTP cache is stuck reporting a valve as running but
        # the phone app (and the actual valve) say otherwise.
        for coord, sub, port in _resolve_targets(hass, call.data[ATTR_ENTITY_ID]):
            coord.force_idle(sub, port)

    if not hass.services.has_service(DOMAIN, SERVICE_RUN_ZONE):
        hass.services.async_register(
            DOMAIN, SERVICE_RUN_ZONE, _run_zone, schema=RUN_ZONE_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_FORCE_OFF):
        hass.services.async_register(
            DOMAIN, SERVICE_FORCE_OFF, _force_off, schema=FORCE_OFF_SCHEMA
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RUN_ZONE)
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_OFF)
    return unloaded
