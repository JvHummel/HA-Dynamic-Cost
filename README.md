<picture>
  <source media="(prefers-color-scheme: dark)" srcset="artwork/dark_icon.svg">
  <img src="artwork/icon.svg" alt="" width="96">
</picture>

# Dynamic Cost

[![CI](https://github.com/JvHummel/HA-Dynamic-Cost/actions/workflows/ci.yml/badge.svg)](https://github.com/JvHummel/HA-Dynamic-Cost/actions/workflows/ci.yml)

A Home Assistant integration that splits your household's energy flows —
grid, solar, battery and house — and values every split at the prices that
applied when the energy moved: fixed tariffs, or a dynamic contract's price
sensors. The full design is in [Plan.md](Plan.md).

## Installing

Requires Home Assistant 2026.2 or later (for the battery capacity field).

1. Install the files, either way:
   - **HACS:** HACS → ⋮ → Custom repositories → add
     `https://github.com/JvHummel/HA-Dynamic-Cost` as type *Integration*,
     then download **Dynamic Cost**.
   - **By hand:** copy `custom_components/dynamic_cost/` into the
     `custom_components/` folder of your Home Assistant configuration
     directory (create the folder if it doesn't exist).
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Dynamic Cost**, and
   follow the wizard: grid sensors, tariffs, accounting options, currency,
   and an optional curtailment source.
4. On the hub's page, use **Add device** for each solar array, battery and
   general (sub-metered) device.

Every hub and every device can be reconfigured afterwards from its menu.
Switching tariff type keeps all records; only the currency is fixed.

## Settings

Energy is counted in *windows*: stretches of time that each settle at one
import price and one export price. Every setting below feeds one of the
window figures used in [How the splits are calculated](#how-the-splits-are-calculated).

### Hub

| Setting | Takes | Effect |
| --- | --- | --- |
| Grid import sensor, Grid export sensor | Cumulative meters, any energy unit | `GridImport`, `GridExport`: how much each meter rose during the window. A drop counts as a meter reset: 0 for that window |
| Tariff type | Fixed or Dynamic | Fixed: windows close on a timer. Dynamic: a window closes when the import price changes. Switching later keeps all totals |
| Import tariff, Export tariff *(fixed)* | Price per kWh, may be negative | `ImportPrice`, `ExportPrice` of every window |
| Tracking window *(fixed)* | Minutes | The length of every window |
| Import price sensor *(dynamic)* | Price per kWh, or per MWh (converted) | `ImportPrice`. A change closes the window and opens the next at the new price; an unavailable or unchanged price keeps it open |
| Export price sensor *(dynamic)* | As the import price | `ExportPrice`: read when the window opens, locked 60 seconds later, or at the first reading after that. A window can't close until it's locked |
| Warn when a window stays open longer than *(dynamic)* | Minutes, default 120 | Logs a warning. Changes no numbers |
| Treat solar as free | On/off, default off | On: `SolarRate` is 0 in every window |
| Currency | Currency code | The unit of every money sensor. Can't be changed after setup |
| Curtailment source | Optional on/off entity (`input_boolean`, `switch`, `binary_sensor`). Only asked with *Treat solar as free* off | Read like the export price: locked 60 seconds into the window. Locked on: `SolarRate` is 0 for that window. Empty: never curtailed |

### Devices

| Device | Takes | Effect |
| --- | --- | --- |
| General device | Energy used: a cumulative meter | Its own *Energy used* and *Energy cost*. Moves no hub split: its energy is already part of *House energy usage* |
| Solar panels | Energy produced: a cumulative meter | Adds to `Solar`. The first one adds the solar splits |
| Battery | Energy charged, Energy discharged: cumulative meters | Add to `BatteryCharge`, `BatteryDischarge`. The first one adds the battery splits and *Battery energy cost* |
| | Usable capacity: a number in kWh, or an entity reporting it | The ceiling of the battery pool (see `BatteryRate`) |

**Usable capacity** is the energy the battery can hold between its lowest
and highest allowed charge: 9 kWh for a 10 kWh battery used from 10% to
100%. Don't subtract round-trip losses: the ceiling is there to absorb them.
Too high only makes *Battery energy cost* follow prices a little more
slowly; too low makes part of each discharge count at the import price. So
when unsure, round up. A battery added before 0.4.0 has no capacity until
you reconfigure it; until every battery has one, there is no ceiling.

## How the splits are calculated

At every window close, each meter's rise gives five figures in kWh:
`Solar` (all solar devices), `GridImport`, `GridExport`, `BatteryCharge` and
`BatteryDischarge` (all batteries). Each split adds its result for the
window to its total. `ImportPrice` and `ExportPrice` are the window's locked
prices.

| Energy split | Formula |
| --- | --- |
| House energy usage | `Solar + GridImport + BatteryDischarge − BatteryCharge − GridExport` |
| Solar to house | `MAX(0, MIN(Solar − GridExport, HouseEnergyUsage − BatteryDischarge))` |
| Solar to battery | `MAX(0, Solar − GridExport − SolarToHouse)` |
| Grid to battery | `MIN(GridImport, BatteryCharge − SolarToBattery)` |
| Solar to grid | `Solar − SolarToBattery − SolarToHouse` |
| Grid to house | `MAX(0, GridImport − GridToBattery)` |
| Battery to house | `MAX(0, HouseEnergyUsage − GridToHouse − SolarToHouse)` |
| Battery to grid | `MAX(0, BatteryDischarge − BatteryToHouse)` |

A `MAX(0, …)` that comes out negative means none of that source went there.

| Money split | Formula |
| --- | --- |
| Grid import cost | `GridImport × ImportPrice` |
| Grid export profit | `GridExport × ExportPrice` |
| Grid to house cost | `GridToHouse × ImportPrice` |
| Grid to battery cost | `GridToBattery × ImportPrice` |
| Solar to house savings | `SolarToHouse × ImportPrice` |
| Solar to grid profit | `SolarToGrid × ExportPrice` |
| Solar to battery cost | `SolarToBattery × SolarRate` |
| Battery to house savings | `BatteryToHouse × ImportPrice` |
| Battery to grid profit | `BatteryToGrid × ExportPrice` |
| House energy usage cost | `GridToHouse × ImportPrice + SolarToHouse × SolarRate + BatteryToHouse × BatteryRate` |

| Summary | Formula |
| --- | --- |
| Grid import, Grid export | `GridImport`, `GridExport` |
| Solar production | `Solar` |
| Battery charged, Battery discharged | `BatteryCharge`, `BatteryDischarge` |
| Grid net cost | Grid import cost − Grid export profit |
| Solar value | Solar to house savings + Solar to grid profit |
| Battery net savings | Battery to house savings + Battery to grid profit − Grid to battery cost − Solar to battery cost |

- **`SolarRate`**, what a kWh of solar used or stored cost: `ExportPrice`, the
  income given up by not exporting it. It's 0 with *Treat solar as free* on or
  in a curtailed window, and negative when `ExportPrice` is.
- **`BatteryRate`**, what a kWh from the batteries cost: the average of the
  *battery pool*, the kWh in all batteries together with what charging them
  cost. Charging adds `GridToBattery` at `ImportPrice` and `SolarToBattery` at
  `SolarRate`; discharging (`BatteryToHouse + BatteryToGrid`) takes kWh out at
  the average. kWh beyond what the pool holds were in the battery before
  tracking began, and count at `ImportPrice`. Above the usable capacity, the
  extra kWh are losses: they're dropped, but their cost stays in the pool.
  *Battery energy cost* shows the average.
- **A general device's Energy cost** is its energy `E × House energy usage cost
  ÷ (GridToHouse + SolarToHouse + BatteryToHouse)`: the house's cost per kWh
  for that window. With no house energy measured in the window:
  `E × ImportPrice`.

## What you get

- **Per hub** — up to 8 energy splits (kWh) and 10 financial splits (your
  currency), such as *Solar to house* and *Solar to house savings*. All are
  cumulative `total` sensors, so they work with long-term statistics and the
  Energy dashboard, and may go negative where the maths says so. Splits that
  need solar or a battery only appear once you've added one.
- **Summaries per hub** — per source, what it moved and what it cost or
  earned in all: *Grid import*, *Grid export* and *Grid net cost*; *Solar
  production* and *Solar value*; *Battery charged*, *Battery discharged* and
  *Battery net savings*.
- **Diagnostics, for dynamic tariffs** — the window's status, its import
  and export price, its curtailment state (with a curtailment source),
  whether those are still tentative or locked in, when the window opened and
  when the last one closed. If a window stays open longer than
  the warning time you set (default 120 minutes), a warning is logged:
  usually the price sensor is wrong or stuck. With a battery, in either
  tariff mode: *Battery energy cost*, what a kWh in it cost on average.
- **Per device** — its energy per sensor (*Energy produced*, *Energy
  charged*, …), and for general devices also *Energy cost*: what that
  energy really cost, by where the house's energy came from.

Sensors can report in any energy unit (Wh, kWh, MWh, …) and prices per
kWh or per MWh: everything is converted to kWh. Totals survive restarts.
Unavailable sensors are waited for rather than guessed; see the Resilience
section of the plan.

## Layout

    custom_components/dynamic_cost/
      const.py        config keys, enums, entity keys
      calc.py         the energy and financial splits; no Home Assistant
      units.py        converting energy to kWh and prices to per kWh
      window.py       tracking windows, snapshots, price locks, persistence
      sensor.py       the entities
      brand/          the icon Home Assistant shows (2026.3+)
      config_flow.py  setup wizard, reconfiguration, device flows
      __init__.py     entry setup/unload wiring
    tests/
      simulator.py         fake sensors and prices to drive a hub with
      test_calc.py         calculation engine; no Home Assistant needed
      test_units.py        unit conversion; no running Home Assistant
      test_window.py       windows, prices, resilience, logging, lifecycle
      test_sensor.py       entities, units, devices
      test_config_flow.py  setup wizard, validation, reconfigure
      test_persistence.py  restarts and configuration changes
    scripts/
      mutation_check.py    proves the tests can catch real bugs
      render_brand.py      turns artwork/*.svg into the icons in brand/
    artwork/               the icon's source drawings

## Development

Current Home Assistant needs a newer Python than most distributions ship
(3.14.2+ as of 2026.9), so the venv is built with uv, which fetches that
Python for you:

    curl -LsSf https://astral.sh/uv/install.sh | sh     # once; installs uv
    uv venv --python 3.14 .venv
    uv pip install --python .venv -r requirements-dev.txt
    source .venv/bin/activate          # fish: source .venv/bin/activate.fish

    pytest                             # the whole suite, a few seconds
    ruff check custom_components tests scripts
    python scripts/mutation_check.py   # every planted bug must be CAUGHT

GitHub Actions runs the same three on every push and pull request, plus
hassfest (Home Assistant's own manifest and translation checks) and HACS
validation, and repeats them weekly to catch breakage from new Home
Assistant releases. See `.github/workflows/ci.yml`.

## Releasing

Work towards a new version is published as betas first, and the final
release follows once it has proven itself.

1. **Start the version** with the first push after a release: set
   `version` in `custom_components/dynamic_cost/manifest.json` to the next
   version plus a beta suffix (`0.3.0-beta.1`), and add a section at the top
   of [CHANGELOG.md](CHANGELOG.md), `## [0.3.0] - Unreleased`, saying what
   changes for someone using the integration.
2. **Every push** while it's in progress counts the beta up (`-beta.2`, …)
   and keeps the changelog section current. Once CI is green, tag it:

       git tag v0.3.0-beta.1
       git push origin v0.3.0-beta.1

   That publishes a pre-release. HACS only offers it to installations that
   have beta versions turned on for this repository.
3. **Finish the version:** remove the suffix (`0.3.0`), replace `Unreleased`
   with the date, push, and tag `v0.3.0` once CI is green.

The Release workflow runs the full CI on the tagged commit, checks that the
tag matches the manifest, that CHANGELOG.md has a section for the version,
and that a final release's section is dated, then publishes the release with
that section as its notes, plus a link to the full diff. If a check fails,
nothing is published.

## License

MIT — see [LICENSE](LICENSE).
