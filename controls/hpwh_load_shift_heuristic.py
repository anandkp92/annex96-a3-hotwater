"""
HPWH Load Shift — Heuristic Scheduler.

A bottom-up greedy algorithm for scheduling a heat-pump water heater (HPWH)
to minimise electricity cost while meeting an hourly hot-water load.

Algorithm overview
------------------
The algorithm works in two phases per iteration:

  Phase A — minimum-input baseline
    Apply min_input to every hour from 0 through the first unsatisfied hour.
    This guarantees the tank is always at least trickling charge.

  Phase B — cheapest-hour boost
    From the cheapest eligible hour (≤ first unsatisfied hour) to the most
    expensive, push output toward max_input until the first unsatisfied hour
    is covered.

After every assignment, overflow clipping is applied to prevent the tank
from exceeding max_storage_capacity.

Convergence
-----------
  converged = True   All hours satisfied within max_iterations.
  converged = False  Load cannot be met (e.g. max_input < load).
                     Returns the schedule clipped to max_input as a
                     best-effort result.

Public API
----------
  hpwh_load_shift(params, verbose, start_hour) -> (schedule_dict, bool)
  simulate_soc(schedule, params)               -> list[float]
  get_storage(schedule, params)                -> list[float]   (alias)
  iteration_plot(operation, params, ...)       -> None
"""

import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def hpwh_load_shift(params, verbose=False, start_hour=0):
    """
    Compute a heuristic HPWH schedule that minimises electricity cost.

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
            Coefficient of performance per interval (convert thermal→electrical).
        initial_soc         : float
            Tank state of charge at the start [kWh].
        min_storage_capacity : float
            Minimum SOC reserve that must be maintained [kWh].
        max_storage_capacity : float
            Maximum tank SOC [kWh].
        min_input           : float or list[float]
            Minimum HP thermal output per interval [kWh].
            A scalar is broadcast to all intervals.
        max_input           : float or list[float]
            Maximum HP thermal output per interval [kWh].
            A scalar is broadcast to all intervals.

    verbose : bool, optional
        Print iteration details. Default False.
    start_hour : int, optional
        Hour offset used for plot x-axis labels only. Default 0.

    Returns
    -------
    schedule : dict
        control  : list[float]  — HP thermal output per interval [kWh].
        cost     : list[float]  — Electricity cost per interval [$].
    converged : bool
    """
    params = _validate_params(params)
    N = params['n']
    min_inp = params['min_input']
    max_inp = params['max_input']

    # Check whether the initial SOC alone satisfies the full load.
    zero_schedule = [0.0] * N
    if _first_unsatisfied(zero_schedule, params) is None:
        if verbose:
            print("Initial SOC covers the entire load. No charging needed.")
        return _build_schedule(zero_schedule, params), True

    # Price-rank: indices sorted cheapest first.
    price_rank = _rank_by_price(params['price'])

    schedule = [0.0] * N
    max_iters = 3 * N

    for iteration in range(1, max_iters + 1):
        target = _first_unsatisfied(schedule, params)
        if target is None:
            if verbose:
                print(f"Converged in {iteration - 1} iterations.")
            return _build_schedule(schedule, params), True

        if verbose:
            print(f"\nIteration {iteration}: first unsatisfied hour = {target}")

        # ---- Phase A: apply min_input baseline up to and including target ----
        changed = False
        for h in range(target + 1):
            if schedule[h] < min_inp[h]:
                schedule[h] = min_inp[h]
                changed = True

        schedule = _clip_overflow(schedule, params)

        # Re-check: baseline alone may have satisfied target.
        new_target = _first_unsatisfied(schedule, params)
        if new_target is None:
            if verbose:
                print("  Phase A satisfied all hours.")
            return _build_schedule(schedule, params), True
        if new_target != target:
            if verbose:
                print(f"  Phase A advanced target from {target} to {new_target}.")
            continue  # outer loop will re-evaluate

        # ---- Phase B: boost cheapest eligible hours toward max_input --------
        boosted_any = False
        for hour in price_rank:
            if hour > target:
                continue
            if schedule[hour] >= max_inp[hour]:
                continue

            schedule[hour] = max_inp[hour]
            schedule = _clip_overflow(schedule, params)
            boosted_any = True

            if verbose:
                print(f"  Phase B: boosted hour {hour} "
                      f"(price=${params['price'][hour]:.5f}/kWh)")

            new_target = _first_unsatisfied(schedule, params)
            if new_target is None:
                if verbose:
                    print("  Phase B satisfied all hours.")
                return _build_schedule(schedule, params), True
            if new_target != target:
                break  # progress; restart outer loop

        if not boosted_any:
            # No eligible hour could be boosted.  Load exceeds max capacity.
            if verbose:
                print("  No eligible hours remain. Returning max-input schedule.")
            fallback = _clip_overflow(list(max_inp), params)
            return _build_schedule(fallback, params), False

    # Max iterations reached.
    if verbose:
        print("Max iterations reached without convergence.")
    return _build_schedule(schedule, params), False


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


def _first_unsatisfied(schedule, params):
    """
    Return the index of the first interval where the load is not met,
    or None if all intervals are satisfied.

    An interval is satisfied when:  soc + schedule[h] >= load[h] + min_soc
    (meaning the tank can supply the load while maintaining the reserve).
    """
    load = params['load']
    min_soc = params['min_storage_capacity']
    soc = params['initial_soc']
    for h in range(params['n']):
        if soc + schedule[h] >= load[h] + min_soc:
            soc = soc + schedule[h] - load[h]
        else:
            return h
    return None


def _clip_overflow(schedule, params):
    """
    Forward-simulate the schedule and clip any hour that would push SOC
    above max_storage_capacity.

    For each hour:
        headroom  = max_soc - soc + load[h]   (room to charge without overflow)
        clipped   = max(min(schedule[h], headroom), 0.0)
        soc       = soc + clipped - load[h]

    Returns a new list; does not modify the input.
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


def _rank_by_price(prices):
    """Return hour indices sorted cheapest first (ties broken by index)."""
    return sorted(range(len(prices)), key=lambda h: (prices[h], h))


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

    # Broadcast scalar min/max_input to per-hour lists.
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
