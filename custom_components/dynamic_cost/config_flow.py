"""Config flow for Dynamic Cost.

Structure:
  DynamicCostConfigFlow  -- the hub setup wizard (steps 1-5 of the plan), and
                            reconfiguring a hub, which walks the same steps
  GeneralSubentryFlow    -- "add a general device"
  SolarSubentryFlow      -- "add solar panels"
  BatterySubentryFlow    -- "add a battery"
"""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from . import units
from .const import (
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
    TariffMode,
)

# An energy-sensor picker. Restricting by device_class here is layer one of
# the config validation in the plan: it makes most wrong-sensor mistakes
# impossible rather than something to catch afterwards.
ENERGY_SENSOR_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy")
)

PRICE_SENSOR_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor")
)

# Anything with an on/off state the user's automations can drive.
CURTAILMENT_SOURCE_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain=["input_boolean", "switch", "binary_sensor"]
    )
)

# Tariffs may be negative; "any" step allows prices like 0.23456.
TARIFF_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX, step="any")
)

WINDOW_MINUTES_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        mode=selector.NumberSelectorMode.BOX,
        min=1,
        step=1,
        unit_of_measurement="min",
    )
)

STALE_WINDOW_MINUTES_SELECTOR = WINDOW_MINUTES_SELECTOR

# The hub fields that hold a sensor, so _check_sensors reads only those and
# not the numbers that share a step with them.
HUB_SENSOR_FIELDS = (
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
)

# The fields, hub and device, that hold an energy sensor: _check_sensors
# checks that those report in an energy unit.
ENERGY_SENSOR_FIELDS = (
    CONF_GRID_IMPORT_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_IMPORT_SENSOR,
    CONF_EXPORT_SENSOR,
)

TARIFF_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[mode.value for mode in TariffMode],
        translation_key="tariff_mode",
    )
)

DEFAULT_TITLE = "Dynamic Cost"


