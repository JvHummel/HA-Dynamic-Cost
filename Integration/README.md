# Dynamic Cost -- skeleton

A working-but-empty Home Assistant integration. Every file loads; the bodies
marked `IMPLEMENT ME` are yours to fill in.

## Layout

    custom_components/dynamic_cost/
      const.py        complete -- config keys and enums
      calc.py         EXERCISE  -- pure maths, no HA. Do this first.
      window.py       EXERCISE  -- snapshots, deltas, window boundaries.
      sensor.py       EXERCISE  -- the entities.
      config_flow.py  EXERCISE  -- setup wizard. Do this last.
      __init__.py     complete -- entry setup/unload wiring.
    tests/
      simulator.py    complete -- your test bench. Not an exercise.
      test_calc.py    some written, some for you.
      test_window.py  exercises, plus complete lifecycle tests at the bottom.

## Getting started

Current Home Assistant needs a newer Python than most distributions ship
(3.14.2+ as of 2026.9), so the venv is built with uv, which fetches that
Python for you:

    curl -LsSf https://astral.sh/uv/install.sh | sh     # once; installs uv
    uv venv --python 3.14 .venv
    uv pip install --python .venv -r requirements-dev.txt
    source .venv/bin/activate          # fish: source .venv/bin/activate.fish

    pytest tests/test_calc.py -v       # start here

## Order of work

1. `calc.py` until `pytest tests/test_calc.py` is green. No Home Assistant
   involved at all -- this is plain arithmetic you can check by hand.
2. `window.py`, using `tests/test_window.py` and the simulator to drive it.
3. `sensor.py` and `config_flow.py` last, once the logic underneath is proven.
