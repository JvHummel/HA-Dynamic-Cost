# Dynamic Cost HACS Integration — Implementation Plan

Sep 18, 2026 · drafted with @Joey

## Overview

Five components make up the integration:

1. **Calculation engine** — pure arithmetic (the energy and financial splits), no Home Assistant dependency at all.
2. **Window engine** — decides when a tracking window opens and closes (fixed timer or dynamic import-price change), and feeds the calculation engine the deltas for that window.
3. **Config flow** — the hub setup wizard, plus per-device subentries (general / solar / battery) addable later without re-running the wizard.
4. **Entities** — the sensors Home Assistant shows, built from the engine's output at each window close.
5. **Persistence** — cumulative totals, in-progress window state, and the locked export price all survive a restart, in one store per hub owned by the window engine.

Suggested build order is in the last section.

## Calculation engine

Plain arithmetic, decoupled from Home Assistant entirely — testable with hand-worked numbers before any integration code exists. Running totals live in the window engine's own state (not `utility_meter`), so outputs are free to go negative, exactly as the spec calls for.

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
- **The installation was curtailed during the window.** Curtailed PV could not have exported that energy, so storing it cost nothing. The integration doesn't guess when curtailment is happening. The user may not know either, and it isn't always driven by negative prices; a grid operator can remotely curtail installations to balance the grid, for example. Instead, each hub can name a curtailment source: an entity the user's own automations switch on and off, fed from whatever API or logic tells them curtailment is active. This applies to fixed and dynamic tariffs alike, since operators can curtail regardless of contract type. Hubs with the checkbox on don't track curtailment, since solar already counts as free there.

The calculation engine only sees the combined `TreatSolarAsFree`; which of the two caused it is the window engine's business.

`HouseEnergyUsageCost` is a blended figure — cash actually paid to the grid plus the notional value of the solar and battery portions — not a net "after offsets" cost, so it can read as large even in a window where almost nothing was paid to the grid.

Keep the `MAX(0, …)` clamps. They do more than absorb meter jitter: a negative intermediate result means none of that source went to that destination, and the clamp turns it into zero. Without them, a night where the battery discharges 5 kWh, the house uses 2 and 3 are exported reports 3 kWh solar to grid and 0 kWh battery to grid. `test_calc.py` should pin this case.

## Config flow: hub + device subentries

A hub is a normal config entry; each device is a config subentry nested under it — addable, editable, and removable from the hub's own page without re-running hub setup. No limit on hubs, and no limit on devices (of any type) per hub.

Home Assistant forms can't show or hide a field based on another field in the same form, so conditional fields get their own step, and the flow skips that step when it doesn't apply.

| Step | Field(s) | Notes |
| --- | --- | --- |
| 1 | Grid import sensor, grid export sensor | Both mandatory; picker restricted to `device_class: energy` entities |
| 2 | Tariff mode | Fixed or Dynamic — branches the next step |
| 2a (Fixed) | Import tariff, export tariff, tracking window (minutes) | Tariffs in currency/kWh; window duration drives the fixed timer |
| 2b (Dynamic) | Import price sensor, export price sensor | Window boundaries follow the import price sensor |
| 3 | Treat solar to battery energy as free | Checkbox, off by default |
| 4 | Currency | Sets `native_unit_of_measurement` for every monetary sensor under this hub. Defaults to Home Assistant's own currency setting. Fixed after setup |
| 5 (checkbox off) | Curtailment source | Optional entity picker limited to `input_boolean`, `switch` and `binary_sensor`. Empty means never curtailed; hubs may share one entity. Offered in both tariff modes; skipped entirely with the checkbox on |

| Device type | Sensors required | Tracks |
| --- | --- | --- |
| General | Import sensor only | Import energy + import value; pure sub-metering, does **not** feed the hub-level splits |
| Solar | Export sensor only | Production energy only; combined with every other solar device for the hub's `Solar` figure |
| Battery | Import sensor + export sensor | Charge and discharge energy only; combined with every other battery device for the hub's `BatteryCharge` / `BatteryDischarge` figures |

