# Controls

Python package implementing the Easy Shift algorithm for optimal scheduling of heat pump water heaters.

## What is Easy Shift?

Easy Shift (EASY-SHIFT: Equipment Scheduling Algorithm for Thermal Energy Storage with Load Shifting) is a simple, generic, and intuitive control algorithm for providing demand flexibility with heat pump (HP) and thermal energy storage (TES) systems. It was developed at Lawrence Berkeley National Laboratory (LBNL) by Brian Woo-Shem and Peter Grant.

The algorithm computes a near-optimal heat pump operation schedule that minimizes electricity cost while meeting thermal load requirements and respecting storage constraints.

## How It Works

The algorithm operates in iterative steps:

1. **Rank hours by price** — All hours in the scheduling horizon are sorted by electricity cost (cheapest first). Ties are broken with small random perturbations.

2. **Identify unsatisfied hours** — The algorithm maintains a status vector tracking whether the thermal load at each hour is met (considering both heat pump output and stored energy).

3. **Assign operation to cheapest hours** — Starting from the first unsatisfied hour, the algorithm searches backwards through cheaper hours and turns on the heat pump at maximum capacity.

4. **Apply storage constraint** — If running at max capacity would exceed the tank's storage limit, the output is reduced to the maximum level that fits within storage.

5. **Apply cheaper-hours constraint** — If the current assignment would satisfy load beyond the next unsatisfied hour, the algorithm checks whether a cheaper hour exists further ahead. If so, it reduces the current hour's output to the minimum needed, saving capacity for the cheaper future hour.

6. **Iterate** — Steps 2–5 repeat until all hours are satisfied or the algorithm reaches the maximum iteration count.

### Receding Horizon Control

For real-world deployment, Easy Shift is designed to be used in a **receding horizon** (model predictive control) fashion:
- At each time step, run the algorithm with updated forecasts (prices, weather, load)
- Apply only the first hour's control action
- Re-run at the next time step with new information

This approach provides robustness against forecast uncertainty.

## Target Devices

Easy Shift was developed for **heat pump water heaters (HPWHs)** where the hot water tank itself serves as thermal energy storage — no separate TES tank is required. The water heater tank stores thermal energy by heating water above the minimum usable temperature during cheap hours, then draws down that stored energy during expensive hours.

However, the algorithm is generic and can be applied to any system with:

- A controllable device (heat pump, electric resistance heater, etc.)
- Thermal or electrical storage with capacity constraints (whether a water heater tank, a dedicated TES tank, or building thermal mass)
- Time-varying electricity prices
- Known or forecasted load profiles

Typical applications include:
- Residential heat pump water heaters (50–80 gallon tanks acting as their own TES)
- Dedicated thermal energy storage tanks paired with heat pumps
- Heat pump space heating with building thermal mass as storage
- Any device where load can be shifted in time using storage

## Key Assumptions

- **Hourly time steps** — The algorithm operates on 1-hour intervals. Sub-hourly resolution is not currently supported.
- **Perfect storage model** — Storage is modeled as a simple energy balance (input - load = change in storage). No thermal losses, stratification, or temperature-dependent effects are modeled.
- **Known forecasts** — Electricity prices, thermal load, and COP are assumed known for the full horizon. In practice, use the receding horizon approach to handle forecast uncertainty.
- **No startup/shutdown costs** — The heat pump can turn on/off freely at any hour with no penalty or minimum run time.
- **Linear storage** — Storage level changes linearly with heat pump output minus load. No nonlinear effects (e.g., COP varying with tank temperature).
- **Single device** — The algorithm schedules one heat pump. Fleet-level coordination requires running separate instances or a higher-level controller.

## Files

| File | Description |
|---|---|
| `__init__.py` | Package exports for Easy Shift and CTA-2045 functions |
| `easy_shift.py` | Easy Shift algorithm implementation |
| `cta2045.py` | CTA-2045 schedule generation from Easy Shift output or prices |

## Parameters

