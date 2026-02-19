"""
CTA-2045 schedule generation from Easy Shift output or OpenADR price signals.

Converts heat pump operation schedules into CTA-2045-B Level 2 demand response
commands for water heaters.

CTA-2045-B commands for water heaters:
    Shed     (-1): Lower setpoint, disable heat pump — coast on stored energy
    Normal    (0): Default operation
    Load Up   (1): Raise setpoint, heat the tank to prepare for a future shed
    Advanced Load Up (2): Aggressively heat tank (max setpoint, tight deadband)

References:
    - CTA-2045-B Level 2 Guidance for Water Heater OEMs
    - CTA-2045-B Level 2 Command Overview v20
"""

import numpy as np


# CTA-2045 signal constants
SHED = -1
NORMAL = 0
LOAD_UP = 1
ADVANCED_LOAD_UP = 2

SIGNAL_NAMES = {
    SHED: "Shed",
    NORMAL: "Normal",
    LOAD_UP: "Load Up",
    ADVANCED_LOAD_UP: "Advanced Load Up",
}


def hpwh_load_shift_to_cta2045(schedule, params):
    """
    Convert hpwh_load_shift output to a CTA-2045 command schedule.

    Mapping logic (output fraction relative to max_input):
        - output = 0           → Shed
        - output < 30% of max  → Normal
        - output 30–80% of max → Load Up
        - output ≥ 80% of max  → Advanced Load Up

    Parameters
    ----------
    schedule : dict
        Output from hpwh_load_shift(). Must contain 'control' and 'cost'.
    params : dict
        The params dict passed to hpwh_load_shift(). Must contain
        'n', 'max_input', and 'price'.

    Returns
    -------
    schedule_out : dict
        CTA-2045 schedule with keys:
        - signals : list[int] — CTA-2045 command per hour (-1, 0, 1, 2)
        - signal_names : list[str] — Human-readable command names
        - hours : list[int] — Hour indices
        - hp_output_kWh : list[float] — HP thermal output from scheduler
        - prices : list[float] — Electricity prices ($/kWh)
        - costs : list[float] — Electricity cost at each hour
    """
    N = params['n']
    control = schedule['control']
    max_input = params['max_input']
    prices = params['price']

    # Normalise scalar max_input to list.
    if not hasattr(max_input, '__getitem__'):
        max_input = [float(max_input)] * N

    signals = []
    for hour in range(N):
        max_out = max_input[hour]
        output = control[hour]

        if max_out == 0:
            signals.append(NORMAL)
        elif output == 0:
            signals.append(SHED)
        elif output / max_out < 0.3:
            signals.append(NORMAL)
        elif output / max_out < 0.8:
            signals.append(LOAD_UP)
        else:
            signals.append(ADVANCED_LOAD_UP)

    return {
        'signals':      signals,
        'signal_names': [SIGNAL_NAMES[s] for s in signals],
        'hours':        list(range(N)),
        'hp_output_kWh': list(control),
        'prices':       list(prices),
        'costs':        list(schedule['cost']),
    }


def easy_shift_to_cta2045(operation, parameters):
    """
    Convert Easy Shift output to a CTA-2045 command schedule.

    Maps the continuous heat pump output (kWh) from Easy Shift into discrete
    CTA-2045 commands based on operation level relative to capacity.

    Mapping logic:
        - HP off (output = 0) during high-price hours → Shed
        - HP at low output (< 30% of max) → Normal
        - HP at moderate output (30-80% of max) → Load Up
        - HP at high output (>= 80% of max) → Advanced Load Up

    Parameters
    ----------
    operation : dict
        Output from easy_shift(). Must contain 'control' and 'cost' keys.
    parameters : dict
        The same parameters dict passed to easy_shift(). Must contain
        'horizon', 'control' (with 'max'), and 'elec_costs'.

    Returns
    -------
    schedule : dict
        CTA-2045 schedule with keys:
        - signals : list[int] — CTA-2045 command per hour (-1, 0, 1, 2)
        - signal_names : list[str] — Human-readable command names
        - hours : list[int] — Hour indices
        - hp_output_kWh : list[float] — Original HP output from Easy Shift
        - prices : list[float] — Electricity prices ($/kWh)
        - costs : list[float] — Electricity cost at each hour
    """
    N = parameters['horizon']
    control = operation['control']
    control_max = parameters['control']['max']
    prices = parameters['elec_costs']

    signals = []
    for hour in range(N):
        max_output = control_max[hour]
        output = control[hour]

        if max_output == 0:
            signals.append(NORMAL)
        elif output == 0:
            signals.append(SHED)
        elif output / max_output < 0.3:
            signals.append(NORMAL)
        elif output / max_output < 0.8:
            signals.append(LOAD_UP)
        else:
            signals.append(ADVANCED_LOAD_UP)

    return {
        'signals': signals,
        'signal_names': [SIGNAL_NAMES[s] for s in signals],
        'hours': list(range(N)),
        'hp_output_kWh': list(control),
        'prices': list(prices),
        'costs': list(operation['cost']),
    }


