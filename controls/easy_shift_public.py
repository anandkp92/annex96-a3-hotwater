"""
EasyShift: Equipment Scheduling Algorithm for Thermal Energy Storage with Load Shifting.

Independent implementation written from the publicly described algorithm:
  B. Woo-Shem and P. Grant, "EASY-SHIFT: Equipment Scheduling Algorithm
  for Thermal Energy Storage with Load Shifting," Lawrence Berkeley National Laboratory.
  Reference presentation:
    https://drive.google.com/file/d/1ustmh-rE7693udh-mc096bhgSyDrT89D/view

Algorithm (six steps from the public reference):
  1. Rank all hours by electricity price, cheapest first.
  2. Find the first hour where load demand is not satisfied.
  3. Assign the heat pump at maximum capacity to the cheapest eligible hour
     (an "eligible" hour is one at or before the unsatisfied hour — pre-heating only).
  4. Storage constraint: if the tank would overflow, reduce output until it fits.
  5. Cheaper-hours look-ahead: if a cheaper hour lies ahead, reduce the current
     hour's output to preserve tank capacity for that cheaper hour.
  6. Repeat steps 2–5 until all hours are satisfied or a maximum iteration
     count is exceeded (returns converged=False as a fallback signal).

Public API (function names and return dict keys):
  easy_shift(params, verbose, start_hour) -> (schedule_dict, converged_bool)
  simulate_soc(hp_out, params)           -> list[float]  (same as get_storage)
  get_storage(hp_out, params)            -> list[float]  (alias of simulate_soc)
  iteration_plot(schedule, params, ...)  -> None
"""

import random

import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def easy_shift(params, verbose=False, start_hour=0):
    """
    Compute a near-optimal heat pump schedule minimising electricity cost
    while meeting hourly load and respecting thermal storage constraints.

    Parameters
    ----------
    params : dict
        horizon : int
            Number of hours in the scheduling window.
        elec_costs : list[float]
            Hourly electricity prices [$/kWh], length = horizon.
        load : dict
            type  : str           — must be 'hourly'.
            value : list[float]   — hourly thermal load [kWh], length = horizon.
        control : dict
            max   : list[float]   — max HP output per hour [kWh].
            min   : list[float]   — min HP output per hour [kWh].
            units : str, optional — y-axis label for plots.
            name  : str, optional — legend label for plots.
        constraints : dict
            storage_capacity : bool  — enforce storage limits.
            max_storage      : float — max tank SOC [kWh].
            min_storage      : float — minimum SOC reserve [kWh].
            initial_soc      : float — starting SOC [kWh].
            cheaper_hours    : bool  — apply cheaper-hours look-ahead.
        hardware : dict
            heatpump : bool        — True = heat pump (apply COP to cost).
            COP      : list[float] — coefficient of performance per hour.

    verbose : bool, optional
        Print iteration details. Default False.
    start_hour : int, optional
        Hour offset used only for plotting, not for scheduling. Default 0.

    Returns
    -------
    schedule : dict
        control     : list[float] — HP thermal output per hour [kWh].
        control_max : list[float] — effective per-hour cap (may be reduced).
        mode        : list        — unused; NaN-filled for API compatibility.
        cost        : list[float] — electricity cost per hour [$].
    converged : bool
        True if all hours are satisfied; False triggers fallback logic.
    """
    params = _validate_params(params)
    N = params['horizon']

    # Working schedule: hp_out may grow each iteration; hp_max may shrink.
    hp_out = [0.0] * N
    hp_max = list(params['control']['max'])

    # Step 1 — rank hours cheapest first (ties broken by random perturbation).
    price_rank = _rank_hours_by_price(params['elec_costs'])

    max_iters = 2 * N
    n_iter = 0

    while True:
        n_iter += 1
        if n_iter > max_iters:
            if verbose:
                print("EasyShift: max iterations reached without convergence.")
            return _build_schedule(hp_out, hp_max, params), False

        # Step 2 — find the first hour where load is not satisfied.
        unsat = _unsatisfied_mask(hp_out, params)
        if sum(unsat) == 0:
            if verbose:
                print(f"EasyShift: converged in {n_iter} iterations.")
            return _build_schedule(hp_out, hp_max, params), True

        target = unsat.index(1)
        if verbose:
            print(f"\nIteration {n_iter}: first unsatisfied hour = {target}")

        assigned_any = False

        for hour in price_rank:
            # Pre-heat only: skip hours that occur after the unsatisfied one.
            if hour > target:
                continue

            # Skip hours already running at their effective maximum.
            if hp_out[hour] >= hp_max[hour]:
                continue

            # Skip hours blocked by a storage-full event at an earlier hour.
            if params['constraints']['storage_capacity']:
                soc_trace = _simulate_soc(hp_out, params)
                full_idx = next(
                    (h for h in range(len(soc_trace))
                     if round(soc_trace[h], 1) == params['constraints']['max_storage']),
                    None,
                )
                if full_idx is not None and hour <= full_idx and hour != 0:
                    continue

            if verbose:
                print(f"  Assigning hour {hour} "
                      f"(price = ${params['elec_costs'][hour]:.4f}/kWh)")

            # Snapshot the full schedule and the scalar value for this hour
            # before making any changes (used as the base for constraint checks).
            backup_val = hp_out[hour]
            backup_arr = hp_out.copy()

            # Step 3 — assign maximum output to this hour.
            hp_out[hour] = hp_max[hour]

            # Step 4 — reduce output if tank would overflow.
            if params['constraints']['storage_capacity']:
                hp_out, hp_max = _apply_storage_cap(
                    hour, backup_val, hp_out, hp_max, params, verbose
                )

            # Step 5 — reduce output if a cheaper upcoming hour can serve load.
            if params['constraints']['cheaper_hours']:
                hp_out = _apply_cheaper_hours(
                    hour, backup_arr, target, hp_out, hp_max, params, verbose
                )

            assigned_any = True

            new_unsat = _unsatisfied_mask(hp_out, params)
            if sum(new_unsat) == 0:
                return _build_schedule(hp_out, hp_max, params), True

            # Step 6 — if the unsatisfied front advanced, restart the scan.
            if new_unsat.index(1) != target:
                break  # progress made; outer while re-evaluates

        if not assigned_any:
            # Exhausted all eligible hours with no assignments possible.
            if verbose:
                print("EasyShift: no eligible hours remain; cannot converge.")
            return _build_schedule(hp_out, hp_max, params), False


