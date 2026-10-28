"""Tests for the evaluation pipeline (analysis/replay.py, analysis/evaluate.py).

The traces below are SYNTHETIC and only check that the pipeline's logic is right
(log format round-trips, replay reproduces the rules, scoring counts correctly).
They are NOT experimental results; real results come from run_experiments.sh.
Run from the repository root: python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
import evaluate  # noqa: E402
import replay  # noqa: E402

HOSTS = [f'10.0.0.{i}' for i in range(1, 5)]
V = '10.0.0.4'     # victim / server
A = '10.0.0.3'     # the host under test
T0 = 1000.0


def write_polls(path, n, spec):
    """Write n polls (2 s apart) through the controller's real _log_poll.
    spec(i, ip) -> dict(tx, rx, age, cpu, host_tx); age=None means no telemetry."""
    mod = replay._MOD
    old = mod.POLL_LOG
    mod.POLL_LOG = str(path)
    try:
        m = object.__new__(mod.NetworkMonitor)
        for i in range(n):
            now = T0 + 2 * i
            for ip in HOSTS:
                s = spec(i, ip)
                tel = None if s['age'] is None else {
                    '_rx_ts': now - s['age'], 'cpu_pct': s['cpu'], 'tx_bps': s['host_tx']}
                m._log_poll(now, ip, s['tx'], s['rx'], tel)
    finally:
        mod.POLL_LOG = old


def base(i, ip):
    """Normal: h1,h2 send 1 Mbps to h4; everyone reports fresh low-CPU telemetry."""
    tx = 1e6 if ip in ('10.0.0.1', '10.0.0.2') else 0.0
    rx = 2e6 if ip == V else 0.0
    return dict(tx=tx, rx=rx, age=1.0, cpu=20.0, host_tx=tx)


def with_flow(spec_a, host=A, rate=9e6, cpu=92.0, window=(0, 99), telemetry=True):
    """Overlay a flow host -> victim on the base traffic for polls in `window`."""
    def spec(i, ip):
        s = base(i, ip)
        on = window[0] <= i < window[1]
        if on and ip == host:
            s['tx'] = rate
            if telemetry:                       # a dead client keeps its last (calm) values
                s.update(host_tx=rate, cpu=cpu)
        if on and ip == V:
            s['rx'] += rate
        if not telemetry and i >= window[0] and ip == host:
            s['age'] = 1.0 + 2 * (i - window[0] + 1)     # telemetry stops arriving
        return s
    return spec


def run_alerts(n, spec, **kw):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'polls.csv'
        write_polls(p, n, spec)
        return replay.replay(replay.read_polls(p), **kw)


def rules(alerts, host=None):
    return {a['rule'] for a in alerts if host in (None, a['host'])}


class ReplayTests(unittest.TestCase):
    def test_log_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'polls.csv'
            write_polls(p, 3, base)
            polls = replay.read_polls(p)
            self.assertEqual(len(polls), 3)
            self.assertEqual(len(polls[0][1]), 4)
            row = [r for r in polls[0][1] if r['host'] == V][0]
            self.assertEqual((row['sw_rx'], row['age'], row['cpu']), (2e6, 1.0, 20.0))

    def test_log_is_off_by_default(self):
        m = object.__new__(replay._MOD.NetworkMonitor)
        self.assertIsNone(replay._MOD.POLL_LOG)
        m._log_poll(1.0, '10.0.0.1', 1.0, 1.0, None)    # must be a silent no-op

    def test_normal_is_silent(self):
        self.assertEqual(run_alerts(20, base), [])

    def test_flood_raises_host_flood_and_congestion(self):
        a = run_alerts(20, with_flow(base))
        self.assertEqual(rules(a, A), {'HOST_FLOOD'})
        self.assertEqual(rules(a, V), {'LINK_CONGESTION'})

    def test_benign_bulk_is_a_false_alarm_source(self):
        a = run_alerts(20, with_flow(base, rate=8e6, cpu=10.0))
        self.assertEqual(rules(a, A), {'HIGH_VOLUME_LOW_CPU'})
        self.assertEqual(rules(a, V), {'LINK_CONGESTION'})

    def test_short_burst_is_missed(self):
        self.assertEqual(run_alerts(20, with_flow(base, window=(5, 6))), [])

    def test_flood_during_blackout_misses_host_flood(self):
        a = run_alerts(25, with_flow(base, window=(5, 99), telemetry=False))
        self.assertNotIn('HOST_FLOOD', rules(a))
        self.assertIn('TELEMETRY_STALE', rules(a, A))
        self.assertIn('LINK_CONGESTION', rules(a, V))

    def test_threshold_override_and_restore(self):
        spec = with_flow(base, rate=8e6, cpu=85.0)
        self.assertEqual(rules(run_alerts(20, spec), A), {'HOST_FLOOD'})
        loose = run_alerts(20, spec, thresholds={'cpu_high': 90})
        self.assertEqual(rules(loose, A), {'HIGH_VOLUME_LOW_CPU'})
        self.assertEqual(replay._MOD.T['cpu_high'], 80.0)          # restored
        slow = run_alerts(20, with_flow(base, window=(5, 6)), params={'CONFIRM_CYCLES': 1})
        self.assertIn('HOST_FLOOD', rules(slow, A))

    def test_baselines_flag_what_single_signals_see(self):
        busy_cpu = with_flow(base, rate=1e6, cpu=90.0)          # busy host, little traffic
        self.assertIn('HOST_FLOOD', rules(run_alerts(20, busy_cpu, detector='telemetry_only'), A))
        self.assertEqual(run_alerts(20, busy_cpu, detector='switch_only'), [])
        bulk = with_flow(base, rate=8e6, cpu=10.0)               # big transfer, idle CPU
        self.assertIn('HOST_FLOOD', rules(run_alerts(20, bulk, detector='switch_only'), A))
        self.assertEqual(rules(run_alerts(20, bulk, detector='telemetry_only'), A), set())


class ScoringTests(unittest.TestCase):
    def truth(self, expected_a=(), allowed_a=()):
        hosts = {ip: {'expected': [], 'allowed': []} for ip in HOSTS}
        hosts[A] = {'expected': list(expected_a), 'allowed': list(allowed_a)}
        return {'scenario': 't', 'phases': [
            {'name': 'warm', 't0': T0, 't1': T0 + 10, 'scored': False, 'hosts': hosts},
            {'name': 'main', 't0': T0 + 10, 't1': T0 + 50, 'scored': True, 'hosts': hosts}]}

    def alert(self, rule, ts, host=A, sev='CRITICAL'):
        return {'ts': ts, 'host': host, 'rule': rule, 'severity': sev}

    def counts(self, truth, alerts):
        return evaluate.totals([evaluate.score(truth, alerts)])

    def test_tp_with_time_to_detect(self):
        c = self.counts(self.truth(['HOST_FLOOD']), [self.alert('HOST_FLOOD', T0 + 16)])
        self.assertEqual((c['TP'], c['FN'], c['FP'], c['TN']), (1, 0, 0, 3))
        self.assertAlmostEqual(c['mean_ttd'], 6.0)

    def test_fn_when_nothing_fires(self):
        c = self.counts(self.truth(['HOST_FLOOD']), [])
        self.assertEqual((c['TP'], c['FN'], c['FP'], c['TN']), (0, 1, 0, 3))

    def test_fp_and_allowed_and_unscored_phase(self):
        t = self.truth([], ['TELEMETRY_STALE'])
        alerts = [self.alert('HOST_FLOOD', T0 + 20),                # unexpected -> FP
                  self.alert('TELEMETRY_STALE', T0 + 20),           # allowed -> ignored
                  self.alert('HOST_FLOOD', T0 + 5)]                 # warm-up -> not scored
        c = self.counts(t, alerts)
        self.assertEqual((c['TP'], c['FN'], c['FP'], c['TN']), (0, 0, 1, 3))
        self.assertEqual(c['false_alarm_rate'], 0.25)

    def test_wrong_host_is_not_a_tp(self):
        c = self.counts(self.truth(['HOST_FLOOD']), [self.alert('HOST_FLOOD', T0 + 20, V)])
        self.assertEqual((c['TP'], c['FN'], c['FP']), (0, 1, 1))


class EndToEndTests(unittest.TestCase):
    def test_report_is_generated_from_run_folders(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d) / 'runs'
            for name, spec, scen, exp in (
                    ('normal_1', base, 'normal', {}),
                    ('flood_1', with_flow(base, window=(5, 20)), 'flood',
                     {A: ['HOST_FLOOD'], V: ['LINK_CONGESTION']})):
                r = runs / name
                r.mkdir(parents=True)
                write_polls(r / 'polls.csv', 20, spec)
                live = replay.replay(replay.read_polls(r / 'polls.csv'))
                (r / 'alerts.jsonl').write_text(
                    ''.join(json.dumps(a) + '\n' for a in live))
                hosts = {ip: {'expected': exp.get(ip, []), 'allowed': []} for ip in HOSTS}
                (r / 'truth.json').write_text(json.dumps({'scenario': scen, 'phases': [
                    {'name': 'warm', 't0': T0, 't1': T0 + 8, 'scored': False, 'hosts': hosts},
                    {'name': 'main', 't0': T0 + 8, 't1': T0 + 40, 'scored': True, 'hosts': hosts}]}))
            sys.argv = ['evaluate.py', '--runs', str(runs), '--out', str(Path(d) / 'out')]
            with contextlib.redirect_stdout(io.StringIO()):
                evaluate.main()
            text = (Path(d) / 'out' / 'REPORT.md').read_text()
            for h in ('Confusion matrix', 'Normal-traffic baseline', 'single-signal baselines',
                      'Threshold sensitivity', 'Replay reproduced the live alerts exactly'):
                self.assertIn(h, text)
            self.assertTrue((Path(d) / 'out' / 'sweep.csv').exists())
            self.assertIn('TP = 2', text)          # HOST_FLOOD + LINK_CONGESTION detected


if __name__ == '__main__':
    unittest.main()
