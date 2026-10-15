"""Window engine for Dynamic Cost.

Owns the snapshot/delta bookkeeping and decides when a tracking window opens
and closes. This is the only place that talks to both Home Assistant state
and the calculation engine.

Build this SECOND, after calc.py is finished and tested.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from datetime import timedelta
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN
)

from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .calc import WindowEnergy
from .const import (
    CONF_CURTAILMENT_SOURCE,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
    DOMAIN,
    EXPORT_PRICE_GRACE_SECONDS,
    SAVE_DELAY_SECONDS,
    STORAGE_VERSION,
    DeviceType,
    TariffMode,
)

_LOGGER = logging.getLogger(__name__)

UNSET = object()
"""Sentinel: no export price or curtailment state captured for this window yet."""


def storage_key(entry_id: str) -> str:
    """Name of one hub's store file under .storage/ (plan: Persistence)."""
    return f"{DOMAIN}.{entry_id}"
# end storage_key


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
# end Device


@dataclass
class TrackedSensor:
    """One source sensor plus the snapshot of its last good reading."""

    entity_id: str
    snapshot: float | None = None
    unavailable_logged: bool = False
# end TrackedSensor


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
    # A boundary was reached but a lock was still missing, so the window is
    # held open; the lock that arrives last closes it.
    close_pending: bool = False
    # Dynamic mode: the import price the NEXT window opens with, taken from
    # the price change that triggered the (possibly pending) boundary.
    next_import_price: float | None = None
# end WindowState


