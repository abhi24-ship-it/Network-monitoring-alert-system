#!/usr/bin/env python3
"""Score recorded runs against ground truth and write results/REPORT.md.

  python3 analysis/evaluate.py                 # reads results/runs/*, writes results/
  python3 analysis/evaluate.py --runs DIR --out DIR

Per run folder it needs truth.json (from scenarios.py), polls.csv and
alerts.jsonl (from the controller). Everything in the report is computed from
those files; nothing is typed in by hand.

Scoring unit = (scored phase, host). For each unit the truth lists the rules that
SHOULD fire ('expected') and rules that are tolerable ('allowed'):
  expected rule fired in the phase      -> TP      expected rule did not fire -> FN
  a rule fired that is neither          -> FP      unit with no expectation and no alert -> TN
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay  # noqa: E402

SWEEPS = {
    ('thresholds', 'util_high'): [0.3, 0.5, 0.6, 0.7, 0.8, 0.9],
    ('thresholds', 'cpu_high'): [50, 60, 70, 80, 90],
    ('thresholds', 'util_low'): [0.1, 0.2, 0.3],
    ('thresholds', 'mismatch_ratio'): [0.2, 0.35, 0.5, 0.7],
    ('params', 'CONFIRM_CYCLES'): [1, 2, 3],
    ('params', 'STALE_AFTER'): [4.0, 6.0, 10.0],
}


# ------------------------------------------------------------- loading ------
def load_runs(runs_dir):
    runs = []
    for d in sorted(Path(runs_dir).glob('*')):
        t, p = d / 'truth.json', d / 'polls.csv'
        if not (t.exists() and p.exists()):
            print(f'skip {d.name}: missing truth.json or polls.csv', file=sys.stderr)
            continue
        truth = json.loads(t.read_text())
        live = []
        a = d / 'alerts.jsonl'
        if a.exists():
            live = [json.loads(x) for x in a.read_text().splitlines() if x.strip()]
        runs.append({'name': d.name, 'scenario': truth['scenario'], 'truth': truth,
                     'polls': replay.read_polls(p), 'live': live})
    return runs


# ------------------------------------------------------------- scoring ------
def score(truth, alerts):
    """-> list of per-unit result dicts."""
    out = []
    for ph in truth['phases']:
        if not ph['scored']:
            continue
        for ip, h in ph['hosts'].items():
            exp, alw = set(h['expected']), set(h['allowed'])
            got = [x for x in alerts if x['host'] == ip and ph['t0'] <= x['ts'] <= ph['t1']]
            rules = {x['rule'] for x in got}
            tp = sorted(rules & exp)
            fn = sorted(exp - rules)
            fp = sorted(rules - exp - alw)
            ttd = {r: min(x['ts'] for x in got if x['rule'] == r) - ph['t0'] for r in tp}
            sev = {x['rule']: x['severity'] for x in got}
            out.append({'phase': ph['name'], 'host': ip, 'expected': sorted(exp),
                        'tp': tp, 'fn': fn, 'fp': fp, 'ttd': ttd, 'sev': sev,
                        'tn': not exp and not rules - alw, 'seen': sorted(rules)})
    return out


def totals(units_by_run):
    c = Counter()
    ttds = []
    benign = fp_units = 0
    for units in units_by_run:
        for u in units:
            c['TP'] += len(u['tp'])
            c['FN'] += len(u['fn'])
            c['FP'] += len(u['fp'])
            c['TN'] += u['tn']
            ttds += list(u['ttd'].values())
            if not u['expected']:
                benign += 1
                fp_units += bool(u['fp'])
    c['recall'] = c['TP'] / (c['TP'] + c['FN']) if c['TP'] + c['FN'] else float('nan')
    c['precision'] = c['TP'] / (c['TP'] + c['FP']) if c['TP'] + c['FP'] else float('nan')
    c['false_alarm_rate'] = fp_units / benign if benign else float('nan')
    c['mean_ttd'] = statistics.mean(ttds) if ttds else float('nan')
    return c


def evaluate_all(runs, source='live', **kw):
    """source='live' scores the alerts the controller really raised;
    otherwise replays polls.csv with the given detector/thresholds/params."""
    res = []
    for r in runs:
        alerts = r['live'] if source == 'live' else replay.replay(r['polls'], source, **kw)
        res.append(score(r['truth'], alerts))
    return res


# --------------------------------------------------------------- report -----
def fmt(x, nd=2):
    return 'n/a' if x != x else f'{x:.{nd}f}'


def table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def baseline_section(runs):
    stats = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r['scenario'] != 'normal':
            continue
        steady = [p for p in r['truth']['phases'] if p['scored']]
        for now, rows in r['polls']:
            if not any(p['t0'] <= now <= p['t1'] for p in steady):
                continue
            for row in rows:
                s = stats[row['host']]
                s['tx_util'].append(row['sw_tx'] / replay.CAPACITY)
                s['rx_util'].append(row['sw_rx'] / replay.CAPACITY)
                if row['cpu'] is not None:
                    s['cpu'].append(row['cpu'])
                    big = max(row['sw_tx'], row['host_tx'])
                    s['mismatch'].append(abs(row['sw_tx'] - row['host_tx']) / big if big else 0.0)
    if not stats:
        return 'No `normal` runs found.\n'
    rows = []
    for host in sorted(stats):
        for k, label in (('tx_util', 'uplink util'), ('rx_util', 'downlink util'),
                         ('cpu', 'CPU %'), ('mismatch', 'mismatch ratio')):
            v = sorted(stats[host][k])
            if v:
                p95 = v[min(len(v) - 1, int(0.95 * len(v)))]
                rows.append((host, label, fmt(statistics.mean(v), 3), fmt(p95, 3), fmt(v[-1], 3)))
    return table(['host', 'signal', 'mean', 'p95', 'max'], rows) + '\n'


def scenario_section(runs, units_by_run):
    agg = defaultdict(lambda: defaultdict(list))
    for r, units in zip(runs, units_by_run):
        for u in units:
            key = (r['scenario'], u['phase'], u['host'], tuple(u['expected']))
            agg[key]['runs'].append(r['name'])
            agg[key]['tp'] += u['tp']
            agg[key]['fn'] += u['fn']
            agg[key]['fp'] += [f"{x} ({u['sev'].get(x, '?')})" for x in u['fp']]
            agg[key]['ttd'] += list(u['ttd'].values())
    rows = []
    for (sc, ph, host, exp), a in sorted(agg.items()):
        n = len(a['runs'])
        if not exp and not a['fp']:
            continue          # uninteresting true negatives; counted in totals
        fps = Counter(a['fp'])
        rows.append((sc, host, ', '.join(exp) or '(nothing)',
                     f"{len(a['tp'])}/{n}" if exp else '-',
                     fmt(statistics.mean(a['ttd']), 1) + ' s' if a['ttd'] else '-',
                     '; '.join(f'{k} x{v}' for k, v in sorted(fps.items())) or '-'))
    return table(['scenario', 'host', 'expected alert', 'detected (runs)',
                  'mean time to detect', 'false alarms (rule, severity, runs)'], rows) + '\n'


def comparison_section(runs):
    rows = []
    for label, src in (('combined (live alerts)', 'live'), ('combined (replayed)', 'combined'),
                       ('switch-only baseline', 'switch_only'),
                       ('telemetry-only baseline', 'telemetry_only')):
        c = totals(evaluate_all(runs, src))
        rows.append((label, c['TP'], c['FN'], c['FP'], c['TN'], fmt(c['recall']),
                     fmt(c['precision']), fmt(c['false_alarm_rate']), fmt(c['mean_ttd'], 1)))
    return table(['detector', 'TP', 'FN', 'FP', 'TN', 'recall', 'precision',
                  'false-alarm rate (benign units)', 'mean TTD s'], rows) + '\n'


def fidelity_section(runs):
    bad = []
    for r in runs:
        a = Counter((x['host'], x['rule']) for x in r['live'])
        b = Counter((x['host'], x['rule']) for x in replay.replay(r['polls']))
        if a != b:
            bad.append((r['name'], dict(a), dict(b)))
    if not bad:
        return f'Replay reproduced the live alerts exactly on all {len(runs)} runs.\n'
    rows = [(n, json.dumps({f'{h}/{k}': v for (h, k), v in a.items()}),
             json.dumps({f'{h}/{k}': v for (h, k), v in b.items()})) for n, a, b in bad]
    return (f'**Replay differs from live alerts on {len(bad)} of {len(runs)} runs** '
            '(sweeps for those runs are approximate):\n\n'
            + table(['run', 'live', 'replayed'], rows) + '\n')


def sweep(runs, outdir):
    rows = []
    for (kind, name), values in SWEEPS.items():
        for v in values:
            kw = {kind: {name: v}}
            c = totals(evaluate_all(runs, 'combined', **kw))
            rows.append({'parameter': name, 'value': v, 'TP': c['TP'], 'FN': c['FN'],
                         'FP': c['FP'], 'TN': c['TN'], 'recall': c['recall'],
                         'false_alarm_rate': c['false_alarm_rate'], 'mean_ttd': c['mean_ttd']})
    grid = []
    for uh in SWEEPS[('thresholds', 'util_high')]:
        for ch in SWEEPS[('thresholds', 'cpu_high')]:
            c = totals(evaluate_all(runs, 'combined', thresholds={'util_high': uh, 'cpu_high': ch}))
            grid.append({'util_high': uh, 'cpu_high': ch, 'TP': c['TP'], 'FN': c['FN'],
                         'FP': c['FP'], 'TN': c['TN']})
    for name, data in (('sweep.csv', rows), ('sweep_grid.csv', grid)):
        with open(Path(outdir) / name, 'w') as f:
            f.write(','.join(data[0]) + '\n')
            for d in data:
                f.write(','.join(f'{d[k]:.4g}' if isinstance(d[k], float) else str(d[k])
                                 for k in d) + '\n')
    return rows, grid


def plot(rows, outdir):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    params = list(dict.fromkeys(r['parameter'] for r in rows))
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.5))
    for ax, p in zip(axes.flat, params):
        pts = [r for r in rows if r['parameter'] == p]
        x = [str(r['value']) for r in pts]
        ax.plot(x, [r['TP'] for r in pts], 'o-', color='#1f77b4', label='TP (detected)')
        ax.plot(x, [r['FP'] for r in pts], 's--', color='#d62728', label='FP (false alarms)')
        ax.plot(x, [r['FN'] for r in pts], '^:', color='#555555', label='FN (missed)')
        ax.set_title(p)
        ax.set_xlabel('value')
        ax.grid(alpha=.3)
    axes.flat[0].set_ylabel('count over all runs')
    axes.flat[0].legend(fontsize=8)
    fig.suptitle('Threshold sensitivity: one parameter varied, others at default')
    fig.tight_layout()
    fig.savefig(Path(outdir) / 'sweep.png', dpi=130)
    return True


def main():
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=str(root / 'results' / 'runs'))
    ap.add_argument('--out', default=str(root / 'results'))
    args = ap.parse_args()
    runs = load_runs(args.runs)
    if not runs:
        sys.exit(f'no usable runs in {args.runs} (run ./run_experiments.sh first)')
    Path(args.out).mkdir(parents=True, exist_ok=True)

    live_units = evaluate_all(runs, 'live')
    c = totals(live_units)
    sweep_rows, _ = sweep(runs, args.out)
    has_plot = plot(sweep_rows, args.out)
    per_scen = Counter(r['scenario'] for r in runs)

    md = ['# Threshold and false-positive / false-negative evaluation',
          '',
          '_Generated by `analysis/evaluate.py` from the run folders. Do not edit by hand._',
          '',
          f'**Runs:** {len(runs)} ({", ".join(f"{k} x{v}" for k, v in sorted(per_scen.items()))}).',
          '**Caveat:** CPU values are staged with `--cpu-override`, so every CPU-based '
          'outcome below reflects the rules, not real host load.',
          '',
          '## 1. Confusion matrix (live alerts, default thresholds)',
          '',
          'Unit = (scored phase, host). TP: expected alert fired. FN: expected alert did not fire. '
          'FP: an unexpected alert fired. TN: nothing expected and nothing fired.',
          '',
          table(['', 'something wrong', 'nothing wrong'],
                [('alert raised', f"TP = {c['TP']}", f"FP = {c['FP']}"),
                 ('no alert', f"FN = {c['FN']}", f"TN = {c['TN']}")]),
          '',
          f"Recall {fmt(c['recall'])} · precision {fmt(c['precision'])} · "
          f"false-alarm rate on benign units {fmt(c['false_alarm_rate'])} · "
          f"mean time to detect {fmt(c['mean_ttd'], 1)} s",
          '',
          '## 2. Where the errors come from',
          '',
          scenario_section(runs, live_units),
          '## 3. Normal-traffic baseline (basis for the thresholds)',
          '',
          baseline_section(runs),
          '## 4. Combined detector vs single-signal baselines',
          '',
          'Same runs, same scoring, same two-poll confirmation and cooldown.',
          '',
          comparison_section(runs),
          '## 5. Threshold sensitivity',
          '',
          'One parameter varied at a time on the recorded polls (`sweep.csv`; two-parameter '
          'grid in `sweep_grid.csv`).' + (' Chart: `sweep.png`.' if has_plot else ''),
          '',
          table(['parameter', 'value', 'TP', 'FN', 'FP', 'TN', 'recall', 'false-alarm rate'],
                [(r['parameter'], r['value'], r['TP'], r['FN'], r['FP'], r['TN'],
                  fmt(r['recall']), fmt(r['false_alarm_rate'])) for r in sweep_rows]),
          '',
          '## 6. Replay fidelity check',
          '',
          fidelity_section(runs)]
    (Path(args.out) / 'REPORT.md').write_text('\n'.join(md))
    print(f'wrote {Path(args.out) / "REPORT.md"}')


if __name__ == '__main__':
    main()
