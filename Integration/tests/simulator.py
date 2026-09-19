"""A fake Home Assistant world for driving Dynamic Cost during development.

This file is COMPLETE and working. It is your test bench, not an exercise.

The point: you can push meter readings and price changes at your integration
and watch what comes out, in milliseconds, without ever restarting Home
Assistant. Nothing here touches a real HA instance -- it drives the same
hass object that Home Assistant's own test suite uses.

Typical use, from a test:

    sim = Simulator(hass)
    await sim.async_setup_hub(
        solar=["sensor.fake_solar"],
        batteries=[("sensor.fake_bat_charge", "sensor.fake_bat_discharge")],
    )

    sim.set_meter("sensor.fake_solar", 10.0)     # totals, not deltas
    sim.set_price(import_price=0.25, export_price=0.08)
    await sim.async_advance_window()             # settle a window

    print(sim.get_state("sensor.dynamic_cost_solar_to_house"))
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_cost.const import (
    CONF_CURRENCY,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_OPPORTUNITY_COST_MODE,
    CONF_TARIFF_MODE,
    DOMAIN,
    OpportunityCostMode,
    TariffMode,
)

GRID_IMPORT = "sensor.sim_grid_import"
GRID_EXPORT = "sensor.sim_grid_export"
IMPORT_PRICE = "sensor.sim_import_price"
EXPORT_PRICE = "sensor.sim_export_price"


class Simulator:
    """Drives a Dynamic Cost hub with fake sensors."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.entry: MockConfigEntry | None = None
        self._meters: dict[str, float] = {}

    # -- setup -------------------------------------------------------------

    async def async_setup_hub(
        self,
        *,
        solar: list[str] | None = None,
        batteries: list[tuple[str, str]] | None = None,
        general: list[str] | None = None,
        mode: OpportunityCostMode = OpportunityCostMode.ALWAYS,
        tariff_mode: TariffMode = TariffMode.DYNAMIC,
    ) -> MockConfigEntry:
        """Create a hub config entry plus device subentries, and load it."""
        # Seed every sensor at 0 so nothing starts unavailable.
        for entity_id in (GRID_IMPORT, GRID_EXPORT):
            self.set_meter(entity_id, 0.0)
        self.set_price(import_price=0.0, export_price=0.0)

        for entity_id in (solar or []) + (general or []):
            self.set_meter(entity_id, 0.0)
        for charge, discharge in batteries or []:
            self.set_meter(charge, 0.0)
            self.set_meter(discharge, 0.0)

        self.entry = MockConfigEntry(
            domain=DOMAIN,
            title="Simulated hub",
            data={
                CONF_GRID_IMPORT_SENSOR: GRID_IMPORT,
                CONF_GRID_EXPORT_SENSOR: GRID_EXPORT,
                CONF_TARIFF_MODE: tariff_mode,
                CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE,
                CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE,
                CONF_OPPORTUNITY_COST_MODE: mode,
                CONF_CURRENCY: "EUR",
            },
        )
        self.entry.add_to_hass(self._hass)
        await self._hass.config_entries.async_setup(self.entry.entry_id)
        await self._hass.async_block_till_done()
        return self.entry

    # -- driving the world -------------------------------------------------

    def set_meter(self, entity_id: str, total: float) -> None:
        """Set a meter to a CUMULATIVE total (the way a real meter reports)."""
        self._meters[entity_id] = total
        self._hass.states.async_set(
            entity_id,
            str(total),
            {"unit_of_measurement": "kWh", "device_class": "energy",
             "state_class": "total_increasing"},
        )

    def advance_meter(self, entity_id: str, delta: float) -> None:
        """Add to a meter's total, i.e. simulate energy actually flowing."""
        self.set_meter(entity_id, self._meters.get(entity_id, 0.0) + delta)

    def set_price(
        self, *, import_price: float | None = None, export_price: float | None = None
    ) -> None:
        """Update either or both price sensors."""
        if import_price is not None:
            self._hass.states.async_set(
                IMPORT_PRICE, str(import_price),
                {"unit_of_measurement": "EUR/kWh"},
            )
        if export_price is not None:
            self._hass.states.async_set(
                EXPORT_PRICE, str(export_price),
                {"unit_of_measurement": "EUR/kWh"},
            )

    def set_unavailable(self, entity_id: str) -> None:
        """Knock a sensor offline, to exercise the resilience rules."""
        self._hass.states.async_set(entity_id, STATE_UNAVAILABLE)

    def restore(self, entity_id: str) -> None:
        """Bring a knocked-out sensor back at its last known total."""
        self.set_meter(entity_id, self._meters.get(entity_id, 0.0))

    # -- observing ---------------------------------------------------------

    def get_state(self, entity_id: str) -> str | None:
        """Read one of your integration's output entities."""
        state = self._hass.states.get(entity_id)
        return state.state if state else None

    def dump(self, prefix: str = "sensor.dynamic_cost") -> dict[str, Any]:
        """Return every entity your integration currently exposes.

        Handy while developing: print this after each window to see the whole
        picture at once instead of checking entities one at a time.
        """
        return {
            state.entity_id: state.state
            for state in self._hass.states.async_all()
            if state.entity_id.startswith(prefix)
        }