def prices_to_cta2045(prices, thresholds=None):
    """
    Convert electricity prices directly to CTA-2045 commands using price thresholds.

    This is a simpler alternative to running Easy Shift first. It uses price
    percentiles to classify each hour into a CTA-2045 command.

    Default thresholds (percentile-based):
        - Below 25th percentile → Advanced Load Up (very cheap, heat aggressively)
        - 25th to 50th percentile → Load Up (cheap, pre-heat)
        - 50th to 75th percentile → Normal (moderate price)
        - Above 75th percentile → Shed (expensive, coast)

    Parameters
    ----------
    prices : list[float]
        Electricity prices ($/kWh) for each hour.
    thresholds : dict, optional
        Custom percentile thresholds with keys:
        - shed_above : float — Percentile above which to shed (default 75)
        - normal_above : float — Percentile above which to use normal (default 50)
        - load_up_above : float — Percentile above which to load up (default 25)
        Below load_up_above → Advanced Load Up.

    Returns
    -------
    schedule : dict
        CTA-2045 schedule with keys:
        - signals : list[int] — CTA-2045 command per hour
        - signal_names : list[str] — Human-readable command names
        - hours : list[int] — Hour indices
        - prices : list[float] — Input prices
        - thresholds_used : dict — Price thresholds ($/kWh) used for classification
    """
    if thresholds is None:
        thresholds = {
            'shed_above': 75,
            'normal_above': 50,
            'load_up_above': 25,
        }

    prices_arr = np.array(prices)
    p_shed = np.percentile(prices_arr, thresholds['shed_above'])
    p_normal = np.percentile(prices_arr, thresholds['normal_above'])
    p_load_up = np.percentile(prices_arr, thresholds['load_up_above'])

    signals = []
    for price in prices:
        if price >= p_shed:
            signals.append(SHED)
        elif price >= p_normal:
            signals.append(NORMAL)
        elif price >= p_load_up:
            signals.append(LOAD_UP)
        else:
            signals.append(ADVANCED_LOAD_UP)

    return {
        'signals': signals,
        'signal_names': [SIGNAL_NAMES[s] for s in signals],
        'hours': list(range(len(prices))),
        'prices': list(prices),
        'thresholds_used': {
            'shed_above': float(p_shed),
            'normal_above': float(p_normal),
            'load_up_above': float(p_load_up),
        },
    }


def format_schedule(schedule):
    """
    Format a CTA-2045 schedule as a human-readable string.

    Parameters
    ----------
    schedule : dict
        Output from easy_shift_to_cta2045() or prices_to_cta2045().

    Returns
    -------
    str
        Formatted schedule table.
    """
    lines = []
    lines.append(f"{'Hour':>4}  {'Signal':>4}  {'Command':<18}  {'Price':>10}")
    lines.append("-" * 45)

    has_output = 'hp_output_kWh' in schedule
    if has_output:
        lines[0] = f"{'Hour':>4}  {'Signal':>4}  {'Command':<18}  {'Price':>10}  {'HP Output':>10}"
        lines[1] = "-" * 60

    for i, hour in enumerate(schedule['hours']):
        signal = schedule['signals'][i]
        name = schedule['signal_names'][i]
        price = schedule['prices'][i]

        if has_output:
            output = schedule['hp_output_kWh'][i]
            lines.append(
                f"{hour:>4}  {signal:>4}  {name:<18}  ${price:>9.5f}  {output:>8.2f} kWh"
            )
        else:
            lines.append(f"{hour:>4}  {signal:>4}  {name:<18}  ${price:>9.5f}")

    return "\n".join(lines)


def plot_schedule(schedule, start_hour=0, saveas=""):
    """
    Plot a CTA-2045 schedule showing commands and prices over time.

    Parameters
    ----------
    schedule : dict
        Output from easy_shift_to_cta2045() or prices_to_cta2045().
    start_hour : int, optional
        Hour offset for x-axis labels.
    saveas : str, optional
        If non-empty, save the plot to this filename instead of showing it.
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    N = len(schedule['signals'])
    hours = [h + start_hour for h in range(N)]

    signal_colors = {
        SHED: '#e74c3c',           # red
        NORMAL: '#95a5a6',         # gray
        LOAD_UP: '#3498db',        # blue
        ADVANCED_LOAD_UP: '#2ecc71',  # green
    }

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 6), sharex=True,
                                    gridspec_kw={'height_ratios': [1, 1.5]})

    # Top: CTA-2045 signals as colored bars
    colors = [signal_colors[s] for s in schedule['signals']]
    ax1.bar(hours, schedule['signals'], color=colors, width=0.8, align='center')
    ax1.set_ylabel("CTA-2045 Signal")
    ax1.set_yticks([SHED, NORMAL, LOAD_UP, ADVANCED_LOAD_UP])
    ax1.set_yticklabels(["Shed (-1)", "Normal (0)", "Load Up (1)", "Adv Load Up (2)"])
    ax1.axhline(y=0, color='black', linewidth=0.5)
    ax1.set_title("CTA-2045 Demand Response Schedule")

    patches = [mpatches.Patch(color=signal_colors[s], label=SIGNAL_NAMES[s])
               for s in [ADVANCED_LOAD_UP, LOAD_UP, NORMAL, SHED]]
    ax1.legend(handles=patches, loc='upper right', fontsize=8)

    # Bottom: prices (and HP output if available)
    x_step = hours + [hours[-1] + 1]
    prices_step = list(schedule['prices']) + [schedule['prices'][-1]]
    ax2.step(x_step, prices_step, where='post', color='gray', alpha=0.8, label='Price ($/kWh)')
    ax2.set_ylabel("Price ($/kWh)")
    ax2.set_xlabel("Hour")

    if 'hp_output_kWh' in schedule:
        ax2_twin = ax2.twinx()
        output_step = list(schedule['hp_output_kWh']) + [schedule['hp_output_kWh'][-1]]
        ax2_twin.step(x_step, output_step, where='post', color='blue', alpha=0.5,
                      label='HP Output (kWh)')
        ax2_twin.set_ylabel("HP Output (kWh)")
        ax2_twin.legend(loc='upper right')

    ax2.legend(loc='upper left')
    ax2.set_xticks(hours)

    plt.tight_layout()
    if saveas:
        plt.savefig(saveas)
    else:
        plt.show()
