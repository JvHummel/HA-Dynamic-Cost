# Dynamic Cost HACS Integration — Implementation Plan

Sep 18, 2026 · drafted with @Joey

## Overview

Five components make up the integration:

1. **Calculation engine** — pure arithmetic (the energy and financial splits), no Home Assistant dependency at all.
2. **Window engine** — decides when a tracking window opens and closes (fixed timer or dynamic import-price change), and feeds the calculation engine the deltas for that window.
3. **Config flow** — the hub setup wizard, plus per-device subentries (general / solar / battery) addable later without re-running the wizard.
4. **Entities** — the sensors Home Assistant shows, built from the engine's output at each window close.
5. **Persistence** — cumulative totals, in-progress window state, and the locked export price all survive a restart.

Suggested build order is in the last section.

## Calculation engine

Plain arithmetic, decoupled from Home Assistant entirely — testable with hand-worked numbers before any integration code exists. Totals live in this engine's own state (not `utility_meter`), so outputs are free to go negative, exactly as the spec calls for.

```
// Inputs for this window: aggregated across all devices of each type
//   Solar, GridImport, GridExport, BatteryCharge, BatteryDischarge   (kWh)
//   ImportPrice, ExportPrice                                         (currency/kWh)
//   TreatSolarAsFree   (bool: hub checkbox OR curtailment locked on for this
//                       window; decided by the window engine)

// -- energy splits --
HouseEnergyUsage = Solar + GridImport + BatteryDischarge - BatteryCharge - GridExport
SolarToHouse     = MAX(0, MIN(Solar - GridExport, HouseEnergyUsage - BatteryDischarge))
SolarToBattery   = MAX(0, Solar - GridExport - SolarToHouse)
GridToBattery    = MIN(GridImport, BatteryCharge - SolarToBattery)
SolarToGrid      = Solar - SolarToBattery - SolarToHouse
GridToHouse      = MAX(0, GridImport - GridToBattery)
BatteryToHouse   = MAX(0, HouseEnergyUsage - GridToHouse - SolarToHouse)
BatteryToGrid    = MAX(0, BatteryDischarge - BatteryToHouse)

// -- financial splits --
GridImportCost        = GridImport * ImportPrice
GridExportCost        = GridExport * -ExportPrice       // negative when ExportPrice > 0, i.e. revenue
SolarToHouseSavings   = SolarToHouse * ImportPrice
GridToBatteryCost     = GridToBattery * ImportPrice
BatteryToHouseSavings = BatteryToHouse * ImportPrice
SolarToGridProfit     = SolarToGrid * ExportPrice
GridToHouseCost       = GridToHouse * ImportPrice
BatteryToGridProfit   = BatteryToGrid * ExportPrice

SolarToBatteryCost:
    if TreatSolarAsFree:  return 0
    return SolarToBattery * ExportPrice

HouseEnergyUsageCost = GridToHouseCost + SolarToHouseSavings + BatteryToHouseSavings
```

`SolarToBatteryCost` is an opportunity cost: solar stored in the battery could have been exported instead, so it is valued at the export price. It is deliberately not clamped, so a negative export price gives a negative cost, because storing that energy avoided paying to export it. It is zero when solar counts as free. There are two ways that can happen:

- **The user never counts it.** A hub checkbox, "Treat solar to battery energy as free".
- **The installation was curtailed during the window.** Curtailed PV could not have exported that energy, so storing it cost nothing. The integration doesn't guess when curtailment is happening. The user may not know either, and it isn't always driven by negative prices; a grid operator can remotely curtail installations to balance the grid, for example. Instead, each hub can name a curtailment source: an entity the user's own automations switch on and off, fed from whatever API or logic tells them curtailment is active.

The calculation engine only sees the combined `TreatSolarAsFree`; which of the two caused it is the window engine's business.

`HouseEnergyUsageCost` is a blended figure — cash actually paid to the grid plus the notional value of the solar and battery portions — not a net "after offsets" cost, so it can read as large even in a window where almost nothing was paid to the grid.

Keep the `MAX(0, …)` clamps. They do more than absorb meter jitter: a negative intermediate result means none of that source went to that destination, and the clamp turns it into zero. Without them, a night where the battery discharges 5 kWh, the house uses 2 and 3 are exported reports 3 kWh solar to grid and 0 kWh battery to grid. `test_calc.py` should pin this case.

## Config flow: hub + device subentries

A hub is a normal config entry; each device is a config subentry nested under it — addable, editable, and removable from the hub's own page without re-running hub setup. No limit on hubs, and no limit on devices (of any type) per hub.