def simulate_soc(hp_out, params):
    """
    Forward-simulate tank state of charge for a given HP output schedule.

    Parameters
    ----------
    hp_out : list[float]
        HP thermal output per hour [kWh].
    params : dict
        Must contain: constraints.initial_soc, horizon, load.value.

    Returns
    -------
    list[float]
        SOC at each hour boundary; length = horizon + 1.
        soc[0] = initial_soc; soc[h+1] = soc[h] + hp_out[h] - load[h].
    """
    return _simulate_soc(hp_out, params)


# Alias so callers using the old name (get_storage) work unchanged.
def get_storage(control, parameters):
    """Alias for simulate_soc; provided for backward compatibility."""
    return _simulate_soc(control, parameters)


def iteration_plot(operation, parameters, start_hour=0, saveas=""):
    """
    Plot the HP schedule against prices, load, and storage levels.

    Parameters
    ----------
    operation : dict
        Output from easy_shift(). Must contain 'control'.
    parameters : dict
        The parameters dict passed to easy_shift().
    start_hour : int, optional
        Hour offset for x-axis labels.
    saveas : str, optional
        Save figure to this path instead of showing interactively.
    """
    N = parameters['horizon']
    hp_out = operation['control']
    prices = parameters['elec_costs']
    x = list(range(start_hour, N + start_hour + 1))

    fig, ax = plt.subplots(figsize=(13, 4))
    ax2 = ax.twinx()

    hp_plot = hp_out + [hp_out[-1]]
    ax.step(x, hp_plot, where='post', color='steelblue', alpha=0.7,
            label=parameters['control'].get('name', 'HP Output'))

    price_plot = prices + [prices[-1]]
    price_label = 'Electricity price'
    if 'price_structure_name' in parameters:
        price_label += ', ' + parameters['price_structure_name']
    ax2.step(x, price_plot, where='post', color='gray', alpha=0.5,
             label=price_label)

    if parameters['load']['type'] == 'hourly':
        load_plot = parameters['load']['value'] + [parameters['load']['value'][-1]]
        ax.step(x, load_plot, where='post', color='firebrick', alpha=0.5,
                label='Load')

    if parameters['constraints']['storage_capacity']:
        soc = _simulate_soc(hp_out, parameters)
        ax.plot(x, soc, color='darkorange', alpha=0.7, label='Storage SOC')
        ax.plot(x, [parameters['constraints']['max_storage']] * len(x),
                color='darkorange', linestyle=':', alpha=0.5,
                label='Storage limits')
        ax.plot(x, [parameters['constraints']['min_storage']] * len(x),
                color='darkorange', linestyle=':', alpha=0.5)

    ax.set_xlabel("Time [hours]")
    ax.set_ylabel(parameters['control'].get('units', 'kWh'))
    ax2.set_ylabel("Cost [USD/kWh]")
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

