"""
HPWH Load Shift — Linear Program Scheduler.

Exact minimum-cost schedule for a heat-pump water heater (HPWH) formulated
as a linear program (LP) and solved with scipy's HiGHS backend.

LP formulation
--------------
Variables
    e[h]  — HP thermal output in interval h  [kWh],  h = 0 … N-1

Objective (minimise electrical energy cost)
    min  Σ_h  e[h] · price[h] / COP[h]

Constraints
    Per-interval bounds:
        min_input[h]  ≤  e[h]  ≤  max_input[h]          ∀ h

    Storage bounds (cumulative sum expressed as a lower-triangular system):
        Let L  = lower-triangular matrix of ones  (N × N)
        Let σ₀ = initial_soc
        Let u  = cumulative load vector  (u[h] = Σ_{i≤h} load[i])

        Upper bound on SOC (tank must not overflow):
            (L·e)[h] ≤ max_soc − σ₀ + u[h]      ∀ h

        Lower bound on SOC (tank must stay above reserve):
            (L·e)[h] ≥ min_soc − σ₀ + u[h]
            ⟺  -(L·e)[h] ≤ σ₀ − min_soc − u[h]  ∀ h

Infeasibility / fallback
--------------------------
If the LP is infeasible (load exceeds max capacity), the function returns
converged=False and a best-effort schedule built by clipping max_input to
the storage overflow constraint (same overflow-clipping used in the heuristic).

Public API
----------
  hpwh_load_shift(params, verbose, start_hour) -> (schedule_dict, bool)
  simulate_soc(schedule, params)               -> list[float]
  get_storage(schedule, params)                -> list[float]   (alias)
  iteration_plot(operation, params, ...)       -> None

The public API is identical to hpwh_load_shift_heuristic so the two modules
can be swapped with a single import-line change.
"""

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import linprog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def hpwh_load_shift(params, verbose=False, start_hour=0):
    """
    Compute the minimum-cost HPWH schedule via linear programming.

    Parameters
    ----------
    params : dict
        n                   : int
            Number of intervals in the forecast horizon.
        price               : list[float]
            Electricity price per interval [$/kWh].
        load                : list[float]
            Thermal load per interval [kWh].
        cop                 : list[float]
            Coefficient of performance per interval.
        initial_soc         : float
            Tank state of charge at the start [kWh].
        min_storage_capacity : float
            Minimum SOC reserve [kWh].
        max_storage_capacity : float
            Maximum tank SOC [kWh].
        min_input           : float or list[float]
            Minimum HP thermal output per interval [kWh].
        max_input           : float or list[float]
            Maximum HP thermal output per interval [kWh].

    verbose : bool, optional
        Print solver status. Default False.
    start_hour : int, optional
        Hour offset used for plot x-axis labels only. Default 0.

    Returns
    -------
    schedule : dict
        control  : list[float]  — HP thermal output per interval [kWh].
        cost     : list[float]  — Electricity cost per interval [$].
    converged : bool
        True if the LP is feasible and optimal; False otherwise.
    """
    params = _validate_params(params)
    N = params['n']
    price = np.array(params['price'], dtype=float)
    cop = np.array(params['cop'], dtype=float)
    load = np.array(params['load'], dtype=float)
    min_inp = np.array(params['min_input'], dtype=float)
    max_inp = np.array(params['max_input'], dtype=float)
    sigma0 = params['initial_soc']
    max_soc = params['max_storage_capacity']
    min_soc = params['min_storage_capacity']

    # Objective: electrical cost = thermal_output / COP * price
    c = price / cop

    # Lower-triangular constraint matrix.
    L = np.tril(np.ones((N, N)))

    # Cumulative load vector.
    cum_load = np.cumsum(load)

    # Inequality constraints: A_ub @ e <= b_ub
    #   Row 0…N-1  → upper SOC bound: L·e ≤ (max_soc - σ₀) + cum_load
    #   Row N…2N-1 → lower SOC bound: -L·e ≤ (σ₀ - min_soc) - cum_load
    A_ub = np.vstack([L, -L])
    b_ub = np.concatenate([
        (max_soc - sigma0) + cum_load,
        (sigma0 - min_soc) - cum_load,
    ])

    bounds = [(min_inp[h], max_inp[h]) for h in range(N)]

    result = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')

    if verbose:
        print(f"LP status: {result.status} — {result.message}")

    if result.status == 0:  # optimal
        schedule = list(result.x)
        return _build_schedule(schedule, params), True
    else:
        if verbose:
            print("LP infeasible or unbounded. Returning max-input fallback.")
        fallback = _clip_overflow(list(params['max_input']), params)
        return _build_schedule(fallback, params), False


