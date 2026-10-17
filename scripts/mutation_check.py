"""Check that the test suite catches real bugs, not just that it passes.

Run from the repository root:  .venv/bin/python scripts/mutation_check.py

Copies the project into a temporary directory, runs the whole suite there
(it must pass), then plants each bug below one at a time; every one must make
the suite fail. A bug that slips through means a test can't fail when it
should. STALE means a planting point no longer matches the code: update it.

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

# name: (file, exact original text, replacement)
MUTANTS: dict[str, tuple[str, str, str]] = {
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
        "return SPLIT_REQUIRES[key] <= device_types",
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
            target.write_text(text.replace(original, replacement))

            result = run_pytest(work, "-x")
            failed = [
                line.split("::")[-1].split(" ")[0]
                for line in result.stdout.splitlines()
                if line.startswith("FAILED")
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