Both the hub and its devices stay editable after setup:

- **Hub settings** are changed through a reconfigure flow that walks the same steps as setup, pre-filled with the current values. Everything can change except the currency: changing it would mix two currencies in the existing totals, and Home Assistant's long-term statistics treat a unit change as a break. A different currency means a new hub.
- **Switching tariff type is explicitly allowed.** A user who moves from a fixed to a dynamic contract (or back) can update the hub and keep all their records; the totals carry on, and windows follow the new contract from the reload on.
- **Each device** has its own reconfigure step with the same fields as adding it: its name and its sensor(s). Its type can't change; for that, remove the device and add it again.

Every edit reloads the hub. How the engine picks up after a change is covered under Persistence.

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

The first window opens as soon as the engine has everything a window needs to open, which is only an import price:

- **Fixed tariffs:** the import price is the configured tariff, so the first window opens at hub load.
- **Dynamic tariffs:** the engine reads the import price sensor at hub load. If it has a real reading, the first window opens with it. If not, no window opens at all: the engine waits in a "no window yet" state, and the first real import price that arrives opens the first window rather than closing one. Opening with an unknown price would make that first real price look like a change, and settle a window with no price to value it at.

Export price and curtailment aren't needed to open a window; they lock later, as described below. When the first window opens, every energy sensor is read once to set its baseline snapshot. Energy that flowed before then isn't tracked, just like energy from before the integration was installed. A sensor with no snapshot yet, because it was unavailable at that moment, gets its baseline from its first successful reading, which contributes 0.

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

With fixed tariffs, the configured export tariff stands in for the export price sensor: it is the tentative export price at every window open and locks when the grace period elapses, like any other. Fixed mode needs no special case in the locking logic.

If the window is ready to close (import price changed) but export price for it never locked, the window is **not** closed — energy keeps accumulating into the extended window until export price locks, at which point it settles and closes normally.

The curtailment signal uses the same mechanism as export price: read tentatively at window open, updated by changes during the 60-second grace period, locked when the grace period elapses. If the source is unavailable, it locks on the first real reading, and the window cannot close until it has. With no source configured, every window counts as uncurtailed and nothing waits. The config flow only saves a source when the checkbox is off, so the engine just checks whether one is configured. Any on/off entity the user's automations can drive works as a source; the integration doesn't own the switch.

When a window closes, the engine passes `TreatSolarAsFree = hub checkbox OR locked curtailment state` to the calculation engine.

## Resilience

One rule covers every failure mode here: never guess a value — wait for real data instead.

| Signal | Unavailable behavior |
| --- | --- |
| Any energy sensor (grid or per-device) | Contributes 0 to this window's delta; its snapshot is **not** advanced, so the next successful read is compared against the last known good value, however many windows it took to recover |
| Energy sensor with no snapshot yet | Its first successful reading only sets the baseline and contributes 0. That happens when it was unavailable as the first window opened, or when a device has just been added |
| Energy sensor reads lower than its snapshot | Treated as a counter reset (e.g. a daily-reset production meter): delta counts as 0 and the snapshot advances to the new reading. A reset is a discontinuity, not timing skew, so unlike jitter it never self-corrects |
| Import price sensor | Treated as "no change" — the current window simply stays open |
| Import price sensor, first start with no window yet | No window opens until a real reading arrives; nothing is tracked until then |
| Export price sensor, within the grace period | The grace period becomes the earliest possible lock time, not a deadline — it locks the moment a real reading arrives, even minutes later |
| Export price sensor, window ready to close but nothing ever locked | Window is not closed — held open, energy still accumulating, until export price locks |
| Curtailment source | Same as export price: locks on the first real reading and holds the window open until it does. With no source configured, nothing is read and every window counts as uncurtailed |

This is expected to only happen from an upstream API error or a misconfiguration, not as routine behavior — which is also why it's safe to just wait rather than build more elaborate fallback logic.

Restarts follow the same rule. Sensor snapshots are persisted along with the rest of the in-progress window state, so energy that flowed while Home Assistant was down lands in the first window after the restart, just as it does after an unavailable sensor recovers. Not persisting them would drop that energy from the splits. Because a restart restores the window that was open, an unavailable import price at that point is ordinary "no change"; the "no window yet" state only arises on the very first start.

