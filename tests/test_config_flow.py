"""Tests for the hub setup wizard, reconfiguration and the device flows.

Most of these drive the flows without loading the integration (the no_setup
fixture), so they test the forms and the saved data only. The one reload test
uses the simulator, because it needs a running hub.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_cost.const import (
    CONF_CURRENCY,
    CONF_CURTAILMENT_SOURCE,
    CONF_DEVICE_NAME,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_EXPORT_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_STALE_WINDOW_MINUTES,
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
    DEFAULT_STALE_WINDOW_MINUTES,
    DOMAIN,
    DeviceType,
)

from .simulator import EXPORT_PRICE as SIM_EXPORT_PRICE
from .simulator import GRID_EXPORT as SIM_GRID_EXPORT
from .simulator import GRID_IMPORT as SIM_GRID_IMPORT
from .simulator import IMPORT_PRICE as SIM_IMPORT_PRICE

GRID_IN = "sensor.cf_grid_import"
GRID_OUT = "sensor.cf_grid_export"
IMPORT_PRICE = "sensor.cf_import_price"
EXPORT_PRICE = "sensor.cf_export_price"
METER_A = "sensor.cf_meter_a"
METER_B = "sensor.cf_meter_b"
SWITCH = "input_boolean.cf_curtailment"
MISSING = "sensor.cf_does_not_exist"
DOWN = "sensor.cf_unavailable"
TEXT = "sensor.cf_not_a_number"

GRID = {CONF_GRID_IMPORT_SENSOR: GRID_IN, CONF_GRID_EXPORT_SENSOR: GRID_OUT}
PRICES = {
    CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE,
    CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE,
}
DYNAMIC_HUB = {
    **GRID,
    CONF_TARIFF_MODE: "dynamic",
    **PRICES,
    CONF_TREAT_SOLAR_AS_FREE: False,
    CONF_CURRENCY: "EUR",
}


@pytest.fixture(autouse=True)
def world(hass):
    """Entities for the pickers to choose from, and a non-default currency."""
    energy = {"device_class": "energy", "unit_of_measurement": "kWh"}
    for entity_id in (GRID_IN, GRID_OUT, METER_A, METER_B):
        hass.states.async_set(entity_id, "100.0", energy)
    # end for
    hass.states.async_set(IMPORT_PRICE, "0.25")
    hass.states.async_set(EXPORT_PRICE, "0.08")
    hass.states.async_set(SWITCH, "off")
    hass.states.async_set(DOWN, "unavailable", energy)
    hass.states.async_set(TEXT, "on", energy)
    hass.config.currency = "USD"
# end world


@pytest.fixture
def no_setup():
    """Finish flows without loading the integration itself."""
    with patch(
        "custom_components.dynamic_cost.async_setup_entry", return_value=True
    ):
        yield
    # end with
# end no_setup


def _fields(result) -> set[str]:
    return {str(key) for key in result["data_schema"].schema}
# end _fields


def _prefill(result, field: str):
    """A form field's default or suggested value, whichever the flow used."""
    for key in result["data_schema"].schema:
        if str(key) == field:
            if key.default is not vol.UNDEFINED:
                return key.default()
            # end if
            return (key.description or {}).get("suggested_value")
        # end if
    # end for
    raise AssertionError(f"{field} is not in the {result['step_id']} form")
# end _prefill


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
# end _start


async def _submit(hass, result, user_input):
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
# end _submit


def _assert_step(result, step_id: str) -> None:
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == step_id, result
    assert not result.get("errors"), result["errors"]
# end _assert_step


# -- hub setup ---------------------------------------------------------------

