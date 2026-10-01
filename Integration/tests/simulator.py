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

    print(sim.get_total("solar_to_house"))            # a hub split
    print(sim.get_total("export_energy", device="Solar 1"))

Entity ids depend on device names, so the simulator finds your entities by
unique id (const.unique_id) instead. Devices are named "Solar 1",
"Battery 1", "General 1" and so on, in the order you pass them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

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
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
    DOMAIN,
    EXPORT_PRICE_GRACE_SECONDS,
    DeviceType,
    TariffMode,
    unique_id,
)

GRID_IMPORT = "sensor.sim_grid_import"
GRID_EXPORT = "sensor.sim_grid_export"
IMPORT_PRICE = "sensor.sim_import_price"
EXPORT_PRICE = "sensor.sim_export_price"
CURTAILMENT = "input_boolean.sim_curtailment"


class Simulator:
    """Drives a Dynamic Cost hub with fake sensors."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.entry: MockConfigEntry | None = None
        self._meters: dict[str, float] = {}
        self._import_price = 0.0
        self._export_price = 0.0
        self._tariff_mode = TariffMode.DYNAMIC
        self._window_minutes = 15
    # end __init__

    # -- setup -------------------------------------------------------------

    async def async_setup_hub(
        self,
        *,
        solar: list[str] | None = None,
        batteries: list[tuple[str, str]] | None = None,
        general: list[str] | None = None,
        treat_solar_as_free: bool = False,
        curtailment: bool = False,
        import_price_available: bool = True,
        tariff_mode: TariffMode = TariffMode.DYNAMIC,
        import_tariff: float = 0.0,
        export_tariff: float = 0.0,
        window_minutes: int = 15,
    ) -> MockConfigEntry:
        """Create a hub config entry plus device subentries, and load it.

        solar and general are lists of sensor ids; batteries is a list of
        (charge sensor, discharge sensor) pairs. Each becomes one subentry,
        exactly as if the user had clicked "Add device" in the UI.

        import_tariff, export_tariff and window_minutes only matter in
        fixed tariff mode.

        curtailment=True configures CURTAILMENT as the hub's curtailment
        source (starting off); drive it with set_curtailed(). Left False,
        the hub has no source and every window counts as uncurtailed. Like
        the config flow, it is only allowed with treat_solar_as_free off.

        import_price_available=False starts the hub with the import price
        sensor unavailable, for the plan's first-start "no window yet" case.
        Publish a real price with set_price() to open the first window.
        """
        if curtailment and treat_solar_as_free:
            raise ValueError(
                "the config flow only offers a curtailment source with "
                "treat_solar_as_free off"
            )
        # end if

        self._tariff_mode = tariff_mode
        self._window_minutes = window_minutes

        # Seed every sensor at 0 so nothing starts unavailable.
        for entity_id in (GRID_IMPORT, GRID_EXPORT):
            self.set_meter(entity_id, 0.0)
        self.set_price(import_price=0.0, export_price=0.0)
        if not import_price_available:
            self.set_unavailable(IMPORT_PRICE)
        # end if
        if curtailment:
            self.set_curtailed(False)
        # end if

        for entity_id in (solar or []) + (general or []):
            self.set_meter(entity_id, 0.0)
        for charge, discharge in batteries or []:
            self.set_meter(charge, 0.0)
            self.set_meter(discharge, 0.0)

        subentries = []
        for i, entity_id in enumerate(solar or [], start=1):
            subentries.append(self._subentry(
                DeviceType.SOLAR, f"Solar {i}", {CONF_EXPORT_SENSOR: entity_id}
            ))
        for i, (charge, discharge) in enumerate(batteries or [], start=1):
            subentries.append(self._subentry(
                DeviceType.BATTERY, f"Battery {i}",
                {CONF_IMPORT_SENSOR: charge, CONF_EXPORT_SENSOR: discharge},
            ))
        # end for
        for i, entity_id in enumerate(general or [], start=1):
            subentries.append(self._subentry(
                DeviceType.GENERAL, f"General {i}", {CONF_IMPORT_SENSOR: entity_id}
            ))

        data: dict[str, Any] = {
            CONF_GRID_IMPORT_SENSOR: GRID_IMPORT,
            CONF_GRID_EXPORT_SENSOR: GRID_EXPORT,
            CONF_TARIFF_MODE: tariff_mode,
            CONF_IMPORT_PRICE_SENSOR: IMPORT_PRICE,
            CONF_EXPORT_PRICE_SENSOR: EXPORT_PRICE,
            CONF_IMPORT_TARIFF: import_tariff,
            CONF_EXPORT_TARIFF: export_tariff,
            CONF_WINDOW_MINUTES: window_minutes,
            CONF_TREAT_SOLAR_AS_FREE: treat_solar_as_free,
            CONF_CURRENCY: "EUR",
        }
        # An empty optional field is absent from the saved data, not None.
        if curtailment:
            data[CONF_CURTAILMENT_SOURCE] = CURTAILMENT
        # end if

        self.entry = MockConfigEntry(
            domain=DOMAIN,
            title="Simulated hub",
            data=data,
            subentries_data=subentries,
        )
        self.entry.add_to_hass(self._hass)
        await self._hass.config_entries.async_setup(self.entry.entry_id)
        await self._hass.async_block_till_done()
        return self.entry
    # end async_setup_hub

    @staticmethod
    def _subentry(
        device_type: DeviceType, name: str, sensors: dict[str, str]
    ) -> dict[str, Any]:
        """Build the same subentry data the device config flows would save."""
        return {
            "subentry_type": device_type,
            "title": name,
            "data": {CONF_DEVICE_NAME: name, **sensors},
            "unique_id": None,
        }
    # end _subentry

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
    # end set_meter

    def advance_meter(self, entity_id: str, delta: float) -> None:
        """Add to a meter's total, i.e. simulate energy actually flowing."""
        self.set_meter(entity_id, self._meters.get(entity_id, 0.0) + delta)
    # end advance_meter

    def set_price(
        self, *, import_price: float | None = None, export_price: float | None = None
    ) -> None:
        """Update either or both price sensors."""
        if import_price is not None:
            self._import_price = import_price
            self._hass.states.async_set(
                IMPORT_PRICE, str(import_price),
                {"unit_of_measurement": "EUR/kWh"},
            )
        # end if
        if export_price is not None:
            self._export_price = export_price
            # force_update: a real price feed publishes the export price at
            # every boundary even when the value is unchanged, and the engine
            # relies on seeing that event to lock the new window's price.
            self._hass.states.async_set(
                EXPORT_PRICE, str(export_price),
                {"unit_of_measurement": "EUR/kWh"},
                force_update=True,
            )
        # end if
    # end set_price

    def set_curtailed(self, curtailed: bool) -> None:
        """Turn the curtailment source on or off, as a user automation would."""
        self._hass.states.async_set(CURTAILMENT, STATE_ON if curtailed else STATE_OFF)
    # end set_curtailed

    async def async_advance_window(
        self, *, import_price: float | None = None, export_price: float | None = None
    ) -> None:
        """Settle the current window and open the next one.

        Dynamic mode, in order:
          1. jump past the grace period, so the current window's export
             price locks
          2. publish the next import price -- this is the boundary. If you
             don't pass one, the current price is nudged up by 0.01, because
             an unchanged import price is deliberately NOT a boundary
          3. republish the export price (the next one, or the current one) so
             the new window has a tentative export price
          4. jump past the grace period again, so that price locks

        Fixed mode: jump one window length ahead, then past the grace period.
        import_price and export_price are ignored -- the tariffs are fixed.

        Advance your meters BEFORE calling this; that energy lands in the
        window being closed.
        """
        if self._tariff_mode == TariffMode.FIXED:
            await self._async_jump(timedelta(minutes=self._window_minutes))
            await self._async_jump(timedelta(seconds=EXPORT_PRICE_GRACE_SECONDS + 1))
            return
        # end if

        await self._async_jump(timedelta(seconds=EXPORT_PRICE_GRACE_SECONDS + 1))
        if import_price is None:
            import_price = round(self._import_price + 0.01, 6)
        # end if
        self.set_price(import_price=import_price)
        await self._hass.async_block_till_done()
        self.set_price(
            export_price=self._export_price if export_price is None else export_price
        )
        await self._hass.async_block_till_done()
        await self._async_jump(timedelta(seconds=EXPORT_PRICE_GRACE_SECONDS + 1))
    # end async_advance_window

    async def _async_jump(self, delta: timedelta) -> None:
        """Fire every timer that would have run within delta from now."""
        async_fire_time_changed(self._hass, dt_util.utcnow() + delta)
        await self._hass.async_block_till_done()
    # end _async_jump

    def set_unavailable(self, entity_id: str) -> None:
        """Knock a sensor offline, to exercise the resilience rules."""
        self._hass.states.async_set(entity_id, STATE_UNAVAILABLE)
    # end set_unavailable

    def restore(self, entity_id: str) -> None:
        """Bring a knocked-out sensor back at its last known total."""
        self.set_meter(entity_id, self._meters.get(entity_id, 0.0))
    # end restore

    # -- observing ---------------------------------------------------------

    def get_state(self, entity_id: str) -> str | None:
        """Read any entity's raw state by entity id."""
        state = self._hass.states.get(entity_id)
        return state.state if state else None
    # end get_state

    def entity_id_for(self, key: str, device: str | None = None) -> str | None:
        """Find one of your entities by its unique id.

        Without device, key is a hub split key ("solar_to_house"). With
        device, it is that device's counter key ("export_energy"), and device
        is the device's name ("Solar 1").
        """
        assert self.entry is not None, "call async_setup_hub first"
        owner = self.entry.entry_id
        if device is not None:
            owner = next(
                sub.subentry_id
                for sub in self.entry.subentries.values()
                if sub.title == device
            )
        # end if
        return er.async_get(self._hass).async_get_entity_id(
            "sensor", DOMAIN, unique_id(owner, key)
        )
    # end entity_id_for

    def get_total(self, key: str, device: str | None = None) -> float | None:
        """Read one of your entities as a number; None if missing or not numeric."""
        entity_id = self.entity_id_for(key, device)
        raw = self.get_state(entity_id) if entity_id else None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
        # end try
    # end get_total

    def dump(self) -> dict[str, Any]:
        """Return every entity your integration currently exposes.

        Handy while developing: print this after each window to see the whole
        picture at once instead of checking entities one at a time.
        """
        assert self.entry is not None, "call async_setup_hub first"
        registry = er.async_get(self._hass)
        return {
            reg.entity_id: self.get_state(reg.entity_id)
            for reg in er.async_entries_for_config_entry(
                registry, self.entry.entry_id
            )
        }
    # end dump
# end Simulator