## Persistence

Each hub has one store: Home Assistant's `Store` helper, a JSON file under `.storage/` named after the hub's config entry id. The window engine owns it and is the single source of truth. It holds:

- every cumulative total, hub-level splits and per-device counters alike
- every energy sensor's snapshot
- the in-progress window: its prices, lock state and grace state

The engine saves after every change worth surviving a restart (a window opening, a lock, a window closing), with a short delay so bursts of changes become one write. Home Assistant flushes pending writes at shutdown, and the engine writes immediately when it unloads, so a reload never races its own delayed save. Deleting the hub deletes its store.

Everything in the store is keyed by IDs that never change. Home Assistant gives every hub a config entry id and every device a subentry id; renaming either changes only its title. Each total is keyed by the unique id of the entity that shows it, which starts with its owner's id (the config entry id for splits, the subentry id for device counters), and snapshots by the entity id of the sensor they belong to. Alongside the in-progress window, the store keeps a copy of the hub settings that shape windows: tariff type, tariffs, price sensors, window length, the "treat solar as free" checkbox and the curtailment source.

The configuration can change while the hub is stopped, through an edit, or while it was disabled. On load, the engine reconciles the store with the current configuration:

| Change | Handling |
| --- | --- |
| Device renamed | Nothing to do: its subentry id is unchanged |
| Device added | No stored data: its counters start at 0 and its first reading sets the baseline |
| Device removed | Its stored totals and snapshots are dropped |
| Device or grid sensor switched to another sensor | The new sensor has no snapshot, so its first reading sets a fresh baseline and contributes 0. The device keeps its totals |
| Window-shaping hub settings changed (including tariff type) | The stored window is discarded and the first-window rules apply. Snapshots of unchanged sensors are kept, so no energy is lost: it lands in the new first window, just as downtime energy does after a restart. All totals are kept |

## Entities and aggregation

**Hub-level**: the 8 energy-split sensors and 10 financial-split sensors, built each window from `Solar` = sum of every solar device's export delta, `BatteryCharge` / `BatteryDischarge` = summed across all battery devices, `GridImport` / `GridExport` read straight from the hub's own two sensors.

**Device-level**: each device gets its own simple (not split-aware) counters. This is separate bookkeeping, not an input to the hub-level split math.

- Every device counts energy per sensor: a general device its import, a solar device its production, a battery its charge and its discharge. This deliberately duplicates the source meter: it counts from the start of measurement rather than from whenever the source meter started, which may be long before the integration was added.
- Only general devices also count value, at the import price. Together with the energy counter, that gives the device's average energy cost, which is useful with dynamic tariffs. Solar and battery devices count no value: they exist to feed the splits, where their value is accounted for.

Each hub is a Home Assistant device holding the split sensors, and each of its devices is a Home Assistant device of its own, named after it and holding its own counters. Every entity's unique id is its owner's id plus its key: `<config entry id>_<key>` for split sensors, `<subentry id>_<key>` for device counters. Entity ids are generated by Home Assistant from the device and entity names, so they differ between installations; code and tests find entities by unique id instead.

Entities restore nothing themselves: they display the totals the window engine holds (see Persistence).

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
2. A one-time live read of every chosen energy and price sensor at setup:
   - An entity ID that doesn't exist at all is a hard error (`unknown_entity`), since that's unambiguously a typo.
   - An entity whose state isn't a number is a hard error (`not_numeric`): it's the wrong kind of entity.
   - An entity that exists but reads unavailable or unknown at that moment only gets a soft warning, since a briefly-unavailable sensor isn't necessarily misconfigured. Home Assistant form errors always block submission, so the warning is a separate confirmation step that names the sensor and lets the user continue anyway.

   Hard errors block finishing setup until the field is corrected.

## Suggested build order

Build bottom-up rather than top-down, so the unfamiliar Home Assistant/Python plumbing comes last, once the underlying math is already known-good:

