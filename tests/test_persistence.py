"""Tests for persistence: restarts, and configuration changes made meanwhile.

Each test that changes the configuration follows one row of the restore table
in the plan's Persistence section. The hub is stopped and started through the
simulator, which is exactly what Home Assistant does at a restart; the store
lives in pytest's in-memory hass_storage in between.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.dynamic_cost.const import (
    CONF_EXPORT_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_IMPORT_TARIFF,
    CONF_TARIFF_MODE,
    CONF_WINDOW_MINUTES,
    DOMAIN,
    EXPORT_PRICE_GRACE_SECONDS,
    SUMMARY_ENERGY_KEYS,
    SUMMARY_FINANCIAL_KEYS,
    DeviceType,
    TariffMode,
)

from .simulator import EXPORT_PRICE as SIM_EXPORT_PRICE
from .simulator import GRID_EXPORT
from .simulator import IMPORT_PRICE as SIM_IMPORT_PRICE

SOLAR = "sensor.sim_solar"
SUMMARIES = (*SUMMARY_ENERGY_KEYS, *SUMMARY_FINANCIAL_KEYS)
SOLAR_2 = "sensor.sim_solar_2"
SOLAR_NEW = "sensor.sim_solar_new"
PAST_GRACE = EXPORT_PRICE_GRACE_SECONDS + 1


async def _hub_with_one_settled_window(simulator, **hub_options) -> None:
    """A hub whose first real window (2 kWh of solar at 0.30) has settled.

    Leaves a fresh window open at import 0.31, export 0.05.
    """
    await simulator.async_setup_hub(**{"solar": [SOLAR], **hub_options})
    await simulator.async_advance_window(import_price=0.30, export_price=0.05)
    simulator.advance_meter(SOLAR, 2.0)
    await simulator.async_advance_window()
# end _hub_with_one_settled_window


async def test_totals_survive_restart(hass, simulator):
    await _hub_with_one_settled_window(simulator)

    await simulator.async_restart()

    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(0.60)
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(2.0)
# end test_totals_survive_restart


async def test_downtime_energy_lands_in_first_window_after_restart(hass, simulator):
    """The open window is restored, and the downtime energy settles in it."""
    await _hub_with_one_settled_window(simulator)
    simulator.advance_meter(SOLAR, 1.0)              # in the open window

    await simulator.async_stop_hub()
    simulator.advance_meter(SOLAR, 2.0)              # while Home Assistant is down
    await simulator.async_start_hub()
    await simulator.async_advance_window()

    assert simulator.get_total("solar_to_house") == pytest.approx(2.0 + 3.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(
        0.60 + 3.0 * 0.31
    )
# end test_downtime_energy_lands_in_first_window_after_restart


async def test_locked_export_price_survives_restart(hass, simulator):
    """A restored window keeps its locked export price, so it can still close."""
    await _hub_with_one_settled_window(simulator)
    simulator.advance_meter(SOLAR, 2.0)
    simulator.advance_meter(GRID_EXPORT, 2.0)        # all of it exported

    await simulator.async_stop_hub()
    simulator.set_unavailable(SIM_EXPORT_PRICE)      # gone after the restart
    await simulator.async_start_hub()
    simulator.set_price(import_price=0.40)           # boundary
    await hass.async_block_till_done()

    assert simulator.get_total("solar_to_grid") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_grid_profit") == pytest.approx(2.0 * 0.05)
# end test_locked_export_price_survives_restart


async def test_restored_window_counts_its_grace_period_as_over(hass, simulator):
    """Stopped mid-grace: after the restart, the window locks what it has at once.

    Its grace timer died with the restart, so nothing else would ever lock it.
    """
    await simulator.async_setup_hub(solar=[SOLAR])
    await simulator.async_wait(PAST_GRACE)           # setup window locks
    simulator.set_price(export_price=0.05)
    simulator.set_price(import_price=0.30)           # opens a window, grace running
    await hass.async_block_till_done()
    simulator.advance_meter(SOLAR, 2.0)
    simulator.advance_meter(GRID_EXPORT, 2.0)

    await simulator.async_restart()                  # still inside the grace period
    simulator.set_price(import_price=0.40)           # boundary, no time has passed
    await hass.async_block_till_done()

    assert simulator.get_total("solar_to_grid") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_grid_profit") == pytest.approx(2.0 * 0.05)
# end test_restored_window_counts_its_grace_period_as_over


async def test_window_times_survive_restart(hass, simulator):
    """The diagnostics show the same open and close times after a restart."""
    await _hub_with_one_settled_window(simulator)
    opened = simulator.hub_state("window_opened")
    closed = simulator.hub_state("last_window_closed")
    assert "unknown" not in (opened, closed)

    await simulator.async_restart()

    assert simulator.hub_state("window_opened") == opened
    assert simulator.hub_state("last_window_closed") == closed
# end test_window_times_survive_restart


async def test_stale_countdown_continues_after_restart(
    hass, simulator, hass_storage, caplog
):
    """A restart doesn't reset a window's age: one opened 119 minutes before
    the restart warns a minute or two after it, not 120 minutes later."""
    caplog.set_level(logging.INFO, logger="custom_components.dynamic_cost")
    entry = await simulator.async_setup_hub()
    await simulator.async_stop_hub()
    window = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["window"]
    window["opened_at"] = (dt_util.utcnow() - timedelta(minutes=119)).isoformat()

    await simulator.async_start_hub()
    await simulator.async_wait(2 * 60)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and SIM_IMPORT_PRICE in record.getMessage()
    ]
    assert len(warnings) == 1
# end test_stale_countdown_continues_after_restart


async def test_store_from_before_window_times_still_loads(
    hass, simulator, hass_storage
):
    """A v0.1.0 store has no opened_at or last_closed_at: the window is still
    restored, and its age counts from the restart."""
    await _hub_with_one_settled_window(simulator)
    await simulator.async_stop_hub()
    data = hass_storage[f"{DOMAIN}.{simulator.entry.entry_id}"]["data"]
    del data["window"]["opened_at"]
    del data["last_closed_at"]
    simulator.advance_meter(SOLAR, 1.0)

    await simulator.async_start_hub()
    assert simulator.hub_state("window_opened") not in ("unknown", None)
    assert simulator.hub_state("last_window_closed") == "unknown"
    await simulator.async_advance_window()           # the restored window settles
    assert simulator.get_total("solar_to_house") == pytest.approx(3.0)
# end test_store_from_before_window_times_still_loads


async def test_grid_export_cost_becomes_a_profit(hass, simulator, hass_storage):
    """A store from before 0.3.0 has a grid export *cost*: its total carries
    over to the grid export profit with the opposite sign, and the old entity
    is gone."""
    entry = await simulator.async_setup_hub()
    await simulator.async_stop_hub()
    totals = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["totals"]
    totals.pop(f"{entry.entry_id}_grid_export_profit", None)
    totals[f"{entry.entry_id}_grid_export_cost"] = -1.25

    await simulator.async_start_hub()

    assert simulator.get_total("grid_export_profit") == pytest.approx(1.25)
    assert float(simulator.hub_state("grid_export_profit")) == pytest.approx(1.25)
    assert simulator.entity_id_for("grid_export_cost") is None
# end test_grid_export_cost_becomes_a_profit


async def test_summaries_start_from_the_splits(hass, simulator, hass_storage):
    """A store from before the summaries existed gets them worked out from
    its split totals, so they match the splits from the start."""
    await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[("sensor.sim_charge", "sensor.sim_discharge")]
    )
    await simulator.async_advance_window(import_price=0.30, export_price=0.10)
    simulator.advance_meter(SOLAR, 3.0)
    simulator.advance_meter("sensor.sim_charge", 1.0)
    simulator.advance_meter(GRID_EXPORT, 0.5)
    await simulator.async_advance_window()
    counted = {key: simulator.get_total(key) for key in SUMMARIES}
    assert counted["solar_production"] == pytest.approx(3.0)

    await simulator.async_stop_hub()
    totals = hass_storage[f"{DOMAIN}.{simulator.entry.entry_id}"]["data"]["totals"]
    for key in SUMMARIES:
        del totals[f"{simulator.entry.entry_id}_{key}"]
    # end for
    await simulator.async_start_hub()

    for key in SUMMARIES:
        assert simulator.get_total(key) == pytest.approx(counted[key]), key
    # end for
# end test_summaries_start_from_the_splits


async def test_counted_summaries_are_kept(hass, simulator, hass_storage):
    """Only a store without any summaries gets them worked out: summaries
    the engine has counted are never replaced by the splits' version."""
    entry = await simulator.async_setup_hub(solar=[SOLAR])
    await simulator.async_advance_window()
    await simulator.async_stop_hub()
    totals = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["totals"]
    totals[f"{entry.entry_id}_solar_production"] = 99.0

    await simulator.async_start_hub()

    assert simulator.get_total("solar_production") == pytest.approx(99.0)
