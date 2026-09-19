"""Tests for the pure calculation engine.

These need NO Home Assistant at all and run in milliseconds. Start here:
make these pass before you write a single line of window.py.

Several tests are deliberately left for you to fill in -- the exercise is
deciding what the right answer IS, which is the part worth thinking about.
"""

from __future__ import annotations

import pytest

from custom_components.dynamic_cost.calc import (
    WindowEnergy,
    calculate_energy_splits,
    calculate_financial_splits,
    calculate_solar_to_battery_cost,
)
from custom_components.dynamic_cost.const import OpportunityCostMode


def test_all_solar_goes_to_house():
    """5 kWh of solar, house uses all of it, nothing else moving."""
    splits = calculate_energy_splits(WindowEnergy(solar=5.0))

    assert splits.house_energy_usage == pytest.approx(5.0)
    assert splits.solar_to_house == pytest.approx(5.0)
    assert splits.solar_to_battery == pytest.approx(0.0)
    assert splits.solar_to_grid == pytest.approx(0.0)


def test_solar_splits_never_exceed_production():
    """The three solar destinations must always sum back to production."""
    energy = WindowEnergy(
        solar=10.0, grid_import=1.0, grid_export=3.0,
        battery_charge=4.0, battery_discharge=0.5,
    )
    splits = calculate_energy_splits(energy)

    total = splits.solar_to_house + splits.solar_to_battery + splits.solar_to_grid
    assert total == pytest.approx(energy.solar)


def test_import_at_negative_price_earns_money():
    """A negative import price must produce a negative cost, not a clamp."""
    energy = WindowEnergy(grid_import=4.0)
    splits = calculate_energy_splits(energy)
    money = calculate_financial_splits(
        splits, energy,
        import_price=-0.05, export_price=0.0,
        mode=OpportunityCostMode.ALWAYS,
    )
    assert money.grid_import_cost == pytest.approx(-0.20)


@pytest.mark.parametrize(
    ("mode", "export_price", "expected"),
    [
        (OpportunityCostMode.ALWAYS, 0.10, 0.50),
        (OpportunityCostMode.ALWAYS, -0.10, -0.50),
        (OpportunityCostMode.SKIP_WHEN_EXPORT_NEGATIVE, 0.10, 0.50),
        (OpportunityCostMode.SKIP_WHEN_EXPORT_NEGATIVE, -0.10, 0.0),
        (OpportunityCostMode.NEVER, 0.10, 0.0),
        (OpportunityCostMode.NEVER, -0.10, 0.0),
    ],
)
def test_opportunity_cost_modes(mode, export_price, expected):
    """All three modes, on both sides of zero."""
    result = calculate_solar_to_battery_cost(5.0, export_price, mode)
    assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# YOUR TURN
# ---------------------------------------------------------------------------

def test_battery_discharge_covers_house_when_no_solar():
    """Night-time: battery runs the house, nothing from solar, nothing exported.

    WRITE ME. Build a WindowEnergy with only battery_discharge and maybe a
    little grid_import, and assert battery_to_house comes out right.
    """
    pytest.skip("exercise: write this test")


def test_house_energy_usage_cost_blends_three_streams():
    """house_energy_usage_cost = grid_to_house + solar_to_house + battery_to_house.

    WRITE ME. Construct a window where all three contribute, then check the
    blended figure. Remember it is NOT a net 'after offsets' number.
    """
    pytest.skip("exercise: write this test")


def test_meter_jitter_does_not_produce_negative_flows():
    """A slightly inconsistent set of readings must not yield negative splits.

    WRITE ME. Feed numbers that do not quite balance (as two meters updating
    microseconds apart would produce) and assert every energy split is >= 0.
    """
    pytest.skip("exercise: write this test")
