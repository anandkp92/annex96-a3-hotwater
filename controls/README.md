# Controls

Python package for price-responsive HPWH load-shift scheduling and CTA-2045 command generation.

## Overview

Two interchangeable schedulers convert OpenADR price signals into optimal heat pump operation schedules. Both share an identical public API so they can be swapped with a single import-line change.

| | **LP Scheduler** | **Heuristic** |
|---|---|---|
| **File** | `hpwh_load_shift_lp.py` | `hpwh_load_shift_heuristic.py` |
| **Method** | Linear program (HiGHS via scipy) | Bottom-up greedy |
| **Solution quality** | Globally optimal | Near-optimal |
| **Speed** | Fast (milliseconds) | O(N²) worst case |
| **Dependency** | numpy, matplotlib, scipy | numpy, matplotlib |
| **Best for** | Production cost minimisation | Quick experiments, no scipy |

## Files

| File | Description |
|---|---|
| `__init__.py` | Package exports — default import uses LP scheduler |
| `hpwh_load_shift_lp.py` | LP scheduler — globally optimal via `scipy.optimize.linprog` (HiGHS) |
| `hpwh_load_shift_heuristic.py` | Heuristic scheduler — bottom-up greedy, no scipy |
| `cta2045.py` | CTA-2045-B command generation from scheduler output or raw prices |

## How the LP Scheduler Works

Formulates scheduling as a linear program:

- **Variables:** `e[h]` = HP thermal output in hour h  [kWh]
- **Objective:** minimise total electrical cost: `min Σ e[h] · price[h] / COP[h]`
- **Constraints:**
  - Per-hour HP bounds: `min_input[h] ≤ e[h] ≤ max_input[h]`
  - Tank upper bound: cumulative charge ≤ `max_storage − initial_soc + cumulative_load`
  - Tank lower bound: cumulative charge ≥ `min_storage − initial_soc + cumulative_load`
- Solved with `scipy.optimize.linprog` (HiGHS backend)
- Infeasible → max-input fallback with `converged=False`

## How the Heuristic Scheduler Works

Bottom-up greedy with two phases per iteration:

1. **Phase A** — apply `min_input` baseline to all hours up to the first unsatisfied hour
2. **Phase B** — boost cheapest eligible hours toward `max_input` until load is met
3. Overflow clipping after every assignment keeps SOC within tank bounds
4. Repeat until all hours satisfied or max iterations reached

## Target Devices

Both schedulers work for any device with:
- Controllable heat pump or electric heater
- Thermal storage with capacity constraints (water heater tank, dedicated TES, building mass)
- Time-varying electricity prices and a known or forecasted load profile

Typical use: residential heat pump water heaters (50–80 gallon tanks as their own TES).

## Key Assumptions

- **Hourly time steps** — 1-hour intervals; sub-hourly not currently supported.
- **Perfect storage model** — simple energy balance (input − load = ΔstoredEnergy); no thermal losses.
- **Known forecasts** — prices, load, and COP assumed known for the full horizon; use receding-horizon control to handle uncertainty.
- **No startup/shutdown costs** — HP can turn on/off freely at any hour.
- **Single device** — one HP scheduled; fleet control requires running separate instances.

## Parameters

```python
params = {
    "n":                      24,     # number of intervals in the horizon
    "price":                  [...],  # electricity price [$/kWh] per interval
    "load":                   [...],  # thermal load [kWh] per interval
    "cop":                    [...],  # COP per interval (use [cop]*n for constant COP)
    "initial_soc":            6.0,   # starting tank SOC [kWh]
    "min_storage_capacity":   1.0,   # minimum SOC reserve [kWh]
    "max_storage_capacity":  12.0,   # maximum tank SOC [kWh]
    "min_input":              0.0,   # min HP output [kWh] — scalar or list
    "max_input":              4.5,   # max HP output [kWh] — scalar or list
}
```

## Returns

`hpwh_load_shift(params)` returns `(schedule, converged)`:

- **`schedule`** — dict:
  - `control` — HP thermal output [kWh] per interval
  - `cost` — electricity cost [$] per interval (= `control[h] * price[h] / cop[h]`)
- **`converged`** — `True` if feasible; `False` if load exceeds capacity
  (returns max-input schedule clipped for overflow as a best-effort result)

## Usage

```python
# Choose one — identical API:
from controls.hpwh_load_shift_lp       import hpwh_load_shift, simulate_soc, iteration_plot
# from controls.hpwh_load_shift_heuristic import hpwh_load_shift, simulate_soc, iteration_plot

params = {
    "n": 24,
    "price": [0.12, 0.11, 0.10, 0.09, 0.08, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13] * 2,
    "load":  [1.5] * 24,
    "cop":   [3.0] * 24,
    "initial_soc":            6.0,
    "min_storage_capacity":   1.0,
    "max_storage_capacity":  12.0,
    "min_input":              0.0,
    "max_input":              4.5,
}

schedule, converged = hpwh_load_shift(params)
print(f"Converged: {converged}")
print(f"Schedule: {schedule['control']}")
print(f"Total cost: ${sum(schedule['cost']):.4f}")

iteration_plot(schedule, params)

# Get storage SOC trace over time
soc = simulate_soc(schedule['control'], params)
```

## CTA-2045 Schedule Generation

The `cta2045` module converts scheduler output (or raw prices) into CTA-2045-B Level 2 demand response commands for water heaters.

### CTA-2045-B Commands

| Signal | Code | Water Heater Action |
|---|---|---|
| **Shed** | -1 | Lower setpoint, disable heat pump — coast on stored energy |
| **Normal** | 0 | Default operation |
| **Load Up** | 1 | Raise setpoint, pre-heat the tank |
| **Advanced Load Up** | 2 | Aggressively heat tank (max setpoint, tight deadband) |

### Two Approaches

**1. From scheduler output** (`hpwh_load_shift_to_cta2045`): Maps continuous HP output to discrete commands based on fraction of max capacity:
- Output = 0 → Shed
- Output < 30% of max → Normal
- Output 30–80% of max → Load Up
- Output ≥ 80% of max → Advanced Load Up

**2. From prices directly** (`prices_to_cta2045`): Uses price percentiles — no need to run the scheduler first. Default: above 75th percentile → Shed, 50–75th → Normal, 25–50th → Load Up, below 25th → Advanced Load Up.

### Usage

```python
from controls import hpwh_load_shift_to_cta2045, prices_to_cta2045
from controls import format_schedule, plot_schedule

# Approach 1: From scheduler output
schedule, converged = hpwh_load_shift(params)
cta_schedule = hpwh_load_shift_to_cta2045(schedule, params)

# Approach 2: Directly from prices
cta_schedule = prices_to_cta2045(prices)

# Display results
print(format_schedule(cta_schedule))
plot_schedule(cta_schedule)
```

## Receding Horizon Control

For real-world deployment, re-run the scheduler at each time step with updated price and load forecasts:

1. Fetch updated prices from the VTN
2. Run `hpwh_load_shift(params)` for the remaining horizon
3. Apply only the **first interval's** control action to the device
4. Advance time and repeat

This provides robustness against forecast uncertainty.

## Dependencies

- `numpy`
- `matplotlib` (for plotting only)
- `scipy` (required by `hpwh_load_shift_lp.py` only)