def _simulate_soc(hp_out, params):
    """
    Compute SOC at each hour boundary without any capacity clamping.

    soc[0] = initial_soc
    soc[h+1] = soc[h] + hp_out[h] - load[h]
    """
    N = params['horizon']
    load = params['load']['value']
    soc = [params['constraints']['initial_soc']] + [0.0] * N
    for h in range(N):
        soc[h + 1] = soc[h] + hp_out[h] - load[h]
    return soc


def _unsatisfied_mask(hp_out, params):
    """
    Return a list of 0/1 flags indicating whether each hour's load is met.

    1 = load not satisfied; 0 = satisfied.

    The check is sequential: each hour's contribution to storage is only
    carried forward if that hour is satisfied.  This mirrors the
    forward-simulation used in the original reference algorithm.
    """
    N = params['horizon']
    load = params['load']['value']
    min_soc = params['constraints']['min_storage']

    soc = params['constraints']['initial_soc']
    mask = [1] * N
    for h in range(N):
        if soc + hp_out[h] >= load[h] + min_soc:
            soc = soc + hp_out[h] - load[h]
            mask[h] = 0
    return mask


def _rank_hours_by_price(prices):
    """
    Return hour indices sorted cheapest-first.

    Equal prices are broken by a small random perturbation so the ranking
    is deterministic within a run but avoids systematic first-hour bias.
    """
    perturbed = list(prices)
    changed = True
    while changed:
        changed = False
        for i in range(len(perturbed)):
            for j in range(i + 1, len(perturbed)):
                if perturbed[i] == perturbed[j]:
                    perturbed[j] = round(
                        perturbed[j] + random.uniform(-1e-4, 1e-4), 8
                    )
                    changed = True
    return sorted(range(len(prices)), key=lambda h: perturbed[h])


def _apply_storage_cap(hour, backup_val, hp_out, hp_max, params, verbose):
    """
    Algorithm step 4: reduce hp_out[hour] if the tank would overflow.

    Parameters
    ----------
    hour : int
    backup_val : float
        hp_out[hour] before the current assignment (pre-step-3 value).
    hp_out, hp_max : list[float]
        Current schedule and per-hour caps (modified in place).
    params : dict
    verbose : bool

    Returns
    -------
    (hp_out, hp_max) — updated in place and returned for clarity.

    Notes
    -----
    overflow = max excess storage across the horizon, rounded to 1 decimal.

    If removing the overflow still leaves output above both the minimum
    control level and the pre-assignment value, apply the reduction and
    also lower hp_max[hour] to prevent re-inflation on future iterations.
    Otherwise revert hp_out[hour] to its pre-assignment value.
    """
    max_soc = params['constraints']['max_storage']
    soc_trace = _simulate_soc(hp_out, params)
    overflow = round(max(0.0, max(s - max_soc for s in soc_trace)), 1)

    if overflow == 0.0:
        if verbose:
            print(f"    Hour {hour}: storage OK, no reduction needed.")
        return hp_out, hp_max

    reduced = hp_out[hour] - overflow
    min_ctrl = params['control']['min'][hour]

    if reduced > min_ctrl and reduced > backup_val:
        if verbose:
            print(f"    Hour {hour}: reduced by {overflow:.2f} kWh "
                  f"(storage overflow). New output: {reduced:.2f}")
        hp_out[hour] = reduced
        hp_max[hour] = reduced
    else:
        # Cannot absorb the overflow by reducing this hour alone — revert.
        if verbose:
            print(f"    Hour {hour}: cannot absorb overflow={overflow:.2f}, "
                  f"reverting to {backup_val:.2f}.")
        hp_out[hour] = backup_val

    return hp_out, hp_max