```python
parameters = {
    "horizon": 24,                    # Number of hours in the scheduling window

    "elec_costs": [...],              # Electricity price ($/kWh) for each hour. Length = horizon.

    "load": {
        "type": "hourly",             # Must be 'hourly'
        "value": [...]                # Thermal load requirement (kWh) per hour. Length = horizon.
    },

    "control": {
        "max": [...],                 # Max heat pump output (kWh) per hour. Length = horizon.
        "min": [...],                 # Min heat pump output (kWh) per hour. Length = horizon.
        "units": "kWh",              # (Optional) Label for plot y-axis
        "name": "Heat Pump",         # (Optional) Label for plot legend
    },

    "constraints": {
        "storage_capacity": True,     # Enable storage capacity constraint
        "max_storage": 12.0,          # Maximum storage level (kWh)
        "min_storage": 1.0,           # Minimum storage level / reserve (kWh)
        "initial_soc": 6.0,           # Initial state of charge (kWh)
        "cheaper_hours": True,        # Search for cheaper future hours (recommended: True)
    },

    "hardware": {
        "heatpump": True,             # True = heat pump (cost adjusted by COP), False = resistance
        "COP": [...]                  # Coefficient of performance per hour. Length = horizon.
    },
}
```

## Returns

`easy_shift(parameters)` returns a tuple `(operation, converged)`:

- **`operation`** — dict with lists of length = horizon:
  - `control` — heat pump output (kWh) at each hour
  - `control_max` — effective max output (may be reduced by storage constraint)
  - `control_min` — min output
  - `mode` — reserved for future use
  - `cost` — electricity cost at each hour
- **`converged`** — `True` if a feasible schedule was found, `False` if the algorithm could not satisfy all hours

## Usage

```python
from controls import easy_shift, get_storage, iteration_plot

parameters = {
    "horizon": 12,
    "elec_costs": [0.12, 0.11, 0.10, 0.09, 0.08, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13],
    "load": {"type": "hourly", "value": [1.5] * 12},
    "control": {
        "max": [4.5] * 12,
        "min": [0.0] * 12,
    },
    "constraints": {
        "storage_capacity": True,
        "max_storage": 12.0,
        "min_storage": 1.0,
        "initial_soc": 6.0,
        "cheaper_hours": True,
    },
    "hardware": {
        "heatpump": True,
        "COP": [3.0] * 12,
    },
}

operation, converged = easy_shift(parameters)
print(f"Converged: {converged}")
print(f"Schedule: {operation['control']}")
print(f"Total cost: ${sum(operation['cost']):.4f}")

# Plot the result
iteration_plot(operation, parameters)

# Get storage levels over time
storage = get_storage(operation['control'], parameters)
```

## CTA-2045 Schedule Generation

The `cta2045` module converts Easy Shift output (or raw price signals) into CTA-2045-B Level 2 demand response commands for water heaters.

### CTA-2045-B Commands

| Signal | Code | Water Heater Action |
|---|---|---|
| **Shed** | -1 | Lower setpoint, disable heat pump — coast on stored energy |
| **Normal** | 0 | Default operation |
| **Load Up** | 1 | Raise setpoint, pre-heat the tank |
| **Advanced Load Up** | 2 | Aggressively heat tank (max setpoint, tight deadband) |

### Two Approaches

**1. From Easy Shift output** (`easy_shift_to_cta2045`): Maps continuous HP output to discrete CTA-2045 commands based on output level relative to max capacity:
- Output = 0 → Shed
- Output < 30% of max → Normal
- Output 30–80% of max → Load Up
- Output ≥ 80% of max → Advanced Load Up

**2. From prices directly** (`prices_to_cta2045`): Uses price percentiles to classify hours without running Easy Shift first. Default thresholds: above 75th percentile → Shed, 50–75th → Normal, 25–50th → Load Up, below 25th → Advanced Load Up.

### Usage

```python
from controls import easy_shift, easy_shift_to_cta2045, prices_to_cta2045
from controls import format_schedule, plot_schedule

# Approach 1: From Easy Shift output
operation, converged = easy_shift(parameters)
cta_schedule = easy_shift_to_cta2045(operation, parameters)

# Approach 2: Directly from prices
cta_schedule = prices_to_cta2045(prices)

# Display results
print(format_schedule(cta_schedule))
plot_schedule(cta_schedule)
```

## Reference

B. Woo-Shem and P. Grant, "EASY-SHIFT: Equipment Scheduling Algorithm for Thermal Energy Storage with Load Shifting," Lawrence Berkeley National Laboratory (LBNL). [Presentation](https://drive.google.com/file/d/1ustmh-rE7693udh-mc096bhgSyDrT89D/view)

## Dependencies

- `numpy`
- `matplotlib` (for plotting only)
