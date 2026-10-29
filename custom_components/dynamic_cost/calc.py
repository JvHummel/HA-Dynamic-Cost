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
    grid_export_profit: float
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


@dataclass(frozen=True, slots=True)
class BatteryPool:
    """The energy in the hub's batteries, in kWh, and what it cost to put there.

    Carried from window to window by the window engine (plan: calculation
    engine, battery pool). All battery devices share one pool, like the
    splits.
    """

    kwh: float = 0.0
    cost: float = 0.0

    @property
    def average_cost(self) -> float | None:
        """Cost per kWh of what's in the pool, or None while it's empty."""
        if self.kwh <= 0:
            return None
        # end if
        return self.cost / self.kwh
    # end average_cost
# end BatteryPool


def calculate_solar_rate(export_price: float, treat_solar_as_free: bool) -> float:
    """What a kWh of solar used at home or stored cost: the export price it
    could have fetched, or 0 when solar counts as free.

    treat_solar_as_free is decided by the caller: it is true when the hub's
    checkbox is on OR the curtailment source locked "on" for this window
    (curtailed PV could not have exported that energy, so it really was
    free). This function doesn't care which of the two it was.

    Deliberately NOT clamped: a negative export price gives a negative rate,
    because using the energy avoided paying to export it.
    """
    if treat_solar_as_free:
        return 0.0
    # end if
    return export_price
# end calculate_solar_rate


def calculate_solar_to_battery_cost(
    solar_to_battery: float,
    export_price: float,
    treat_solar_as_free: bool,
) -> float:
    """Value the solar energy that went into the battery.

    This is an opportunity cost: that energy could have been exported and
    sold instead, so it is valued at the solar rate (the EXPORT price, or 0
    when solar counts as free), not the import price.
    """
    return solar_to_battery * calculate_solar_rate(export_price, treat_solar_as_free)
# end calculate_solar_to_battery_cost


def settle_battery_pool(
    pool: BatteryPool,
    splits: EnergySplits,
    import_price: float,
    export_price: float,
    treat_solar_as_free: bool,
    capacity: float | None,
) -> tuple[BatteryPool, float]:
    """Run one window through the battery pool.

    Returns the pool after this window and the battery rate: what each kWh
    that came out of the batteries in this window cost. Charging goes in
    first, at what it cost; discharging comes out at the pool's average.
    Energy coming out beyond what the pool holds was in the battery before
    tracking started, and counts at the import price.

    Above capacity, the excess kWh can only be losses: they are dropped but
    their cost stays, so the energy that does come out pays for them.
    capacity None means unknown: no ceiling.
    """
    kwh = pool.kwh + splits.grid_to_battery + splits.solar_to_battery
    cost = (pool.cost
            + splits.grid_to_battery * import_price
            + calculate_solar_to_battery_cost(splits.solar_to_battery,
                                              export_price,
                                              treat_solar_as_free))

    discharged = splits.battery_to_house + splits.battery_to_grid
    if discharged > 0:
        known = min(discharged, max(0.0, kwh))
        average = cost / kwh if kwh > 0 else 0.0
        battery_rate = (known * average
                        + (discharged - known) * import_price) / discharged
        kwh -= known
        cost -= known * average
    else:
        # Nothing came out, so nothing is valued at this rate.
        battery_rate = import_price
    # end if

    if kwh <= 0:
        kwh, cost = 0.0, 0.0      # empty: no rounding residue left behind
    elif capacity is not None and kwh > capacity:
        kwh = capacity            # cost stays: see the docstring
    # end if

    return BatteryPool(kwh=kwh, cost=cost), battery_rate
# end settle_battery_pool


