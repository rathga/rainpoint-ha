"""Shared helpers for RainPoint entities.

The DeviceInfo construction is identical across switch/sensor/binary_sensor
platforms; keep it in one place so adding a new field (sw_version,
hw_version, serial_number) only has to happen once.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from homgarapi.devices import HomgarDevice, HomgarHubDevice

from .const import DOMAIN


def hub_identifier(hub: HomgarHubDevice) -> str:
    return f"hub-{hub.mid}"


def sub_identifier(sub: HomgarDevice) -> str:
    return f"sub-{getattr(sub, 'sid', None) or sub.did}"


def hub_device_info(hub: HomgarHubDevice) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, hub_identifier(hub))},
        name=hub.name,
        manufacturer="RainPoint",
        model=hub.model,
        sw_version=getattr(hub, "softVer", None) or None,
    )


def sub_device_info(hub: HomgarHubDevice, sub: HomgarDevice) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, sub_identifier(sub))},
        name=sub.name,
        manufacturer="RainPoint",
        model=sub.model,
        via_device=(DOMAIN, hub_identifier(hub)),
    )
