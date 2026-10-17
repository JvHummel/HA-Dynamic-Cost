"""Tests for the window engine, driven through the simulator.

These DO boot a Home Assistant instance -- but in-process, in about a second,
with no restart and no real config directory.

Every expected number is worked out by hand from the plan's formulas, so a
failure means the integration disagrees with the plan. The lifecycle tests at
the bottom check start/stop wiring on a bare engine, without the simulator.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dynamic_cost.const import (
    CONF_CURTAILMENT_SOURCE,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_TARIFF_MODE,
    CONF_WINDOW_MINUTES,
    EXPORT_PRICE_GRACE_SECONDS,
    TariffMode,
)
from custom_components.dynamic_cost.window import WindowEngine

from .simulator import CURTAILMENT as SIM_CURTAILMENT
from .simulator import EXPORT_PRICE as SIM_EXPORT_PRICE
from .simulator import GRID_EXPORT, GRID_IMPORT
from .simulator import IMPORT_PRICE as SIM_IMPORT_PRICE

SOLAR = "sensor.sim_solar"
SOLAR_2 = "sensor.sim_solar_2"
CHARGE = "sensor.sim_battery_charge"
DISCHARGE = "sensor.sim_battery_discharge"
CHARGE_2 = "sensor.sim_battery_2_charge"
DISCHARGE_2 = "sensor.sim_battery_2_discharge"
GENERAL = "sensor.sim_general"
PAST_GRACE = EXPORT_PRICE_GRACE_SECONDS + 1


async def _priced_window(simulator, import_price: float, export_price: float) -> None:
    """Settle whatever window is open and open a fresh one at these prices.

    When this returns, the new window's export price has locked, so energy
    added next lands in a window priced at exactly import_price/export_price.
    """
    await simulator.async_advance_window(
        import_price=import_price, export_price=export_price
    )
# end _priced_window


async def test_single_window_settles(hass, simulator):
    """One window: solar runs the house, valued at the import price."""
    await simulator.async_setup_hub(solar=[SOLAR])
    await _priced_window(simulator, 0.30, 0.05)

    simulator.advance_meter(SOLAR, 2.0)
    await simulator.async_advance_window()

    assert simulator.get_total("house_energy_usage") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_grid") == pytest.approx(0.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(0.60)
    assert simulator.get_total("house_energy_usage_cost") == pytest.approx(0.60)
    assert simulator.get_total("grid_import_cost") == pytest.approx(0.0)
# end test_single_window_settles


async def test_read_once_leaves_no_gap(hass, simulator):
    """Deltas across consecutive windows sum to the total energy moved.

    The regression test for the read-once rule: if a boundary ever reads a
    sensor twice, energy goes missing between windows and these totals drift.
    """
    await simulator.async_setup_hub()
    await _priced_window(simulator, 0.20, 0.05)

    for kwh in (1.0, 2.5, 0.5):      # windows priced 0.20, 0.21, 0.22
        simulator.advance_meter(GRID_IMPORT, kwh)
        await simulator.async_advance_window()
    # end for

    assert simulator.get_total("house_energy_usage") == pytest.approx(4.0)
    assert simulator.get_total("grid_to_house") == pytest.approx(4.0)
    assert simulator.get_total("grid_import_cost") == pytest.approx(
        1.0 * 0.20 + 2.5 * 0.21 + 0.5 * 0.22
    )
# end test_read_once_leaves_no_gap


async def test_unavailable_device_contributes_zero_then_catches_up(hass, simulator):
    """An offline sensor sits out its windows, then reports the whole gap."""
    await simulator.async_setup_hub(solar=[SOLAR])
    await _priced_window(simulator, 0.30, 0.05)

    simulator.set_unavailable(SOLAR)
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_house") == pytest.approx(0.0)
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(0.0)

    simulator.advance_meter(SOLAR, 5.0)   # back online: it kept producing
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_house") == pytest.approx(5.0)
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(5.0)
# end test_unavailable_device_contributes_zero_then_catches_up


async def test_unavailable_import_price_keeps_window_open(hass, simulator):
    """An unavailable import price is 'no change', never a boundary."""
    await simulator.async_setup_hub()
    await _priced_window(simulator, 0.30, 0.05)
    simulator.advance_meter(GRID_IMPORT, 1.0)

    simulator.set_unavailable(SIM_IMPORT_PRICE)
    await simulator.async_wait(3600)
    assert simulator.get_total("house_energy_usage") == pytest.approx(0.0)

    simulator.set_price(import_price=0.40)    # a real, different price: boundary
    await hass.async_block_till_done()
    assert simulator.get_total("house_energy_usage") == pytest.approx(1.0)
    assert simulator.get_total("grid_import_cost") == pytest.approx(0.30)
# end test_unavailable_import_price_keeps_window_open


async def test_late_export_price_still_locks(hass, simulator):
    """A window without an export price is held open until one arrives.

    While it is held, energy keeps accumulating into it and later import
    price changes only update the price the next window opens with.
    """
    await simulator.async_setup_hub()
    await simulator.async_wait(PAST_GRACE)          # setup window locks
    simulator.set_unavailable(SIM_EXPORT_PRICE)
    simulator.set_price(import_price=0.30)           # opens a window w/o export
    await hass.async_block_till_done()

    simulator.advance_meter(GRID_IMPORT, 1.0)
    await simulator.async_wait(PAST_GRACE)           # grace passes, no lock
    simulator.set_price(import_price=0.40)           # ready to close: held
    await hass.async_block_till_done()
    assert simulator.get_total("house_energy_usage") == pytest.approx(0.0)

    simulator.advance_meter(GRID_IMPORT, 0.5)        # still this window
    simulator.set_price(import_price=0.45)           # latest price wins
    await hass.async_block_till_done()
    assert simulator.get_total("house_energy_usage") == pytest.approx(0.0)

    simulator.set_price(export_price=0.05)           # late arrival: settles now
    await hass.async_block_till_done()
    assert simulator.get_total("house_energy_usage") == pytest.approx(1.5)
    assert simulator.get_total("grid_import_cost") == pytest.approx(1.5 * 0.30)

    simulator.advance_meter(GRID_IMPORT, 1.0)        # next window: at 0.45
    await simulator.async_advance_window()
    assert simulator.get_total("grid_import_cost") == pytest.approx(
        1.5 * 0.30 + 1.0 * 0.45
    )
# end test_late_export_price_still_locks


async def test_energy_counter_reset_counts_as_zero(hass, simulator):
    """A meter reading lower than its snapshot is a reset, not negative energy."""
    await simulator.async_setup_hub(solar=[SOLAR])
    await _priced_window(simulator, 0.30, 0.05)

    simulator.set_meter(SOLAR, 10.0)
    await simulator.async_advance_window()
    simulator.set_meter(SOLAR, 0.5)                  # e.g. a daily reset
    await simulator.async_advance_window()
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(10.0)

    simulator.advance_meter(SOLAR, 2.0)              # counted from the reset value
    await simulator.async_advance_window()
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(12.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(12.0)
# end test_energy_counter_reset_counts_as_zero


async def test_general_device_does_not_feed_splits(hass, simulator):
    """General devices are sub-metering only, valued at the import price."""
    await simulator.async_setup_hub(general=[GENERAL])
    await _priced_window(simulator, 0.30, 0.05)

    simulator.advance_meter(GRID_IMPORT, 3.0)
    simulator.advance_meter(GENERAL, 1.0)            # part of those 3 kWh
    await simulator.async_advance_window()

    assert simulator.get_total("house_energy_usage") == pytest.approx(3.0)
    assert simulator.get_total("grid_import_cost") == pytest.approx(0.90)
    general = "General 1"
    assert simulator.get_total("import_energy", device=general) == pytest.approx(1.0)
    assert simulator.get_total("import_value", device=general) == pytest.approx(0.30)
# end test_general_device_does_not_feed_splits


async def test_solar_and_battery_devices_aggregate(hass, simulator):
    """Every solar device adds to Solar, every battery to charge/discharge."""
    await simulator.async_setup_hub(
        solar=[SOLAR, SOLAR_2],
        batteries=[(CHARGE, DISCHARGE), (CHARGE_2, DISCHARGE_2)],
    )
    await _priced_window(simulator, 0.30, 0.10)

    simulator.advance_meter(SOLAR, 1.0)
    simulator.advance_meter(SOLAR_2, 2.0)            # Solar = 3
    simulator.advance_meter(CHARGE, 0.5)
    simulator.advance_meter(CHARGE_2, 0.5)           # BatteryCharge = 1
    await simulator.async_advance_window()

    # HouseEnergyUsage = 3 - 1 = 2, all of it from solar; the rest charged.
    assert simulator.get_total("house_energy_usage") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_battery") == pytest.approx(1.0)
    assert simulator.get_total("solar_to_battery_cost") == pytest.approx(0.10)
    assert simulator.get_total("export_energy", device="Solar 2") == pytest.approx(2.0)
    battery = "Battery 1"
    assert simulator.get_total("import_energy", device=battery) == pytest.approx(0.5)
    assert simulator.get_total("export_energy", device=battery) == pytest.approx(0.0)
# end test_solar_and_battery_devices_aggregate


async def test_treat_solar_as_free_checkbox(hass, simulator):
    """With the checkbox on, solar into the battery never costs anything."""
    await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[(CHARGE, DISCHARGE)], treat_solar_as_free=True
    )
    await _priced_window(simulator, 0.30, 0.10)

    simulator.advance_meter(SOLAR, 3.0)
    simulator.advance_meter(CHARGE, 3.0)
    await simulator.async_advance_window()

    assert simulator.get_total("solar_to_battery") == pytest.approx(3.0)
    assert simulator.get_total("solar_to_battery_cost") == pytest.approx(0.0)
# end test_treat_solar_as_free_checkbox


async def test_curtailed_window_counts_solar_to_battery_as_free(hass, simulator):
    """Curtailment locked on for a window zeroes its solar-to-battery cost.

    The state locks when the grace period ends; switching it later only
    affects the next window.
    """
    await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[(CHARGE, DISCHARGE)], curtailment=True
    )
    simulator.set_curtailed(True)
    await _priced_window(simulator, 0.30, 0.10)      # opens curtailed, locks

    simulator.advance_meter(SOLAR, 3.0)
    simulator.advance_meter(CHARGE, 3.0)
    simulator.set_curtailed(False)                   # too late for this window
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_battery") == pytest.approx(3.0)
    assert simulator.get_total("solar_to_battery_cost") == pytest.approx(0.0)

    simulator.advance_meter(SOLAR, 3.0)              # next window: not curtailed
    simulator.advance_meter(CHARGE, 3.0)
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_battery_cost") == pytest.approx(0.30)
# end test_curtailed_window_counts_solar_to_battery_as_free


async def test_unavailable_curtailment_holds_window_open(hass, simulator):
    """No curtailment reading means no lock, and no lock means no close."""
    await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[(CHARGE, DISCHARGE)], curtailment=True
    )
    simulator.set_price(export_price=0.10)
    await simulator.async_wait(PAST_GRACE)           # setup window locks
    simulator.set_unavailable(SIM_CURTAILMENT)
    simulator.set_price(import_price=0.30)           # opens a window w/o state
    await hass.async_block_till_done()

    simulator.advance_meter(SOLAR, 3.0)
    simulator.advance_meter(CHARGE, 3.0)
    await simulator.async_wait(PAST_GRACE)           # export locks, curtailment can't
    simulator.set_price(import_price=0.40)           # ready to close: held
    await hass.async_block_till_done()
    assert simulator.get_total("solar_to_battery") == pytest.approx(0.0)

    simulator.set_curtailed(True)                    # late arrival: settles now
    await hass.async_block_till_done()
    assert simulator.get_total("solar_to_battery") == pytest.approx(3.0)
    assert simulator.get_total("solar_to_battery_cost") == pytest.approx(0.0)
# end test_unavailable_curtailment_holds_window_open


async def test_first_start_without_import_price_opens_no_window(hass, simulator):
    """First start with no import price: nothing is tracked until one arrives."""
    await simulator.async_setup_hub(solar=[SOLAR], import_price_available=False)

    simulator.advance_meter(SOLAR, 2.0)              # before any window: never counted
    await simulator.async_wait(3600)
    assert simulator.get_total("solar_to_house") == pytest.approx(0.0)

    simulator.set_price(import_price=0.30)           # opens the first window
    await hass.async_block_till_done()
    assert simulator.get_total("solar_to_house") == pytest.approx(0.0)

    simulator.advance_meter(SOLAR, 1.0)
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_house") == pytest.approx(1.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(0.30)
# end test_first_start_without_import_price_opens_no_window


async def test_unavailable_logging_is_edge_triggered(hass, simulator, caplog):
    """One warning when a sensor goes down, one info line when it recovers."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(solar=[SOLAR])
    await _priced_window(simulator, 0.30, 0.05)

    def lines(level: int) -> int:
        return sum(
            1 for record in caplog.records
            if record.levelno == level
            and record.name.startswith("custom_components.dynamic_cost")
            and SOLAR in record.getMessage()
        )
    # end lines

    simulator.set_unavailable(SOLAR)
    for _ in range(3):
        await simulator.async_advance_window()
    # end for
    assert lines(logging.WARNING) == 1

    simulator.restore(SOLAR)
    for _ in range(2):
        await simulator.async_advance_window()
    # end for
    assert lines(logging.INFO) == 1
    assert lines(logging.WARNING) == 1
