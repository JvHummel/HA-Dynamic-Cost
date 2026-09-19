"""Pure calculation engine for Dynamic Cost.

IMPORTANT: this module must never import Home Assistant. It is plain
arithmetic on plain numbers, so you can test it with hand-worked values
without any of the framework being involved. Build and finish this file
FIRST -- everything else depends on it being correct.

Everything here operates on a single tracking window's energy deltas.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import OpportunityCostMode


@dataclass(frozen=True, slots=True)
class WindowEnergy:
    """Energy that moved during one window, in kWh, aggregated per category.

    All values are positive. Solar/battery figures are already summed across
    every solar/battery device belonging to the hub.
    """

    solar: float = 0.0
    grid_import: float = 0.0
    grid_export: float = 0.0
    battery_charge: float = 0.0
    battery_discharge: float = 0.0


@dataclass(frozen=True, slots=True)
class EnergySplits:
    """The eight derived energy flows for one window, in kWh."""

    house_energy_usage: float
    solar_to_house: float
    solar_to_battery: float
    grid_to_battery: float
    solar_to_grid: float
    grid_to_house: float
    battery_to_house: float
    battery_to_grid: float


@dataclass(frozen=True, slots=True)
class FinancialSplits:
    """The ten derived monetary figures for one window, in the hub currency."""

    grid_import_cost: float
    grid_export_cost: float
    house_energy_usage_cost: float
    solar_to_house_savings: float
    solar_to_battery_cost: float
    grid_to_battery_cost: float
    solar_to_grid_profit: float
    grid_to_house_cost: float
    battery_to_house_savings: float
    battery_to_grid_profit: float


def calculate_energy_splits(energy: WindowEnergy) -> EnergySplits:
    """Split one window's raw energy figures into the eight derived flows."""

    """
        House energy usage is the 'ground truth' for a couple of the next
        calculations.

        The house energy usage is calculated as the difference between all
        generated/imported energy, and the exported/battery charge energy. 
    """
    house_energy_usage = (( # All energy that was produced
                            energy.solar +
                            energy.grid_import + 
                            energy.battery_discharge
                            ) - ( # subtract all energy that was exported
                            energy.battery_charge +
                            energy.grid_export
                          ))

    solar_to_house     = max(0, min(energy.solar - energy.grid_export,
                                    house_energy_usage - energy.battery_discharge))

    solar_to_battery   = max(0, energy.solar - energy.grid_export - solar_to_house)
    grid_to_battery    = min(energy.grid_import, energy.battery_charge - solar_to_battery)
    solar_to_grid      = energy.solar - solar_to_battery - solar_to_house
    grid_to_house      = max(0, energy.grid_import - grid_to_battery)
    battery_to_house   = max(0, house_energy_usage - grid_to_house
                                    - solar_to_house)
    battery_to_grid    = max(0, energy.battery_discharge - battery_to_house)
    

    return EnergySplits(
        battery_to_grid    = battery_to_grid,
        battery_to_house   = battery_to_house,
        grid_to_battery    = grid_to_battery,
        grid_to_house      = grid_to_house,
        house_energy_usage = house_energy_usage,
        solar_to_battery   = solar_to_battery,
        solar_to_grid      = solar_to_grid,
        solar_to_house     = solar_to_house
    )

    raise NotImplementedError


def calculate_solar_to_battery_cost(
    solar_to_battery: float,
    export_price: float,
    mode: OpportunityCostMode,
) -> float:
    """Value the solar energy that went into the battery.

    IMPLEMENT ME.

    This is an opportunity cost: that energy could have been exported and
    sold instead, so it is valued at the EXPORT price, not the import price.

        NEVER                     -> 0.0 (user treats solar as free)
        SKIP_WHEN_EXPORT_NEGATIVE -> 0.0 when export_price < 0, else the
                                     normal calculation (user is likely
                                     curtailing PV at negative prices, so
                                     that energy really was free)
        ALWAYS                    -> solar_to_battery * export_price
    """
    raise NotImplementedError


def calculate_financial_splits(
    splits: EnergySplits,
    energy: WindowEnergy,
    import_price: float,
    export_price: float,
    mode: OpportunityCostMode,
) -> FinancialSplits:
    """Convert one window's energy splits into monetary figures.

    IMPLEMENT ME.

        grid_import_cost        = grid_import * import_price
        grid_export_cost        = grid_export * -export_price
        solar_to_house_savings  = solar_to_house * import_price
        grid_to_battery_cost    = grid_to_battery * import_price
        battery_to_house_savings= battery_to_house * import_price
        solar_to_grid_profit    = solar_to_grid * export_price
        grid_to_house_cost      = grid_to_house * import_price
        battery_to_grid_profit  = battery_to_grid * export_price
        solar_to_battery_cost   = calculate_solar_to_battery_cost(...)

        house_energy_usage_cost = grid_to_house_cost
                                   + solar_to_house_savings
                                   + battery_to_house_savings

    Note that nothing here is clamped. Every one of these is allowed to go
    negative -- that is the whole point of tracking dynamic tariffs, where
    import at a negative price earns money and export at a negative price
    costs money.
    """
    raise NotImplementedError


def calculate_device_value(
    energy_delta: float,
    price: float,
) -> float:
    """Value one device's energy movement for one window.

    IMPLEMENT ME.

    This is deliberately trivial (energy * price) and separate from the split
    maths above: per-device counters are plain sub-metering, they do not feed
    the hub-level splits at all. Kept as its own function so the rounding or
    sign conventions only ever have to change in one place.
    """
    raise NotImplementedError