| Step | Field(s) | Notes |
| --- | --- | --- |
| 1 | Grid import sensor, grid export sensor | Both mandatory; picker restricted to `device_class: energy` entities |
| 2 | Tariff mode | Fixed or Dynamic — branches the next step |
| 2a (Fixed) | Import tariff, export tariff, tracking window (minutes) | Tariffs in currency/kWh; window duration drives the fixed timer |
| 2b (Dynamic) | Import price sensor, export price sensor | Window boundaries follow the import price sensor |
| 3 | Treat solar to battery energy as free; curtailment source | Checkbox, off by default. Curtailment source is optional: an entity picker limited to `input_boolean`, `switch` and `binary_sensor`. Empty means never curtailed; hubs may share one entity |
| 4 | Currency | Sets `native_unit_of_measurement` for every monetary sensor under this hub |

| Device type | Sensors required | Tracks |
| --- | --- | --- |
| General | Import sensor only | Import energy + import value; pure sub-metering, does **not** feed the hub-level splits |
| Solar | Export sensor only | Export energy + export value; combined with every other solar device for the hub's `Solar` figure |
| Battery | Import sensor + export sensor | Import/export energy + value; combined with every other battery device for the hub's `BatteryCharge` / `BatteryDischarge` figures |

## Window engine

Every sensor read at a window boundary is read exactly once and reused for both the delta calculation and the new snapshot — never read twice, or a gap opens up between windows and energy silently vanishes from the accounting.

```
on_boundary_triggered():
    for each tracked entity (grid import, grid export, every device's sensor(s)):
        reading = try_read_sensor_state()      // the ONE and ONLY read
        if reading is available:
            delta[entity] = reading - snapshot[entity]
            snapshot[entity] = reading          // only advance on a successful read
        else:
            delta[entity] = 0
            // snapshot[entity] stays at its last known good value

    run_calculation_engine(delta.Solar, delta.GridImport, delta.GridExport,
                            delta.BatteryCharge, delta.BatteryDischarge, ...)
```

Triggers: a repeating timer from hub load for fixed tariffs; a state-change listener on the import price sensor for dynamic tariffs (an unavailable reading, or one equal to the current window's price, is a no-op — the window just stays open).

Export price locks on a grace period rather than a hard deadline:

```
on_window_open(import_price):
    reading = try_read(export_price_sensor)
    tentative_export_price = reading if available else UNSET
    start_grace_period_timer(60s)

on_export_price_sensor_update(event):
    if new value is available:
        tentative_export_price = new value
        if grace period already elapsed and nothing locked yet:
            lock_export_price(tentative_export_price)   // late arrival — lock immediately

on_grace_period_elapsed():
    if tentative_export_price is UNSET:
        return                                           // don't lock yet
    lock_export_price(tentative_export_price)
```

If the window is ready to close (import price changed) but export price for it never locked, the window is **not** closed — energy keeps accumulating into the extended window until export price locks, at which point it settles and closes normally.

The curtailment signal uses the same mechanism as export price: read tentatively at window open, updated by changes during the 60-second grace period, locked when the grace period elapses. If the source is unavailable, it locks on the first real reading, and the window cannot close until it has. With no source configured, every window counts as uncurtailed and nothing waits. Any on/off entity the user's automations can drive works as a source; the integration doesn't own the switch.

When a window closes, the engine passes `TreatSolarAsFree = hub checkbox OR locked curtailment state` to the calculation engine.

## Resilience

One rule covers every failure mode here: never guess a value — wait for real data instead.

| Signal | Unavailable behavior |
| --- | --- |
| Any energy sensor (grid or per-device) | Contributes 0 to this window's delta; its snapshot is **not** advanced, so the next successful read is compared against the last known good value, however many windows it took to recover |
| Energy sensor reads lower than its snapshot | Treated as a counter reset (e.g. a daily-reset production meter): delta counts as 0 and the snapshot advances to the new reading. A reset is a discontinuity, not timing skew, so unlike jitter it never self-corrects |
| Import price sensor | Treated as "no change" — the current window simply stays open |
| Export price sensor, within the grace period | The grace period becomes the earliest possible lock time, not a deadline — it locks the moment a real reading arrives, even minutes later |
| Export price sensor, window ready to close but nothing ever locked | Window is not closed — held open, energy still accumulating, until export price locks |
| Curtailment source | Same as export price: locks on the first real reading and holds the window open until it does. With no source configured, nothing is read and every window counts as uncurtailed |

This is expected to only happen from an upstream API error or a misconfiguration, not as routine behavior — which is also why it's safe to just wait rather than build more elaborate fallback logic.

Restarts follow the same rule. Sensor snapshots are persisted along with the rest of the in-progress window state, so energy that flowed while Home Assistant was down lands in the first window after the restart, just as it does after an unavailable sensor recovers. Not persisting them would drop that energy from the splits.

## Entities and aggregation

**Hub-level**: the 8 energy-split sensors and 10 financial-split sensors, built each window from `Solar` = sum of every solar device's export delta, `BatteryCharge` / `BatteryDischarge` = summed across all battery devices, `GridImport` / `GridExport` read straight from the hub's own two sensors.

**Device-level**: each device gets its own simple (not split-aware) counters — energy and value, using whichever price applies to its direction. This is separate bookkeeping, not an input to the hub-level split math.

Every monetary sensor gets `device_class: monetary`, `state_class: total`, and the hub's chosen currency as its unit, so long-term statistics and the Energy dashboard work the same way they were set up to in the original YAML package.

## Logging and config-flow validation

Logging is edge-triggered — one line per state transition, not one per window, or a sensor stuck down for hours would flood the log:

```
on_sensor_becomes_unavailable(entity_id):
    if not already flagged:
        log.warning("<entity_id> unavailable — treating as 0 kWh until it recovers")
        flag it

on_sensor_recovers(entity_id):
    if it was flagged:
        log.info("<entity_id> recovered after being unavailable")
        clear the flag
```

Config-flow validation is two cheap layers:

1. Sensor pickers are restricted to `device_class: energy` entities where applicable, so most wrong-sensor mistakes are impossible to make rather than caught after the fact.
2. A one-time live read at setup: an entity ID that doesn't exist at all is a hard error (blocks finishing setup, since that's unambiguously a typo). An entity that exists but reads unavailable at that moment is only a soft warning shown in the form — setup can still complete, since a briefly-unavailable sensor isn't necessarily misconfigured.