1. The calculation engine, as plain functions with no Home Assistant dependency — write it, hand it numbers worked out by hand, confirm it matches this plan.
2. The window engine's logic (snapshot/delta, the export-price lock) fed by fake/mock sensor readings — still no real Home Assistant plumbing.
3. Only then the config flow, subentries, and entities — the part with the most unfamiliar async idioms, tackled once everything underneath it is already tested.

## Development setup and conventions

Development runs in VS Code against a project virtualenv and in-process tests; no Home Assistant restarts are needed.

- Editor: Code OSS with the Python and Ruff extensions. Set `"python.analysis.typeCheckingMode": "standard"` so Pylance flags type mismatches while typing — annotations are not enforced at runtime.
- Terminal: VS Code's terminal profile is bash. In fish, activate with `source .venv/bin/activate.fish` instead.
- Environment: `.venv` in the project root, built with `uv` on Python 3.14 (`uv venv --python 3.14 .venv`, then `uv pip install --python .venv -r requirements-dev.txt`). Current Home Assistant requires Python 3.14.2+, and config subentries need Home Assistant 2025.3 or later. With an older Python, pip silently installs an old Home Assistant that lacks them, and the config flow fails to import.
- Tests, two tiers: `test_calc.py` needs no Home Assistant and runs in milliseconds. The rest boot an in-process `hass` via `pytest-homeassistant-custom-component` and use `async_fire_time_changed` to skip ahead through timers: `test_window.py` (windows, prices, resilience, logging; plus lifecycle tests on a bare `WindowEngine`), `test_sensor.py` (entities, classes, devices), `test_config_flow.py` (setup, validation, reconfiguring hubs and devices) and `test_persistence.py` (restarts and the restore table). Most drive the integration through `tests/simulator.py`. Expected values are worked out by hand from this doc's formulas, so a failing test means the code disagrees with the plan.
- Frozen dataclasses (`WindowEnergy`, `EnergySplits`, `FinancialSplits`) are immutable: compute into locals, then construct once with keyword arguments.

| Kind | Convention |
| --- | --- |
| Functions, variables, parameters | `snake_case` (PEP 8; Ruff's `N` rules enforce it) |
| Private members | `_leading_underscore` |
| Module constants | `UPPER_SNAKE_CASE` |
| Classes | `UpperCamelCase` |
| Block ends | An `# end …` comment at the block's own indentation: `# end <name>` after every function and class, `# end if` / `# end match` after every if-chain and match, and `# end for` / `while` / `with` / `try` after those blocks when they span 5+ lines. Trailing comments inside a block stay above it |

## Working agreement for Claude sessions

The skeleton's `IMPLEMENT ME` bodies in `custom_components/` are the author's practice exercises: guide, explain, and review them, but don't write them unless asked. The tests are Claude's: Claude writes and maintains the whole `tests/` directory, so the author can focus on the integration and check their work immediately.

- Complete as shipped: `const.py`, `__init__.py`, and everything under `tests/`. Everything else in `custom_components/` holds exercises.
- Tests follow the design: when this doc changes, Claude updates the tests in the same change. New or changed tests are proven against a throwaway reference implementation kept outside the project (never copied in), and checked to fail when a matching bug is planted, so a red test always points at the integration, not at the test.
- When the design changes, updating the skeleton's scaffolding to match is fair game for Claude: constants, signatures, stubs and their docstrings, tests, config-flow fields, `strings.json`, and the simulator. The function bodies stay exercises.
- The author is new to Python, Home Assistant, and Linux, coming from C, assembly, and Delphi Pascal — explain idioms by contrast with those where it helps.
- This doc describes the current design, as if it had always been that way. A new idea may start as a numbered addendum while it's evaluated together; once agreed, it's woven into the relevant sections and the addendum is removed before anything is implemented.
- Before acting on a proposed change, check it against this doc. Point out anything it contradicts or makes obsolete, including the reasoning behind earlier decisions, and settle that first.
- Code-level follow-ups go in `# TODO:` comments at the spot, not in this doc.
- Run the tests and read failures directly rather than asking for pasted output.
