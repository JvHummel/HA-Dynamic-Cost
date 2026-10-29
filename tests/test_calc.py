"""Tests for the pure calculation engine.

These need NO Home Assistant at all and run in milliseconds. Start here:
make these pass before you write a single line of window.py.

They pin the plan's formulas, including its MAX(0, ...) clamp case.
"""

from __future__ import annotations

import pytest

from custom_components.dynamic_cost.calc import (
    BatteryPool,
    WindowEnergy,
    calculate_device_value,
    calculate_energy_splits,
    calculate_financial_splits,
    calculate_solar_to_battery_cost,
    calculate_summaries,
    settle_battery_pool,
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
        treat_solar_as_free=False, battery_rate=0.0,
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
# More hand-worked windows
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


MIXED = WindowEnergy(battery_discharge=0.5, solar=2, grid_import=0.5)
"""House uses 3: 0.5 from the grid, 2 from solar, 0.5 from the battery."""


@pytest.mark.parametrize(
    ("treat_solar_as_free", "expected"),
    [
        (False, 0.5 * 0.25 + 2 * 0.125 + 0.5 * 0.08),   # 0.415
        (True, 0.5 * 0.25 + 2 * 0.0 + 0.5 * 0.08),      # 0.165
    ],
)
def test_house_energy_usage_cost_is_what_each_part_cost(treat_solar_as_free, expected):
    """Grid at the import price, solar at the export price (0 when free),
    battery at the battery rate."""
    splits = calculate_energy_splits(MIXED)

    money = calculate_financial_splits(
        splits, MIXED,
        import_price=0.25,
        export_price=0.125,
        treat_solar_as_free=treat_solar_as_free,
        battery_rate=0.08,
    )

    assert money.house_energy_usage_cost == pytest.approx(expected)
# end test_house_energy_usage_cost_is_what_each_part_cost


def _device_value(energy: WindowEnergy, device_kwh: float, *, free=False,
                  export_price=0.10, battery_rate=0.0) -> float:
    """A general device using device_kwh in a window at import price 0.30."""
    splits = calculate_energy_splits(energy)
    money = calculate_financial_splits(
        splits, energy, import_price=0.30, export_price=export_price,
        treat_solar_as_free=free, battery_rate=battery_rate,
    )
    return calculate_device_value(device_kwh, splits, money, 0.30)
# end _device_value


def test_device_pays_the_houses_cost_per_kwh():
    """All grid: the import price. All solar: the export price, or nothing
    when solar is free, or less than nothing at a negative export price."""
    assert _device_value(WindowEnergy(grid_import=2.0), 1.0) == pytest.approx(0.30)
    assert _device_value(WindowEnergy(solar=2.0), 1.0) == pytest.approx(0.10)
    assert _device_value(WindowEnergy(solar=2.0), 1.0, free=True) == pytest.approx(0.0)
    assert _device_value(
        WindowEnergy(solar=2.0), 1.0, export_price=-0.05
    ) == pytest.approx(-0.05)
# end test_device_pays_the_houses_cost_per_kwh


def test_device_in_a_mixed_window_pays_the_mix():
    """House: 0.5 grid at 0.30, 2 solar at 0.10, 0.5 battery at 0.08, so 3 kWh
    cost 0.15 + 0.20 + 0.04 = 0.39. A device using 1.5 of them pays half."""
    assert _device_value(MIXED, 1.5, battery_rate=0.08) == pytest.approx(0.195)
# end test_device_in_a_mixed_window_pays_the_mix


def test_device_without_a_house_mix_pays_the_import_price():
    """Nothing measured for the house (meter timing): what the grid charges."""
    assert _device_value(WindowEnergy(), 1.0) == pytest.approx(0.30)
# end test_device_without_a_house_mix_pays_the_import_price


def _settle(pool: BatteryPool, energy: WindowEnergy, *, import_price=0.30,
            export_price=0.10, free=False, capacity=None):
    return settle_battery_pool(
        pool, calculate_energy_splits(energy),
        import_price=import_price, export_price=export_price,
        treat_solar_as_free=free, capacity=capacity,
    )
# end _settle


def test_pool_averages_what_charging_cost():
    """3 kWh solar (at 0.10) and 1 kWh grid (at 0.30) into the battery: 4 kWh
    that cost 0.60, 0.15 each. Discharging 2 to the house takes 0.30 out."""
    pool, _ = _settle(BatteryPool(), WindowEnergy(solar=3.0, grid_import=1.0,
                                                  battery_charge=4.0))
    assert pool.kwh == pytest.approx(4.0)
    assert pool.cost == pytest.approx(0.60)
    assert pool.average_cost == pytest.approx(0.15)

    pool, rate = _settle(pool, WindowEnergy(battery_discharge=2.0))
    assert rate == pytest.approx(0.15)
    assert pool.kwh == pytest.approx(2.0)
    assert pool.cost == pytest.approx(0.30)
# end test_pool_averages_what_charging_cost


def test_free_solar_charges_the_pool_at_nothing():
    pool, _ = _settle(BatteryPool(), WindowEnergy(solar=2.0, battery_charge=2.0),
                      free=True)
    assert pool.kwh == pytest.approx(2.0)
    assert pool.cost == pytest.approx(0.0)
# end test_free_solar_charges_the_pool_at_nothing


def test_discharge_beyond_the_pool_counts_at_the_import_price():
    """1 kWh in the pool at 0.10; 3 come out at import price 0.40: the other
    2 were in the battery before tracking. (0.10 + 2 * 0.40) / 3 = 0.30."""
    pool, rate = _settle(BatteryPool(kwh=1.0, cost=0.10),
                         WindowEnergy(battery_discharge=3.0), import_price=0.40)
    assert rate == pytest.approx(0.30)
    assert pool == BatteryPool()
    assert pool.average_cost is None
# end test_discharge_beyond_the_pool_counts_at_the_import_price


@pytest.mark.parametrize(
    ("capacity", "kwh"), [(10.0, 10.0), (None, 11.0), (20.0, 11.0)]
)
def test_capacity_trims_losses_but_keeps_their_cost(capacity, kwh):
    """9 kWh at 0.90 plus 2 from the grid at 0.20: 11 kWh that cost 1.30.
    With 10 kWh capacity, the extra kWh can only be losses: dropped, while
    its cost stays. No capacity, or room to spare: nothing is trimmed."""
    pool, _ = _settle(BatteryPool(kwh=9.0, cost=0.90),
                      WindowEnergy(grid_import=2.0, battery_charge=2.0),
                      import_price=0.20, capacity=capacity)
    assert pool.kwh == pytest.approx(kwh)
    assert pool.cost == pytest.approx(1.30)
# end test_capacity_trims_losses_but_keeps_their_cost


@pytest.mark.parametrize(
    ("export_price", "expected"),
    [(0.10, 0.20), (0.0, 0.0), (-0.05, -0.10)],
)
def test_grid_export_profit_follows_the_export_price(export_price, expected):
    """Exporting 2 kWh earns money at a positive price, and costs money --
    a negative profit -- at a negative one."""
    energy = WindowEnergy(solar=2.0, grid_export=2.0)
    money = calculate_financial_splits(
        calculate_energy_splits(energy), energy,
        import_price=0.25,
        export_price=export_price,
        treat_solar_as_free=False,
        battery_rate=0.0,
    )

    assert money.grid_export_profit == pytest.approx(expected)
# end test_grid_export_profit_follows_the_export_price


def test_summaries_add_up_per_source():
    """Solar 4, import 1, export 1, charge 2, discharge 0.5 at 0.30/0.10.

    The splits work out to: solar 2 to the house, 1 to the battery, 1 to
    the grid; the grid's 1 kWh all to the battery; the battery 0.5 to the
    house. So the battery earned 0.15 in the house but paid 0.30 for grid
    energy and 0.10 for the solar it stored.
    """
    energy = WindowEnergy(solar=4.0, grid_import=1.0, grid_export=1.0,
                          battery_charge=2.0, battery_discharge=0.5)
    money = calculate_financial_splits(
        calculate_energy_splits(energy), energy,
        import_price=0.30,
        export_price=0.10,
        treat_solar_as_free=False,
        battery_rate=0.30,
    )

    summaries = calculate_summaries(energy, money)

    assert summaries.grid_import_energy  == pytest.approx(1.0)
    assert summaries.grid_export_energy  == pytest.approx(1.0)
    assert summaries.solar_production    == pytest.approx(4.0)
    assert summaries.battery_charged     == pytest.approx(2.0)
    assert summaries.battery_discharged  == pytest.approx(0.5)
    assert summaries.grid_net_cost       == pytest.approx(0.30 - 0.10)
    assert summaries.solar_value         == pytest.approx(0.60 + 0.10)
    assert summaries.battery_net_savings == pytest.approx(0.15 - 0.30 - 0.10)
# end test_summaries_add_up_per_source
