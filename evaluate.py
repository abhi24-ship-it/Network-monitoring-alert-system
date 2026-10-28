#!/usr/bin/env python3
"""Score the recorded runs: which alerts were right, wrong or missing.

  python3 evaluate.py            # reads results/<scenario>_<n>/, prints and writes results/summary.md

Per run, the alerts in alerts.jsonl are compared with what SHOULD fire:
  expected alert fired         -> TP     expected alert missing    -> FN
  alert that was not expected  -> FP     nothing expected, none fired -> TN
Time to detect = first matching alert minus the 'start' mark in events.jsonl.
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

# (host IP, rule) pairs that should fire in each scenario. h3 = 10.0.0.3, h4 = 10.0.0.4.
EXPECTED = {
    'normal': set(),                                    # nothing wrong
    'abnormal': {('10.0.0.3', 'HOST_FLOOD'), ('10.0.0.4', 'LINK_CONGESTION')},
    'benign_backup': set(),                             # legitimate: nothing wrong
    'slow_attack': {('10.0.0.3', 'HOST_FLOOD')},        # real attack, but quiet
    'telemetry_kill': {('10.0.0.3', 'TELEMETRY_STALE')},
}
ROOT = Path(__file__).resolve().parent / 'results'


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def main():
    rows, total = [], Counter()
    for scen, expected in EXPECTED.items():
        runs = sorted(ROOT.glob(f'{scen}_*'))
        if not runs:
            continue
        tp = fn = fp = tn = 0
        ttd, false_alarms = [], Counter()
        for d in runs:
            alerts = lines(d / 'alerts.jsonl')
            start = next((e['ts'] for e in lines(d / 'events.jsonl') if e['event'] == 'start'), None)
            seen = {(a['host'], a['rule']) for a in alerts}
            tp += len(expected & seen)
            fn += len(expected - seen)
            for host, rule in seen - expected:
                fp += 1
                false_alarms[f'{rule} on {host}'] += 1
            tn += not expected and not seen
            for host, rule in expected & seen:
                first = min(a['ts'] for a in alerts if (a['host'], a['rule']) == (host, rule))
                if start:
                    ttd.append(first - start)
        total.update(TP=tp, FN=fn, FP=fp, TN=tn)
        rows.append((scen, len(runs), f'{tp}/{tp + fn}' if expected else '-',
                     f'{statistics.mean(ttd):.1f} s' if ttd else '-',
                     '; '.join(f'{k} (x{v})' for k, v in sorted(false_alarms.items())) or '-'))

    if not rows:
        sys.exit('no runs found in results/ (run ./run_experiments.sh first)')
    out = ['| scenario | runs | expected alerts detected | mean time to detect | false alarms |',
           '|---|---|---|---|---|']
    out += ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows]
    c = total
    out += ['', '| | something wrong | nothing wrong |', '|---|---|---|',
            f"| alert raised | TP = {c['TP']} | FP = {c['FP']} |",
            f"| no alert | FN = {c['FN']} | TN = {c['TN']} |"]
    text = '\n'.join(out) + '\n'
    print(text)
    (ROOT / 'summary.md').write_text(text)


if __name__ == '__main__':
    main()
