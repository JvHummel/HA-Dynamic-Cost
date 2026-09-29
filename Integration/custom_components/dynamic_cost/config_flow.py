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
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
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

        This is also where layer two of validation goes: read each chosen
        entity once. A non-existent entity id is a hard error (return the
        form again with errors={"base": "unknown_entity"}); an entity that
        exists but currently reads unavailable is only a warning, because a
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

        IMPLEMENT ME. Then continue to async_step_options().
        """
        raise NotImplementedError
    # end async_step_dynamic

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Steps 3 and 4: solar-to-battery accounting and currency.

        IMPLEMENT ME.

        CONF_TREAT_SOLAR_AS_FREE is a checkbox (selector.BooleanSelector),
        off by default.

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

        IMPLEMENT ME.

        Finish with self.async_create_entry(title=<name>, data=...).
        Remember: general devices are pure sub-metering and must never feed
        the hub's split calculations.
        """
        raise NotImplementedError
    # end async_step_user
# end GeneralSubentryFlow


class SolarSubentryFlow(ConfigSubentryFlow):
    """Add a solar array."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name and ONE export (production) sensor.

        IMPLEMENT ME.
        """
        raise NotImplementedError
    # end async_step_user
# end SolarSubentryFlow


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add a battery."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name, an import (charge) sensor and an export (discharge) sensor.

        IMPLEMENT ME.
        """
        raise NotImplementedError
    # end async_step_user
# end BatterySubentryFlow
