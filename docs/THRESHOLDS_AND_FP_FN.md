# Thresholds and false-positive / false-negative analysis

This document explains **how the detector's limits were chosen and tested**, and
what kinds of wrong answers it can give. Every number about measured behaviour
lives in the generated `results/REPORT.md`; this file contains no measured numbers
and must not be filled with any that did not come from a run.

Related rubric item: *Performance Evaluation & Comparison* (metrics, multiple
conditions, clear results, comparison with a baseline).

---

## 1. Terms

An **alert** is a *positive*. The **truth** is whether something was actually wrong
(each scenario in `scenarios.py` states this in advance).

| | something wrong | nothing wrong |
|---|---|---|
| **alert raised** | true positive (TP) | false positive (FP): a false alarm |
| **no alert** | false negative (FN): a miss | true negative (TN) |

Scoring unit = (scored phase, host). Expected rules come from the scenario
(`expected`), rules that are acceptable side effects are `allowed`, and anything
else that fires is a false positive. See `analysis/evaluate.py`.

## 2. Threshold table

Current values are in `monitor_controller.py`. They were originally round numbers
chosen so that two staged scenarios separate cleanly; that does not show they are
good. The procedure below replaces "chosen" with "derived and tested".

| Parameter | Default | What it controls | How it is justified | Tested range |
|---|---|---|---|---|
| `util_high` | 0.70 | Link counts as heavily loaded | Margin above the normal-run maximum (REPORT section 3) | 0.3 – 0.9 |
| `util_low` | 0.20 | Link counts as quiet (CPU_SATURATION) | Above the normal-run maximum for a quiet host | 0.1 – 0.3 |
| `cpu_high` | 80 | CPU counts as busy | Margin above normal-run CPU maximum | 50 – 90 |
| `mismatch_ratio` | 0.50 | Allowed gap between host claim and switch measurement | Above the normal-run mismatch p95 | 0.2 – 0.7 |
| `mismatch_min_bps` | 500 000 | Ignore mismatch on tiny traffic | Fixed floor; not swept | – |
| `CONFIRM_CYCLES` | 2 | Consecutive polls before alerting | Trades delay (2 s per cycle) against filtering blips | 1 – 3 |
| `STALE_AFTER` | 6 s | Telemetry age counted as missing | 3 missed 2 s reports | 4 – 10 s |
| `COOLDOWN`, `MIN_BPS` | 10 s, 50 kbps | Alert repetition; stale-host activity floor | Not swept (no effect on TP/FP counts) | – |

### Procedure to (re)derive a threshold

1. Run the `normal` scenario several times (`./run_experiments.sh normal`).
2. Read the per-host mean / p95 / max in REPORT section 3.
3. Set the limit above the observed normal maximum with an explicit margin, and
   write the reasoning in the **decision log** below.
4. Read REPORT section 5 to see what moving the limit does to TP, FP and FN, and
   confirm the chosen value sits where recall is kept and false alarms are low.
5. Re-run the full set to confirm.

### Decision log (fill in after the real runs)

| Parameter | Observed normal max / p95 | Chosen value | Margin and reason | Date, who |
|---|---|---|---|---|
| `util_high` | | | | |
| `cpu_high` | | | | |
| `util_low` | | | | |
| `mismatch_ratio` | | | | |
| `CONFIRM_CYCLES` | | | | |

## 3. Expected false positives and false negatives (hypotheses)

These come from reading the rules. Each one is a **prediction that a scenario in
`scenarios.py` tests**; treat it as confirmed only if `results/REPORT.md` shows it.

### False positives (alert, nothing wrong)

| Situation | Scenario | Expected wrong alert | Why |
|---|---|---|---|
| Legitimate 8 Mbps backup, host CPU 85% | `backup_busy_cpu` | HOST_FLOOD (critical) | Both conditions are met; the rule cannot see intent |
| Legitimate 8 Mbps transfer, idle CPU | `benign_bulk` | HIGH_VOLUME_LOW_CPU (warning) | Volume alone |
| Busy server receiving from many hosts | `many_to_one`, `benign_bulk` | LINK_CONGESTION (critical) | Only incoming load is checked |
| Healthy host whose client stops reporting | `telemetry_kill` | TELEMETRY_STALE | Correct for the symptom, but the host is not faulty |

### False negatives (something wrong, no alert)

| Situation | Scenario | Missed alert | Why |
|---|---|---|---|
| Low-rate attack, 2 Mbps and CPU 30% | `slow_attack` | HOST_FLOOD | Under every limit |
| Flood shorter than two polls | `short_burst` | HOST_FLOOD | Debounce needs `CONFIRM_CYCLES` consecutive polls |
| Flood while telemetry is dead | `kill_then_flood` | HOST_FLOOD | The rule sits inside the "fresh telemetry" branch; only LINK_CONGESTION (victim) and TELEMETRY_STALE can still fire |
| Compromised host reports low CPU | `forged_low_cpu` | HOST_FLOOD downgraded to HIGH_VOLUME_LOW_CPU | Telemetry is unauthenticated; severity miss rather than total miss |

### Implementation weaknesses that also cause misses

`tests/test_monitor.py` records three known bugs as expected failures. Their
detection impact: a flow rule that expires and is re-created gives a wrong
(under-estimated) first rate; a telemetry packet that is valid JSON but not an
object, or lacks `tx_bps`, can break the collector or the evaluation. A crash in
either path silences detection, which shows up as a miss, not as an error.

## 4. Comparison with single-signal baselines

`analysis/evaluate.py` replays the same recorded polls through two simpler
detectors, with the same two-poll confirmation and cooldown:

- **switch-only**: HOST_FLOOD when uplink utilisation is high, LINK_CONGESTION when downlink is high.
- **telemetry-only**: HOST_FLOOD when reported CPU is high, TELEMETRY_STALE when reports stop.

The project's claim is that combining the two views removes false alarms either
view alone would raise (for example a bulk transfer at low CPU, or a compute job at
low traffic). REPORT section 4 tests that claim. If the data does not support it,
report that.

## 5. How to reproduce

On the Ubuntu VM (see README setup):

```bash
./run_experiments.sh                 # all scenarios x 5 repeats, about 70 minutes
python3 analysis/evaluate.py         # writes results/REPORT.md, sweep.csv, sweep.png
```

A faster first pass: `REPEATS=2 ./run_experiments.sh normal flood benign_bulk telemetry_kill`.

`results/runs/*/` holds the raw evidence per run (`polls.csv`, `alerts.jsonl`,
`truth.json`). Commit it together with `results/REPORT.md`.

## 6. Limits of this evaluation (state these in the write-up)

- **CPU is staged** with `--cpu-override`; CPU-based outcomes test the rules, not real host load.
- **Truth is defined by the scenario author.** "Attack" and "benign" are labels we set, so FP/FN counts measure agreement with our labels.
- **Few repeats, one topology** (4 hosts, 1 switch, 10 Mbps): the counts are indicative, not statistical.
- **Replay reuses the real rules** on logged inputs; the fidelity check in REPORT section 6 shows whether replay and live alerts agree.
- **Synthetic traces in `tests/test_evaluate.py` are for checking the tooling only**, never for results.
