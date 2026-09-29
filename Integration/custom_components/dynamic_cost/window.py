"""Window engine for Dynamic Cost.

Owns the snapshot/delta bookkeeping and decides when a tracking window opens
and closes. This is the only place that talks to both Home Assistant state
and the calculation engine.

Build this SECOND, after calc.py is finished and tested.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from .calc import WindowEnergy
from .const import (
    CONF_CURTAILMENT_SOURCE,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
    EXPORT_PRICE_GRACE_SECONDS,
    DeviceType,
    TariffMode,
)

_LOGGER = logging.getLogger(__name__)

UNSET = object()
"""Sentinel: no export price or curtailment state captured for this window yet."""


@dataclass(frozen=True)
class Device:
    """One device subentry (solar array, battery or general device).

    Built in __init__.py from entry.subentries. Complete -- nothing to
    implement. Which sensor fields are set depends on device_type:
    GENERAL has only import_sensor, SOLAR only export_sensor, BATTERY both.
    """

    subentry_id: str
    device_type: DeviceType
    name: str
    import_sensor: str | None = None
    export_sensor: str | None = None


@dataclass
class TrackedSensor:
    """One source sensor plus the snapshot of its last good reading."""

    entity_id: str
    snapshot: float | None = None
    unavailable_logged: bool = False


@dataclass
class WindowState:
    """Everything the engine knows about the window currently in progress."""

    import_price: float | None = None
    tentative_export_price: Any = UNSET
    locked_export_price: float | None = None
    tentative_curtailed: Any = UNSET
    locked_curtailed: bool | None = None
    grace_elapsed: bool = False
    cancel_grace: Callable[[], None] | None = None


class WindowEngine:
    """Drives tracking windows for one hub."""

    def __init__(
        self, hass: HomeAssistant, config: dict[str, Any], devices: list[Device]
    ) -> None:
        """Store config and set up empty state. No listeners yet.

        config holds the hub's own settings (the CONF_* keys from the config
        flow); devices holds its subentries. You will want one TrackedSensor
        in self._sensors per sensor you read at a boundary: the hub's two grid
        sensors plus every sensor of every device.
        """
        self._hass = hass
        self._config = config
        self._devices = devices
        self._sensors: dict[str, TrackedSensor] = {}
        self._window = WindowState()
        self._unsubscribes: list[Callable[[], None]] = []
        self._last_known_export_price: float | None = None

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Register listeners/timers and open the first window."""
        match self._config.get(CONF_TARIFF_MODE):
            case TariffMode.DYNAMIC:
                self._unsubscribes.append(
                    async_track_state_change_event( 
                        self._hass,
                        self._config.get(CONF_IMPORT_PRICE_SENSOR),
                        self._async_import_price_changed
                    )
                )

                self._unsubscribes.append(
                    async_track_state_change_event( 
                        self._hass,
                        self._config.get(CONF_EXPORT_PRICE_SENSOR),
                        self._async_export_price_changed
                    )
                )

            case TariffMode.FIXED:
                self._unsubscribes.append(
                    async_track_time_interval(
                        self._hass,
                        self._async_boundary_reached,
                        self._config.get(CONF_WINDOW_MINUTES) * 60
                    )
                )

            case _:
                # Future modes?
                raise NotImplementedError

        # TODO: Addendum 1 -- when CONF_CURTAILMENT_SOURCE is set, also track
        # it with self._async_curtailment_changed (in both tariff modes).


    @callback
    def async_stop(self) -> None:
        """Tear down every listener and timer.

        IMPLEMENT ME.

        Call each callable in self._unsubscribes, clear the list, and cancel
        the grace-period timer if one is pending. Getting this right is what
        stops duplicate listeners piling up when the user reloads the entry.
        """
        raise NotImplementedError

    @callback
    def _open_window(self, import_price: float | None) -> None:
        """Start a fresh window: new WindowState, new grace timer."""
        if self._window.cancel_grace is not None:
            self._window.cancel_grace()

        self._window = WindowState(import_price=import_price)
        # TODO: Addendum 1 -- read the curtailment source tentatively here.
        # With no source configured, lock it as False straight away so the
        # window never waits on it.

        self._window.cancel_grace = async_call_later(
            self._hass,
            EXPORT_PRICE_GRACE_SECONDS,
            self._async_grace_elapsed
        )


    # -- sensor reading ----------------------------------------------------

    def _read_sensor(self, tracked: TrackedSensor) -> float | None:
        """Read one sensor's current numeric value, or None if unavailable.

        IMPLEMENT ME.

        Fetch the state via self._hass.states.get(tracked.entity_id). Return
        None if the state is missing, is STATE_UNKNOWN/STATE_UNAVAILABLE, or
        does not parse as a float.

        Also handle the edge-triggered logging here (or call the helpers
        below): log a warning the first time a sensor goes unavailable and an
        info line when it recovers -- never once per window, or a sensor that
        stays down for hours floods the log.
        """
        raise NotImplementedError

    def _collect_deltas(self) -> dict[str, float]:
        """Read every tracked sensor once and turn it into this window's delta.

        IMPLEMENT ME.

        This is the single most important function in the integration to get
        right. For each tracked sensor:

            reading = self._read_sensor(tracked)      # THE ONE AND ONLY READ
            if reading is not None:
                delta = reading - tracked.snapshot     (0.0 if no snapshot yet)
                tracked.snapshot = reading             # reuse the SAME value
            else:
                delta = 0.0
                # leave tracked.snapshot alone, so when the sensor recovers
                # its next delta spans the whole gap instead of being lost

        Never call _read_sensor twice for one boundary. If you read once for
        the subtraction and again for the snapshot, anything that arrives
        between the two reads vanishes from the accounting forever, and the
        error accumulates window after window.

        Also guard against a negative delta, which means the source sensor
        reset (e.g. a daily-reset production counter). Treat it as 0 for this
        window rather than letting it corrupt the split maths.

        Return a dict keyed however you like; _aggregate() turns it into a
        WindowEnergy.
        """
        raise NotImplementedError

    def _aggregate(self, deltas: dict[str, float]) -> WindowEnergy:
        """Sum per-device deltas into the hub-level categories.

        IMPLEMENT ME.

        - solar            = sum of every SOLAR device's export delta
        - battery_charge   = sum of every BATTERY device's import delta
        - battery_discharge= sum of every BATTERY device's export delta
        - grid_import/export = the hub's own two sensors

        GENERAL devices are deliberately excluded -- they are pure
        sub-metering and must not influence the splits.
        """
        raise NotImplementedError

    # -- window boundaries -------------------------------------------------

    @callback
    def _async_import_price_changed(self, event: Any) -> None:
        """Handle a state change on the import price sensor (dynamic mode).

        IMPLEMENT ME.

            - unavailable/unparseable new value -> return, do nothing at all.
              The window simply stays open (per the plan, an unavailable price
              is treated as 'no change', never as a boundary).
            - value equal to the current window's import price -> return.
            - otherwise -> this is a real boundary: close the current window
              and open a new one with this price.
        """
        raise NotImplementedError

    @callback
    def _async_export_price_changed(self, event: Any) -> None:
        """Handle a state change on the export price sensor (dynamic mode).

        IMPLEMENT ME.

            - unavailable/unparseable -> return.
            - store it as self._window.tentative_export_price.
            - if the grace period has ALREADY elapsed and nothing was locked
              yet (tentative was UNSET when the timer fired), lock immediately
              -- this is the late-arrival path that lets a recovering sensor
              unblock a window minutes later.
        """
        raise NotImplementedError

    @callback
    def _async_grace_elapsed(self, _now: Any) -> None:
        """Fire EXPORT_PRICE_GRACE_SECONDS after the window opened.

        IMPLEMENT ME.

            - mark self._window.grace_elapsed = True
            - if tentative_export_price is UNSET: return WITHOUT locking. The
              grace period is the earliest a price may lock, not a deadline.
            - otherwise lock it (see _lock_export_price).
            - same again for tentative_curtailed (see _lock_curtailment).
        """
        raise NotImplementedError

    @callback
    def _lock_export_price(self, price: float) -> None:
        """Fix the export price for the window in progress.

        IMPLEMENT ME.

        Store it on self._window, remember it as self._last_known_export_price
        (persist that -- it should survive a restart), and if a window was
        waiting to close on this price (and curtailment has locked too),
        close it now.
        """
        raise NotImplementedError

    @callback
    def _async_curtailment_changed(self, event: Any) -> None:
        """Handle a state change on the curtailment source (Addendum 1).

        IMPLEMENT ME.

        The mirror image of _async_export_price_changed:

            - "on" -> True, "off" -> False; anything else (unavailable,
              unknown) -> return. Never guess a curtailment state.
            - store it as self._window.tentative_curtailed.
            - if the grace period has already elapsed and nothing was locked
              yet, lock immediately (late-arrival path).
        """
        raise NotImplementedError

    @callback
    def _lock_curtailment(self, curtailed: bool) -> None:
        """Fix the curtailment state for the window in progress.

        IMPLEMENT ME.

        Store it as self._window.locked_curtailed, and if a window was
        waiting to close on it (and the export price has locked too), close
        it now.
        """
        raise NotImplementedError

    async def _async_boundary_reached(self, _now: Any = None) -> None:
        """Close the current window and open the next one.

        IMPLEMENT ME.

        Order matters:
            1. If the export price or the curtailment state has not locked
               for this window yet, DO NOT close. Leave the snapshots
               untouched so energy keeps accumulating into the extended
               window, and let whichever lock arrives last finish the job.
            2. deltas = self._collect_deltas()
            3. energy = self._aggregate(deltas)
            4. run calc.calculate_energy_splits / calculate_financial_splits,
               with treat_solar_as_free = the hub's CONF_TREAT_SOLAR_AS_FREE
               checkbox OR this window's locked_curtailed
            5. hand the results to whatever the sensor platform registered
               (see async_add_listener below)
            6. open the fresh window: new prices, reset grace timer via
               async_call_later(hass, EXPORT_PRICE_GRACE_SECONDS, ...)
        """
        raise NotImplementedError

    # -- plumbing for the sensor platform ----------------------------------

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity to be told when a window has been settled.

        IMPLEMENT ME.

        Keep a list of callbacks; return a function that removes the one just
        added. This is the standard Home Assistant pattern for letting many
        entities follow one data source without each of them polling.
        """
        raise NotImplementedError