async def test_dynamic_setup_creates_hub(hass, no_setup):
    """Steps 1, 2, 2b, 3+4 and 5 in order, and exactly the data they collected."""
    result = await _start(hass)
    _assert_step(result, "user")
    result = await _submit(hass, result, GRID)
    _assert_step(result, "tariff")
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    _assert_step(result, "dynamic")
    result = await _submit(hass, result, PRICES)
    _assert_step(result, "options")
    result = await _submit(
        hass, result, {CONF_TREAT_SOLAR_AS_FREE: False, CONF_CURRENCY: "USD"}
    )
    _assert_step(result, "curtailment")
    result = await _submit(hass, result, {CONF_CURTAILMENT_SOURCE: SWITCH})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        **GRID,
        CONF_TARIFF_MODE: "dynamic",
        **PRICES,
        CONF_STALE_WINDOW_MINUTES: DEFAULT_STALE_WINDOW_MINUTES,
        CONF_TREAT_SOLAR_AS_FREE: False,
        CONF_CURRENCY: "USD",
        CONF_CURTAILMENT_SOURCE: SWITCH,
    }
# end test_dynamic_setup_creates_hub


async def test_stale_window_timeout_is_saved_and_prefilled(hass, no_setup):
    """Step 2b's timeout defaults to 120 minutes, and a chosen one is saved.

    Reconfiguring a hub from before the setting existed offers the default.
    """
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    assert _prefill(result, CONF_STALE_WINDOW_MINUTES) == 120
    result = await _submit(hass, result, {**PRICES, CONF_STALE_WINDOW_MINUTES: 45})
    result = await _submit(
        hass, result, {CONF_TREAT_SOLAR_AS_FREE: True, CONF_CURRENCY: "USD"}
    )
    assert result["data"][CONF_STALE_WINDOW_MINUTES] == 45

    entry = MockConfigEntry(domain=DOMAIN, title="Hub", data=DYNAMIC_HUB)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await _submit(hass, result, GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    _assert_step(result, "dynamic")
    assert _prefill(result, CONF_STALE_WINDOW_MINUTES) == 120
# end test_stale_window_timeout_is_saved_and_prefilled


async def test_fixed_setup_with_free_solar_skips_curtailment(hass, no_setup):
    """With the checkbox on, step 5 is skipped and no source is saved."""
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "fixed"})
    _assert_step(result, "fixed")
    result = await _submit(
        hass,
        result,
        {CONF_IMPORT_TARIFF: 0.25, CONF_EXPORT_TARIFF: 0.08, CONF_WINDOW_MINUTES: 15},
    )
    _assert_step(result, "options")
    result = await _submit(
        hass, result, {CONF_TREAT_SOLAR_AS_FREE: True, CONF_CURRENCY: "USD"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        **GRID,
        CONF_TARIFF_MODE: "fixed",
        CONF_IMPORT_TARIFF: 0.25,
        CONF_EXPORT_TARIFF: 0.08,
        CONF_WINDOW_MINUTES: 15,
        CONF_TREAT_SOLAR_AS_FREE: True,
        CONF_CURRENCY: "USD",
    }
# end test_fixed_setup_with_free_solar_skips_curtailment


async def test_empty_curtailment_source_is_not_saved(hass, no_setup):
    """Leaving step 5 empty means "never curtailed": the key is absent."""
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    result = await _submit(hass, result, PRICES)
    result = await _submit(
        hass, result, {CONF_TREAT_SOLAR_AS_FREE: False, CONF_CURRENCY: "USD"}
    )
    result = await _submit(hass, result, {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_CURTAILMENT_SOURCE not in result["data"]
# end test_empty_curtailment_source_is_not_saved


async def test_currency_defaults_to_home_assistant_currency(hass, no_setup):
    """The currency field starts at the currency in Home Assistant's settings."""
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    result = await _submit(hass, result, PRICES)

    _assert_step(result, "options")
    assert _prefill(result, CONF_CURRENCY) == "USD"
# end test_currency_defaults_to_home_assistant_currency


# -- validation --------------------------------------------------------------

@pytest.mark.parametrize(
    ("sensor", "error"), [(MISSING, "unknown_entity"), (TEXT, "not_numeric")]
)
async def test_bad_grid_sensor_blocks_step(hass, no_setup, sensor, error):
    """Hard errors keep the user on the form, attached to the offending field."""
    result = await _submit(
        hass, await _start(hass), {**GRID, CONF_GRID_IMPORT_SENSOR: sensor}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_GRID_IMPORT_SENSOR: error}
# end test_bad_grid_sensor_blocks_step


async def test_unavailable_grid_sensor_asks_for_confirmation(hass, no_setup):
    """A sensor that reads unavailable is a soft warning: confirm, then go on."""
    result = await _submit(
        hass, await _start(hass), {**GRID, CONF_GRID_IMPORT_SENSOR: DOWN}
    )
    _assert_step(result, "confirm_unavailable")
    assert DOWN in result["description_placeholders"]["sensors"]

    result = await _submit(hass, result, {})
    _assert_step(result, "tariff")
# end test_unavailable_grid_sensor_asks_for_confirmation


@pytest.mark.parametrize(
    ("sensor", "error"), [(MISSING, "unknown_entity"), (TEXT, "not_numeric")]
)
async def test_bad_price_sensor_blocks_step(hass, no_setup, sensor, error):
    """The price sensors are validated the same way."""
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    result = await _submit(hass, result, {**PRICES, CONF_EXPORT_PRICE_SENSOR: sensor})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dynamic"
    assert result["errors"] == {CONF_EXPORT_PRICE_SENSOR: error}
# end test_bad_price_sensor_blocks_step


async def test_unavailable_price_sensor_asks_for_confirmation(hass, no_setup):
    """After confirming an unavailable price sensor, the flow continues at step 3."""
    result = await _submit(hass, await _start(hass), GRID)
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
    result = await _submit(hass, result, {**PRICES, CONF_IMPORT_PRICE_SENSOR: DOWN})
    _assert_step(result, "confirm_unavailable")

    result = await _submit(hass, result, {})
    _assert_step(result, "options")
# end test_unavailable_price_sensor_asks_for_confirmation


# -- reconfiguring a hub -----------------------------------------------------

async def test_reconfigure_switches_tariff_type_and_keeps_currency(hass, no_setup):
    """Every setting can change -- including the tariff type -- except currency."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hub",
        data={**DYNAMIC_HUB, CONF_STALE_WINDOW_MINUTES: 60},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    _assert_step(result, "reconfigure")
    assert _prefill(result, CONF_GRID_IMPORT_SENSOR) == GRID_IN
    result = await _submit(hass, result, GRID)
    _assert_step(result, "tariff")
    assert _prefill(result, CONF_TARIFF_MODE) == "dynamic"
    result = await _submit(hass, result, {CONF_TARIFF_MODE: "fixed"})
    _assert_step(result, "fixed")
    result = await _submit(
        hass,
        result,
        {CONF_IMPORT_TARIFF: 0.22, CONF_EXPORT_TARIFF: 0.07, CONF_WINDOW_MINUTES: 30},
    )
    _assert_step(result, "options")
    assert CONF_CURRENCY not in _fields(result)
    result = await _submit(hass, result, {CONF_TREAT_SOLAR_AS_FREE: False})
    _assert_step(result, "curtailment")
    result = await _submit(hass, result, {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TARIFF_MODE] == "fixed"
    assert entry.data[CONF_IMPORT_TARIFF] == 0.22
    assert entry.data[CONF_EXPORT_TARIFF] == 0.07
    assert entry.data[CONF_WINDOW_MINUTES] == 30
    assert entry.data[CONF_CURRENCY] == "EUR"
    # The dynamic-only settings are gone, not left behind.
    for key in (
        CONF_IMPORT_PRICE_SENSOR,
        CONF_EXPORT_PRICE_SENSOR,
        CONF_STALE_WINDOW_MINUTES,
    ):
        assert key not in entry.data, key
    # end for
# end test_reconfigure_switches_tariff_type_and_keeps_currency


async def test_reconfigure_reloads_hub_once(hass, simulator):
    """A reconfigure reloads the running hub exactly once, not twice."""
    entry = await simulator.async_setup_hub()
    config_entries = hass.config_entries

    with patch.object(
        config_entries, "async_reload", wraps=config_entries.async_reload
    ) as reload:
        result = await entry.start_reconfigure_flow(hass)
        result = await _submit(
            hass,
            result,
            {
                CONF_GRID_IMPORT_SENSOR: SIM_GRID_IMPORT,
                CONF_GRID_EXPORT_SENSOR: SIM_GRID_EXPORT,
            },
        )
        result = await _submit(hass, result, {CONF_TARIFF_MODE: "dynamic"})
        result = await _submit(
            hass,
            result,
            {
                CONF_IMPORT_PRICE_SENSOR: SIM_IMPORT_PRICE,
                CONF_EXPORT_PRICE_SENSOR: SIM_EXPORT_PRICE,
            },
        )
        result = await _submit(hass, result, {CONF_TREAT_SOLAR_AS_FREE: True})
        await hass.async_block_till_done()
    # end with

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_TREAT_SOLAR_AS_FREE] is True
    assert reload.call_count == 1
# end test_reconfigure_reloads_hub_once


# -- devices -----------------------------------------------------------------

DEVICE_SENSORS = {
    DeviceType.GENERAL: {CONF_IMPORT_SENSOR: METER_A},
    DeviceType.SOLAR: {CONF_EXPORT_SENSOR: METER_A},
    DeviceType.BATTERY: {CONF_IMPORT_SENSOR: METER_A, CONF_EXPORT_SENSOR: METER_B},
}


@pytest.fixture
def hub(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Hub", data=DYNAMIC_HUB)
    entry.add_to_hass(hass)
    return entry
# end hub


async def _start_device(hass, hub, device_type):
    return await hass.config_entries.subentries.async_init(
        (hub.entry_id, device_type), context={"source": SOURCE_USER}
    )
# end _start_device


async def _submit_device(hass, result, user_input):
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )
# end _submit_device


@pytest.mark.parametrize("device_type", list(DeviceType))
async def test_add_device(hass, no_setup, hub, device_type):
    """Each device type asks for a name and its own sensor(s), nothing more."""
    sensors = DEVICE_SENSORS[device_type]
    result = await _start_device(hass, hub, device_type)
    _assert_step(result, "user")
    assert _fields(result) == {CONF_DEVICE_NAME, *sensors}

    result = await _submit_device(hass, result, {CONF_DEVICE_NAME: "Thing", **sensors})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    [subentry] = hub.subentries.values()
    assert subentry.title == "Thing"
    assert subentry.subentry_type == device_type
    assert dict(subentry.data) == {CONF_DEVICE_NAME: "Thing", **sensors}
# end test_add_device


async def test_add_device_validates_its_sensor(hass, no_setup, hub):
    """Device sensors get the same hard errors and soft warning as the hub's."""
    result = await _start_device(hass, hub, DeviceType.SOLAR)
    result = await _submit_device(
        hass, result, {CONF_DEVICE_NAME: "Roof", CONF_EXPORT_SENSOR: MISSING}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_EXPORT_SENSOR: "unknown_entity"}

    result = await _submit_device(
        hass, result, {CONF_DEVICE_NAME: "Roof", CONF_EXPORT_SENSOR: DOWN}
    )
    _assert_step(result, "confirm_unavailable")
    result = await _submit_device(hass, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
# end test_add_device_validates_its_sensor


async def test_reconfigure_device_changes_name_and_sensor(hass, no_setup):
    """A device's name and sensors can change after adding it; its id can't."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hub",
        data=DYNAMIC_HUB,
        subentries_data=[{
            "subentry_type": DeviceType.SOLAR,
            "title": "Roof",
            "data": {CONF_DEVICE_NAME: "Roof", CONF_EXPORT_SENSOR: METER_A},
            "unique_id": None,
        }],
    )
    entry.add_to_hass(hass)
    [subentry_id] = entry.subentries

    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    _assert_step(result, "reconfigure")
    assert _prefill(result, CONF_EXPORT_SENSOR) == METER_A
    result = await _submit_device(
        hass, result, {CONF_DEVICE_NAME: "Roof east", CONF_EXPORT_SENSOR: METER_B}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    subentry = entry.subentries[subentry_id]
    assert subentry.title == "Roof east"
    assert dict(subentry.data) == {
        CONF_DEVICE_NAME: "Roof east",
        CONF_EXPORT_SENSOR: METER_B,
    }
# end test_reconfigure_device_changes_name_and_sensor