def simulate_soc(schedule, params):
    """
    Forward-simulate tank SOC for a given schedule.

    Parameters
    ----------
    schedule : list[float]
        HP thermal output per interval [kWh].
    params : dict
        Must contain: initial_soc, n, load.

    Returns
    -------
    list[float]
        SOC at each interval boundary; length = n + 1.
        soc[0] = initial_soc; soc[h+1] = soc[h] + schedule[h] - load[h].
    """
    return _simulate_soc(schedule, params)


def get_storage(schedule, params):
    """Alias for simulate_soc; provided for API compatibility."""
    return _simulate_soc(schedule, params)


def iteration_plot(operation, params, start_hour=0, saveas=""):
    """
    Plot HP schedule, load, prices, and storage SOC.

    Parameters
    ----------
    operation : dict
        Output from hpwh_load_shift(). Must contain 'control'.
    params : dict
        The params dict passed to hpwh_load_shift().
    start_hour : int, optional
        Hour offset for x-axis labels.
    saveas : str, optional
        Save figure to this path instead of showing interactively.
    """
    N = params['n']
    hp_out = operation['control']
    prices = params['price']
    x = list(range(start_hour, N + start_hour + 1))

    fig, ax = plt.subplots(figsize=(13, 4))
    ax2 = ax.twinx()

    hp_plot = hp_out + [hp_out[-1]]
    ax.step(x, hp_plot, where='post', color='steelblue', alpha=0.7,
            label='HP Output')

    price_plot = prices + [prices[-1]]
    ax2.step(x, price_plot, where='post', color='gray', alpha=0.5,
             label='Electricity price')

    load_plot = params['load'] + [params['load'][-1]]
    ax.step(x, load_plot, where='post', color='firebrick', alpha=0.5,
            label='Load')

    soc = _simulate_soc(hp_out, params)
    ax.plot(x, soc, color='darkorange', alpha=0.7, label='Storage SOC')
    ax.axhline(params['max_storage_capacity'], color='darkorange',
               linestyle=':', alpha=0.5, label='Storage limits')
    ax.axhline(params['min_storage_capacity'], color='darkorange',
               linestyle=':', alpha=0.5)

    ax.set_xlabel("Time [hours]")
    ax.set_ylabel("kWh")
    ax2.set_ylabel("Cost [$/kWh]")
    ax.set_xticks(list(x))
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')

    if saveas:
        plt.savefig(saveas, bbox_inches='tight')
    else:
        plt.show()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _simulate_soc(schedule, params):
    """SOC trace: soc[0] = initial_soc; soc[h+1] = soc[h] + schedule[h] - load[h]."""
    N = params['n']
    load = params['load']
    soc = [params['initial_soc']] + [0.0] * N
    for h in range(N):
        soc[h + 1] = soc[h] + schedule[h] - load[h]
    return soc


def _clip_overflow(schedule, params):
    """
    Clip a schedule so that SOC never exceeds max_storage_capacity.

    headroom = max_soc - soc + load[h]   (available charging room)
    clipped[h] = max(min(schedule[h], headroom), 0.0)
    """
    N = params['n']
    load = params['load']
    max_soc = params['max_storage_capacity']

    clipped = list(schedule)
    soc = params['initial_soc']
    for h in range(N):
        headroom = max_soc - soc + load[h]
        clipped[h] = max(min(clipped[h], headroom), 0.0)
        soc = soc + clipped[h] - load[h]
    return clipped


def _build_schedule(schedule, params):
    """Assemble the output dict."""
    N = params['n']
    price = params['price']
    cop = params['cop']
    cost = [schedule[h] * price[h] / cop[h] for h in range(N)]
    return {
        'control': schedule,
        'cost':    cost,
    }


def _validate_params(params):
    """Validate required fields and normalise scalar min/max_input to lists."""
    N = params['n']

    for key in ('price', 'load', 'cop'):
        if len(params[key]) != N:
            raise ValueError(f"params['{key}'] length must equal n ({N}).")

    for key in ('initial_soc', 'min_storage_capacity', 'max_storage_capacity'):
        if key not in params:
            raise KeyError(f"Required parameter missing: '{key}'")

    for key in ('min_input', 'max_input'):
        val = params[key]
        if not hasattr(val, '__len__'):
            params[key] = [float(val)] * N
        elif len(val) != N:
            raise ValueError(f"params['{key}'] length must equal n ({N}).")
        else:
            params[key] = [float(v) for v in val]

    if params['min_storage_capacity'] > params['max_storage_capacity']:
        raise ValueError("min_storage_capacity must be <= max_storage_capacity.")

    return params
