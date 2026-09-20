#!/usr/bin/env python3
"""Reproduce the rate, source-power and incremental-temperature figure."""
import argparse
import csv
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('point', type=Path)
    args = parser.parse_args()
    point = args.point
    os.environ.setdefault('MPLCONFIGDIR', str(point / 'plot-cache'))
    schedule = json.loads((point / 'schedule.json').read_text())
    profile = json.loads((point / 'profile.json').read_text())
    with (point / 'stack-temperatures.csv').open() as stream:
        temperatures = list(csv.DictReader(stream))
    stacks = sorted({row['stack'] for row in temperatures if row['stack'].startswith('hbf')})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
    for stack in stacks:
        x, rates = [], []
        for segment in schedule['segments']:
            x += [segment['start_ns']/1e9, segment['end_ns']/1e9]
            rates += [segment['read_Bps'].get(stack, 0)] * 2
        line, = axes[0].plot(x, [rate/1e12 for rate in rates], label=stack)
        coefficient = profile['array_j_per_byte'] + profile['base_j_per_byte']
        axes[1].plot(x, [coefficient*rate for rate in rates], color=line.get_color())
        selected = [row for row in temperatures if row['stack'] == stack]
        axes[2].plot([0] + [int(row['time_ns'])/1e9 for row in selected],
                     [0] + [float(row['increment_above_300k']) for row in selected],
                     color=line.get_color())
    axes[0].set_ylabel('Scenario read rate (TB/s)')
    axes[1].set_ylabel('Read-induced power (W)')
    axes[2].set_ylabel('Stack hotspot rise (K)')
    axes[2].set_xlabel('Time (s)')
    axes[0].legend(ncol=4, frameon=False)
    for axis in axes:
        axis.grid(alpha=.2)
    fig.suptitle('Conditional rate-driven thermal simulation: 50 pJ/B\n'
                 'Coupled package; idle/GPU baseline excluded; no MQSim transactions')
    fig.savefig(point / 'rate-power-temperature.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