# end test_counted_summaries_are_kept


def _as_legacy_store(data: dict, to_source_unit: float) -> None:
    """Turn a stopped hub's store into one from before units were read: its
    snapshots in the sensors' own unit, under the old key."""
    data["snapshots"] = {
        entity_id: kwh * to_source_unit
        for entity_id, kwh in data.pop("snapshots_kwh").items()
    }
# end _as_legacy_store


async def _wh_hub_with_one_settled_window(simulator) -> None:
    """_hub_with_one_settled_window, with the solar meter reporting Wh."""
    simulator.set_unit(SOLAR, "Wh")
    await simulator.async_setup_hub(solar=[SOLAR])
    await simulator.async_advance_window(import_price=0.30, export_price=0.05)
    simulator.advance_meter(SOLAR, 2000.0)
    await simulator.async_advance_window()
# end _wh_hub_with_one_settled_window


@pytest.mark.parametrize("window_kept", [True, False])
async def test_store_from_before_units_carries_on(
    hass, simulator, hass_storage, window_kept
):
    """A store from before units were read holds a Wh meter's snapshot in Wh.
    The first reading converts it, so energy produced while Home Assistant
    was down still counts: no 1000x jump, and not lost as a counter reset.
    That holds when the open window is discarded on start, too: the old
    snapshot is still the baseline of the new first window."""
    await _wh_hub_with_one_settled_window(simulator)
    simulator.advance_meter(SOLAR, 3000.0)   # 2 kWh counted so far
    await simulator.async_stop_hub()
    data = hass_storage[f"{DOMAIN}.{simulator.entry.entry_id}"]["data"]
    _as_legacy_store(data, 1000.0)
    if not window_kept:
        data["window"] = None
    # end if
    simulator.advance_meter(SOLAR, 1000.0)   # produced while down

    await simulator.async_start_hub()
    await simulator.async_advance_window()

    # The settled 2 kWh, 3 kWh that was still in the open window, 1 kWh downtime.
    assert simulator.get_total("solar_production") == pytest.approx(6.0)
