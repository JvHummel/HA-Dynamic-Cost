"""Tests for the window engine, driven through the simulator.

These DO boot a Home Assistant instance -- but in-process, in about a second,
with no restart and no real config directory.

Every one of these is an exercise. The docstrings say what behaviour to pin
down; the plan says what the behaviour should be.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dynamic_cost.const import (
    CONF_CURTAILMENT_SOURCE,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_TARIFF_MODE,
    CONF_WINDOW_MINUTES,
    TariffMode,
)
from custom_components.dynamic_cost.window import WindowEngine


async def test_single_window_settles(hass, simulator):
    """One window with simple flows produces the expected splits.

    WRITE ME.

        await simulator.async_setup_hub(solar=["sensor.sim_solar"])
        simulator.advance_meter("sensor.sim_solar", 2.0)
        simulator.set_price(import_price=0.30, export_price=0.05)
        ... trigger a boundary ...
        assert simulator.get_state("sensor.dynamic_cost_solar_to_house") == ...
    """
    pytest.skip("exercise: write this test")
# end test_single_window_settles


async def test_read_once_leaves_no_gap(hass, simulator):
    """Deltas across consecutive windows must sum to the total energy moved.

    WRITE ME. This is the regression test for the read-once rule: run several
    windows, advancing the meter between each, then assert the accumulated
    total equals the meter's overall movement. If anyone ever reintroduces a
    double read, this is the test that catches it.
    """
    pytest.skip("exercise: write this test")
# end test_read_once_leaves_no_gap


async def test_unavailable_device_contributes_zero_then_catches_up(hass, simulator):
    """An offline sensor sits out its windows, then reports the whole gap.

    WRITE ME.

        simulator.set_unavailable("sensor.sim_solar")
        ... run a window, assert solar contributed 0 ...
        simulator.advance_meter("sensor.sim_solar", 5.0)   # it kept producing
        simulator.restore("sensor.sim_solar")
        ... run another window, assert the full 5.0 lands now ...
    """
    pytest.skip("exercise: write this test")
# end test_unavailable_device_contributes_zero_then_catches_up


async def test_unavailable_import_price_keeps_window_open(hass, simulator):
    """An unavailable import price is 'no change', never a boundary.

    WRITE ME. Assert no new window was settled while it was down.
    """
    pytest.skip("exercise: write this test")
# end test_unavailable_import_price_keeps_window_open


async def test_late_export_price_still_locks(hass, simulator):
    """An export price arriving after the grace period still locks the window.

    WRITE ME. Use async_fire_time_changed to push past the 60s grace period
    with the export price unavailable, assert nothing settled, then bring the
    price back and assert the window settles at that moment.
    """
    pytest.skip("exercise: write this test")
# end test_late_export_price_still_locks


# ---------------------------------------------------------------------------
# Engine lifecycle: async_start / async_stop
#
# Not exercises. These build a WindowEngine directly instead of going through
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
        CONF_TARIFF_MODE: tariff_mode,
        CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE,
        CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE,
        CONF_WINDOW_MINUTES: WINDOW_MINUTES,
    }
    if curtailment:
        config[CONF_CURTAILMENT_SOURCE] = CURTAILMENT
    # end if

    engine = WindowEngine(hass, config, devices=[])
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