class WindowEngine:
    """Drives tracking windows for one hub."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: dict[str, Any],
        devices: list[Device],
    ) -> None:
        """Store config and set up empty state. No listeners yet.

        config holds the hub's own settings (the CONF_* keys from the config
        flow); devices holds its subentries. You will want one TrackedSensor
        in self._sensors per sensor you read at a boundary: the hub's two grid
        sensors plus every sensor of every device. entry_id is the hub's
        config entry id, which names its store.
        """
        self._hass = hass
        self._config = config
        self._devices = devices
        self._sensors: dict[str, TrackedSensor] = {}
        self._window = WindowState()
        self._unsubscribes: list[Callable[[], None]] = []
        # Cumulative totals: the hub-level split keys plus every device's
        # counters, keyed however you like. The sensor platform reads them
        # through get_total(). See the plan's Persistence section.
        self._totals: dict[str, float] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry_id)
        )
    # end __init__

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Register listeners/timers and open the first window if possible.

        First of all, await self._async_restore(). A restored in-progress
        window simply carries on, so the first-window rules below only apply
        when there was nothing to restore.

        Fixed mode: open the first window right away, at CONF_IMPORT_TARIFF.
        Dynamic mode: read the import price sensor. A real reading opens the
        first window at that price; no reading means no window yet, and
        _async_import_price_changed opens the first one when a real price
        arrives. (Plan: window engine, first window.)
        """
        import_price = None

        match self._config.get(CONF_TARIFF_MODE):
            case TariffMode.DYNAMIC:
                self._unsubscribes.append(
                    async_track_state_change_event( 
                        self._hass,
                        self._config.get(CONF_IMPORT_PRICE_SENSOR),
                        self._async_import_price_changed,
                    )
                )

                self._unsubscribes.append(
                    async_track_state_change_event( 
                        self._hass,
                        self._config.get(CONF_EXPORT_PRICE_SENSOR),
                        self._async_export_price_changed,
                    )
                )
                
                import_price = self._read_entity_state(
                    self._config.get(
                        CONF_IMPORT_PRICE_SENSOR
                    )
                )
            # end case DYNAMIC

            case TariffMode.FIXED:
                self._unsubscribes.append(
                    async_track_time_interval(
                        self._hass,
                        self._async_boundary_reached,
                        timedelta(
                            minutes = self._config.get(
                                CONF_WINDOW_MINUTES
                            )
                        ),
                    )
                )

                import_price = self._config.get(CONF_IMPORT_TARIFF)
            # end case FIXED

            case _:
                # Future modes?
                raise NotImplementedError
            # end case _
        # end match

        # Curtailment switch is relevant for both contract types.
        curtailment_switch = self._config.get(CONF_CURTAILMENT_SOURCE)

        if curtailment_switch is not None:
            self._unsubscribes.append(
                async_track_state_change_event( 
                    self._hass,
                    curtailment_switch,
                    self._async_curtailment_changed,
                )
            )
        # end if

        self._open_window(import_price)
    # end async_start

    @callback
    def async_stop(self) -> None:
        """Tear down every listener and timer.

        Call each callable in self._unsubscribes, clear the list, and cancel
        the grace-period timer if one is pending. Getting this right is what
        stops duplicate listeners piling up when the user reloads the entry.
        """
        for unsubscribe in self._unsubscribes:
            unsubscribe()

        self._unsubscribes.clear()

        if self._window.cancel_grace is not None:
            self._window.cancel_grace()

    # end async_stop



    @callback
    def _open_window(self, import_price: float | None) -> None:
        """Start a fresh window: new WindowState, new grace timer."""
        if self._window.cancel_grace is not None:
            self._window.cancel_grace()
        # end if

        self._window = WindowState(import_price=import_price)
        # TODO: read the export price and the curtailment source tentatively
        # here. Export price: the sensor in dynamic mode (plan:
        # on_window_open), CONF_EXPORT_TARIFF in fixed mode.
        # With no source configured, lock it as False straight away so the
        # window never waits on it.

        self._window.cancel_grace = async_call_later(
            self._hass,
            EXPORT_PRICE_GRACE_SECONDS,
            self._async_grace_elapsed
        )
    # end _open_window


    # -- persistence -------------------------------------------------------

    async def _async_restore(self) -> None:
        """Load this hub's store, if it has one.

        IMPLEMENT ME.

        await self._store.async_load() returns None on the very first start,
        when no store exists yet: nothing to restore, and the plan's
        first-window rules apply. Otherwise put the totals, the snapshots and
        the in-progress window back (None back to UNSET where it applies).

        The configuration may have changed while the hub was stopped.
        Reconcile per the table in the plan's Persistence section:
          - totals of devices whose subentry id no longer exists: drop them.
            New devices simply have no totals yet.
          - snapshots: keep only those whose entity id is still a tracked
            sensor. A swapped or new sensor then has no snapshot, so its
            first reading sets a fresh baseline.
          - the in-progress window: restore it only if the stored copy of
            the WINDOW_SETTINGS_KEYS settings equals the current config.
            Otherwise discard it and let the first-window rules apply --
            this is also how a switch of tariff type is picked up. Totals
            are always kept.
        """
        raise NotImplementedError
    # end _async_restore

    @callback
    def _schedule_save(self) -> None:
        """Ask for a save soon. Call after every change worth surviving a restart.

        IMPLEMENT ME.

        Use self._store.async_delay_save() with self._data_to_save and
        SAVE_DELAY_SECONDS. Calls within the delay collapse into one write,
        and Home Assistant flushes a pending write at shutdown.
        """
        raise NotImplementedError
    # end _schedule_save

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Everything the store keeps, as plain JSON-friendly values.

        IMPLEMENT ME.

        The totals, every TrackedSensor's snapshot together with the entity
        id it belongs to, the in-progress window, and a copy of the
        WINDOW_SETTINGS_KEYS settings it was opened under (_async_restore
        compares that copy with the config). JSON only knows dicts,
        lists, strings, numbers, booleans and null, so leave out cancel_grace
        (a function) and turn UNSET into None.
        """
        raise NotImplementedError
    # end _data_to_save

    async def async_save_now(self) -> None:
        """Write the store immediately. Called on unload, after async_stop().

        IMPLEMENT ME. One await of self._store.async_save() with
        self._data_to_save(); it also cancels any delayed save still pending.

        Without this, a reload races itself: the old engine's delayed save
        can land after the new engine has already loaded the store, and
        overwrite whatever the new engine wrote.
        """
        raise NotImplementedError
    # end async_save_now

    # -- sensor reading ----------------------------------------------------

    def _read_entity_state(self, entity_id: str) -> float | None:
        """Read one sensor's current numeric value, or None if unavailable.
        
        Fetch the state via self._hass.states.get(entity_id). Return
        None if the state is missing, is STATE_UNKNOWN/STATE_UNAVAILABLE, or
        does not parse as a float.
        """
        entity_value = None
        
        entity_state = self._hass.states.get(entity_id)

        is_available = ((entity_state is not None) and
                        (entity_state.state != STATE_UNAVAILABLE) and
                        (entity_state.state != STATE_UNKNOWN))            

        if is_available:
            try:
                entity_value = float(entity_state.state)

                if not math.isfinite(entity_value):
                    return None
                # end if
            except (ValueError, TypeError):
                return None # parse failed
            # end try
        # end if

        return entity_value
    # end _read_entity_state

    def _read_sensor(self, tracked: TrackedSensor) -> float | None:
        """Read one sensor's current numeric value, or None if unavailable.
        
        Also handle the edge-triggered logging here: log a warning the first
        time a sensor goes unavailable and an
        info line when it recovers -- never once per window, or a sensor that
        stays down for hours floods the log.
        """
        value = self._read_entity_state(tracked.entity_id)

        if value is not None:
            if tracked.unavailable_logged:
                _LOGGER.info(
                    "%s recovered after being unavailable",
                    tracked.entity_id
                )

                tracked.unavailable_logged = False
            # end if
        else:
            if not tracked.unavailable_logged:
                _LOGGER.warning(
                    "%s unavailable, treating as 0 kWh until it recovers", 
                    tracked.entity_id
                )

                tracked.unavailable_logged = True
            # end if
        # end if
        
        return value
    # end _read_sensor

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
    # end _collect_deltas

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
    # end _aggregate

    # -- window boundaries -------------------------------------------------

    @callback
    def _async_import_price_changed(self, event: Any) -> None:
        """Handle a state change on the import price sensor (dynamic mode).

        IMPLEMENT ME.

            - unavailable/unparseable new value -> return, do nothing at all.
              The window simply stays open (per the plan, an unavailable price
              is treated as 'no change', never as a boundary).
            - no window open yet (first start, price was unavailable) -> open
              the first window with this price. Nothing to close.
            - value equal to the current window's import price -> return.
            - otherwise -> this is a real boundary: store the price as
              self._window.next_import_price and call the boundary. If the
              window is already held open waiting on a lock, this just
              updates next_import_price -- the latest price wins.
        """
        raise NotImplementedError
    # end _async_import_price_changed

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
    # end _async_export_price_changed

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
    # end _async_grace_elapsed

    @callback
    def _lock_export_price(self, price: float) -> None:
        """Fix the export price for the window in progress.

        IMPLEMENT ME.

        Store it on self._window, call self._schedule_save() (a locked price
        must survive a restart), and if self._window.close_pending (and
        curtailment has locked too), close the window now.
        """
        raise NotImplementedError
    # end _lock_export_price

    @callback
    def _async_curtailment_changed(self, event: Any) -> None:
        """Handle a state change on the curtailment source (either tariff mode).

        IMPLEMENT ME.

        The mirror image of _async_export_price_changed:

            - "on" -> True, "off" -> False; anything else (unavailable,
              unknown) -> return. Never guess a curtailment state.
            - store it as self._window.tentative_curtailed.
            - if the grace period has already elapsed and nothing was locked
              yet, lock immediately (late-arrival path).
        """
        raise NotImplementedError
    # end _async_curtailment_changed

    @callback
    def _lock_curtailment(self, curtailed: bool) -> None:
        """Fix the curtailment state for the window in progress.

        IMPLEMENT ME.

        Store it as self._window.locked_curtailed, call
        self._schedule_save(), and if self._window.close_pending (and the
        export price has locked too), close the window now.
        """
        raise NotImplementedError
    # end _lock_curtailment

    async def _async_boundary_reached(self, _now: Any = None) -> None:
        """Close the current window and open the next one.

        IMPLEMENT ME.

        Order matters:
            1. If the export price or the curtailment state has not locked
               for this window yet, DO NOT close: set close_pending and
               return. Leave the snapshots untouched so energy keeps
               accumulating into the extended window, and let whichever lock
               arrives last finish the job.
            2. deltas = self._collect_deltas()
            3. energy = self._aggregate(deltas)
            4. run calc.calculate_energy_splits / calculate_financial_splits
               at this window's import price and locked export price, with
               treat_solar_as_free = the hub's CONF_TREAT_SOLAR_AS_FREE
               checkbox OR this window's locked_curtailed
            5. per-device counters (plan: entities, device-level): every
               device's energy per sensor, including GENERAL devices; value
               only for GENERAL devices, via calc.calculate_device_value()
               at this window's import price
            6. add the results to self._totals, then tell whatever the
               sensor platform registered (see async_add_listener below)
            7. open the fresh window via self._open_window(): at
               next_import_price in dynamic mode, CONF_IMPORT_TARIFF in fixed
            8. self._schedule_save()
        """
        raise NotImplementedError
    # end _async_boundary_reached

    # -- plumbing for the sensor platform ----------------------------------

    @callback
    def get_total(self, key: str) -> float:
        """Current cumulative total for one key, for the sensor platform."""
        return self._totals.get(key, 0.0)
    # end get_total

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity to be told when a window has been settled.

        IMPLEMENT ME.

        Keep a list of callbacks; return a function that removes the one just
        added. This is the standard Home Assistant pattern for letting many
        entities follow one data source without each of them polling.
        """
        raise NotImplementedError
    # end async_add_listener
# end WindowEngine
