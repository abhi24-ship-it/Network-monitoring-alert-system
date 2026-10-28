"""Offline replay of the REAL detector rules over a recorded per-poll log.

monitor_controller.py imports Ryu, which only exists in the Ryu venv. Here Ryu is
stubbed (same trick as tests/test_monitor.py) so the unmodified _evaluate /
_debounce code can be re-run on polls.csv with different thresholds, without
re-running Mininet. Nothing in this file re-implements the combined rules.
"""
import csv
import importlib.util
import sys
import types
from collections import defaultdict
from pathlib import Path
from unittest.mock import Mock, patch

CONTROLLER = Path(__file__).resolve().parents[1] / 'monitor_controller.py'


def load_controller():
    names = ['ryu', 'ryu.base', 'ryu.base.app_manager', 'ryu.controller',
             'ryu.controller.ofp_event', 'ryu.controller.handler', 'ryu.lib',
             'ryu.lib.hub', 'ryu.lib.packet', 'ryu.ofproto', 'ryu.ofproto.ofproto_v1_3']
    modules = {n: types.ModuleType(n) for n in names}
    modules['ryu.base.app_manager'].RyuApp = object
    h = modules['ryu.controller.handler']
    h.CONFIG_DISPATCHER, h.MAIN_DISPATCHER, h.DEAD_DISPATCHER = 'config', 'main', 'dead'
    h.set_ev_cls = lambda *a: lambda f: f
    for n in ('EventOFPSwitchFeatures', 'EventOFPStateChange',
              'EventOFPPacketIn', 'EventOFPFlowStatsReply'):
        setattr(modules['ryu.controller.ofp_event'], n, object)
    for n in ('packet', 'ethernet', 'ipv4', 'udp', 'ether_types'):
        setattr(modules['ryu.lib.packet'], n, Mock())
    modules['ryu.ofproto.ofproto_v1_3'].OFP_VERSION = 4
    spec = importlib.util.spec_from_file_location('replayed_controller', CONTROLLER)
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(mod)
    return mod


_MOD = load_controller()
DEFAULT_T = dict(_MOD.T)
DEFAULT_PARAMS = {'CONFIRM_CYCLES': _MOD.CONFIRM_CYCLES, 'COOLDOWN': _MOD.COOLDOWN,
                  'STALE_AFTER': _MOD.STALE_AFTER, 'MIN_BPS': _MOD.MIN_BPS}
CAPACITY = _MOD.LINK_CAPACITY_BPS
STALE_AFTER = _MOD.STALE_AFTER


def read_polls(path):
    """polls.csv -> list of polls; each poll = (ts, [row dict, ...])."""
    by_ts = defaultdict(list)
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            by_ts[float(r['ts'])].append({
                'host': r['host'],
                'sw_tx': float(r['sw_tx_bps']), 'sw_rx': float(r['sw_rx_bps']),
                'age': float(r['tel_age_s']) if r['tel_age_s'] != '' else None,
                'cpu': float(r['cpu_pct']) if r['cpu_pct'] != '' else None,
                'host_tx': float(r['host_tx_bps']) if r['host_tx_bps'] != '' else None})
    return sorted(by_ts.items())


def _monitor(alerts):
    m = object.__new__(_MOD.NetworkMonitor)
    m.telemetry, m.last_counter, m.datapaths, m.mac_to_port = {}, {}, {}, {}
    m.streak, m.last_alert = defaultdict(int), {}
    m.logger = Mock()
    m._alert = lambda ip, rule, sev, text, now: alerts.append(
        {'ts': now, 'host': ip, 'rule': rule, 'severity': sev})
    return m


def replay(polls, detector='combined', thresholds=None, params=None):
    """Return the alerts a detector would have raised on these polls.

    detector: 'combined'       the real controller rules (default)
              'switch_only'    baseline: only the switch's view (no telemetry)
              'telemetry_only' baseline: only the hosts' own reports
    thresholds / params override monitor_controller.T / module constants.
    """
    T = {**DEFAULT_T, **(thresholds or {})}
    P = {**DEFAULT_PARAMS, **(params or {})}
    saved = (_MOD.T, {k: getattr(_MOD, k) for k in P})
    _MOD.T = T
    for k, v in P.items():
        setattr(_MOD, k, v)
    alerts = []
    try:
        m = _monitor(alerts)
        for now, rows in polls:
            if detector == 'combined':
                m.telemetry = {r['host']: {'cpu_pct': r['cpu'], 'tx_bps': r['host_tx'],
                                           '_rx_ts': now - r['age']}
                               for r in rows if r['age'] is not None}
                m._evaluate({r['host']: r['sw_tx'] for r in rows},
                            {r['host']: r['sw_rx'] for r in rows}, now)
            else:
                for r in rows:
                    m._debounce(r['host'], _baseline_findings(detector, r, T, P), now)
    finally:
        _MOD.T = saved[0]
        for k, v in saved[1].items():
            setattr(_MOD, k, v)
    return alerts


def _baseline_findings(detector, r, T, P):
    f = {}
    if detector == 'switch_only':
        if r['sw_tx'] / CAPACITY >= T['util_high']:
            f['HOST_FLOOD'] = ('CRITICAL', 'uplink high (switch view only)')
        if r['sw_rx'] / CAPACITY >= T['util_high']:
            f['LINK_CONGESTION'] = ('CRITICAL', 'downlink high (switch view only)')
    elif detector == 'telemetry_only':
        fresh = r['age'] is not None and r['age'] <= P['STALE_AFTER']
        if fresh and r['cpu'] >= T['cpu_high']:
            f['HOST_FLOOD'] = ('CRITICAL', 'cpu high (telemetry view only)')
        if r['age'] is not None and not fresh:
            f['TELEMETRY_STALE'] = ('WARNING', 'no fresh telemetry')
    else:
        raise ValueError(detector)
    return f
