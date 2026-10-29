"""Check that the test suite catches real bugs, not just that it passes.

Run from the repository root:  .venv/bin/python scripts/mutation_check.py

Copies the project into a temporary directory, runs the whole suite there
(it must pass), then plants each bug below one at a time; every one must make
the suite fail. A bug that slips through means a test can't fail when it
should. STALE means a planting point no longer matches the code: update it.
BROKEN means the planted bug isn't valid Python, so it would fail every test
without proving anything: fix its replacement text.

Nothing in the project itself is touched.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT / ".venv" / "bin" / "python"
PKG = "custom_components/dynamic_cost/"
WINDOW = PKG + "window.py"
FLOW = PKG + "config_flow.py"
SENSOR = PKG + "sensor.py"
UNITS = PKG + "units.py"
CALC = PKG + "calc.py"

# name: (file, exact original text, replacement)
MUTANTS: dict[str, tuple[str, str, str]] = {
    "house's solar part at the import price again": (
        CALC,
        "splits.solar_to_house * solar_rate   +",
        "splits.solar_to_house * import_price +",
    ),
    "house's battery part at the import price": (
        CALC,
        "splits.battery_to_house * battery_rate)",
        "splits.battery_to_house * import_price)",
    ),
    "device valued at the import price": (
        CALC,
        "return energy_delta * money.house_energy_usage_cost / house",
        "return energy_delta * import_price",
    ),
    "device without a house mix valued at nothing": (
        CALC,
        "    # end if\n    return energy_delta * import_price\n# end calculate_device_value",
        "    # end if\n    return 0.0\n# end calculate_device_value",
    ),
    "solar never free": (
        CALC,
        "    if treat_solar_as_free:\n        return 0.0",
        "    if False:\n        return 0.0",
    ),
    "ceiling drops the losses' cost too": (
        CALC,
        "kwh = capacity            # cost stays",
        "kwh, cost = capacity, cost * capacity / kwh  # cost stays",
    ),
    "no ceiling at all": (
        CALC,
        "elif capacity is not None and kwh > capacity:",
        "elif False:",
    ),
    "discharge beyond the pool left out": (
        CALC,
        "+ (discharged - known) * import_price) / discharged",
        "+ 0.0) / discharged",
    ),
    "capacity entity not converted to kWh": (
        WINDOW,
        "capacity = units.energy_to_kwh(value, self._unit(capacity))",
        "capacity = value",
    ),
    "battery pool not saved": (
        WINDOW,
        '"battery_pool": {"kwh": self._pool.kwh, "cost": self._pool.cost},',
        '"battery_pool": None,',
    ),
    "battery pool kept without batteries": (
        WINDOW,
        "if pool and self._has_battery:",
        "if pool:",
    ),
    "capacity entity not validated at setup": (
        FLOW,
        "sensors[CONF_CAPACITY] = user_input[CONF_CAPACITY]",
        "pass",
    ),
    "battery energy cost on hubs without a battery": (
        SENSOR,
        "    if DeviceType.BATTERY in device_types:\n        hub_entities.append(",
        "    if True:\n        hub_entities.append(",
    ),
    "energy read without converting to kWh": (
        WINDOW,
        "kwh = units.energy_to_kwh(value, unit)",
        "kwh = value",
    ),
    "price read without converting to per kWh": (
        WINDOW,
        "return units.price_per_kwh(value, self._unit(entity_id))",
        "return value",
    ),
    "non-energy unit counted as kWh": (
        UNITS,
        "    elif unit not in EnergyConverter.VALID_UNITS:\n        return None",
        "    elif unit not in EnergyConverter.VALID_UNITS:\n        return value",
    ),
    "no unit not taken as kWh": (
        UNITS,
        "    if unit is None:\n        return value\n    elif",
        "    if unit is None:\n        return None\n    elif",
    ),
    "price per MWh taken as per kWh": (
        UNITS,
        "return value * EnergyConverter.convert(1.0, KWH, per)",
        "return value",
    ),
    "legacy snapshot taken as kWh": (
        WINDOW,
        "tracked.snapshot = units.energy_to_kwh(tracked.legacy_snapshot, unit)",
        "tracked.snapshot = tracked.legacy_snapshot",
    ),
    "legacy snapshots dropped on restore": (
        WINDOW,
        "self._sensors[entity_id].legacy_snapshot = snapshot",
        "pass",
    ),
    "unconverted legacy snapshot not saved": (
        WINDOW,
        "if tracked.legacy_snapshot is not None\n            },",
        "if False\n            },",
    ),
    "first window overwrites a legacy baseline": (
        WINDOW,
        "if tracked.snapshot is None and tracked.legacy_snapshot is None:",
        "if tracked.snapshot is None:",
    ),
    "energy sensor unit not checked at setup": (
        FLOW,
        "elif field in ENERGY_SENSOR_FIELDS and not units.is_energy_unit(",
        "elif False and not units.is_energy_unit(",
    ),
    "device sensors not unit-checked at setup": (
        FLOW,
        "    CONF_IMPORT_SENSOR,\n    CONF_EXPORT_SENSOR,\n    CONF_CAPACITY,\n)",
        "    CONF_CAPACITY,\n)",
    ),
    "negative delta not clamped (counter reset)": (
        WINDOW,
        "delta = max(0.0, reading - tracked.snapshot)",
        "delta = reading - tracked.snapshot",
    ),
    "window closes without an export price": (
        WINDOW,
        "        if not self._is_locked():\n            self._window.close_pending = True",
        "        if False:\n            self._window.close_pending = True",
    ),
    "restored window thrown away": (
        WINDOW,
        'if not window or data.get("settings") != self._window_settings():',
        "if True:",
    ),
    "restored grace not treated as over": (
        WINDOW,
        "        if restored:\n            self._window.grace_elapsed = True\n"
        "            self._start_overdue_timer(self._window.opened_at)\n"
        "            self._lock_tentative_values()\n"
        "            self._close_if_pending()\n            return",
        "        if restored:\n            return",
    ),
    "curtailment ignored": (
        WINDOW,
        "self._config.get(CONF_TREAT_SOLAR_AS_FREE) or window.locked_curtailed",
        "self._config.get(CONF_TREAT_SOLAR_AS_FREE)",
    ),
    "logs a warning every window": (
        WINDOW,
        "        else:\n            if not tracked.unavailable_logged:",
        "        else:\n            if True:",
    ),
    "general device feeds the splits": (
        WINDOW,
        "                case DeviceType.GENERAL:\n                    pass",
        "                case DeviceType.GENERAL:\n"
        "                    solar += deltas[device.import_sensor]",
    ),
    "unavailable import price is a boundary": (
        WINDOW,
        "        if price is None:\n            return\n        # end if\n\n"
        "        if not self._window_open:\n            self._open_window(price, first=True)",
        "        if price is None:\n            self._boundary()\n            return\n"
        "        # end if\n\n"
        "        if not self._window_open:\n            self._open_window(price, first=True)",
    ),
    "first window opened without a price": (
        WINDOW,
        "            if import_price is not None:\n"
        "                self._open_window(import_price, first=True)",
        "            if True:\n                self._open_window(import_price or 0.0, first=True)",
    ),
    "swapped sensor keeps old snapshot": (
        WINDOW,
        "            if entity_id in self._sensors:\n"
        "                self._sensors[entity_id].snapshot = snapshot",
        "            for tracked in self._sensors.values():\n"
        "                if tracked.snapshot is None:\n"
        "                    tracked.snapshot = snapshot",
    ),
    "no save on unload": (
        WINDOW,
        "        await self._store.async_save(self._data_to_save())",
        "        pass",
    ),
    "reconfigure reloads twice": (
        FLOW,
        "            self.hass.config_entries.async_update_entry(\n"
        "                self._get_reconfigure_entry(), data=self._data\n            )\n"
        '            return self.async_abort(reason="reconfigure_successful")',
        "            return self.async_update_reload_and_abort(\n"
        "                self._get_reconfigure_entry(), data=self._data\n            )",
    ),
    "currency editable in reconfigure": (
        FLOW,
        "        if not self._reconfiguring:\n            fields[",
        "        if True:\n            fields[",
    ),
    "unavailable sensor is a hard error": (
        FLOW,
        "            unavailable.append(entity_id)",
        '            errors[field] = "unknown_entity"',
    ),
    "entities not refreshed when a window settles": (
        SENSOR,
        "        self.async_on_remove(self._engine.async_add_listener(self.async_write_ha_state))",
        "        pass",
    ),
    "stale-window countdown never started for a window": (
        WINDOW,
        "        )\n        self._start_overdue_timer(self._window.opened_at)\n\n"
        "        if self._fixed:",
        "        )\n\n        if self._fixed:",
    ),
    "no warning while waiting for the first window": (
        WINDOW,
        "                self._start_overdue_timer(dt_util.utcnow())",
        "                pass",
    ),
    "previous window's countdown keeps running": (
        WINDOW,
        "        if self._cancel_overdue is not None:\n"
        "            self._cancel_overdue()\n"
        "            self._cancel_overdue = None\n"
        "        # end if\n"
        "        self._overdue = False",
        "        self._overdue = False",
    ),
    "stale timeout ignores the hub setting": (
        WINDOW,
        "return self._config.get(CONF_STALE_WINDOW_MINUTES, DEFAULT_STALE_WINDOW_MINUTES)",
        "return DEFAULT_STALE_WINDOW_MINUTES",
    ),
    "fixed windows get the stale warning": (
        WINDOW,
        "        if self._fixed:\n            return\n        # end if\n\n"
        "        self._cancel_overdue = ",
        "        self._cancel_overdue = ",
    ),
    "restart resets a window's age": (
        WINDOW,
        "opened_at=opened_at or dt_util.utcnow(),",
        "opened_at=dt_util.utcnow(),",
    ),
    "held-open window shown as merely overdue": (
        WINDOW,
        "        if self._window.close_pending:\n            return WindowStatus.HELD_OPEN",
        "        if False:\n            return WindowStatus.HELD_OPEN",
    ),
    "entities not refreshed on a lock": (
        WINDOW,
        "        self._schedule_save()\n        self._notify()\n    # end _changed",
        "        self._schedule_save()\n    # end _changed",
    ),
    "splits created without their devices": (
        SENSOR,
        "return TOTAL_REQUIRES[key] <= device_types",
        "return True",
    ),
    "hub entities that no longer apply are kept": (
        SENSOR,
        "            registry.async_remove(registered.entity_id)",
        "            pass",
    ),
    "diagnostics on fixed hubs too": (
        SENSOR,
        "    if entry.data[CONF_TARIFF_MODE] == TariffMode.DYNAMIC:",
        "    if True:",
    ),
    "curtailment diagnostic without a source": (
        SENSOR,
        "_diagnostics(currency, engine.tracks_curtailment)",
        "_diagnostics(currency, True)",
    ),
    "timeout field validated as a sensor": (
        FLOW,
        "                    if field in HUB_SENSOR_FIELDS",
        "                    if True",
    ),
    "timeout kept after switching to fixed": (
        FLOW,
        "                    CONF_EXPORT_PRICE_SENSOR,\n"
        "                    CONF_STALE_WINDOW_MINUTES,\n",
        "                    CONF_EXPORT_PRICE_SENSOR,\n",
    ),
    "tentative values hidden until locked": (
        WINDOW,
        "        return None if tentative is UNSET else tentative",
        "        return None",
    ),
    "lock state never says tentative": (
        WINDOW,
        "        return LockState.TENTATIVE\n    # end _lock_state",
        "        return LockState.WAITING\n    # end _lock_state",
    ),
    "tentative export price changes not shown": (
        WINDOW,
        "        self._changed()\n        self._close_if_pending()\n"
        "    # end _async_export_price_changed",
        "        self._close_if_pending()\n    # end _async_export_price_changed",
    ),
    "grid export profit keeps the old cost's sign": (
        PKG + "calc.py",
        "grid_export_profit      = energy.grid_export      *  export_price",
        "grid_export_profit      = energy.grid_export      * -export_price",
    ),
    "old grid export cost dropped on upgrade": (
        WINDOW,
        "        if old_export in self._totals:",
        "        if False:",
    ),
    "old grid export cost carried over without flipping": (
        WINDOW,
        "                -self._totals.pop(old_export),",
        "                self._totals.pop(old_export),",
    ),
    "battery net savings ignores the solar it stored": (
        PKG + "calc.py",
        "                           money.grid_to_battery_cost -\n"
        "                           money.solar_to_battery_cost)",
        "                           money.grid_to_battery_cost)",
    ),
    "summaries never counted": (
        WINDOW,
        "        for key in SUMMARY_ENERGY_KEYS + SUMMARY_FINANCIAL_KEYS:\n"
        "            self._add(",
        "        for key in ():\n            self._add(",
    ),
    "summaries not worked out for an older store": (
        WINDOW,
        "        self._backfill_summaries()\n",
        "",
    ),
    "counted summaries replaced by the splits' version": (
        WINDOW,
        "        if any(unique_id(self._entry_id, key) in self._totals for key in keys):",
        "        if False:",
    ),
    "value counted for solar devices": (
        PKG + "const.py",
        'DeviceType.SOLAR: ("export_energy",)',
        'DeviceType.SOLAR: ("export_energy", "export_value")',
    ),
}


def copy_project(target: Path) -> None:
    for name in ("custom_components", "tests"):
        shutil.copytree(
            PROJECT / name, target / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    # end for
    shutil.copy(PROJECT / "pyproject.toml", target)
# end copy_project


def run_pytest(cwd: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", *extra],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
# end run_pytest


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dynamic-cost-mutants-") as tmp:
        base = Path(tmp) / "base"
        copy_project(base)
        result = run_pytest(base)
        print("unmodified:", result.stdout.strip().splitlines()[-1])
        if result.returncode:
            print("The suite must pass before bugs are planted.")
            return 1
        # end if

        problems = 0
        for name, (path, original, replacement) in MUTANTS.items():
            work = Path(tmp) / "mutant"
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(base, work)
            target = work / path
            text = target.read_text()
            if text.count(original) != 1:
                print(f"STALE   {name}: planting point not found in {path}")
                problems += 1
                continue
            # end if
            mutated = text.replace(original, replacement)
            # A planted bug that isn't valid Python fails every test without
            # proving anything about them.
            try:
                compile(mutated, path, "exec")
            except SyntaxError as error:
                print(f"BROKEN  {name}: not valid Python ({error.msg})")
                problems += 1
                continue
            # end try
            target.write_text(mutated)

            result = run_pytest(work, "-x")
            # ERROR: a test that failed in its teardown, e.g. a leftover timer.
            failed = [
                line.split("::")[-1].split(" ")[0]
                for line in result.stdout.splitlines()
                if line.startswith(("FAILED", "ERROR"))
            ]
            caught = result.returncode != 0
            problems += not caught
            print(f"{'CAUGHT' if caught else 'MISSED'}  {name}  {failed[:1]}")
        # end for
    # end with
    return 1 if problems else 0
# end main


if __name__ == "__main__":
    sys.exit(main())
# end if
