"""Window engine for Dynamic Cost.

Owns the snapshot/delta bookkeeping and decides when a tracking window opens
and closes. This is the only place that talks to both Home Assistant state
and the calculation engine.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import calc
from .calc import WindowEnergy
from .const import (
    CONF_CURTAILMENT_SOURCE,
    CONF_EXPORT_PRICE_SENSOR,
    CONF_EXPORT_TARIFF,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_IMPORT_PRICE_SENSOR,
    CONF_IMPORT_TARIFF,
    CONF_STALE_WINDOW_MINUTES,
    CONF_TARIFF_MODE,
    CONF_TREAT_SOLAR_AS_FREE,
    CONF_WINDOW_MINUTES,
    DEFAULT_STALE_WINDOW_MINUTES,
    DOMAIN,
    ENERGY_SPLIT_KEYS,
    EXPORT_PRICE_GRACE_SECONDS,
    FINANCIAL_SPLIT_KEYS,
    SAVE_DELAY_SECONDS,
    STORAGE_VERSION,
    WINDOW_SETTINGS_KEYS,
    DeviceType,
    TariffMode,
    WindowStatus,
    unique_id,
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

    Built in __init__.py from entry.subentries. Which sensor fields are set
    depends on device_type: GENERAL has only import_sensor, SOLAR only
    export_sensor, BATTERY both.
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
    """Everything the engine knows about the window currently in progress.

    import_price is None exactly when no window is open: on a first start
    whose import price sensor had no reading yet.
    """

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
    # When the window opened (UTC). Saved, so a restart doesn't reset the
    # stale-window countdown.
    opened_at: datetime | None = None
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
        flow); devices holds its subentries. self._sensors has one
        TrackedSensor per sensor read at a boundary: the hub's two grid
        sensors plus every sensor of every device. entry_id is the hub's
        config entry id, which names its store.
        """
        self._hass = hass
        self._entry_id = entry_id
        self._config = config
        self._devices = devices
        self._sensors: dict[str, TrackedSensor] = {}
        for entity_id in (
            config.get(CONF_GRID_IMPORT_SENSOR),
            config.get(CONF_GRID_EXPORT_SENSOR),
            *(device.import_sensor for device in devices),
            *(device.export_sensor for device in devices),
        ):
            if entity_id:
                self._sensors[entity_id] = TrackedSensor(entity_id)
            # end if
        # end for
        self._window = WindowState()
        self._unsubscribes: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        # Cumulative totals, keyed by the unique id of the entity that shows
        # each one: const.unique_id(self._entry_id, split key) for the hub's
        # splits, const.unique_id(subentry id, counter key) for a device's
        # counters (DEVICE_COUNTER_KEYS). The sensor platform reads them with
        # get_total(its own unique id), and the tests read them the same way.
        self._totals: dict[str, float] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry_id)
        )
        self._last_closed_at: datetime | None = None
        # Dynamic mode's stale-window warning (plan: logging). One timer runs
        # at a time, counting from when the open window opened, or from hub
        # start while there's no window yet. _overdue is True once it fired,
        # until the next window opens.
        self._cancel_overdue: Callable[[], None] | None = None
        self._overdue = False
    # end __init__

    # -- small helpers -----------------------------------------------------

    @property
    def _fixed(self) -> bool:
        return self._config.get(CONF_TARIFF_MODE) == TariffMode.FIXED
    # end _fixed

    @property
    def _window_open(self) -> bool:
        return self._window.import_price is not None
    # end _window_open

    def _curtailment_source(self) -> str | None:
        """The curtailment entity, or None when curtailment isn't tracked.

        The config flow never saves a source with the checkbox on, but a hub
        edited by hand might still have one; the checkbox wins.
        """
        if self._config.get(CONF_TREAT_SOLAR_AS_FREE):
            return None
        # end if
        return self._config.get(CONF_CURTAILMENT_SOURCE)
    # end _curtailment_source

    def _window_settings(self) -> dict[str, Any]:
        return {key: self._config.get(key) for key in WINDOW_SETTINGS_KEYS}
    # end _window_settings

    def _stale_minutes(self) -> float:
        return self._config.get(CONF_STALE_WINDOW_MINUTES, DEFAULT_STALE_WINDOW_MINUTES)
    # end _stale_minutes

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        """Register listeners/timers and open the first window if possible.

        First of all, restore the store. If that put an in-progress window
        back, it simply carries on: its grace timer didn't survive the
        restart, so its grace period counts as over (plan: Persistence), and
        whatever it holds locks now.

        Otherwise the first-window rules apply. Fixed mode: open the first
        window right away, at CONF_IMPORT_TARIFF. Dynamic mode: read the
        import price sensor. A real reading opens the first window at that
        price; no reading means no window yet, and
        _async_import_price_changed opens the first one when a real price
        arrives. (Plan: window engine, first window.)
        """
        restored = await self._async_restore()

        match self._config.get(CONF_TARIFF_MODE):
            case TariffMode.DYNAMIC:
                self._unsubscribes.append(
                    async_track_state_change_event(
                        self._hass,
                        self._config[CONF_IMPORT_PRICE_SENSOR],
                        self._async_import_price_changed,
                    )
                )

                self._unsubscribes.append(
                    async_track_state_change_event(
                        self._hass,
                        self._config[CONF_EXPORT_PRICE_SENSOR],
                        self._async_export_price_changed,
                    )
                )
            # end case DYNAMIC

            case TariffMode.FIXED:
                self._unsubscribes.append(
                    async_track_time_interval(
                        self._hass,
                        self._async_boundary_reached,
                        timedelta(minutes=self._config[CONF_WINDOW_MINUTES]),
                    )
                )
            # end case FIXED

            case _:
                raise ValueError(
                    f"Unknown tariff mode {self._config.get(CONF_TARIFF_MODE)!r}"
                )
            # end case _
        # end match

        # Curtailment is relevant for both contract types.
        curtailment_source = self._curtailment_source()

        if curtailment_source is not None:
            self._unsubscribes.append(
                async_track_state_change_event(
                    self._hass,
                    curtailment_source,
                    self._async_curtailment_changed,
                )
            )
        # end if

        if restored:
            self._window.grace_elapsed = True
            self._start_overdue_timer(self._window.opened_at)
            self._lock_tentative_values()
            self._close_if_pending()
            return
        # end if

        if self._fixed:
            self._open_window(self._config[CONF_IMPORT_TARIFF], first=True)
        else:
            import_price = self._read_entity_state(
                self._config[CONF_IMPORT_PRICE_SENSOR]
            )
            if import_price is not None:
                self._open_window(import_price, first=True)
            else:
                # No window yet: count from now, so a price sensor that
                # never reports gets a warning too.
                self._start_overdue_timer(dt_util.utcnow())
            # end if
        # end if
    # end async_start

    @callback
    def async_stop(self) -> None:
        """Tear down every listener and timer.

        Getting this right is what stops duplicate listeners piling up when
        the user reloads the entry.
        """
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        # end for

        self._unsubscribes.clear()

        if self._window.cancel_grace is not None:
            self._window.cancel_grace()
            self._window.cancel_grace = None
        # end if

        if self._cancel_overdue is not None:
            self._cancel_overdue()
            self._cancel_overdue = None
        # end if
    # end async_stop

    @callback
    def _open_window(self, import_price: float, *, first: bool = False) -> None:
        """Start a fresh window: new WindowState, new grace timer.

        The export price and curtailment state are read tentatively here and
        lock when the grace period ends (plan: window engine). first=True
        marks the hub's first window, which sets the baseline snapshot of
        every sensor that has none yet.
        """
        if self._window.cancel_grace is not None:
            self._window.cancel_grace()
        # end if

        self._window = WindowState(
            import_price=import_price, opened_at=dt_util.utcnow()
        )
        self._start_overdue_timer(self._window.opened_at)

        if self._fixed:
            self._window.tentative_export_price = self._config[CONF_EXPORT_TARIFF]
        else:
            export_price = self._read_entity_state(
                self._config[CONF_EXPORT_PRICE_SENSOR]
            )
            if export_price is not None:
                self._window.tentative_export_price = export_price
            # end if
        # end if

        curtailment_source = self._curtailment_source()
        if curtailment_source is None:
            # Never curtailed: lock straight away so the window never waits.
            self._window.locked_curtailed = False
        else:
            curtailed = self._read_on_off(curtailment_source)
            if curtailed is not None:
                self._window.tentative_curtailed = curtailed
            # end if
        # end if

        if first:
            for tracked in self._sensors.values():
                if tracked.snapshot is None:
                    tracked.snapshot = self._read_sensor(tracked)
                # end if
            # end for
        # end if

        self._window.cancel_grace = async_call_later(
            self._hass,
            EXPORT_PRICE_GRACE_SECONDS,
            self._async_grace_elapsed,
        )
        self._changed()
    # end _open_window

    # -- stale-window warning ----------------------------------------------

    @callback
    def _start_overdue_timer(self, since: datetime) -> None:
        """(Re)start the stale-window countdown from since (dynamic mode only).

        Fixed windows close on a timer, so they can't go stale on a price.
        A since already more than the timeout ago (a restored window) fires
        straight away.
        """
        if self._cancel_overdue is not None:
            self._cancel_overdue()
            self._cancel_overdue = None
        # end if
        self._overdue = False

        if self._fixed:
            return
        # end if

        self._cancel_overdue = async_track_point_in_utc_time(
            self._hass,
            self._async_overdue,
            since + timedelta(minutes=self._stale_minutes()),
        )
    # end _start_overdue_timer

    @callback
    def _async_overdue(self, _now: Any) -> None:
        """The window outlived the stale-window timeout: warn once, saying why.

        Edge-triggered like the unavailable-sensor warnings: one warning
        when a window goes overdue, one info line when it finally closes.
        """
        self._cancel_overdue = None
        self._overdue = True
        minutes = int(self._stale_minutes())
        window = self._window

        if not self._window_open:
            _LOGGER.warning(
                "No tracking window after %d minutes: %s has not reported a "
                "usable import price yet. Nothing is tracked until it does",
                minutes,
                self._config[CONF_IMPORT_PRICE_SENSOR],
            )
        elif window.close_pending:
            missing = []
            if window.locked_export_price is None:
                missing.append(
                    f"an export price from {self._config[CONF_EXPORT_PRICE_SENSOR]}"
                )
            # end if
            if window.locked_curtailed is None:
                missing.append(f"an on/off state from {self._curtailment_source()}")
            # end if
            _LOGGER.warning(
                "Tracking window open for over %d minutes: held open, waiting "
                "for %s",
                minutes,
                " and ".join(missing),
            )
        else:
            _LOGGER.warning(
                "Tracking window open for over %d minutes: import price %s has "
                "stayed at %s since %s. Check that it is the right sensor",
                minutes,
                self._config[CONF_IMPORT_PRICE_SENSOR],
                window.import_price,
                dt_util.as_local(window.opened_at).strftime("%Y-%m-%d %H:%M"),
            )
        # end if

        self._notify()
    # end _async_overdue

    # -- persistence -------------------------------------------------------

    async def _async_restore(self) -> bool:
        """Load this hub's store, if it has one.

        Returns True if an in-progress window was restored, False if not --
        including when totals and snapshots were restored but the window was
        discarded because its settings changed. async_start opens a new
        window exactly when this returns False.

        The configuration may have changed while the hub was stopped, so the
        store is reconciled per the table in the plan's Persistence section:
          - totals of devices whose subentry id no longer exists are dropped
            (a total's key starts with its owner's id);
          - only snapshots of sensors still tracked are kept, so a swapped or
            new sensor sets a fresh baseline on its first reading;
          - the in-progress window is restored only if the WINDOW_SETTINGS_KEYS
            settings it was opened under still match the config. That is
            also how a switch of tariff type is picked up.
        """
        data = await self._store.async_load()
        if not data:
            return False
        # end if

        owners = {device.subentry_id for device in self._devices} | {self._entry_id}
        for key, value in data.get("totals", {}).items():
            if key.split("_", 1)[0] in owners:
                self._totals[key] = value
            # end if
        # end for

        for entity_id, snapshot in data.get("snapshots", {}).items():
            if entity_id in self._sensors:
                self._sensors[entity_id].snapshot = snapshot
            # end if
        # end for

        if data.get("last_closed_at"):
            self._last_closed_at = dt_util.parse_datetime(data["last_closed_at"])
        # end if

        window = data.get("window")
        if not window or data.get("settings") != self._window_settings():
            return False
        # end if

        def unset_if_none(value: Any) -> Any:
            return UNSET if value is None else value
        # end unset_if_none

        # Stores written before opened_at existed: count from now.
        opened_at = None
        if window.get("opened_at"):
            opened_at = dt_util.parse_datetime(window["opened_at"])
        # end if

        self._window = WindowState(
            import_price=window["import_price"],
            tentative_export_price=unset_if_none(window["tentative_export_price"]),
            locked_export_price=window["locked_export_price"],
            tentative_curtailed=unset_if_none(window["tentative_curtailed"]),
            locked_curtailed=window["locked_curtailed"],
            grace_elapsed=window["grace_elapsed"],
            close_pending=window["close_pending"],
            next_import_price=window["next_import_price"],
            opened_at=opened_at or dt_util.utcnow(),
        )
        return True
    # end _async_restore

    @callback
    def _schedule_save(self) -> None:
        """Ask for a save soon. Called after every change worth surviving a restart.

        Calls within SAVE_DELAY_SECONDS collapse into one write, and Home
        Assistant flushes a pending write at shutdown.
        """
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)
    # end _schedule_save

    @callback
    def _notify(self) -> None:
        """Tell every entity to refresh: a total or the window's state moved."""
        for update_callback in list(self._listeners):
            update_callback()
        # end for
    # end _notify

    @callback
    def _changed(self) -> None:
        """Something worth surviving a restart changed: save it and show it."""
        self._schedule_save()
        self._notify()
    # end _changed

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Everything the store keeps, as plain JSON-friendly values.

        JSON only knows dicts, lists, strings, numbers, booleans and null, so
        cancel_grace (a function) is left out and UNSET becomes None.
        """
        window = None
        if self._window_open:
            state = self._window

            def none_if_unset(value: Any) -> Any:
                return None if value is UNSET else value
            # end none_if_unset

            window = {
                "import_price": state.import_price,
                "tentative_export_price": none_if_unset(state.tentative_export_price),
                "locked_export_price": state.locked_export_price,
                "tentative_curtailed": none_if_unset(state.tentative_curtailed),
                "locked_curtailed": state.locked_curtailed,
                "grace_elapsed": state.grace_elapsed,
                "close_pending": state.close_pending,
                "next_import_price": state.next_import_price,
                "opened_at": state.opened_at.isoformat(),
            }
        # end if

        last_closed_at = None
        if self._last_closed_at is not None:
            last_closed_at = self._last_closed_at.isoformat()
        # end if

        return {
            "totals": dict(self._totals),
            "last_closed_at": last_closed_at,
            "snapshots": {
                entity_id: tracked.snapshot
                for entity_id, tracked in self._sensors.items()
                if tracked.snapshot is not None
            },
            "window": window,
            "settings": self._window_settings(),
        }
    # end _data_to_save

    async def async_save_now(self) -> None:
        """Write the store immediately. Called on unload, after async_stop().

        This also cancels any delayed save still pending. Without it, a reload
        races itself: the old engine's delayed save can land after the new
        engine has already loaded the store, and overwrite it.
        """
        await self._store.async_save(self._data_to_save())
    # end async_save_now

    # -- sensor reading ----------------------------------------------------

    def _read_entity_state(self, entity_id: str) -> float | None:
        """Read one sensor's current numeric value, or None if unavailable.

        None if the entity is missing, is STATE_UNKNOWN/STATE_UNAVAILABLE, or
        its state does not parse as a finite number.
        """
        entity_state = self._hass.states.get(entity_id)

        is_available = (
            entity_state is not None
            and entity_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        )
        if not is_available:
            return None
        # end if

        try:
            entity_value = float(entity_state.state)
        except (ValueError, TypeError):
            return None  # parse failed
        # end try

        if not math.isfinite(entity_value):
            return None
        # end if

        return entity_value
    # end _read_entity_state

    def _read_on_off(self, entity_id: str) -> bool | None:
        """Read an on/off entity: True, False, or None if neither (never guess)."""
        entity_state = self._hass.states.get(entity_id)
        if entity_state is None:
            return None
        # end if

        # An if-chain, not match: in a case pattern a bare name like STATE_ON
        # would capture the value instead of comparing against the constant.
        if entity_state.state == STATE_ON:
            return True
        elif entity_state.state == STATE_OFF:
            return False
        else:
            return None
        # end if
    # end _read_on_off

    def _read_sensor(self, tracked: TrackedSensor) -> float | None:
        """Read one sensor's current numeric value, or None if unavailable.

        Logging is edge-triggered: a warning the first time a sensor goes
        unavailable and an info line when it recovers -- never once per
        window, or a sensor that stays down for hours floods the log.
        """
        value = self._read_entity_state(tracked.entity_id)

        if value is not None:
            if tracked.unavailable_logged:
                _LOGGER.info(
                    "%s recovered after being unavailable",
                    tracked.entity_id,
                )

                tracked.unavailable_logged = False
            # end if
        else:
            if not tracked.unavailable_logged:
                _LOGGER.warning(
                    "%s unavailable, treating as 0 kWh until it recovers",
                    tracked.entity_id,
                )

                tracked.unavailable_logged = True
            # end if
        # end if

        return value
    # end _read_sensor

    def _collect_deltas(self) -> dict[str, float]:
        """Read every tracked sensor once and turn it into this window's delta.

        Each sensor is read exactly once per boundary, and that one reading
        serves both the subtraction and the new snapshot. Reading twice would
        let energy that arrives between the two reads vanish from the
        accounting, window after window.

        An unavailable sensor contributes 0 and keeps its snapshot, so its
        next good reading spans the whole gap. A sensor without a snapshot
        contributes 0 and takes its reading as the baseline. A reading below
        the snapshot is a counter reset: 0 for this window, and the snapshot
        moves to the new reading. Keyed by entity id.
        """
        deltas: dict[str, float] = {}

        for entity_id, tracked in self._sensors.items():
            reading = self._read_sensor(tracked)      # THE ONE AND ONLY READ

            if reading is None:
                deltas[entity_id] = 0.0
                continue
            # end if

            if tracked.snapshot is None:
                delta = 0.0
            else:
                delta = max(0.0, reading - tracked.snapshot)
            # end if

            tracked.snapshot = reading                # reuse the SAME value
            deltas[entity_id] = delta
        # end for

        return deltas
    # end _collect_deltas

    def _aggregate(self, deltas: dict[str, float]) -> WindowEnergy:
        """Sum per-device deltas into the hub-level categories.

        GENERAL devices are deliberately excluded -- they are pure
        sub-metering and must not influence the splits.
        """
        solar = 0.0
        battery_charge = 0.0
        battery_discharge = 0.0

        for device in self._devices:
            match device.device_type:
                case DeviceType.SOLAR:
                    solar += deltas[device.export_sensor]
                case DeviceType.BATTERY:
                    battery_charge += deltas[device.import_sensor]
                    battery_discharge += deltas[device.export_sensor]
                case DeviceType.GENERAL:
                    pass
            # end match
        # end for

        return WindowEnergy(
            solar=solar,
            grid_import=deltas.get(self._config.get(CONF_GRID_IMPORT_SENSOR), 0.0),
            grid_export=deltas.get(self._config.get(CONF_GRID_EXPORT_SENSOR), 0.0),
            battery_charge=battery_charge,
            battery_discharge=battery_discharge,
        )
    # end _aggregate

    # -- window boundaries -------------------------------------------------

    @callback
    def _async_import_price_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle a state change on the import price sensor (dynamic mode).

        An unavailable price is 'no change', never a boundary. Without a
        window yet (first start), the first real price opens one. While a
        window is held open waiting on a lock, a new price only updates the
        price the next window opens with -- the latest price wins.
        """
        price = self._read_entity_state(event.data["entity_id"])
        if price is None:
            return
        # end if

        if not self._window_open:
            self._open_window(price, first=True)
            return
        # end if

        if self._window.close_pending:
            self._window.next_import_price = price
            return
        # end if

        if price == self._window.import_price:
            return
        # end if

        self._window.next_import_price = price
        self._boundary()
    # end _async_import_price_changed

    @callback
    def _async_export_price_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle a state change on the export price sensor (dynamic mode).

        Within the grace period this updates the window's tentative price.
        After it, if nothing locked yet, the price locks immediately -- the
        late-arrival path that lets a recovering sensor unblock a window.
        """
        price = self._read_entity_state(event.data["entity_id"])
        if price is None or not self._window_open:
            return
        # end if

        self._window.tentative_export_price = price
        if self._window.grace_elapsed and self._window.locked_export_price is None:
            self._window.locked_export_price = price
            self._changed()
            self._close_if_pending()
        # end if
    # end _async_export_price_changed

    @callback
    def _async_curtailment_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle a state change on the curtailment source (either tariff mode).

        The mirror image of _async_export_price_changed. Anything but on/off
        (unavailable, unknown) is ignored: never guess a curtailment state.
        """
        curtailed = self._read_on_off(event.data["entity_id"])
        if curtailed is None or not self._window_open:
            return
        # end if

        self._window.tentative_curtailed = curtailed
        if self._window.grace_elapsed and self._window.locked_curtailed is None:
            self._window.locked_curtailed = curtailed
            self._changed()
            self._close_if_pending()
        # end if
    # end _async_curtailment_changed

    @callback
    def _async_grace_elapsed(self, _now: Any) -> None:
        """Fire EXPORT_PRICE_GRACE_SECONDS after the window opened.

        Locks whatever tentative values exist. A missing one does NOT lock:
        the grace period is the earliest a value may lock, not a deadline.
        """
        self._window.cancel_grace = None
        self._window.grace_elapsed = True
        self._lock_tentative_values()
        self._close_if_pending()
    # end _async_grace_elapsed

    @callback
    def _lock_tentative_values(self) -> None:
        """Lock the tentative export price and curtailment state, where captured."""
        window = self._window

        if (
            window.locked_export_price is None
            and window.tentative_export_price is not UNSET
        ):
            window.locked_export_price = window.tentative_export_price
        # end if

        if (
            window.locked_curtailed is None
            and window.tentative_curtailed is not UNSET
        ):
            window.locked_curtailed = window.tentative_curtailed
        # end if

        self._changed()
    # end _lock_tentative_values

    def _is_locked(self) -> bool:
        return (
            self._window.locked_export_price is not None
            and self._window.locked_curtailed is not None
        )
    # end _is_locked

    @callback
    def _close_if_pending(self) -> None:
        """Close a held-open window once its last missing lock has arrived."""
        if self._window.close_pending and self._is_locked():
            self._close_window()
        # end if
    # end _close_if_pending

    async def _async_boundary_reached(self, _now: Any = None) -> None:
        """The fixed-mode window timer fired."""
        self._boundary()
    # end _async_boundary_reached

    @callback
    def _boundary(self) -> None:
        """A window boundary: close the window, or hold it open for a lock.

        If the export price or the curtailment state hasn't locked yet, the
        window is NOT closed. The snapshots stay untouched, so energy keeps
        accumulating into the extended window, and whichever lock arrives
        last closes it.
        """
        if not self._window_open:
            return
        # end if

        if not self._is_locked():
            self._window.close_pending = True
            self._changed()
            return
        # end if

        self._close_window()
    # end _boundary

    @callback
    def _close_window(self) -> None:
        """Settle the window in progress, then open the next one."""
        window = self._window
        deltas = self._collect_deltas()
        energy = self._aggregate(deltas)
        treat_solar_as_free = bool(
            self._config.get(CONF_TREAT_SOLAR_AS_FREE) or window.locked_curtailed
        )

        splits = calc.calculate_energy_splits(energy)
        money = calc.calculate_financial_splits(
            splits,
            energy,
            import_price=window.import_price,
            export_price=window.locked_export_price,
            treat_solar_as_free=treat_solar_as_free,
        )

        for key in ENERGY_SPLIT_KEYS:
            self._add(unique_id(self._entry_id, key), getattr(splits, key))
        # end for
        for key in FINANCIAL_SPLIT_KEYS:
            self._add(unique_id(self._entry_id, key), getattr(money, key))
        # end for

        # Device counters: energy per sensor for every device, value only for
        # general devices (plan: entities, device-level).
        for device in self._devices:
            if device.import_sensor:
                energy_in = deltas[device.import_sensor]
                self._add(unique_id(device.subentry_id, "import_energy"), energy_in)
                if device.device_type == DeviceType.GENERAL:
                    self._add(
                        unique_id(device.subentry_id, "import_value"),
                        calc.calculate_device_value(energy_in, window.import_price),
                    )
                # end if
            # end if
            if device.export_sensor:
                self._add(
                    unique_id(device.subentry_id, "export_energy"),
                    deltas[device.export_sensor],
                )
            # end if
        # end for

        self._last_closed_at = dt_util.utcnow()
        if self._overdue:
            _LOGGER.info(
                "Tracking window closed after %d minutes",
                (self._last_closed_at - window.opened_at).total_seconds() // 60,
            )
        # end if

        # Opening the next window saves everything and refreshes the entities.
        if self._fixed:
            self._open_window(self._config[CONF_IMPORT_TARIFF])
        else:
            self._open_window(window.next_import_price)
        # end if
    # end _close_window

    def _add(self, key: str, value: float) -> None:
        self._totals[key] = self._totals.get(key, 0.0) + value
    # end _add

    # -- plumbing for the sensor platform ----------------------------------

    @callback
    def get_total(self, key: str) -> float:
        """Current cumulative total for one unique id (see self._totals)."""
        return self._totals.get(key, 0.0)
    # end get_total

    # Read-only views of the window, for the hub's diagnostic sensors. None
    # means "not known yet": no window, or nothing locked.

    @property
    def status(self) -> WindowStatus:
        """No window yet, held open for a lock, overdue, or simply open.

        Held open wins over overdue: it names the reason the window is late.
        """
        if not self._window_open:
            return WindowStatus.NO_WINDOW
        # end if
        if self._window.close_pending:
            return WindowStatus.HELD_OPEN
        # end if
        if self._overdue:
            return WindowStatus.OVERDUE
        # end if
        return WindowStatus.OPEN
    # end status

    @property
    def tracks_curtailment(self) -> bool:
        return self._curtailment_source() is not None
    # end tracks_curtailment

    @property
    def window_import_price(self) -> float | None:
        return self._window.import_price
    # end window_import_price

    @property
    def window_export_price(self) -> float | None:
        return self._window.locked_export_price
    # end window_export_price

    @property
    def window_curtailed(self) -> bool | None:
        if not self._window_open:
            return None
        # end if
        return self._window.locked_curtailed
    # end window_curtailed

    @property
    def window_opened_at(self) -> datetime | None:
        return self._window.opened_at
    # end window_opened_at

    @property
    def last_window_closed_at(self) -> datetime | None:
        return self._last_closed_at
    # end last_window_closed_at

    @callback
    def async_add_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register an entity to be told when a window has been settled.

        Returns a function that removes it again: the standard Home Assistant
        pattern for letting many entities follow one data source without
        each of them polling.
        """
        self._listeners.append(update_callback)

        def remove() -> None:
            self._listeners.remove(update_callback)
        # end remove

        return remove
    # end async_add_listener
# end WindowEngine
