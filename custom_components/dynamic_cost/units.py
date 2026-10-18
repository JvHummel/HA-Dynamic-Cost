"""Unit conversion for Dynamic Cost.

The engine works in kWh and in prices per kWh, whatever units the source
sensors report in (plan: Units). Home Assistant's own EnergyConverter knows
the energy units: Wh, kWh, MWh, GJ and so on.
"""

from __future__ import annotations

from homeassistant.const import UnitOfEnergy
from homeassistant.util.unit_conversion import EnergyConverter

KWH = UnitOfEnergy.KILO_WATT_HOUR


def is_energy_unit(unit: str | None) -> bool:
    """True if energy_to_kwh can convert from unit. No unit counts as kWh."""
    return unit is None or unit in EnergyConverter.VALID_UNITS
# end is_energy_unit


def energy_to_kwh(value: float, unit: str | None) -> float | None:
    """value, reported in unit, converted to kWh.

    None if unit isn't an energy unit (a power sensor in W, for example):
    such a reading can't be counted. A sensor without a unit is taken to
    report kWh, as every sensor was before units were read.
    """
    if unit is None:
        return value
    elif unit not in EnergyConverter.VALID_UNITS:
        return None
    else:
        return EnergyConverter.convert(value, unit, KWH)
    # end if
# end energy_to_kwh


def price_per_kwh(value: float, unit: str | None) -> float:
    """A price reported in unit, converted to a price per kWh.

    Only the energy unit after the last "/" counts: 120 EUR/MWh becomes
    0.12 EUR/kWh. Without a unit, or without an energy unit after the slash,
    the price is taken as per kWh. The currency part is never converted: the
    hub's currency is chosen at setup, and a sensor in cents can't be told
    apart from one in euros reliably.
    """
    if unit is None or "/" not in unit:
        return value
    # end if

    per = unit.rsplit("/", 1)[1].strip()
    if per not in EnergyConverter.VALID_UNITS:
        return value
    # end if

    # A price per MWh is per 1000 kWh: multiply by how many MWh one kWh is.
    return value * EnergyConverter.convert(1.0, KWH, per)
# end price_per_kwh
