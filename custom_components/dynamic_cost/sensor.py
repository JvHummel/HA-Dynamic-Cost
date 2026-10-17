"""Sensor platform for Dynamic Cost.

Three families of entity live here:
  * hub-level split sensors (up to 8 energy + 10 financial), each created
    only once the hub has the devices it needs
  * per-device counters (energy per sensor; value for general devices)
  * hub-level diagnostic sensors showing the window engine's state, for
    dynamic-tariff hubs only

Each hub is a Home Assistant device holding its split and diagnostic
sensors, and each of its devices is a Home Assistant device of its own
(plan: entities). The splits and counters are cumulative totals kept by the
window engine, so they may go negative -- that is exactly why this does not
build on utility_meter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DynamicCostConfigEntry
from .const import (
    CONF_CURRENCY,
    CONF_TARIFF_MODE,
    DEVICE_COUNTER_KEYS,
    DOMAIN,
    ENERGY_SPLIT_KEYS,
    FINANCIAL_SPLIT_KEYS,
    SPLIT_REQUIRES,
    DeviceType,
    TariffMode,
    WindowStatus,
    unique_id,
)
from .window import WindowEngine


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


@dataclass(frozen=True, kw_only=True)
class DiagnosticDescription(SensorEntityDescription):
    """A diagnostic sensor: which engine value it shows."""

    value_fn: Callable[[WindowEngine], str | float | datetime | None]
# end DiagnosticDescription


def _curtailed_state(engine: WindowEngine) -> str | None:
    curtailed = engine.window_curtailed
    if curtailed is None:
        return None
    # end if
    return "curtailed" if curtailed else "not_curtailed"
# end _curtailed_state


def _diagnostics(currency: str, curtailment: bool) -> list[DiagnosticDescription]:
    """The dynamic-tariff diagnostics (plan: entities, diagnostics).

    The curtailment state only exists when the hub has a curtailment source.
    """
    price_per_kwh = f"{currency}/{UnitOfEnergy.KILO_WATT_HOUR}"
    descriptions = [
        DiagnosticDescription(
            key="window_status",
            translation_key="window_status",
            device_class=SensorDeviceClass.ENUM,
            options=[status.value for status in WindowStatus],
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda engine: engine.status,
        ),
        DiagnosticDescription(
            key="window_import_price",
            translation_key="window_import_price",
            native_unit_of_measurement=price_per_kwh,
            suggested_display_precision=5,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda engine: engine.window_import_price,
        ),
        DiagnosticDescription(
            key="window_export_price",
            translation_key="window_export_price",
            native_unit_of_measurement=price_per_kwh,
            suggested_display_precision=5,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda engine: engine.window_export_price,
        ),
        DiagnosticDescription(
            key="window_opened",
            translation_key="window_opened",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda engine: engine.window_opened_at,
        ),
        DiagnosticDescription(
            key="last_window_closed",
            translation_key="last_window_closed",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda engine: engine.last_window_closed_at,
        ),
    ]
    if curtailment:
        descriptions.append(
            DiagnosticDescription(
                key="window_curtailed",
                translation_key="window_curtailed",
                device_class=SensorDeviceClass.ENUM,
                options=["curtailed", "not_curtailed"],
                entity_category=EntityCategory.DIAGNOSTIC,
                value_fn=_curtailed_state,
            )
        )
    # end if
    return descriptions
# end _diagnostics


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicCostConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create all entities for this hub and its device subentries.

    A split is created only when the hub has every device type it needs
    (const.SPLIT_REQUIRES); without them it could only ever read 0. The hub
    reloads whenever a device is added or removed, so the splits follow.
    Hub entities that no longer apply are removed from the entity registry;
    their totals stay in the store, so a split that comes back carries on.

    Each device's entities are added with its config_subentry_id, so they
    belong to that subentry rather than to the hub.
    """
    currency = entry.data[CONF_CURRENCY]
    engine = entry.runtime_data
    device_types = {
        DeviceType(subentry.subentry_type) for subentry in entry.subentries.values()
    }

    def applies(key: str) -> bool:
        return SPLIT_REQUIRES[key] <= device_types
    # end applies

    hub_entities: list[SensorEntity] = [
        DynamicCostSplitSensor(entry, _energy(key, key))
        for key in ENERGY_SPLIT_KEYS
        if applies(key)
    ] + [
        DynamicCostSplitSensor(entry, _monetary(key, key, currency))
        for key in FINANCIAL_SPLIT_KEYS
        if applies(key)
    ]
    if entry.data[CONF_TARIFF_MODE] == TariffMode.DYNAMIC:
        hub_entities += [
            DynamicCostDiagnosticSensor(entry, description)
            for description in _diagnostics(currency, engine.tracks_curtailment)
        ]
    # end if

    _remove_stale_hub_entities(hass, entry, {e.unique_id for e in hub_entities})
    async_add_entities(hub_entities)

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


@callback
def _remove_stale_hub_entities(
    hass: HomeAssistant, entry: DynamicCostConfigEntry, keep: set[str | None]
) -> None:
    """Drop the hub's own registry entries that this setup no longer creates.

    Hub entities are the ones without a subentry; Home Assistant already
    removes a device's entities when the device itself is removed.
    """
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.config_subentry_id is None and registered.unique_id not in keep:
            registry.async_remove(registered.entity_id)
        # end if
    # end for
# end _remove_stale_hub_entities


def _hub_device(entry: DynamicCostConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)
# end _hub_device


class _DynamicCostEntity(SensorEntity):
    """Shared behaviour: follow the hub's window engine."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: DynamicCostConfigEntry,
        owner_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Bind the entity to its engine, under owner_id's description.key.

        The unique id is what lets Home Assistant remember the user's
        renames and customisations across restarts.
        """
        self._engine = entry.runtime_data
        self.entity_description = description
        self._attr_unique_id = unique_id(owner_id, description.key)
    # end __init__

    async def async_added_to_hass(self) -> None:
        """Subscribe to the engine, which says when anything shown changed.

        Nothing to restore here: the engine holds and persists everything
        (plan: Persistence), so the entity just displays it.
        """
        self.async_on_remove(self._engine.async_add_listener(self.async_write_ha_state))
    # end async_added_to_hass
# end _DynamicCostEntity


class _DynamicCostTotal(_DynamicCostEntity):
    """Show one engine total."""

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
        self._attr_device_info = _hub_device(entry)
    # end __init__
# end DynamicCostSplitSensor


class DynamicCostDiagnosticSensor(_DynamicCostEntity):
    """One view of the window engine's state, on the hub's own device."""

    entity_description: DiagnosticDescription

    def __init__(
        self, entry: DynamicCostConfigEntry, description: DiagnosticDescription
    ) -> None:
        super().__init__(entry, entry.entry_id, description)
        self._attr_device_info = _hub_device(entry)
    # end __init__

    @property
    def native_value(self) -> str | float | datetime | None:
        return self.entity_description.value_fn(self._engine)
    # end native_value
# end DynamicCostDiagnosticSensor


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
