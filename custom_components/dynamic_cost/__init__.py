"""The Dynamic Cost integration.

This file is COMPLETE and working -- it is the wiring that makes Home
Assistant load your integration. Persistence lives in the window engine, not
here (plan: Persistence).
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CAPACITY,
    CONF_DEVICE_NAME,
    CONF_EXPORT_SENSOR,
    CONF_IMPORT_SENSOR,
    STORAGE_VERSION,
    DeviceType,
)
from .window import Device, WindowEngine, storage_key

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type DynamicCostConfigEntry = ConfigEntry[WindowEngine]


async def async_setup_entry(
    hass: HomeAssistant, entry: DynamicCostConfigEntry
) -> bool:
    """Set up one Dynamic Cost hub from a config entry."""
    # Hub settings live in entry.data; the reconfigure flow edits them there.
    config = dict(entry.data)

    # Devices are subentries, which are NOT part of entry.data -- each one is
    # stored separately in entry.subentries, keyed by subentry id.
    devices = [
        Device(
            subentry_id=subentry.subentry_id,
            device_type=DeviceType(subentry.subentry_type),
            name=subentry.data.get(CONF_DEVICE_NAME, subentry.title),
            import_sensor=subentry.data.get(CONF_IMPORT_SENSOR),
            export_sensor=subentry.data.get(CONF_EXPORT_SENSOR),
            capacity=subentry.data.get(CONF_CAPACITY),
        )
        for subentry in entry.subentries.values()
    ]

    engine = WindowEngine(hass, entry.entry_id, config, devices)
    await engine.async_start()

    # runtime_data is the modern place to hang per-entry objects; it is typed
    # by the annotation above and cleaned up automatically on unload.
    entry.runtime_data = engine

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True
# end async_setup_entry


async def async_unload_entry(
    hass: HomeAssistant, entry: DynamicCostConfigEntry
) -> bool:
    """Unload a config entry, tearing down listeners and timers."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.async_stop()
        await entry.runtime_data.async_save_now()
    # end if
    return unload_ok
# end async_unload_entry


async def async_reload_entry(
    hass: HomeAssistant, entry: DynamicCostConfigEntry
) -> None:
    """Reload after any edit: hub reconfigured, device added, changed or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
# end async_reload_entry


async def async_remove_entry(
    hass: HomeAssistant, entry: DynamicCostConfigEntry
) -> None:
    """Delete the hub's store when the hub itself is deleted (plan: Persistence)."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
# end async_remove_entry
