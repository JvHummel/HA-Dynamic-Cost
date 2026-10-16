"""Sensor platform for Dynamic Cost.

Two families of entity live here:
  * hub-level split sensors (8 energy + 10 financial)
  * per-device counters (energy per sensor; value for general devices)

Each hub is a Home Assistant device holding its split sensors, and each of
its devices is a Home Assistant device of its own (plan: entities). All of
these are cumulative totals kept by the window engine, so they may go
negative -- that is exactly why this does not build on utility_meter.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DynamicCostConfigEntry
from .const import (
    CONF_CURRENCY,
    DEVICE_COUNTER_KEYS,
    DOMAIN,
    ENERGY_SPLIT_KEYS,
    FINANCIAL_SPLIT_KEYS,
    DeviceType,
    unique_id,
)


def _energy(key: str, translation_key: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
    )
# end _energy


def _monetary(key: str, translation_key: str, currency: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=currency,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    )
# end _monetary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicCostConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create all entities for this hub and its device subentries.

    Each device's entities are added with its config_subentry_id, so they
    belong to that subentry rather than to the hub.
    """
    currency = entry.data[CONF_CURRENCY]

    async_add_entities(
        [DynamicCostSplitSensor(entry, _energy(key, key)) for key in ENERGY_SPLIT_KEYS]
        + [
            DynamicCostSplitSensor(entry, _monetary(key, key, currency))
            for key in FINANCIAL_SPLIT_KEYS
        ]
    )

    for subentry in entry.subentries.values():
        device_type = DeviceType(subentry.subentry_type)
        descriptions = []
        for key in DEVICE_COUNTER_KEYS[device_type]:
            # Names differ per type ("Energy charged" for a battery's import),
            # so the translation key includes the type.
            translation_key = f"{device_type}_{key}"
            if key.endswith("_value"):
                descriptions.append(_monetary(key, translation_key, currency))
            else:
                descriptions.append(_energy(key, translation_key))
            # end if
        # end for

        async_add_entities(
            [
                DynamicCostDeviceSensor(entry, subentry, description)
                for description in descriptions
            ],
            config_subentry_id=subentry.subentry_id,
        )
    # end for
# end async_setup_entry


class _DynamicCostTotal(SensorEntity):
    """Shared behaviour: show one engine total, refresh when a window settles."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: DynamicCostConfigEntry,
        owner_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Bind the entity to the engine total of owner_id's description.key.

        The unique id doubles as the engine's key for this total, and it is
        what lets Home Assistant remember the user's renames and
        customisations across restarts.
        """
        self._engine = entry.runtime_data
        self.entity_description = description
        self._attr_unique_id = unique_id(owner_id, description.key)
    # end __init__

    async def async_added_to_hass(self) -> None:
        """Subscribe to the engine so this entity updates when a window settles.

        Nothing to restore here: the engine holds and persists every total
        (plan: Persistence), so the entity just displays it.
        """
        self.async_on_remove(self._engine.async_add_listener(self.async_write_ha_state))
    # end async_added_to_hass

    @property
    def native_value(self) -> float:
        """Return the current cumulative total, which the engine keeps under
        this entity's unique id."""
        return self._engine.get_total(self.unique_id)
    # end native_value
# end _DynamicCostTotal


class DynamicCostSplitSensor(_DynamicCostTotal):
    """One hub-level split output, on the hub's own device."""

    def __init__(
        self, entry: DynamicCostConfigEntry, description: SensorEntityDescription
    ) -> None:
        super().__init__(entry, entry.entry_id, description)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
        )
    # end __init__
# end DynamicCostSplitSensor


class DynamicCostDeviceSensor(_DynamicCostTotal):
    """One counter of one device, on that device's own Home Assistant device.

    Renaming the device changes only the device name; the subentry id, and
    with it every unique id, never changes.
    """

    def __init__(
        self,
        entry: DynamicCostConfigEntry,
        subentry: ConfigSubentry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(entry, subentry.subentry_id, description)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
        )
    # end __init__
# end DynamicCostDeviceSensor