# end test_store_from_before_units_carries_on


async def test_unconverted_snapshot_survives_another_restart(
    hass, simulator, hass_storage
):
    """A legacy snapshot whose sensor stays unavailable can't be converted
    yet: it's saved as it was and converted once the sensor is back."""
    await _wh_hub_with_one_settled_window(simulator)
    await simulator.async_stop_hub()
    _as_legacy_store(
        hass_storage[f"{DOMAIN}.{simulator.entry.entry_id}"]["data"], 1000.0
    )

    simulator.set_unavailable(SOLAR)
    await simulator.async_start_hub()
    await simulator.async_advance_window()
    await simulator.async_restart()
    assert SOLAR in hass_storage[f"{DOMAIN}.{simulator.entry.entry_id}"]["data"][
        "snapshots"
    ]

    simulator.advance_meter(SOLAR, 500.0)    # back at its total plus 500 Wh
    await simulator.async_advance_window()
    assert simulator.get_total("solar_production") == pytest.approx(2.5)
# end test_unconverted_snapshot_survives_another_restart


async def test_renamed_device_keeps_its_totals(hass, simulator):
    """Row: device renamed. Its subentry id is unchanged, so nothing changes."""
    await _hub_with_one_settled_window(simulator)

    await simulator.async_stop_hub()
    await simulator.async_update_device("Solar 1", new_name="Roof")
    await simulator.async_start_hub()

    assert simulator.get_total("export_energy", device="Roof") == pytest.approx(2.0)
    simulator.advance_meter(SOLAR, 1.0)
    await simulator.async_advance_window()
    assert simulator.get_total("export_energy", device="Roof") == pytest.approx(3.0)
# end test_renamed_device_keeps_its_totals


