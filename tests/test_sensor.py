"""Tests for the sensor platform: which entities exist, and how they look.

Values are covered by test_window.py; these pin the entities themselves --
which exist, device classes, units, state classes, unique ids and Home
Assistant devices -- plus the dynamic-tariff diagnostics (plan: entities and
aggregation).
"""

from __future__ import annotations

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.dynamic_cost.const import (
    CONF_EXPORT_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_IMPORT_TARIFF,
    CONF_TARIFF_MODE,
    CONF_WINDOW_MINUTES,
    DEVICE_COUNTER_KEYS,
    DOMAIN,
    ENERGY_SPLIT_KEYS,
    EXPORT_PRICE_GRACE_SECONDS,
    FINANCIAL_SPLIT_KEYS,
    DeviceType,
    TariffMode,
)

from .simulator import CURTAILMENT as SIM_CURTAILMENT
from .simulator import EXPORT_PRICE as SIM_EXPORT_PRICE

SOLAR = "sensor.sim_solar"
CHARGE = "sensor.sim_battery_charge"
DISCHARGE = "sensor.sim_battery_discharge"
GENERAL = "sensor.sim_general"
PAST_GRACE = EXPORT_PRICE_GRACE_SECONDS + 1

# Which splits a hub gets, written out from the plan rather than taken from
# SPLIT_REQUIRES, so a wrong constant fails here too.
ALWAYS = {
    "house_energy_usage", "grid_to_house", "grid_import_cost",
    "grid_export_cost", "house_energy_usage_cost", "grid_to_house_cost",
}
WITH_SOLAR = {
    "solar_to_house", "solar_to_grid", "solar_to_house_savings",
    "solar_to_grid_profit",
}
WITH_BATTERY = {
    "grid_to_battery", "battery_to_house", "battery_to_grid",
    "grid_to_battery_cost", "battery_to_house_savings", "battery_to_grid_profit",
}
WITH_BOTH = {"solar_to_battery", "solar_to_battery_cost"}
DIAGNOSTICS = {
    "window_status", "window_import_price", "window_export_price",
    "export_price_lock", "window_opened", "last_window_closed",
}


def _attributes(hass, entity_id: str | None, what: str) -> dict:
    assert entity_id is not None, f"no entity for {what}"
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} ({what}) has no state"
    return {
        "device_class": state.attributes.get("device_class"),
        "unit": state.attributes.get("unit_of_measurement"),
        "state_class": state.attributes.get("state_class"),
        "value": float(state.state),
    }
# end _attributes


ENERGY = {"device_class": "energy", "unit": "kWh", "state_class": "total"}


def _monetary(currency: str) -> dict:
    return {"device_class": "monetary", "unit": currency, "state_class": "total"}
# end _monetary


def _hub_keys(hass, entry) -> set[str]:
    """Keys of every entity registered to the hub itself (not to a device)."""
    prefix = f"{entry.entry_id}_"
    return {
        reg.unique_id.removeprefix(prefix)
        for reg in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if reg.config_subentry_id is None
    }
# end _hub_keys


async def test_hub_split_sensors(hass, simulator):
    """With solar and a battery, all 18 splits exist, start at 0, and carry
    the plan's classes."""
    await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[(CHARGE, DISCHARGE)], currency="USD"
    )

    for key in ENERGY_SPLIT_KEYS:
        attrs = _attributes(hass, simulator.entity_id_for(key), key)
        assert attrs == ENERGY | {"value": 0.0}, key
    # end for
    for key in FINANCIAL_SPLIT_KEYS:
        attrs = _attributes(hass, simulator.entity_id_for(key), key)
        assert attrs == _monetary("USD") | {"value": 0.0}, key
    # end for
# end test_hub_split_sensors


@pytest.mark.parametrize(
    ("solar", "battery", "expected"),
    [
        (False, False, ALWAYS),
        (True, False, ALWAYS | WITH_SOLAR),
        (False, True, ALWAYS | WITH_BATTERY),
        (True, True, ALWAYS | WITH_SOLAR | WITH_BATTERY | WITH_BOTH),
    ],
)
async def test_splits_need_their_devices(hass, simulator, solar, battery, expected):
    """A split exists only once the hub has the devices it depends on.

    A general device feeds no splits, so it never adds any.
    """
    entry = await simulator.async_setup_hub(
        solar=[SOLAR] if solar else None,
        batteries=[(CHARGE, DISCHARGE)] if battery else None,
        general=[GENERAL],
        tariff_mode=TariffMode.FIXED,
    )
    assert _hub_keys(hass, entry) == expected
# end test_splits_need_their_devices


