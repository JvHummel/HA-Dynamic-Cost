"""Tests for the sensor platform: which entities exist, and how they look.

Values are covered by test_window.py; these pin the entities themselves --
device classes, units, state classes, unique ids and Home Assistant devices
(plan: entities and aggregation).
"""

from __future__ import annotations

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.dynamic_cost.const import (
    DEVICE_COUNTER_KEYS,
    DOMAIN,
    ENERGY_SPLIT_KEYS,
    FINANCIAL_SPLIT_KEYS,
    DeviceType,
)

SOLAR = "sensor.sim_solar"
CHARGE = "sensor.sim_battery_charge"
DISCHARGE = "sensor.sim_battery_discharge"
GENERAL = "sensor.sim_general"


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


async def test_hub_split_sensors(hass, simulator):
    """All 18 split sensors exist, start at 0, and carry the plan's classes."""
    await simulator.async_setup_hub(currency="USD")

    for key in ENERGY_SPLIT_KEYS:
        attrs = _attributes(hass, simulator.entity_id_for(key), key)
        assert attrs == ENERGY | {"value": 0.0}, key
    # end for
    for key in FINANCIAL_SPLIT_KEYS:
        attrs = _attributes(hass, simulator.entity_id_for(key), key)
        assert attrs == _monetary("USD") | {"value": 0.0}, key
    # end for
# end test_hub_split_sensors


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
