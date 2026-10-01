"""Constants for the Dynamic Cost integration.

Nothing to implement here -- this file is complete. It exists so that the
string keys used for config storage live in exactly one place, instead of
being retyped (and mistyped) across config_flow.py, __init__.py and sensor.py.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "dynamic_cost"

# ---------------------------------------------------------------------------
# Hub configuration keys
# ---------------------------------------------------------------------------
CONF_GRID_IMPORT_SENSOR: Final = "grid_import_sensor"
CONF_GRID_EXPORT_SENSOR: Final = "grid_export_sensor"
CONF_TARIFF_MODE: Final = "tariff_mode"
CONF_IMPORT_TARIFF: Final = "import_tariff"          # fixed mode only
CONF_EXPORT_TARIFF: Final = "export_tariff"          # fixed mode only
CONF_WINDOW_MINUTES: Final = "window_minutes"        # fixed mode only
CONF_IMPORT_PRICE_SENSOR: Final = "import_price_sensor"   # dynamic mode only
CONF_EXPORT_PRICE_SENSOR: Final = "export_price_sensor"   # dynamic mode only
CONF_TREAT_SOLAR_AS_FREE: Final = "treat_solar_as_free"
# Optional; absent means never curtailed.
CONF_CURTAILMENT_SOURCE: Final = "curtailment_source"
CONF_CURRENCY: Final = "currency"

# ---------------------------------------------------------------------------
# Device (subentry) configuration keys
# ---------------------------------------------------------------------------
CONF_DEVICE_TYPE: Final = "device_type"
CONF_DEVICE_NAME: Final = "device_name"
CONF_IMPORT_SENSOR: Final = "import_sensor"
CONF_EXPORT_SENSOR: Final = "export_sensor"


class TariffMode(StrEnum):
    """How the tracking window is driven."""

    FIXED = "fixed"
    DYNAMIC = "dynamic"
# end TariffMode


class DeviceType(StrEnum):
    """Kind of device tracked under a hub."""

    GENERAL = "general"      # import sensor only
    SOLAR = "solar"          # export sensor only
    BATTERY = "battery"      # both
# end DeviceType


# Per-device counters, by device type (plan: entities, device-level). Every
# device counts energy per sensor; only general devices also count value.
DEVICE_COUNTER_KEYS: Final = {
    DeviceType.GENERAL: ("import_energy", "import_value"),
    DeviceType.SOLAR: ("export_energy",),
    DeviceType.BATTERY: ("import_energy", "export_energy"),
}

# Subentry types must be registered under these exact strings.
SUBENTRY_TYPES: Final = (DeviceType.GENERAL, DeviceType.SOLAR, DeviceType.BATTERY)

# Seconds after a window opens during which export-price and curtailment
# updates still count for that window. See the plan's window-engine section.
EXPORT_PRICE_GRACE_SECONDS: Final = 60

# Persistence (see the plan). Bump STORAGE_VERSION whenever the stored layout
# changes, so an old file can be recognised and migrated. Saves wait
# SAVE_DELAY_SECONDS so a burst of changes becomes one write.
STORAGE_VERSION: Final = 1
SAVE_DELAY_SECONDS: Final = 10

# The hub settings that shape windows. The store keeps a copy of them next to
# the in-progress window; if they differ on load, that window is discarded
# (plan: Persistence). Currency is not here: it can't change after setup.
WINDOW_SETTINGS_KEYS: Final = (
    CONF_TARIFF_MODE,
    CONF_IMPORT_TARIFF,
    CONF_EXPORT_TARIFF,
    CONF_WINDOW_MINUTES,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_CURTAILMENT_SOURCE,
)

# ---------------------------------------------------------------------------
# Names of the hub-level split outputs. Used to generate entities.
# ---------------------------------------------------------------------------
ENERGY_SPLIT_KEYS: Final = (
    "house_energy_usage",
    "solar_to_house",
    "solar_to_battery",
    "grid_to_battery",
    "solar_to_grid",
    "grid_to_house",
    "battery_to_house",
    "battery_to_grid",
)

FINANCIAL_SPLIT_KEYS: Final = (
    "grid_import_cost",
    "grid_export_cost",
    "house_energy_usage_cost",
    "solar_to_house_savings",
    "solar_to_battery_cost",
    "grid_to_battery_cost",
    "solar_to_grid_profit",
    "grid_to_house_cost",
    "battery_to_house_savings",
    "battery_to_grid_profit",
)


def unique_id(owner_id: str, key: str) -> str:
    """An entity's unique id: its owner's id plus its key (plan: entities).

    owner_id is the hub's config entry id for split sensors, or the device's
    subentry id for device counters. Both the sensor platform and the
    simulator use this, so the format lives in one place.
    """
    return f"{owner_id}_{key}"
# end unique_id