async def test_splits_follow_devices_being_added_and_removed(hass, simulator):
    """Adding solar creates its splits; removing it removes them again, and
    adding it back carries on from the kept total."""
    entry = await simulator.async_setup_hub()
    await simulator.async_advance_window(import_price=0.30, export_price=0.05)

    await simulator.async_add_device(
        DeviceType.SOLAR, "Roof", {CONF_EXPORT_SENSOR: SOLAR}
    )
    assert _hub_keys(hass, entry) >= WITH_SOLAR
    simulator.set_meter(SOLAR, 0.0)
    await simulator.async_advance_window()
    simulator.advance_meter(SOLAR, 2.0)
    await simulator.async_advance_window()
    assert float(simulator.hub_state("solar_to_house")) == pytest.approx(2.0)

    await simulator.async_remove_device("Roof")
    assert not WITH_SOLAR & _hub_keys(hass, entry)
    assert simulator.entity_id_for("solar_to_house") is None

    await simulator.async_add_device(
        DeviceType.SOLAR, "Roof", {CONF_EXPORT_SENSOR: SOLAR}
    )
    assert float(simulator.hub_state("solar_to_house")) == pytest.approx(2.0)
# end test_splits_follow_devices_being_added_and_removed


@pytest.mark.parametrize(
    ("tariff_mode", "curtailment", "expected"),
    [
        (TariffMode.FIXED, False, set()),
        (TariffMode.FIXED, True, set()),
        (TariffMode.DYNAMIC, False, DIAGNOSTICS),
        (
            TariffMode.DYNAMIC,
            True,
            DIAGNOSTICS | {"window_curtailed", "curtailment_lock"},
        ),
    ],
)
async def test_diagnostics_only_for_dynamic_hubs(
    hass, simulator, tariff_mode, curtailment, expected
):
    """Dynamic hubs get the diagnostics; the curtailment one needs a source."""
    entry = await simulator.async_setup_hub(
        tariff_mode=tariff_mode, curtailment=curtailment
    )
    assert _hub_keys(hass, entry) - ALWAYS == expected

    registry = er.async_get(hass)
    for key in expected:
        registered = registry.async_get(simulator.entity_id_for(key))
        assert registered.entity_category == "diagnostic", key
    # end for
# end test_diagnostics_only_for_dynamic_hubs


async def test_switching_to_fixed_removes_diagnostics(hass, simulator):
    """Reconfiguring to a fixed contract removes the diagnostics again."""
    entry = await simulator.async_setup_hub()
    assert _hub_keys(hass, entry) >= DIAGNOSTICS

    await simulator.async_update_hub(
        {
            CONF_TARIFF_MODE: TariffMode.FIXED,
            CONF_IMPORT_TARIFF: 0.25,
            CONF_EXPORT_TARIFF: 0.08,
            CONF_WINDOW_MINUTES: 15,
        }
    )
    assert _hub_keys(hass, entry) == ALWAYS
# end test_switching_to_fixed_removes_diagnostics


async def test_diagnostics_show_the_window(hass, simulator):
    """Prices, curtailment and times follow the window, including a lock
    that happens without any window closing."""
    await simulator.async_setup_hub(curtailment=True)
    assert simulator.hub_state("window_status") == "open"
    assert simulator.hub_state("last_window_closed") == "unknown"

    await simulator.async_advance_window(import_price=0.30, export_price=0.05)
    assert simulator.hub_state("window_import_price") == "0.3"
    assert simulator.hub_state("window_export_price") == "0.05"
    assert simulator.hub_state("window_curtailed") == "not_curtailed"
    assert simulator.hub_state("window_opened") not in ("unknown", None)
    closed = simulator.hub_state("last_window_closed")
    assert closed not in ("unknown", None)

    # A new window: its export price shows straight away, tentatively,
    # and locks when the grace period ends.
    simulator.set_price(import_price=0.40)
    await hass.async_block_till_done()
    assert simulator.hub_state("window_import_price") == "0.4"
    assert simulator.hub_state("window_export_price") == "0.05"
    assert simulator.hub_state("export_price_lock") == "tentative"
    await simulator.async_wait(PAST_GRACE)
    assert simulator.hub_state("window_export_price") == "0.05"
    assert simulator.hub_state("export_price_lock") == "locked"

    simulator.set_curtailed(True)
    simulator.set_price(import_price=0.50)
    await hass.async_block_till_done()
    await simulator.async_wait(PAST_GRACE)
    assert simulator.hub_state("window_curtailed") == "curtailed"
# end test_diagnostics_show_the_window


