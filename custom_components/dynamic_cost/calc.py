"""Pure calculation engine for Dynamic Cost.

IMPORTANT: this module must never import Home Assistant. It is plain
arithmetic on plain numbers, so you can test it with hand-worked values
without any of the framework being involved. Everything else depends on it
being correct.

Everything here operates on a single tracking window's energy deltas.
"""

from __future__ import annotations

from dataclasses import dataclass


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
# end WindowEnergy


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
# end EnergySplits


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
# end FinancialSplits


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
    grid_to_battery    = min(energy.grid_import,
                             energy.battery_charge - solar_to_battery)
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
# end calculate_energy_splits


def calculate_solar_to_battery_cost(
    solar_to_battery: float,
    export_price: float,
    treat_solar_as_free: bool,
) -> float:
    """Value the solar energy that went into the battery.

    This is an opportunity cost: that energy could have been exported and
    sold instead, so it is valued at the EXPORT price, not the import price.

        treat_solar_as_free -> 0.0
        otherwise           -> solar_to_battery * export_price

    treat_solar_as_free is decided by the caller: it is true when the hub's
    checkbox is on OR the curtailment source locked "on" for this window
    (curtailed PV could not have exported that energy, so it really was
    free). This function doesn't care which of the two it was.

    Note the otherwise-case is NOT clamped: a negative export price gives a
    negative cost, because storing the energy avoided paying to export it.
    """
    if treat_solar_as_free:
        export_price = 0
    # end if

    return solar_to_battery * export_price
# end calculate_solar_to_battery_cost


def calculate_financial_splits(
    splits: EnergySplits,
    energy: WindowEnergy,
    import_price: float,
    export_price: float,
    treat_solar_as_free: bool,
) -> FinancialSplits:
    """Convert one window's energy splits into monetary figures."""
    grid_import_cost        = energy.grid_import      *  import_price
    grid_export_cost        = energy.grid_export      * -export_price
    solar_to_house_savings  = splits.solar_to_house   *  import_price
    grid_to_battery_cost    = splits.grid_to_battery  *  import_price
    battery_to_house_savings= splits.battery_to_house *  import_price
    solar_to_grid_profit    = splits.solar_to_grid    *  export_price
    grid_to_house_cost      = splits.grid_to_house    *  import_price
    battery_to_grid_profit  = splits.battery_to_grid  *  export_price
    
    solar_to_battery_cost   = calculate_solar_to_battery_cost(splits.solar_to_battery, 
                                                              export_price, 
                                                              treat_solar_as_free)

    house_energy_usage_cost = (grid_to_house_cost     + 
                               solar_to_house_savings +
                               battery_to_house_savings)
    return FinancialSplits(
        battery_to_grid_profit   = battery_to_grid_profit,
        battery_to_house_savings = battery_to_house_savings,
        grid_export_cost         = grid_export_cost,
        grid_import_cost         = grid_import_cost,
        grid_to_battery_cost     = grid_to_battery_cost,
        grid_to_house_cost       = grid_to_house_cost,
        house_energy_usage_cost  = house_energy_usage_cost,
        solar_to_battery_cost    = solar_to_battery_cost,
        solar_to_grid_profit     = solar_to_grid_profit,
        solar_to_house_savings   = solar_to_house_savings
    )
# end calculate_financial_splits

def calculate_device_value(
    energy_delta: float,
    price: float,
) -> float:
    """Value one device's energy movement for one window.

    Only general devices count value, at the import price (plan: entities,
    device-level). Solar and battery devices count energy only.

    This is deliberately trivial (energy * price) and separate from the split
    maths above: per-device counters are plain sub-metering, they do not feed
    the hub-level splits at all. Kept as its own function so the rounding or
    sign conventions only ever have to change in one place.
    """
    return energy_delta * price
# end calculate_device_value