def calculate_financial_splits(
    splits: EnergySplits,
    energy: WindowEnergy,
    import_price: float,
    export_price: float,
    treat_solar_as_free: bool,
    battery_rate: float,
) -> FinancialSplits:
    """Convert one window's energy splits into monetary figures.

    battery_rate is what a kWh out of the batteries cost this window, from
    settle_battery_pool. It only values the house's battery part.
    """
    grid_import_cost        = energy.grid_import      *  import_price
    grid_export_profit      = energy.grid_export      *  export_price
    solar_to_house_savings  = splits.solar_to_house   *  import_price
    grid_to_battery_cost    = splits.grid_to_battery  *  import_price
    battery_to_house_savings= splits.battery_to_house *  import_price
    solar_to_grid_profit    = splits.solar_to_grid    *  export_price
    grid_to_house_cost      = splits.grid_to_house    *  import_price
    battery_to_grid_profit  = splits.battery_to_grid  *  export_price
    
    solar_to_battery_cost   = calculate_solar_to_battery_cost(splits.solar_to_battery, 
                                                              export_price, 
                                                              treat_solar_as_free)

    # What the house's energy really cost: grid at the import price, solar
    # at what it could have earned, battery at what charging it cost.
    solar_rate              = calculate_solar_rate(export_price, treat_solar_as_free)
    house_energy_usage_cost = (grid_to_house_cost                   +
                               splits.solar_to_house * solar_rate   +
                               splits.battery_to_house * battery_rate)
    return FinancialSplits(
        battery_to_grid_profit   = battery_to_grid_profit,
        battery_to_house_savings = battery_to_house_savings,
        grid_export_profit       = grid_export_profit,
        grid_import_cost         = grid_import_cost,
        grid_to_battery_cost     = grid_to_battery_cost,
        grid_to_house_cost       = grid_to_house_cost,
        house_energy_usage_cost  = house_energy_usage_cost,
        solar_to_battery_cost    = solar_to_battery_cost,
        solar_to_grid_profit     = solar_to_grid_profit,
        solar_to_house_savings   = solar_to_house_savings
    )
# end calculate_financial_splits


@dataclass(frozen=True, slots=True)
class Summaries:
    """Hub-wide totals for one window: per source, what moved and what it
    cost or earned in all (plan: entities, summaries)."""

    grid_import_energy: float
    grid_export_energy: float
    solar_production: float
    battery_charged: float
    battery_discharged: float
    grid_net_cost: float
    solar_value: float
    battery_net_savings: float
# end Summaries


def calculate_summaries(energy: WindowEnergy, money: FinancialSplits) -> Summaries:
    """Sum one window up per source.

    The energy figures are the meters' own: grid import and export straight
    from the grid sensors, solar and battery from their devices. Solar's
    value is what it earned directly; solar stored in the battery is the
    battery's to earn back, so the battery's savings pay for it at the
    export price it could have fetched.
    """
    grid_net_cost       = money.grid_import_cost - money.grid_export_profit
    solar_value         = (money.solar_to_house_savings +
                           money.solar_to_grid_profit)
    battery_net_savings = (money.battery_to_house_savings +
                           money.battery_to_grid_profit -
                           money.grid_to_battery_cost -
                           money.solar_to_battery_cost)

    return Summaries(
        grid_import_energy  = energy.grid_import,
        grid_export_energy  = energy.grid_export,
        solar_production    = energy.solar,
        battery_charged     = energy.battery_charge,
        battery_discharged  = energy.battery_discharge,
        grid_net_cost       = grid_net_cost,
        solar_value         = solar_value,
        battery_net_savings = battery_net_savings,
    )
# end calculate_summaries

def calculate_device_value(
    energy_delta: float,
    splits: EnergySplits,
    money: FinancialSplits,
    import_price: float,
) -> float:
    """What one general device's energy cost in one window.

    No meter can tell where a device's energy came from, but the house's mix
    in this window is known: the device pays the house's cost per kWh
    (plan: entities, device-level). With no house energy to take a mix from,
    it pays what the grid would have charged.

    Only general devices count value; solar and battery devices count
    energy only. Device counters don't feed the hub-level splits.
    """
    house = splits.grid_to_house + splits.solar_to_house + splits.battery_to_house
    if house > 0:
        return energy_delta * money.house_energy_usage_cost / house
    # end if
    return energy_delta * import_price
# end calculate_device_value
