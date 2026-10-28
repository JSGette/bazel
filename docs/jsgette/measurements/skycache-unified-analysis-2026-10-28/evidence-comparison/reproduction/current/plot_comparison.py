#!/usr/bin/env python3
"""Plot prevalidated passed/measured three-arm rows as raw samples and medians.

Input: JSON array with workload, arm, elapsed_seconds, total_cpu_seconds,
maxrss_kib. Arm values are vanilla, fork-off, fork-on. This script cannot prove
command/BEP validation from those five fields; export only validated measured
commands from their archived run summaries. Optional status/role fields, when
provided, must be passed/measured. --output accepts PNG or SVG; the default is
<input-stem>-comparison.png beside the input. Existing outputs are not replaced.
Matplotlib is needed only for rendering; validation uses the standard library.
"""

import argparse
import json
import math
from pathlib import Path
import statistics

ARMS = ('vanilla', 'fork-off', 'fork-on')
LABELS = ('Vanilla', 'Fork,\nSkycache off', 'Fork,\nSkycache on')
COLORS = ('#737982', '#a4abb3', '#376d9e')
METRICS = (('elapsed_seconds', 'Latency', 'Seconds', 1),
           ('total_cpu_seconds', 'Total CPU', 'Seconds', 1),
           ('maxrss_kib', 'Peak child RSS', 'MiB', 1 / 1024))


def validate_rows(value):
    if not isinstance(value, list) or not value:
        raise ValueError('input must be a nonempty JSON array of measured rows')
    workloads = {}
    for number, row in enumerate(value, 1):
        if not isinstance(row, dict):
            raise ValueError(f'row {number}: expected an object')
        if row.get('status', 'passed') != 'passed' or row.get('role', 'measured') != 'measured':
            raise ValueError(f'row {number}: only passed measured commands may be plotted')
        workload = row.get('workload')
        if not isinstance(workload, str) or not workload.strip():
            raise ValueError(f'row {number}: workload must be a nonempty string')
        arm = row.get('arm')
        if arm not in ARMS:
            raise ValueError(f'row {number}: unrecognized arm {arm!r}')
        for key, unused_title, unused_unit, unused_scale in METRICS:
            metric = row.get(key)
            if type(metric) not in (int, float) or not math.isfinite(metric) or metric < 0:
                raise ValueError(f'row {number}: {key} must be a finite nonnegative number')
            if key != 'total_cpu_seconds' and metric == 0:
                raise ValueError(f'row {number}: {key} must be positive')
        groups = workloads.setdefault(workload, {name: [] for name in ARMS})
        groups[arm].append(row)
    for workload, groups in workloads.items():
        missing = [name for name, rows in groups.items() if not rows]
        if missing:
            raise ValueError(f'workload {workload!r}: missing arms {missing}')
    return workloads


def number_label(value):
    if value >= 100:
        return f'{value:,.0f}'
    if value >= 10:
        return f'{value:,.1f}'
    return f'{value:,.2f}'


def render(workloads, output):
    output = Path(output)
    if output.suffix.lower() not in ('.png', '.svg'):
        raise ValueError('output must have a .png or .svg extension')
    if output.exists() or output.is_symlink():
        raise FileExistsError('output already exists; choose a new path')
    if not output.parent.is_dir():
        raise ValueError('output parent directory must already exist')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.titlesize': 12, 'axes.labelsize': 10,
                         'text.parse_math': False, 'svg.fonttype': 'none'})
    count = len(workloads)
    figure, axes = plt.subplots(count, 3, figsize=(12.8, 3.9 * count + 1.35), squeeze=False)
    figure.patch.set_facecolor('white')
    sample_counts = []
    for row_index, (workload, groups) in enumerate(workloads.items()):
        ns = [len(groups[arm]) for arm in ARMS]
        sample_counts.append(f'{workload}: n=' + '/'.join(map(str, ns)))
        for column, (metric, title, unit, scale) in enumerate(METRICS):
            axis = axes[row_index, column]
            values = {arm: sorted(row[metric] * scale for row in groups[arm]) for arm in ARMS}
            maximum = max(max(samples) for samples in values.values())
            axis.set_ylim(0, maximum * 1.25 if maximum > 0 else 1)
            axis.set_xlim(-0.6, 2.6)
            for index, arm in enumerate(ARMS):
                samples = values[arm]
                # Deterministic spread makes repeated equal values visible; there is no random resampling.
                offsets = [0] if len(samples) == 1 else [-0.13 + 0.26 * i / (len(samples) - 1)
                                                       for i in range(len(samples))]
                axis.scatter([index + offset for offset in offsets], samples,
                             s=28, color=COLORS[index], alpha=0.65, edgecolors='none', zorder=3)
                median = statistics.median(samples)
                axis.plot([index - 0.20, index + 0.20], [median, median],
                          color=COLORS[index], linewidth=3.2, solid_capstyle='round', zorder=4)
                axis.annotate(number_label(median), (index, median), xytext=(0, 11),
                              textcoords='offset points', ha='center', va='bottom',
                              color='#252c33', fontsize=10,
                              bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': 0.88, 'pad': 1}, zorder=5)
            axis.set_xticks(range(3), LABELS)
            axis.set_ylabel(unit)
            axis.set_title(title, loc='left', pad=10)
            axis.grid(axis='y', color='#e4e7eb', linewidth=0.7)
            axis.set_axisbelow(True)
            axis.tick_params(axis='both', length=0, labelcolor='#4d555e')
            for side in ('top', 'right', 'left'):
                axis.spines[side].set_visible(False)
            axis.spines['bottom'].set_color('#c5cbd1')
        axes[row_index, 0].text(0, 1.19, workload, transform=axes[row_index, 0].transAxes,
                                fontsize=13, weight='bold', color='#252c33', va='bottom')
    figure.suptitle('Three-arm comparison', x=0.06, y=0.995, ha='left', fontsize=17, weight='bold')
    figure.legend(handles=[Line2D([], [], marker='o', linestyle='none', color='#737982',
                                  markersize=5, label='Recorded sample'),
                           Line2D([], [], color='#737982', linewidth=3, label='Median (numeric label)')],
                  loc='upper right', bbox_to_anchor=(0.975, 0.995), frameon=False, ncol=2)
    caption = ('Passed measured commands only; descriptive samples, no confidence intervals or tail estimates.\n'
               'Peak RSS is the maximum child-process RSS, not retained heap. Axes start at zero; panel scales vary.\n'
               'Sample counts in Vanilla / Fork off / Fork on order: ' + '; '.join(sample_counts))
    figure.text(0.06, 0.025, caption, ha='left', va='bottom', fontsize=9, color='#59616b', wrap=True)
    figure.subplots_adjust(left=0.065, right=0.98, top=0.83 if count == 1 else 0.90,
                           bottom=0.23 if count == 1 else 0.14, hspace=0.65, wspace=0.30)
    try:
        figure.savefig(output, dpi=180, facecolor='white', bbox_inches='tight')
    finally:
        plt.close(figure)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    workloads = validate_rows(json.loads(args.data.read_text(encoding='utf-8')))
    output = args.output or args.data.with_name(args.data.stem + '-comparison.png')
    print(render(workloads, output))


if __name__ == '__main__':
    main()
