"""Tests for the pure calculation engine.

These need NO Home Assistant at all and run in milliseconds. Start here:
make these pass before you write a single line of window.py.

The tests under YOUR TURN were the author's own exercises; the rest pin the
plan's formulas, including its MAX(0, ...) clamp case.
"""

from __future__ import annotations

import pytest

from custom_components.dynamic_cost.calc import (
    WindowEnergy,
    calculate_energy_splits,
    calculate_financial_splits,
    calculate_solar_to_battery_cost,
)


def test_all_solar_goes_to_house():
    """5 kWh of solar, house uses all of it, nothing else moving."""
    splits = calculate_energy_splits(WindowEnergy(solar=5.0))

    assert splits.house_energy_usage == pytest.approx(5.0)
    assert splits.solar_to_house == pytest.approx(5.0)
    assert splits.solar_to_battery == pytest.approx(0.0)
    assert splits.solar_to_grid == pytest.approx(0.0)
# end test_all_solar_goes_to_house


def test_solar_splits_never_exceed_production():
    """The three solar destinations must always sum back to production."""
    energy = WindowEnergy(
        solar=10.0, grid_import=1.0, grid_export=3.0,
        battery_charge=4.0, battery_discharge=0.5,
    )
    splits = calculate_energy_splits(energy)

    total = splits.solar_to_house + splits.solar_to_battery + splits.solar_to_grid
    assert total == pytest.approx(energy.solar)
# end test_solar_splits_never_exceed_production


def test_import_at_negative_price_earns_money():
    """A negative import price must produce a negative cost, not a clamp."""
    energy = WindowEnergy(grid_import=4.0)
    splits = calculate_energy_splits(energy)
    money = calculate_financial_splits(
        splits, energy,
        import_price=-0.05, export_price=0.0,
        treat_solar_as_free=False,
    )
    assert money.grid_import_cost == pytest.approx(-0.20)
# end test_import_at_negative_price_earns_money


@pytest.mark.parametrize(
    ("treat_solar_as_free", "export_price", "expected"),
    [
        (False, 0.10, 0.50),
        (False, -0.10, -0.50),
        (True, 0.10, 0.0),
        (True, -0.10, 0.0),
    ],
)
def test_solar_to_battery_cost(treat_solar_as_free, export_price, expected):
    """Free means zero; otherwise the cost follows the export price's sign."""
    result = calculate_solar_to_battery_cost(5.0, export_price, treat_solar_as_free)
    assert result == pytest.approx(expected)
# end test_solar_to_battery_cost


def test_night_export_comes_from_battery_not_solar():
    """The plan's clamp case: battery discharges 5, house uses 2, 3 exported.

    Without the MAX(0, ...) clamps this reports 3 kWh solar to grid and none
    from the battery, at night.
    """
    splits = calculate_energy_splits(
        WindowEnergy(battery_discharge=5.0, grid_export=3.0)
    )

    assert splits.house_energy_usage == pytest.approx(2.0)
    assert splits.solar_to_grid == pytest.approx(0.0)
    assert splits.solar_to_house == pytest.approx(0.0)
    assert splits.battery_to_house == pytest.approx(2.0)
    assert splits.battery_to_grid == pytest.approx(3.0)
# end test_night_export_comes_from_battery_not_solar


# ---------------------------------------------------------------------------
# YOUR TURN
# ---------------------------------------------------------------------------

def test_battery_discharge_covers_house_when_no_solar():
    """Night-time: battery runs the house, nothing from solar"""
    splits = calculate_energy_splits(WindowEnergy(battery_discharge=3.0,
                                                  grid_import=0.05,
                                                  grid_export=0.1))

    assert splits.house_energy_usage == pytest.approx(2.95)
    assert splits.solar_to_house     == pytest.approx(0)
    assert splits.solar_to_battery   == pytest.approx(0)
    assert splits.solar_to_grid      == pytest.approx(0)
    assert splits.battery_to_grid    == pytest.approx(0.1)
    assert splits.battery_to_house   == pytest.approx(2.9)
    assert splits.grid_to_battery    == pytest.approx(0)
    assert splits.grid_to_house      == pytest.approx(0.05)
# end test_battery_discharge_covers_house_when_no_solar


def test_house_energy_usage_cost_blends_three_streams():
    """house_energy_usage_cost = grid_to_house + solar_to_house + battery_to_house."""
    energy = WindowEnergy(battery_discharge=0.5,
                          solar=2,
                          grid_import=0.5)

    splits = calculate_energy_splits(energy)

    money = calculate_financial_splits(
        splits, energy,
        import_price=0.25,
        export_price=0.125,
        treat_solar_as_free=False,
    )

    assert money.house_energy_usage_cost == pytest.approx(0.75)
# end test_house_energy_usage_cost_blends_three_streams