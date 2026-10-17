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
# Dynamic mode only: warn when a window stays open longer than this.
CONF_STALE_WINDOW_MINUTES: Final = "stale_window_minutes"
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


class WindowStatus(StrEnum):
    """What the window engine is doing, for the hub's status sensor."""

    NO_WINDOW = "no_window"  # first start, waiting for a real import price
    OPEN = "open"
    HELD_OPEN = "held_open"  # boundary reached, waiting for a lock
    OVERDUE = "overdue"      # open longer than the hub's stale-window timeout
# end WindowStatus


class LockState(StrEnum):
    """Where a window's export price or curtailment state stands."""

    WAITING = "waiting"      # no reading yet
    TENTATIVE = "tentative"  # read, but may still change until it locks
    LOCKED = "locked"        # fixed for this window
# end LockState


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

# Dynamic mode: how long a window may stay open before a warning is logged,
# for hubs that never set CONF_STALE_WINDOW_MINUTES (plan: logging).
DEFAULT_STALE_WINDOW_MINUTES: Final = 120

# Persistence (see the plan). Bump STORAGE_VERSION whenever the stored layout
# changes, so an old file can be recognised and migrated. Saves wait
# SAVE_DELAY_SECONDS so a burst of changes becomes one write. A new field
# that is read with a default when missing doesn't need a bump.
STORAGE_VERSION: Final = 1
SAVE_DELAY_SECONDS: Final = 10

# The hub settings that shape windows. The store keeps a copy of them next to
# the in-progress window; if they differ on load, that window is discarded
# (plan: Persistence). Currency is not here: it can't change after setup.
# Nor is CONF_STALE_WINDOW_MINUTES: it only decides when to warn.
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
    "grid_export_profit",
    "house_energy_usage_cost",
    "solar_to_house_savings",
    "solar_to_battery_cost",
    "grid_to_battery_cost",
    "solar_to_grid_profit",
    "grid_to_house_cost",
    "battery_to_house_savings",
    "battery_to_grid_profit",
)

# Hub-wide summaries: per-source totals, and what each source cost or earned
# in all (plan: entities, summaries).
SUMMARY_ENERGY_KEYS: Final = (
    "grid_import_energy",
    "grid_export_energy",
    "solar_production",
    "battery_charged",
    "battery_discharged",
)

SUMMARY_FINANCIAL_KEYS: Final = (
    "grid_net_cost",
    "solar_value",
    "battery_net_savings",
)


# Which device types a hub needs before a split or summary can be anything
# but 0. Its entity is only created once the hub has them (plan: entities).
_SOLAR = frozenset({DeviceType.SOLAR})
_BATTERY = frozenset({DeviceType.BATTERY})
_BOTH = _SOLAR | _BATTERY
TOTAL_REQUIRES: Final[dict[str, frozenset[DeviceType]]] = {
    "house_energy_usage": frozenset(),
    "solar_to_house": _SOLAR,
    "solar_to_battery": _BOTH,
    "grid_to_battery": _BATTERY,
    "solar_to_grid": _SOLAR,
    "grid_to_house": frozenset(),
    "battery_to_house": _BATTERY,
    "battery_to_grid": _BATTERY,
    "grid_import_cost": frozenset(),
    "grid_export_profit": frozenset(),
    "house_energy_usage_cost": frozenset(),
    "solar_to_house_savings": _SOLAR,
    "solar_to_battery_cost": _BOTH,
    "grid_to_battery_cost": _BATTERY,
    "solar_to_grid_profit": _SOLAR,
    "grid_to_house_cost": frozenset(),
    "battery_to_house_savings": _BATTERY,
    "battery_to_grid_profit": _BATTERY,
    "grid_import_energy": frozenset(),
    "grid_export_energy": frozenset(),
    "solar_production": _SOLAR,
    "battery_charged": _BATTERY,
    "battery_discharged": _BATTERY,
    "grid_net_cost": frozenset(),
    "solar_value": _SOLAR,
    "battery_net_savings": _BATTERY,
}


def unique_id(owner_id: str, key: str) -> str:
    """An entity's unique id: its owner's id plus its key (plan: entities).

    owner_id is the hub's config entry id for split sensors, or the device's
    subentry id for device counters. Both the sensor platform and the
    simulator use this, so the format lives in one place.
    """
    return f"{owner_id}_{key}"
# end unique_id
