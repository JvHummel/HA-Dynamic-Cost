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

- **Per hub** — 8 energy splits (kWh) and 10 financial splits (your
  currency), such as *Solar to house* and *Solar to house savings*. All are
  cumulative `total` sensors, so they work with long-term statistics and the
  Energy dashboard, and may go negative where the maths says so.
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

1. Set the new version in `custom_components/dynamic_cost/manifest.json`
   (for example `0.1.1`), commit and push.
2. Tag that commit `v` plus the same version, and push the tag:

       git tag v0.1.1
       git push origin v0.1.1

   (In VS Code: Source Control → ⋯ → Tags → Create Tag, then push it.)

The Release workflow runs the full CI on the tagged commit, checks that the
tag matches the manifest, and publishes a GitHub release with notes generated
from the commits since the previous tag. HACS then offers it as an update. A
tag with a suffix, such as `v0.2.0-beta.1`, becomes a pre-release, which HACS
only offers to users who opted into betas.

## License

MIT — see [LICENSE](LICENSE).