def _check_sensors(
    hass: HomeAssistant, sensors: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Layer two of the plan's config-flow validation, for any flow step.

    sensors maps each form field to the entity id chosen in it. Each entity
    is read once. Returns (errors, unavailable):
      - errors: {field: "unknown_entity"} for an entity that doesn't exist,
        {field: "not_numeric"} for one whose state isn't a number,
        {field: "not_energy_unit"} for an energy sensor whose unit can't be
        converted to kWh (one with no unit counts as kWh). Each blocks the
        step.
      - unavailable: entity ids that exist but read unavailable or unknown.
        Not an error -- the flow continues via a confirmation step.
    """
    errors: dict[str, str] = {}
    unavailable: list[str] = []

    for field, entity_id in sensors.items():
        state = hass.states.get(entity_id)

        if state is None:
            errors[field] = "unknown_entity"
        elif state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            unavailable.append(entity_id)
        else:
            try:
                is_number = math.isfinite(float(state.state))
            except ValueError:
                is_number = False
            # end try
            if not is_number:
                errors[field] = "not_numeric"
            elif field in ENERGY_SENSOR_FIELDS and not units.is_energy_unit(
                state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            ):
                errors[field] = "not_energy_unit"
            # end if
        # end if
    # end for

    return errors, unavailable
# end _check_sensors


class DynamicCostConfigFlow(ConfigFlow, domain=DOMAIN):
    """Hub setup wizard, and hub reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold answers across the multi-step flow.

        self._data is what gets saved as the hub's configuration. The other
        two attributes are flow state only: which unavailable sensors to show
        in the confirmation step, and which step comes after it.
        """
        self._data: dict[str, Any] = {}
        self._unavailable: list[str] = []
        self._after_confirm = ""
    # end __init__

    @property
    def _reconfiguring(self) -> bool:
        return self.source == SOURCE_RECONFIGURE
    # end _reconfiguring

    async def _async_sensor_step(
        self,
        step_id: str,
        schema: vol.Schema,
        user_input: dict[str, Any] | None,
        next_step: str,
    ) -> ConfigFlowResult:
        """A form of sensor pickers, validated with _check_sensors().

        Fields that aren't in HUB_SENSOR_FIELDS are saved unchecked. Errors
        show the form again. Sensors that read unavailable go to the
        confirmation step first, because a briefly-unavailable sensor is not
        necessarily misconfigured. Then continue to next_step.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, unavailable = _check_sensors(
                self.hass,
                {
                    field: entity_id
                    for field, entity_id in user_input.items()
                    if field in HUB_SENSOR_FIELDS
                },
            )
            if not errors:
                self._data.update(user_input)
                if unavailable:
                    self._unavailable = unavailable
                    self._after_confirm = next_step
                    return await self.async_step_confirm_unavailable()
                # end if
                return await getattr(self, f"async_step_{next_step}")()
            # end if
        # end if

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )
    # end _async_sensor_step

    @staticmethod
    def _grid_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_GRID_IMPORT_SENSOR): ENERGY_SENSOR_SELECTOR,
                vol.Required(CONF_GRID_EXPORT_SENSOR): ENERGY_SENSOR_SELECTOR,
            }
        )
    # end _grid_schema

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: the two grid sensors."""
        return await self._async_sensor_step(
            "user", self._grid_schema(), user_input, "tariff"
        )
    # end async_step_user

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the hub's settings after setup (plan: config flow).

        Walks the same steps as setup, pre-filled with the current values.
        Every setting may change, INCLUDING the tariff type: a user who
        switches contracts keeps their records, and the engine discards the
        in-progress window on reload. The currency stays as it is.
        """
        if not self._data:
            self._data = dict(self._get_reconfigure_entry().data)
        # end if
        return await self._async_sensor_step(
            "reconfigure", self._grid_schema(), user_input, "tariff"
        )
    # end async_step_reconfigure

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: fixed or dynamic tariffs.

        The other mode's settings are dropped, so a reconfigured hub never
        keeps stale ones around.
        """
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_TARIFF_MODE] == TariffMode.FIXED:
                for key in (
                    CONF_IMPORT_PRICE_SENSOR,
                    CONF_EXPORT_PRICE_SENSOR,
                    CONF_STALE_WINDOW_MINUTES,
                ):
                    self._data.pop(key, None)
                # end for
                return await self.async_step_fixed()
            # end if
            for key in (CONF_IMPORT_TARIFF, CONF_EXPORT_TARIFF, CONF_WINDOW_MINUTES):
                self._data.pop(key, None)
            # end for
            return await self.async_step_dynamic()
        # end if

        schema = vol.Schema({vol.Required(CONF_TARIFF_MODE): TARIFF_MODE_SELECTOR})
        return self.async_show_form(
            step_id="tariff",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
        )
    # end async_step_tariff

    async def async_step_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: import tariff, export tariff, window length in minutes."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_options()
        # end if

        schema = vol.Schema(
            {
                vol.Required(CONF_IMPORT_TARIFF): TARIFF_SELECTOR,
                vol.Required(CONF_EXPORT_TARIFF): TARIFF_SELECTOR,
                vol.Required(CONF_WINDOW_MINUTES, default=15): WINDOW_MINUTES_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="fixed",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
        )
    # end async_step_fixed

    async def async_step_dynamic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: import and export price sensors, and the stale-window warning.

        The warning's timeout defaults to DEFAULT_STALE_WINDOW_MINUTES, also
        when reconfiguring a hub set up before the setting existed.
        """
        schema = vol.Schema(
            {
                vol.Required(CONF_IMPORT_PRICE_SENSOR): PRICE_SENSOR_SELECTOR,
                vol.Required(CONF_EXPORT_PRICE_SENSOR): PRICE_SENSOR_SELECTOR,
                vol.Required(
                    CONF_STALE_WINDOW_MINUTES, default=DEFAULT_STALE_WINDOW_MINUTES
                ): STALE_WINDOW_MINUTES_SELECTOR,
            }
        )
        return await self._async_sensor_step("dynamic", schema, user_input, "options")
    # end async_step_dynamic

    async def async_step_confirm_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Soft warning: a chosen sensor exists but reads unavailable right now.

        A form with no fields that names the sensors. Submitting it means
        "continue anyway": go on to the step that would have come next.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_unavailable",
                data_schema=vol.Schema({}),
                description_placeholders={"sensors": ", ".join(self._unavailable)},
            )
        # end if
        return await getattr(self, f"async_step_{self._after_confirm}")()
    # end async_step_confirm_unavailable

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Steps 3 and 4: solar-to-battery accounting and currency.

        The currency defaults to Home Assistant's own currency setting, and
        is left out when reconfiguring: it is fixed after setup. With the
        checkbox off (either tariff mode), step 5 follows; with it on, no
        curtailment source is saved at all.
        """
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_TREAT_SOLAR_AS_FREE]:
                self._data.pop(CONF_CURTAILMENT_SOURCE, None)
                return self._async_finish()
            # end if
            return await self.async_step_curtailment()
        # end if

        fields: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_TREAT_SOLAR_AS_FREE,
                default=self._data.get(CONF_TREAT_SOLAR_AS_FREE, False),
            ): selector.BooleanSelector(),
        }
        if not self._reconfiguring:
            fields[vol.Required(CONF_CURRENCY, default=self.hass.config.currency)] = (
                selector.TextSelector()
            )
        # end if
        return self.async_show_form(step_id="options", data_schema=vol.Schema(fields))
    # end async_step_options

    async def async_step_curtailment(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: curtailment source (checkbox off only, either tariff mode).

        The field is optional: leaving it empty means "never curtailed", and
        an empty optional field is simply absent from user_input.
        """
        if user_input is not None:
            self._data.pop(CONF_CURTAILMENT_SOURCE, None)
            self._data.update(user_input)
            return self._async_finish()
        # end if

        schema = vol.Schema(
            {vol.Optional(CONF_CURTAILMENT_SOURCE): CURTAILMENT_SOURCE_SELECTOR}
        )
        return self.async_show_form(
            step_id="curtailment",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
        )
    # end async_step_curtailment

    @callback
    def _async_finish(self) -> ConfigFlowResult:
        """Create the hub, or save a reconfigured one.

        Reconfiguring updates the entry and lets the update listener in
        __init__.py reload the hub once. (async_update_reload_and_abort would
        reload it a second time on top of that listener.)
        """
        if self._reconfiguring:
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(), data=self._data
            )
            return self.async_abort(reason="reconfigure_successful")
        # end if
        return self.async_create_entry(title=DEFAULT_TITLE, data=self._data)
    # end _async_finish

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Declare which kinds of device may be added under a hub.

        This is what puts "Add device" buttons on the hub's page.
        """
        return {
            DeviceType.GENERAL: GeneralSubentryFlow,
            DeviceType.SOLAR: SolarSubentryFlow,
            DeviceType.BATTERY: BatterySubentryFlow,
        }
    # end async_get_supported_subentry_types
# end DynamicCostConfigFlow


class _DeviceSubentryFlow(ConfigSubentryFlow):
    """Shared add/reconfigure flow for one device type.

    Subclasses only list their sensor fields. Adding and reconfiguring use
    the same form and validation; the device type itself can't change.
    """

    SENSOR_FIELDS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._answers: dict[str, Any] = {}
        self._unavailable: list[str] = []
    # end __init__

    def _schema(self) -> vol.Schema:
        fields: dict[vol.Marker, Any] = {
            vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
        }
        for field in self.SENSOR_FIELDS:
            fields[vol.Required(field)] = ENERGY_SENSOR_SELECTOR
        # end for
        return vol.Schema(fields)
    # end _schema

    async def _async_device_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        current: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, unavailable = _check_sensors(
                self.hass, {field: user_input[field] for field in self.SENSOR_FIELDS}
            )
            if not errors:
                self._answers = dict(user_input)
                if unavailable:
                    self._unavailable = unavailable
                    return await self.async_step_confirm_unavailable()
                # end if
                return self._async_finish()
            # end if
        # end if

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                self._schema(), user_input or current or {}
            ),
            errors=errors,
        )
    # end _async_device_step

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a device: a name and its sensor(s)."""
        return await self._async_device_step("user", user_input)
    # end async_step_user

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a device's name and sensor(s) after adding it.

        The hub reloads, and a changed sensor starts from a fresh baseline
        (plan: Persistence).
        """
        return await self._async_device_step(
            "reconfigure", user_input, dict(self._get_reconfigure_subentry().data)
        )
    # end async_step_reconfigure

    async def async_step_confirm_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Soft warning, as in the hub flow; confirming saves the device."""
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_unavailable",
                data_schema=vol.Schema({}),
                description_placeholders={"sensors": ", ".join(self._unavailable)},
            )
        # end if
        return self._async_finish()
    # end async_step_confirm_unavailable

    @callback
    def _async_finish(self) -> SubentryFlowResult:
        name = self._answers[CONF_DEVICE_NAME]
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=name,
                data=self._answers,
            )
        # end if
        return self.async_create_entry(title=name, data=self._answers)
    # end _async_finish
# end _DeviceSubentryFlow


class GeneralSubentryFlow(_DeviceSubentryFlow):
    """Add a general (consumption-only) device: pure sub-metering."""

    SENSOR_FIELDS = (CONF_IMPORT_SENSOR,)
# end GeneralSubentryFlow


class SolarSubentryFlow(_DeviceSubentryFlow):
    """Add a solar array: one production sensor."""

    SENSOR_FIELDS = (CONF_EXPORT_SENSOR,)
# end SolarSubentryFlow


class BatterySubentryFlow(_DeviceSubentryFlow):
    """Add a battery: a charge sensor and a discharge sensor."""

    SENSOR_FIELDS = (CONF_IMPORT_SENSOR, CONF_EXPORT_SENSOR)
# end BatterySubentryFlow
