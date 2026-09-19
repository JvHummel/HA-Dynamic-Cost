"""Config flow for Dynamic Cost.

Build this LAST. It is the most framework-heavy part of the integration and
the least interesting logically, so leave it until the maths underneath it is
already proven.

Structure:
  DynamicCostConfigFlow  -- the hub setup wizard (steps 1-4 of the plan)
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
    CONF_DEVICE_NAME,
    CONF_EXPORT_SENSOR,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_OPPORTUNITY_COST_MODE,
    CONF_TARIFF_MODE,
    CONF_WINDOW_MINUTES,
    DOMAIN,
    DeviceType,
    OpportunityCostMode,
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


class DynamicCostConfigFlow(ConfigFlow, domain=DOMAIN):
    """Hub setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold answers across the multi-step flow."""
        self._data: dict[str, Any] = {}

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

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: fixed or dynamic tariffs.

        IMPLEMENT ME.

        One selector over TariffMode. Branch to async_step_fixed() or
        async_step_dynamic() depending on the answer.
        """
        raise NotImplementedError

    async def async_step_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: import tariff, export tariff, window length in minutes.

        IMPLEMENT ME. Then continue to async_step_options().
        """
        raise NotImplementedError

    async def async_step_dynamic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: import and export price sensors.

        IMPLEMENT ME. Then continue to async_step_options().
        """
        raise NotImplementedError

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Steps 3 and 4: opportunity-cost mode and currency.

        IMPLEMENT ME.

        CONF_OPPORTUNITY_COST_MODE is a single required choice over the three
        OpportunityCostMode values -- render it as radio buttons via
        SelectSelectorMode.LIST rather than a dropdown.

        Finish with self.async_create_entry(title=..., data=self._data).
        """
        raise NotImplementedError

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


class SolarSubentryFlow(ConfigSubentryFlow):
    """Add a solar array."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name and ONE export (production) sensor.

        IMPLEMENT ME.
        """
        raise NotImplementedError


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add a battery."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for a name, an import (charge) sensor and an export (discharge) sensor.

        IMPLEMENT ME.
        """
        raise NotImplementedError
