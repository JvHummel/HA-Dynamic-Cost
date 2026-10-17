# Dynamic Cost

[![CI](https://github.com/JvHummel/HA-Dynamic-Cost/actions/workflows/ci.yml/badge.svg)](https://github.com/JvHummel/HA-Dynamic-Cost/actions/workflows/ci.yml)

A Home Assistant integration that splits your household's energy flows —
grid, solar, battery and house — and values every split at the prices that
applied when the energy moved: fixed tariffs, or a dynamic contract's price
sensors. The full design is in [Plan.md](Plan.md).

## Installing

Requires Home Assistant 2025.3 or later (config subentries).

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

## What you get

- **Per hub** — up to 8 energy splits (kWh) and 10 financial splits (your
  currency), such as *Solar to house* and *Solar to house savings*. All are
  cumulative `total` sensors, so they work with long-term statistics and the
  Energy dashboard, and may go negative where the maths says so. Splits that
  need solar or a battery only appear once you've added one.
- **Diagnostics, for dynamic tariffs** — the window's status, its import
  and export price, its curtailment state (with a curtailment source), when
  it opened and when the last one closed. If a window stays open longer than
  the warning time you set (default 120 minutes), a warning is logged:
  usually the price sensor is wrong or stuck.
- **Per device** — its energy per sensor (*Energy produced*, *Energy
  charged*, …), and for general devices also *Energy cost* at the import
  price.

Totals survive restarts. Unavailable sensors are waited for rather than
guessed; see the Resilience section of the plan.

## Layout

    custom_components/dynamic_cost/
      const.py        config keys, enums, entity keys
      calc.py         the energy and financial splits; no Home Assistant
      window.py       tracking windows, snapshots, price locks, persistence
      sensor.py       the entities
      config_flow.py  setup wizard, reconfiguration, device flows
      __init__.py     entry setup/unload wiring
    tests/
      simulator.py         fake sensors and prices to drive a hub with
      test_calc.py         calculation engine; no Home Assistant needed
      test_window.py       windows, prices, resilience, logging, lifecycle
      test_sensor.py       entities, units, devices
      test_config_flow.py  setup wizard, validation, reconfigure
      test_persistence.py  restarts and configuration changes
    scripts/
      mutation_check.py    proves the tests can catch real bugs

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