async def test_lock_sensors_go_from_waiting_to_tentative_to_locked(hass, simulator):
    """Without readings a window waits; a reading within the grace period
    shows as tentative, and may still change; the grace period locks it."""
    await simulator.async_setup_hub(curtailment=True)
    await simulator.async_wait(PAST_GRACE)
    simulator.set_unavailable(SIM_EXPORT_PRICE)
    simulator.set_unavailable(SIM_CURTAILMENT)
    simulator.set_price(import_price=0.30)           # opens a window, no readings
    await hass.async_block_till_done()
    assert simulator.hub_state("export_price_lock") == "waiting"
    assert simulator.hub_state("window_export_price") == "unknown"
    assert simulator.hub_state("curtailment_lock") == "waiting"
    assert simulator.hub_state("window_curtailed") == "unknown"

    simulator.set_price(export_price=0.06)           # within the grace period
    simulator.set_curtailed(True)
    await hass.async_block_till_done()
    assert simulator.hub_state("export_price_lock") == "tentative"
    assert simulator.hub_state("window_export_price") == "0.06"
    assert simulator.hub_state("curtailment_lock") == "tentative"
    assert simulator.hub_state("window_curtailed") == "curtailed"

    simulator.set_price(export_price=0.07)           # the tentative price moves
    await hass.async_block_till_done()
    assert simulator.hub_state("window_export_price") == "0.07"

    await simulator.async_wait(PAST_GRACE)
    assert simulator.hub_state("export_price_lock") == "locked"
    assert simulator.hub_state("window_export_price") == "0.07"
    assert simulator.hub_state("curtailment_lock") == "locked"
# end test_lock_sensors_go_from_waiting_to_tentative_to_locked


async def test_status_shows_a_window_held_open(hass, simulator):
    """A boundary without an export price holds the window: "held_open"."""
    await simulator.async_setup_hub()
    await simulator.async_wait(PAST_GRACE)
    simulator.set_unavailable(SIM_EXPORT_PRICE)
    simulator.set_price(import_price=0.30)       # closes; next has no export
    await hass.async_block_till_done()
    await simulator.async_wait(PAST_GRACE)
    simulator.set_price(import_price=0.40)       # boundary, but nothing locked
    await hass.async_block_till_done()
    assert simulator.hub_state("window_status") == "held_open"

    simulator.set_price(export_price=0.05)       # the late lock closes it
    await hass.async_block_till_done()
    assert simulator.hub_state("window_status") == "open"
# end test_status_shows_a_window_held_open


async def test_device_counters_match_device_type(hass, simulator):
    """Energy per sensor for every device; value only for general devices.

    The expectations are written out from the plan rather than taken from
    DEVICE_COUNTER_KEYS, so a wrong constant fails here too.
    """
    entry = await simulator.async_setup_hub(
        solar=[SOLAR], batteries=[(CHARGE, DISCHARGE)], general=[GENERAL]
    )
    expected = {
        "Solar 1": {"export_energy"},
        "Battery 1": {"import_energy", "export_energy"},
        "General 1": {"import_energy", "import_value"},
    }
    registry = er.async_get(hass)

    for subentry in entry.subentries.values():
        prefix = f"{subentry.subentry_id}_"
        keys = {
            reg.unique_id.removeprefix(prefix)
            for reg in er.async_entries_for_config_entry(registry, entry.entry_id)
            if reg.config_subentry_id == subentry.subentry_id
        }
        assert keys == expected[subentry.title], subentry.title

        for key in keys:
            what = f"{subentry.title} {key}"
            entity_id = simulator.entity_id_for(key, subentry.title)
            attrs = _attributes(hass, entity_id, what)
            kind = _monetary("EUR") if key.endswith("_value") else ENERGY
            assert attrs == kind | {"value": 0.0}, what
        # end for
    # end for
# end test_device_counters_match_device_type


async def test_each_device_is_its_own_ha_device(hass, simulator):
    """The hub holds the splits; every device holds its own counters."""
    entry = await simulator.async_setup_hub(solar=[SOLAR], general=[GENERAL])
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert hub is not None and hub.name == entry.title
    split = entity_registry.async_get(simulator.entity_id_for("solar_to_house"))
    assert split.device_id == hub.id

    for subentry in entry.subentries.values():
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, subentry.subentry_id), entry.entry_id
        )
        assert device is not None and device.name == subentry.title
        key = DEVICE_COUNTER_KEYS[DeviceType(subentry.subentry_type)][0]
        counter = entity_registry.async_get(
            simulator.entity_id_for(key, device=subentry.title)
        )
        assert counter.device_id == device.id
        assert counter.config_subentry_id == subentry.subentry_id
    # end for
# end test_each_device_is_its_own_ha_device


@pytest.mark.parametrize("key", ["solar_to_house", "solar_to_house_savings"])
async def test_split_sensor_updates_when_a_window_settles(hass, simulator, key):
    """An entity's state follows the engine's total as windows settle."""
    await simulator.async_setup_hub(solar=[SOLAR])
    await simulator.async_advance_window(import_price=0.30, export_price=0.05)

    simulator.advance_meter(SOLAR, 2.0)
    await simulator.async_advance_window()

    expected = {"solar_to_house": 2.0, "solar_to_house_savings": 0.60}[key]
    assert float(hass.states.get(simulator.entity_id_for(key)).state) == pytest.approx(
        expected
    )
# end test_split_sensor_updates_when_a_window_settles
