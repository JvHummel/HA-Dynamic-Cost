"""Tests for unit conversion (plan: Units). Pure arithmetic, no hass needed."""

from __future__ import annotations

import pytest

from custom_components.dynamic_cost.units import (
    energy_to_kwh,
    is_energy_unit,
    price_per_kwh,
)


@pytest.mark.parametrize(
    ("value", "unit", "kwh"),
    [
        (1500.0, "Wh", 1.5),
        (2.0, "MWh", 2000.0),
        (3.6, "MJ", 1.0),
        (4.0, "kWh", 4.0),
        (5.0, None, 5.0),          # no unit: taken as kWh
    ],
)
def test_energy_converts_to_kwh(value, unit, kwh):
    assert energy_to_kwh(value, unit) == pytest.approx(kwh)
    assert is_energy_unit(unit)
# end test_energy_converts_to_kwh


@pytest.mark.parametrize("unit", ["W", "kW", "EUR", ""])
def test_non_energy_unit_cannot_be_counted(unit):
    """A power sensor (W) or anything else that isn't energy: no reading."""
    assert energy_to_kwh(100.0, unit) is None
    assert not is_energy_unit(unit)
# end test_non_energy_unit_cannot_be_counted


@pytest.mark.parametrize(
    ("value", "unit", "per_kwh"),
    [
        (120.0, "EUR/MWh", 0.12),
        (0.00012, "EUR/Wh", 0.12),
        (0.25, "EUR/kWh", 0.25),
        (0.25, "€/ kWh", 0.25),
        (0.25, None, 0.25),        # no unit: taken as per kWh
        (0.25, "EUR", 0.25),       # no energy unit after a slash: as is
        (25.0, "ct/kWh", 25.0),    # the currency part is never converted
    ],
)
def test_price_converts_to_per_kwh(value, unit, per_kwh):
    assert price_per_kwh(value, unit) == pytest.approx(per_kwh)
# end test_price_converts_to_per_kwh
