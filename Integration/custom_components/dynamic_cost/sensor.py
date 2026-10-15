"""Sensor platform for Dynamic Cost.

Build this THIRD, once calc.py and window.py work.

Two families of entity live here:
  * hub-level split sensors (8 energy + 10 financial)
  * per-device counters (energy per sensor; value for general devices)

Each hub is a Home Assistant device holding its split sensors, and each of
its devices is a Home Assistant device of its own (plan: entities).
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
        - for each device subentry, one DynamicCostDeviceSensor per key in
          DEVICE_COUNTER_KEYS[its type]: *_energy keys are ENERGY in kWh,
          import_value is MONETARY in the hub currency. Remember to pass
          config_subentry_id=... to async_add_entities so each device's
          entities are attached to that subentry, not the hub.

    All of these are cumulative totals kept by your own engine, so they may
    go negative -- that is exactly why this does not build on utility_meter.
    """
    raise NotImplementedError
# end async_setup_entry


class DynamicCostSplitSensor(SensorEntity):
    """One hub-level split output."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine, key: str, description: SensorEntityDescription) -> None:
        """Store the engine and the key this entity reports.

        IMPLEMENT ME.

        Set self._attr_unique_id with const.unique_id(entry id, key). Stable
        unique ids are what let Home Assistant remember the user's renames
        and customisations across restarts, and the simulator finds
        entities by them.

        Set self._attr_device_info to a DeviceInfo with
        identifiers={(DOMAIN, entry id)} and the hub's title as name, so the
        split sensors sit under the hub's own device.
        """
        raise NotImplementedError
    # end __init__

    async def async_added_to_hass(self) -> None:
        """Subscribe to the engine so this entity updates when a window settles.

        IMPLEMENT ME.

        Use self.async_on_remove(self._engine.async_add_listener(...)) so the
        subscription is cleaned up automatically when the entity goes away.
        Nothing to restore here: the engine holds and persists every total
        (plan: Persistence), so the entity just displays it.
        """
        raise NotImplementedError
    # end async_added_to_hass

    @property
    def native_value(self) -> float | None:
        """Return the current cumulative total for this key.

        IMPLEMENT ME. The engine keeps it under this entity's unique id:
        self._engine.get_total(self.unique_id).
        """
        raise NotImplementedError
    # end native_value
# end DynamicCostSplitSensor


class DynamicCostDeviceSensor(SensorEntity):
    """One counter of one device: its energy for one sensor, or its value."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, engine, device, key: str, description: SensorEntityDescription
    ) -> None:
        """Store the engine, the device and the key this entity reports.

        IMPLEMENT ME.

        Unique id: const.unique_id(device.subentry_id, key). Device info:
        identifiers={(DOMAIN, device.subentry_id)}, name=device.name, so
        each device gets its own Home Assistant device. Renaming the device
        changes only that name; the subentry id never changes.
        """
        raise NotImplementedError
    # end __init__

    async def async_added_to_hass(self) -> None:
        """Subscribe to the engine, as DynamicCostSplitSensor does.

        IMPLEMENT ME.
        """
        raise NotImplementedError
    # end async_added_to_hass

    @property
    def native_value(self) -> float | None:
        """Return this counter's cumulative total, from the engine.

        IMPLEMENT ME. As for the split sensors: get_total(self.unique_id).
        """
        raise NotImplementedError
    # end native_value
# end DynamicCostDeviceSensor