## Suggested build order

Build bottom-up rather than top-down, so the unfamiliar Home Assistant/Python plumbing comes last, once the underlying math is already known-good:

1. The calculation engine, as plain functions with no Home Assistant dependency — write it, hand it numbers worked out by hand, confirm it matches this plan.
2. The window engine's logic (snapshot/delta, the export-price lock) fed by fake/mock sensor readings — still no real Home Assistant plumbing.
3. Only then the config flow, subentries, and entities — the part with the most unfamiliar async idioms, tackled once everything underneath it is already tested.

## Development setup and conventions

Development runs in VS Code against a project virtualenv and in-process tests; no Home Assistant restarts are needed.

- Editor: Code OSS with the Python and Ruff extensions. Set `"python.analysis.typeCheckingMode": "standard"` so Pylance flags type mismatches while typing — annotations are not enforced at runtime.
- Terminal: VS Code's terminal profile is bash. In fish, activate with `source .venv/bin/activate.fish` instead.
- Environment: `.venv` in the project root, `pip install -r requirements-dev.txt` inside it.
- Tests, two tiers: `test_calc.py` needs no Home Assistant and runs in milliseconds. `test_window.py` boots an in-process `hass` via `pytest-homeassistant-custom-component`, drives it with `tests/simulator.py`, and uses `async_fire_time_changed` to skip ahead through timers.
- Frozen dataclasses (`WindowEnergy`, `EnergySplits`, `FinancialSplits`) are immutable: compute into locals, then construct once with keyword arguments.

| Kind | Convention |
| --- | --- |
| Functions, variables, parameters | `snake_case` (PEP 8; Ruff's `N` rules enforce it) |
| Private members | `_leading_underscore` |
| Module constants | `UPPER_SNAKE_CASE` |
| Classes | `UpperCamelCase` |

## Working agreement for Claude sessions

The skeleton's `IMPLEMENT ME` bodies are the author's practice exercises: guide, explain, and review them, but don't write them unless asked.

- Complete as shipped: `const.py`, `__init__.py`, `tests/simulator.py`. Everything else holds exercises.
- When the design changes, updating the skeleton's scaffolding to match is fair game for Claude: constants, signatures, stubs and their docstrings, tests, config-flow fields, `strings.json`, and the simulator. The function bodies stay exercises.
- The author is new to Python, Home Assistant, and Linux, coming from C, assembly, and Delphi Pascal — explain idioms by contrast with those where it helps.
- This doc describes the current design, as if it had always been that way. A new idea may start as a numbered addendum while it's evaluated together; once agreed, it's woven into the relevant sections and the addendum is removed before anything is implemented.
- Code-level follow-ups go in `# TODO:` comments at the spot, not in this doc.
- Run the tests and read failures directly rather than asking for pasted output.
