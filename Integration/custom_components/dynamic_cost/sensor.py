"""Sensor platform for Dynamic Cost.

Build this THIRD, once calc.py and window.py work.

Two families of entity live here:
  * hub-level split sensors (8 energy + 10 financial)
  * per-device counters (energy + value, per direction)
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DynamicCostConfigEntry
from .const import DOMAIN, ENERGY_SPLIT_KEYS, FINANCIAL_SPLIT_KEYS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicCostConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all entities for this hub and its device subentries.

    IMPLEMENT ME.

        - one DynamicCostSplitSensor per key in ENERGY_SPLIT_KEYS
          (device_class ENERGY, unit kWh, state_class TOTAL)
        - one DynamicCostSplitSensor per key in FINANCIAL_SPLIT_KEYS
          (device_class MONETARY, unit = the hub's configured currency,
          state_class TOTAL)
        - for each device subentry, its own counters. Remember to pass
          config_subentry_id=... to async_add_entities so each device's
          entities are attached to that subentry, not the hub.

    All of these are cumulative totals kept by your own engine, so they may
    go negative -- that is exactly why this does not build on utility_meter.
    """
    raise NotImplementedError


class DynamicCostSplitSensor(SensorEntity):
    """One hub-level split output."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine, key: str, description: SensorEntityDescription) -> None:
        """Store the engine and the key this entity reports.

        IMPLEMENT ME.

        Set self._attr_unique_id to something stable and collision-proof --
        the config entry id plus the key is the usual pattern. Stable unique
        ids are what let Home Assistant remember the user's renames and
        customisations across restarts.
        """
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Subscribe to the engine so this entity updates when a window settles.

        IMPLEMENT ME.

        Use self.async_on_remove(self._engine.async_add_listener(...)) so the
        subscription is cleaned up automatically when the entity goes away.
        Also restore the previous cumulative total here (see RestoreSensor)
        so a restart does not zero everything.
        """
        raise NotImplementedError

    @property
    def native_value(self) -> float | None:
        """Return the current cumulative total for this key.

        IMPLEMENT ME.
        """
        raise NotImplementedError