# end test_unavailable_logging_is_edge_triggered


def _window_logs(caplog, level: int) -> list[str]:
    """The stale-window lines logged at level (plan: logging)."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == level
        and record.name.startswith("custom_components.dynamic_cost")
        and "racking window" in record.getMessage()
    ]
# end _window_logs


async def test_stale_window_warns_once_then_logs_its_close(hass, simulator, caplog):
    """Open past the default 120 minutes: one warning naming the import price
    sensor, however long it lasts, and one info line when it finally closes."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(solar=[SOLAR])

    await simulator.async_wait(119 * 60)
    assert _window_logs(caplog, logging.WARNING) == []
    assert simulator.hub_state("window_status") == "open"

    await simulator.async_wait(121 * 60)
    warnings = _window_logs(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert SIM_IMPORT_PRICE in warnings[0]
    assert simulator.hub_state("window_status") == "overdue"

    await simulator.async_wait(300 * 60)
    assert len(_window_logs(caplog, logging.WARNING)) == 1

    simulator.advance_meter(SOLAR, 1.0)              # still counted in the end
    await simulator.async_advance_window(import_price=0.30)
    assert len(_window_logs(caplog, logging.INFO)) == 1
    assert simulator.hub_state("window_status") == "open"
    assert simulator.get_total("solar_to_house") == pytest.approx(1.0)
# end test_stale_window_warns_once_then_logs_its_close


async def test_stale_window_timeout_follows_hub_setting(hass, simulator, caplog):
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(stale_window_minutes=30)

    await simulator.async_wait(29 * 60)
    assert _window_logs(caplog, logging.WARNING) == []
    await simulator.async_wait(31 * 60)
    assert len(_window_logs(caplog, logging.WARNING)) == 1
# end test_stale_window_timeout_follows_hub_setting


async def test_closing_a_window_cancels_its_countdown(hass, simulator, caplog):
    """Only the window that actually went stale warns: a window that closed in
    time leaves no countdown running behind it.

    (Each async_wait counts from the same real "now", so the two windows'
    countdowns end at nearly the same moment; a leftover one would show up as
    a second warning.)
    """
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(stale_window_minutes=30)

    await simulator.async_advance_window()           # the first window closes
    await simulator.async_wait(31 * 60)              # the second goes stale
    assert len(_window_logs(caplog, logging.WARNING)) == 1
# end test_closing_a_window_cancels_its_countdown


async def test_stale_warning_names_a_missing_lock(hass, simulator, caplog):
    """Held open past the timeout: the warning names the export price sensor."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub()
    await simulator.async_wait(PAST_GRACE)
    simulator.set_unavailable(SIM_EXPORT_PRICE)
    simulator.set_price(import_price=0.30)           # next window: no export price
    await hass.async_block_till_done()
    simulator.set_price(import_price=0.40)           # boundary: held open
    await hass.async_block_till_done()

    await simulator.async_wait(121 * 60)
    warnings = _window_logs(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "held open" in warnings[0] and SIM_EXPORT_PRICE in warnings[0]
    assert simulator.hub_state("window_status") == "held_open"
# end test_stale_warning_names_a_missing_lock


async def test_no_window_yet_warns_too(hass, simulator, caplog):
    """A first start whose import price never arrives also gets its warning."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(import_price_available=False)
    assert simulator.hub_state("window_status") == "no_window"

    await simulator.async_wait(121 * 60)
    warnings = _window_logs(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].startswith("No tracking window")
    assert SIM_IMPORT_PRICE in warnings[0]
# end test_no_window_yet_warns_too


async def test_fixed_windows_never_warn(hass, simulator, caplog):
    """Fixed windows close on their own timer, so they never go stale -- even
    a window longer than the timeout."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    await simulator.async_setup_hub(
        tariff_mode=TariffMode.FIXED,
        import_tariff=0.25,
        export_tariff=0.08,
        window_minutes=180,
        stale_window_minutes=30,
    )
    await simulator.async_wait(121 * 60)
    assert _window_logs(caplog, logging.WARNING) == []
# end test_fixed_windows_never_warn


async def test_fixed_tariff_windows(hass, simulator):
    """Fixed tariffs: timer-driven windows at the configured prices."""
    await simulator.async_setup_hub(
        solar=[SOLAR],
        tariff_mode=TariffMode.FIXED,
        import_tariff=0.25,
        export_tariff=0.08,
        window_minutes=15,
    )

    simulator.advance_meter(SOLAR, 2.0)
    simulator.advance_meter(GRID_EXPORT, 1.0)        # house used 1, exported 1
    simulator.set_price(import_price=0.99)           # ignored in fixed mode
    await simulator.async_advance_window()

    assert simulator.get_total("solar_to_house") == pytest.approx(1.0)
    assert simulator.get_total("solar_to_grid") == pytest.approx(1.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(0.25)
    assert simulator.get_total("solar_to_grid_profit") == pytest.approx(0.08)
    assert simulator.get_total("grid_export_profit") == pytest.approx(0.08)

    simulator.advance_meter(SOLAR, 1.0)              # a second window
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(0.50)
# end test_fixed_tariff_windows


# ---------------------------------------------------------------------------
# Engine lifecycle: async_start / async_stop
#
# These build a WindowEngine directly instead of going through
# the simulator, so they test the listener wiring on its own. Each handler is
# swapped for a recorder before start, so the tests only see WHETHER a
# handler fired, never what it did.
# ---------------------------------------------------------------------------

IMPORT_PRICE = "sensor.test_import_price"
EXPORT_PRICE = "sensor.test_export_price"
CURTAILMENT = "input_boolean.test_curtailment"
WINDOW_MINUTES = 15

HANDLERS = (
    "_async_import_price_changed",
    "_async_export_price_changed",
    "_async_curtailment_changed",
    "_async_boundary_reached",
    "_async_grace_elapsed",
)


def _recorder(calls: Counter, name: str):
    """A stand-in for one engine handler that only counts its calls."""
    if name == "_async_boundary_reached":
        async def record_async(*_args) -> None:
            calls[name] += 1
        # end record_async
        return record_async
    # end if

    @callback
    def record(*_args) -> None:
        calls[name] += 1
    # end record
    return record
# end _recorder


def _make_engine(
    hass: HomeAssistant, tariff_mode: TariffMode, *, curtailment: bool = False
) -> tuple[WindowEngine, Counter]:
    """An engine with no devices and every handler replaced by a recorder."""
    config = {
        CONF_GRID_IMPORT_SENSOR: "sensor.test_grid_import",
        CONF_GRID_EXPORT_SENSOR: "sensor.test_grid_export",
        CONF_TARIFF_MODE: tariff_mode,
        CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE,
        CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE,
        CONF_IMPORT_TARIFF: 0.25,
        CONF_EXPORT_TARIFF: 0.08,
        CONF_WINDOW_MINUTES: WINDOW_MINUTES,
    }
    if curtailment:
        config[CONF_CURTAILMENT_SOURCE] = CURTAILMENT
    # end if

    engine = WindowEngine(hass, "test_entry", config, devices=[])
    calls: Counter = Counter()
    for name in HANDLERS:
        # An instance attribute shadows the method of the same name, so the
        # engine's own `self._async_...` lookups find the recorder instead.
        setattr(engine, name, _recorder(calls, name))
    # end for
    return engine, calls
# end _make_engine


async def _poke_everything(hass: HomeAssistant) -> None:
    """Trigger every kind of event the engine could possibly be listening for."""
    hass.states.async_set(IMPORT_PRICE, "0.30")
    hass.states.async_set(EXPORT_PRICE, "0.05")
    hass.states.async_set(CURTAILMENT, "on")
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(minutes=WINDOW_MINUTES + 1)
    )
    await hass.async_block_till_done()
# end _poke_everything


async def test_start_dynamic_listens_to_both_prices(hass):
    """Dynamic mode: price changes reach their handlers; there is no timer."""
    engine, calls = _make_engine(hass, TariffMode.DYNAMIC)
    await engine.async_start()

    await _poke_everything(hass)

    assert calls["_async_import_price_changed"] == 1
    assert calls["_async_export_price_changed"] == 1
    assert calls["_async_boundary_reached"] == 0
    engine.async_stop()
# end test_start_dynamic_listens_to_both_prices


async def test_start_fixed_runs_window_timer(hass):
    """Fixed mode: the window timer fires; the price sensors are ignored."""
    engine, calls = _make_engine(hass, TariffMode.FIXED)
    await engine.async_start()

    await _poke_everything(hass)

    assert calls["_async_boundary_reached"] == 1
    assert calls["_async_import_price_changed"] == 0
    assert calls["_async_export_price_changed"] == 0
    engine.async_stop()
# end test_start_fixed_runs_window_timer


@pytest.mark.parametrize("tariff_mode", [TariffMode.DYNAMIC, TariffMode.FIXED])
@pytest.mark.parametrize(("curtailment", "expected"), [(True, 1), (False, 0)])
async def test_start_tracks_curtailment_source_only_when_set(
    hass, tariff_mode, curtailment, expected
):
    """A configured curtailment source is tracked in both tariff modes."""
    engine, calls = _make_engine(hass, tariff_mode, curtailment=curtailment)
    await engine.async_start()

    await _poke_everything(hass)

    assert calls["_async_curtailment_changed"] == expected
    engine.async_stop()
# end test_start_tracks_curtailment_source_only_when_set


@pytest.mark.parametrize("tariff_mode", [TariffMode.DYNAMIC, TariffMode.FIXED])
async def test_stop_removes_every_listener(hass, tariff_mode):
    """After stop, nothing the engine registered fires any more."""
    engine, calls = _make_engine(hass, tariff_mode, curtailment=True)
    await engine.async_start()

    engine.async_stop()
    await _poke_everything(hass)

    assert sum(calls.values()) == 0
# end test_stop_removes_every_listener


async def test_stop_cancels_pending_grace_timer(hass):
    """A grace timer still running at stop must never fire afterwards."""
    engine, calls = _make_engine(hass, TariffMode.DYNAMIC)
    await engine.async_start()
    engine._open_window(0.25)

    engine.async_stop()
    await _poke_everything(hass)

    assert calls["_async_grace_elapsed"] == 0
# end test_stop_cancels_pending_grace_timer


async def test_stop_is_safe_before_any_window_opened(hass):
    """Stopping an engine that never opened a window must not raise."""
    engine, _calls = _make_engine(hass, TariffMode.DYNAMIC)

    engine.async_stop()
# end test_stop_is_safe_before_any_window_opened


async def test_restart_does_not_duplicate_listeners(hass):
    """start -> stop -> start (an entry reload) leaves exactly one of each."""
    engine, calls = _make_engine(hass, TariffMode.DYNAMIC, curtailment=True)
    await engine.async_start()
    engine.async_stop()
    await engine.async_start()

    await _poke_everything(hass)

    assert calls["_async_import_price_changed"] == 1
    assert calls["_async_export_price_changed"] == 1
    assert calls["_async_curtailment_changed"] == 1
    engine.async_stop()
# end test_restart_does_not_duplicate_listeners