async def test_added_device_starts_from_a_fresh_baseline(hass, simulator):
    """Row: device added. Counters start at 0; the first reading is the baseline."""
    await _hub_with_one_settled_window(simulator)

    await simulator.async_stop_hub()
    simulator.set_meter(SOLAR_2, 7.0)                # a meter with history
    await simulator.async_add_device(
        DeviceType.SOLAR, "Solar 2", {CONF_EXPORT_SENSOR: SOLAR_2}
    )
    await simulator.async_start_hub()
    await simulator.async_advance_window()

    assert simulator.get_total("export_energy", device="Solar 2") == pytest.approx(0.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)

    simulator.advance_meter(SOLAR_2, 1.0)
    await simulator.async_advance_window()
    assert simulator.get_total("export_energy", device="Solar 2") == pytest.approx(1.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(3.0)
# end test_added_device_starts_from_a_fresh_baseline


async def test_removed_device_is_dropped_and_hub_totals_kept(hass, simulator):
    """Row: device removed. Its data goes; what it contributed stays."""
    await _hub_with_one_settled_window(simulator, solar=[SOLAR, SOLAR_2])
    simulator.advance_meter(SOLAR_2, 1.0)
    await simulator.async_advance_window()           # solar_to_house now 3

    await simulator.async_stop_hub()
    await simulator.async_remove_device("Solar 2")
    await simulator.async_start_hub()

    assert simulator.get_total("solar_to_house") == pytest.approx(3.0)
    assert simulator.entity_id_for("export_energy", device="Solar 2") is None
    simulator.advance_meter(SOLAR, 1.0)
    simulator.advance_meter(SOLAR_2, 5.0)            # no longer tracked
    await simulator.async_advance_window()
    assert simulator.get_total("solar_to_house") == pytest.approx(4.0)
# end test_removed_device_is_dropped_and_hub_totals_kept


async def test_swapped_sensor_starts_from_a_fresh_baseline(hass, simulator):
    """Row: sensor switched. No spike from the new meter's history; totals kept."""
    await _hub_with_one_settled_window(simulator)

    await simulator.async_stop_hub()
    simulator.set_meter(SOLAR_NEW, 500.0)
    await simulator.async_update_device(
        "Solar 1", sensors={CONF_EXPORT_SENSOR: SOLAR_NEW}
    )
    await simulator.async_start_hub()
    await simulator.async_advance_window()

    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(2.0)
    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)

    simulator.advance_meter(SOLAR_NEW, 2.0)
    await simulator.async_advance_window()
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(4.0)
# end test_swapped_sensor_starts_from_a_fresh_baseline


async def test_tariff_switch_keeps_records_and_energy(hass, simulator):
    """Row: window-shaping settings changed, here dynamic -> fixed.

    The open window is discarded, but its energy isn't lost: it lands in the
    first fixed window, priced at the new tariff. Earlier totals stay.
    """
    await _hub_with_one_settled_window(simulator)
    simulator.advance_meter(SOLAR, 1.0)              # in the discarded window

    await simulator.async_stop_hub()
    await simulator.async_update_hub({
        CONF_TARIFF_MODE: TariffMode.FIXED,
        CONF_IMPORT_TARIFF: 0.20,
        CONF_EXPORT_TARIFF: 0.05,
        CONF_WINDOW_MINUTES: 15,
    })
    await simulator.async_start_hub()
    simulator.advance_meter(SOLAR, 1.0)
    await simulator.async_advance_window()

    assert simulator.get_total("solar_to_house") == pytest.approx(2.0 + 2.0)
    assert simulator.get_total("solar_to_house_savings") == pytest.approx(
        0.60 + 2.0 * 0.20
    )
# end test_tariff_switch_keeps_records_and_energy


async def test_device_added_while_running_keeps_totals(hass, simulator):
    """Adding a device reloads a running hub; nothing is lost in the reload."""
    await _hub_with_one_settled_window(simulator)

    await simulator.async_add_device(
        DeviceType.SOLAR, "Solar 2", {CONF_EXPORT_SENSOR: SOLAR_2}
    )

    assert simulator.get_total("solar_to_house") == pytest.approx(2.0)
    assert simulator.get_total("export_energy", device="Solar 1") == pytest.approx(2.0)
# end test_device_added_while_running_keeps_totals


async def test_deleting_hub_deletes_its_store(hass, simulator, hass_storage):
    entry = await simulator.async_setup_hub(solar=[SOLAR])
    await simulator.async_restart()                  # unloading writes the store
    key = f"{DOMAIN}.{entry.entry_id}"
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage
# end test_deleting_hub_deletes_its_store