def _apply_cheaper_hours(hour, backup_arr, target, hp_out, hp_max,
                         params, verbose):
    """
    Algorithm step 5: reduce hp_out[hour] if a cheaper upcoming hour exists.

    After step 3/4, some previously unsatisfied hours may now be satisfied.
    If a cheaper hour lies in the newly satisfied window, we can save money
    by reducing the current hour's output — leaving more tank capacity for
    the cheaper hour to fill later.

    Parameters
    ----------
    hour : int
        The hour whose output we may reduce.
    backup_arr : list[float]
        Full schedule snapshot taken *before* the step-3 assignment.
        Used as the base for feasibility test scenarios.
    target : int
        The first unsatisfied hour at the start of this outer iteration.
    hp_out : list[float]
        Current schedule (after step 4); modified in place.
    hp_max : list[float]
        Effective per-hour caps (after step 4).
    params : dict
    verbose : bool

    Returns
    -------
    hp_out : list[float]
    """
    prices = params['elec_costs']
    load = params['load']['value']
    min_soc = params['constraints']['min_storage']

    # Recompute status after the step-3/4 assignment.
    new_unsat = _unsatisfied_mask(hp_out, params)
    if sum(new_unsat) == 0:
        return hp_out  # already fully satisfied

    new_target = new_unsat.index(1)
    if new_target == target:
        return hp_out  # unsatisfied front did not advance; nothing to defer to

    # Is there any hour in [target, new_target] that is cheaper than `hour`?
    window = list(range(target, new_target + 1))
    window_prices = [prices[h] for h in window]
    if min(window_prices) >= prices[hour]:
        return hp_out  # no cheaper option in the window

    # Find the first (lowest-index) cheaper hour in the window.
    cheaper_hour = next(
        h for h in window if prices[h] < prices[hour]
    )

    # Scan output levels for `hour` from min to (post-step-4) max.
    # Select the level that leaves the lowest SOC at cheaper_hour —
    # maximising the capacity available for the cheaper hour to exploit —
    # while keeping all hours up to cheaper_hour feasible.
    min_ctrl = params['control']['min'][hour]
    max_ctrl = hp_max[hour]  # already reduced by storage cap if needed
    nsteps = 10

    best_ctrl = hp_out[hour]
    best_soc_at_cheaper = float('inf')

    for step in range(int(min_ctrl * nsteps), int(max_ctrl * nsteps) + 1):
        c = round(step / nsteps, 6)

        # Build a test schedule using the pre-assignment snapshot for all
        # hours except `hour`, which takes the candidate level `c`.
        test_out = backup_arr.copy()
        test_out[hour] = c

        test_unsat = _unsatisfied_mask(test_out, params)
        test_soc = _simulate_soc(test_out, params)

        # The hour immediately before the cheaper hour must stay satisfied;
        # if it becomes unsatisfied at level c, that level is not viable.
        if cheaper_hour > 0 and test_unsat[cheaper_hour - 1] != 0:
            continue

        # Even at its maximum output, the cheaper hour must be able to
        # satisfy its own load.  Skip c if it makes that impossible.
        if (load[cheaper_hour] + min_soc
                >= params['control']['max'][cheaper_hour]
                + test_soc[cheaper_hour]):
            continue

        # Prefer the c that leaves the least energy in the tank just before
        # the cheaper hour (more room for the cheaper hour to operate).
        if test_soc[cheaper_hour] < best_soc_at_cheaper:
            best_soc_at_cheaper = test_soc[cheaper_hour]
            best_ctrl = c

    if verbose and best_ctrl != hp_out[hour]:
        print(f"    Hour {hour}: cheaper-hours deferred from "
              f"{hp_out[hour]:.2f} to {best_ctrl:.2f} "
              f"(cheaper hour = {cheaper_hour}, "
              f"price = ${prices[cheaper_hour]:.4f}/kWh).")

    hp_out[hour] = best_ctrl
    return hp_out


def _build_schedule(hp_out, hp_max, params):
    """Assemble and return the output schedule dictionary."""
    N = params['horizon']
    prices = params['elec_costs']
    cop = params['hardware']['COP']
    is_hp = params['hardware']['heatpump']

    hourly_cost = []
    for h in range(N):
        elec_kwh = hp_out[h] * prices[h]
        if is_hp and cop[h] > 0:
            elec_kwh /= cop[h]
        hourly_cost.append(elec_kwh)

    return {
        'control':     hp_out,
        'control_max': hp_max,
        'mode':        [np.nan] * N,   # unused; kept for API compatibility
        'cost':        hourly_cost,
    }


def _validate_params(params):
    """Validate required fields and fill in optional defaults."""
    N = params['horizon']

    if len(params['elec_costs']) != N:
        raise ValueError(
            f"elec_costs length ({len(params['elec_costs'])}) "
            f"must equal horizon ({N})."
        )

    if (params['load']['type'] == 'hourly'
            and len(params['load']['value']) != N):
        raise ValueError("load.value length must equal horizon.")

    if (len(params['control']['max']) != N
            or len(params['control']['min']) != N):
        raise ValueError("control max and min lengths must equal horizon.")

    params['constraints'].setdefault('storage_capacity', False)
    params['constraints'].setdefault('cheaper_hours', False)
    params['constraints'].setdefault('initial_soc', 0.0)
    params['constraints'].setdefault('min_storage', 0.0)
    params['control'].setdefault('units', 'kWh')
    params['control'].setdefault('name', 'HP Output')

    return params
