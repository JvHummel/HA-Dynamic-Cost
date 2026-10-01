"""Config flow for Dynamic Cost.

Build this LAST. It is the most framework-heavy part of the integration and
the least interesting logically, so leave it until the maths underneath it is
already proven.

Structure:
  DynamicCostConfigFlow  -- the hub setup wizard (steps 1-5 of the plan)
  GeneralSubentryFlow    -- "add a general device"
  SolarSubentryFlow      -- "add solar panels"
  BatterySubentryFlow    -- "add a battery"
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

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
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
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


def _check_sensors(
    hass: HomeAssistant, sensors: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Layer two of the plan's config-flow validation, for any flow step.

    IMPLEMENT ME.

    sensors maps each form field to the entity id chosen in it. Read each
    entity once and return (errors, unavailable):
      - errors: {field: "unknown_entity"} for an entity that doesn't exist,
        {field: "not_numeric"} for one whose state isn't a number. Either one
        blocks the step: show the same form again with errors=errors.
      - unavailable: entity ids that exist but read unavailable or unknown.
        Not an error -- the flow continues via a confirmation step.
    """
    raise NotImplementedError
# end _check_sensors


class DynamicCostConfigFlow(ConfigFlow, domain=DOMAIN):
    """Hub setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold answers across the multi-step flow."""
        self._data: dict[str, Any] = {}
    # end __init__

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: the two grid sensors.

        IMPLEMENT ME.

        Show a form with CONF_GRID_IMPORT_SENSOR and CONF_GRID_EXPORT_SENSOR
        (both ENERGY_SENSOR_SELECTOR, both required). Stash the answers in
        self._data, then return await self.async_step_tariff().

        This is also where layer two of validation goes: _check_sensors().
        Errors show the form again. Sensors that read unavailable go to
        async_step_confirm_unavailable() before continuing, because a
        briefly-unavailable sensor is not necessarily misconfigured.
        """
        raise NotImplementedError
    # end async_step_user

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: fixed or dynamic tariffs.

        IMPLEMENT ME.

        One selector over TariffMode. Branch to async_step_fixed() or
        async_step_dynamic() depending on the answer.
        """
        raise NotImplementedError
    # end async_step_tariff

    async def async_step_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: import tariff, export tariff, window length in minutes.

        IMPLEMENT ME. Then continue to async_step_options().
        """
        raise NotImplementedError
    # end async_step_fixed

    async def async_step_dynamic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: import and export price sensors.

        IMPLEMENT ME. Validate both sensors with _check_sensors(), as in
        step 1, then continue to async_step_options().
        """
        raise NotImplementedError
    # end async_step_dynamic

    async def async_step_confirm_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Soft warning: a chosen sensor exists but reads unavailable right now.

        IMPLEMENT ME.

        Show a form with no fields whose description names the sensors, via
        description_placeholders={"sensors": ...}. Submitting it means
        "continue anyway": go on to the step that would have come next.
        Several steps can send the user here, so remember which step that is
        in an attribute set in __init__ -- not in self._data, which is saved
        as the hub's configuration.
        """
        raise NotImplementedError
    # end async_step_confirm_unavailable

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Steps 3 and 4: solar-to-battery accounting and currency.

        IMPLEMENT ME.

        CONF_TREAT_SOLAR_AS_FREE is a checkbox (selector.BooleanSelector),
        off by default.

        CONF_CURRENCY becomes the unit of every monetary sensor under this
        hub. Default it to self.hass.config.currency, the currency set in
        Home Assistant's own settings. When reconfiguring, leave the field
        out: the currency is fixed after setup.

        Then branch: with the checkbox off (either tariff mode), continue to
        async_step_curtailment(). Otherwise finish here with
        self.async_create_entry(title=..., data=self._data) -- no curtailment
        source is saved at all.
        """
        raise NotImplementedError
    # end async_step_options

    async def async_step_curtailment(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: curtailment source (checkbox off only, either tariff mode).

        IMPLEMENT ME.

        A form with the single field CONF_CURTAILMENT_SOURCE, using
        CURTAILMENT_SOURCE_SELECTOR. It must be vol.Optional, not
        vol.Required: leaving it empty means "never curtailed". An empty
        optional field is simply absent from user_input, so the engine reads
        it with .get() and treats None as no source.

        Finish with self.async_create_entry(title=..., data=self._data).
        """
        raise NotImplementedError
    # end async_step_curtailment

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the hub's settings after setup (plan: config flow).

        IMPLEMENT ME.

        Walk the same steps as setup, starting from step 1's form, pre-filled
        from self._get_reconfigure_entry().data via
        self.add_suggested_values_to_schema(). Every setting may change,
        INCLUDING the tariff type: a user who switches contracts keeps their
        records, and the engine discards the in-progress window on reload.
        The currency stays as it is.

        The shared steps tell the two cases apart with
        self.source == SOURCE_RECONFIGURE. Where setup would call
        async_create_entry, reconfigure instead calls
        self.hass.config_entries.async_update_entry(entry, data=...) and
        returns self.async_abort(reason="reconfigure_successful"). The update
        listener in __init__.py then reloads the hub once. (Avoid
        async_update_reload_and_abort here: together with that listener it
        would reload twice.)
        """
        raise NotImplementedError
    # end async_step_reconfigure

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Declare which kinds of device may be added under a hub.

        This is what puts "Add device" buttons on the hub's page. Complete as
        written -- no implementation needed.
        """
        return {
            DeviceType.GENERAL: GeneralSubentryFlow,
            DeviceType.SOLAR: SolarSubentryFlow,
            DeviceType.BATTERY: BatterySubentryFlow,
        }
    # end async_get_supported_subentry_types
# end DynamicCostConfigFlow


class GeneralSubentryFlow(ConfigSubentryFlow):
    """Add a general (consumption-only) device."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name and ONE import sensor.

        IMPLEMENT ME. Use ENERGY_SENSOR_SELECTOR for the sensor, as in the
        hub's step 1, and validate it with _check_sensors(). Unavailable
        sensors need a confirmation step here too, like the hub's
        async_step_confirm_unavailable, with its own strings under
        config_subentries in strings.json.

        Finish with self.async_create_entry(title=<name>, data=...).
        Remember: general devices are pure sub-metering and must never feed
        the hub's split calculations.
        """
        raise NotImplementedError
    # end async_step_user

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change this device's name and import sensor after adding it.

        IMPLEMENT ME.

        Same fields and validation as async_step_user, pre-filled from
        self._get_reconfigure_subentry().data. Finish with
        self.async_update_and_abort(self._get_entry(), subentry, title=...,
        data=...); the hub reloads, and a changed sensor starts from a fresh
        baseline (plan: Persistence). The device type can't change.
        """
        raise NotImplementedError
    # end async_step_reconfigure
# end GeneralSubentryFlow


class SolarSubentryFlow(ConfigSubentryFlow):
    """Add a solar array."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name and ONE export (production) sensor.

        IMPLEMENT ME. Use ENERGY_SENSOR_SELECTOR for the sensor, and
        validate it as in GeneralSubentryFlow.
        """
        raise NotImplementedError
    # end async_step_user

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change this device's name and production sensor after adding it.

        IMPLEMENT ME.

        Same fields and validation as async_step_user, pre-filled from
        self._get_reconfigure_subentry().data. Finish with
        self.async_update_and_abort(self._get_entry(), subentry, title=...,
        data=...); the hub reloads, and a changed sensor starts from a fresh
        baseline (plan: Persistence). The device type can't change.
        """
        raise NotImplementedError
    # end async_step_reconfigure
# end SolarSubentryFlow


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add a battery."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name, an import (charge) sensor and an export (discharge) sensor.

        IMPLEMENT ME. Use ENERGY_SENSOR_SELECTOR for both sensors, and
        validate them as in GeneralSubentryFlow.
        """
        raise NotImplementedError
    # end async_step_user

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change this device's name and sensors after adding it.

        IMPLEMENT ME.

        Same fields and validation as async_step_user, pre-filled from
        self._get_reconfigure_subentry().data. Finish with
        self.async_update_and_abort(self._get_entry(), subentry, title=...,
        data=...); the hub reloads, and a changed sensor starts from a fresh
        baseline (plan: Persistence). The device type can't change.
        """
        raise NotImplementedError
    # end async_step_reconfigure
# end BatterySubentryFlow
