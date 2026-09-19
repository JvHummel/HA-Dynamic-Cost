"""Tests for the window engine, driven through the simulator.

These DO boot a Home Assistant instance -- but in-process, in about a second,
with no restart and no real config directory.

Every one of these is an exercise. The docstrings say what behaviour to pin
down; the plan says what the behaviour should be.
"""

from __future__ import annotations

import pytest


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


async def test_read_once_leaves_no_gap(hass, simulator):
    """Deltas across consecutive windows must sum to the total energy moved.

    WRITE ME. This is the regression test for the read-once rule: run several
    windows, advancing the meter between each, then assert the accumulated
    total equals the meter's overall movement. If anyone ever reintroduces a
    double read, this is the test that catches it.
    """
    pytest.skip("exercise: write this test")


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


async def test_unavailable_import_price_keeps_window_open(hass, simulator):
    """An unavailable import price is 'no change', never a boundary.

    WRITE ME. Assert no new window was settled while it was down.
    """
    pytest.skip("exercise: write this test")


async def test_late_export_price_still_locks(hass, simulator):
    """An export price arriving after the grace period still locks the window.

    WRITE ME. Use async_fire_time_changed to push past the 60s grace period
    with the export price unavailable, assert nothing settled, then bring the
    price back and assert the window settles at that moment.
    """
    pytest.skip("exercise: write this test")
