"""Regression tests: actual controller methods, with Ryu transport stubbed out.

These checks do not emulate OpenFlow transport or validate Mininet integration.
Run from the repository root: python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from collections import defaultdict

from monitor_utils import decode_telemetry, flow_rate


def load_controller():
    names = ['ryu', 'ryu.base', 'ryu.base.app_manager', 'ryu.controller',
             'ryu.controller.ofp_event', 'ryu.controller.handler', 'ryu.lib',
             'ryu.lib.hub', 'ryu.lib.packet', 'ryu.ofproto', 'ryu.ofproto.ofproto_v1_3']
    modules = {name: types.ModuleType(name) for name in names}
    modules['ryu.base.app_manager'].RyuApp = object
    handler = modules['ryu.controller.handler']
    handler.CONFIG_DISPATCHER = 'config'
    handler.MAIN_DISPATCHER = 'main'
    handler.DEAD_DISPATCHER = 'dead'
    handler.set_ev_cls = lambda *args: lambda f: f
    events = modules['ryu.controller.ofp_event']
    for name in ('EventOFPSwitchFeatures', 'EventOFPStateChange',
                 'EventOFPPacketIn', 'EventOFPFlowStatsReply'):
        setattr(events, name, object)
    for name in ('packet', 'ethernet', 'ipv4', 'udp', 'ether_types'):
        setattr(modules['ryu.lib.packet'], name, Mock())
    modules['ryu.ofproto.ofproto_v1_3'].OFP_VERSION = 4
    spec = importlib.util.spec_from_file_location(
        'tested_controller', Path(__file__).resolve().parents[1] / 'monitor_controller.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


controller = load_controller()
IP = '10.0.0.3'


def make_monitor(cpu=20, rate=1_000_000, now=100):
    monitor = object.__new__(controller.NetworkMonitor)
    monitor.telemetry = {IP: {'cpu_pct': cpu, 'tx_bps': rate, '_rx_ts': now}}
    monitor.last_counter = {}
    monitor.pending_stats = {}
    monitor.datapaths = {}
    monitor.mac_to_port = {}
    monitor.streak = defaultdict(int)
    monitor.last_alert = {}
    monitor.logger = Mock()
    monitor._alert = Mock()
    return monitor


class TelemetryTests(unittest.TestCase):
    def test_valid_and_extra_fields(self):
        msg = {'cpu_pct': 92, 'tx_bps': 9e6, 'seq': 0, 'extra': 'allowed'}
        self.assertEqual(decode_telemetry(json.dumps(msg).encode()), msg)

    def test_reject_bad_payloads(self):
        payloads = [b'[]', b'null', b'42', b'"hello"', b'{', b'\xff', b'{}',
                    b'[' * 2000 + b']' * 2000]
        valid = {'cpu_pct': 20, 'tx_bps': 100, 'seq': 1}
        for key, values in {
            'cpu_pct': [-1, 101, True, '92', None, float('nan'), float('inf'), 10**400],
            'tx_bps': [-1, False, '100', None, float('nan'), float('inf'), 10**400],
            'seq': [-1, True, 1.5, '1', None],
        }.items():
            for value in values:
                payloads.append(json.dumps(dict(valid, **{key: value})).encode())
            payloads.append(json.dumps({k: v for k, v in valid.items() if k != key}).encode())
        for data in payloads:
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                decode_telemetry(data)

    def test_collector_survives_bad_reports_then_receives_valid(self):
        monitor = make_monitor()
        monitor.telemetry = {}
        sock = Mock()
        good = json.dumps({'cpu_pct': 20, 'tx_bps': 100, 'seq': 0}).encode()
        sock.recvfrom.side_effect = [(b'[]', (IP, 1)), (b'{}', (IP, 1)),
                                    (good, (IP, 1)), KeyboardInterrupt()]
        with patch.object(controller.socket, 'socket', return_value=sock), \
                patch.object(controller.time, 'monotonic', return_value=123), \
                self.assertRaises(KeyboardInterrupt):
            monitor._telemetry_loop()
        self.assertEqual(monitor.telemetry[IP]['_rx_ts'], 123)
        self.assertEqual(monitor.telemetry[IP]['seq'], 0)


class RateTests(unittest.TestCase):
    def test_first_sample(self):
        self.assertEqual(flow_rate(None, 500000, 2, 100), 2e6)

    def test_continuous_flow(self):
        self.assertEqual(flow_rate((500000, 2, 100), 1000000, 4, 102), 2e6)

    def test_restart_after_long_gap(self):
        self.assertEqual(flow_rate((1000000, 4, 102), 500000, 2, 202), 2e6)

    def test_restart_with_larger_counter(self):
        self.assertEqual(flow_rate((100, 10, 100), 500000, 2, 102), 2e6)

    def test_restart_age_larger_than_previous_but_younger_than_gap(self):
        self.assertEqual(flow_rate((100, 2, 100), 1500000, 6, 120), 2e6)

    def test_duplicate_sample(self):
        self.assertEqual(flow_rate((100, 2, 100), 100, 2, 100), 0)


class RuleTests(unittest.TestCase):
    def evaluate_twice(self, cpu, tx, rx=0, host_tx=None):
        monitor = make_monitor(cpu, tx if host_tx is None else host_tx)
        monitor._evaluate({IP: tx}, {IP: rx}, 100)
        monitor._alert.assert_not_called()
        monitor._evaluate({IP: tx}, {IP: rx}, 102)
        return {call.args[1] for call in monitor._alert.call_args_list}

    def test_normal(self):
        self.assertEqual(self.evaluate_twice(20, 1e6), set())

    def test_flood_boundary(self):
        self.assertEqual(self.evaluate_twice(80, 7e6), {'HOST_FLOOD'})
        self.assertEqual(self.evaluate_twice(79.9, 7e6), {'HIGH_VOLUME_LOW_CPU'})
        self.assertEqual(self.evaluate_twice(80, 6.99e6), set())

    def test_cpu_boundary(self):
        self.assertEqual(self.evaluate_twice(80, 1.99e6), {'CPU_SATURATION'})
        self.assertEqual(self.evaluate_twice(80, 2e6), set())

    def test_incoming_load_boundary(self):
        self.assertEqual(self.evaluate_twice(20, 0, 7e6), {'LINK_CONGESTION'})

    def test_mismatch_boundaries(self):
        self.assertEqual(self.evaluate_twice(20, 1e6, host_tx=500000), set())
        self.assertEqual(self.evaluate_twice(20, 1e6, host_tx=499999),
                         {'TELEMETRY_MISMATCH'})
        self.assertEqual(self.evaluate_twice(20, 500000, host_tx=0), set())

    def test_stale_suppresses_cpu_rules_but_not_incoming_load(self):
        monitor = make_monitor(92, 9e6)
        for now in (108, 110):
            monitor._evaluate({IP: 9e6}, {IP: 8e6}, now)
        self.assertEqual({c.args[1] for c in monitor._alert.call_args_list},
                         {'TELEMETRY_STALE', 'LINK_CONGESTION'})

    def test_uninstrumented_active_host(self):
        monitor = make_monitor()
        monitor.telemetry = {}
        for now in (100, 102):
            monitor._evaluate({IP: 1e6}, {}, now)
        self.assertEqual(monitor._alert.call_args.args[1], 'TELEMETRY_STALE')

    def test_streak_reset(self):
        monitor = make_monitor(92, 9e6)
        for now, tx in [(100, 9e6), (102, 3e6), (104, 9e6)]:
            monitor.telemetry[IP]['tx_bps'] = tx
            monitor._evaluate({IP: tx}, {}, now)
        monitor._alert.assert_not_called()

    def test_cooldown_and_early_monotonic_clock(self):
        monitor = make_monitor(92, 9e6, now=0)
        for now in (0, 2, 4, 6, 8, 10, 12):
            monitor.telemetry[IP]['_rx_ts'] = now
            monitor._evaluate({IP: 9e6}, {}, now)
        self.assertEqual([c.args[-1] for c in monitor._alert.call_args_list], [2, 12])


class StatsTests(unittest.TestCase):
    def flow(self, port=5001, count=500000, age=2, src=IP):
        return types.SimpleNamespace(
            match={'ipv4_src': src, 'ipv4_dst': '10.0.0.4',
                   'ip_proto': 17, 'udp_dst': port}, byte_count=count,
            duration_sec=age, duration_nsec=0)

    def reply(self, monitor, body, more=False, xid=1, now=100):
        dp = types.SimpleNamespace(id=1, ofproto=types.SimpleNamespace(OFPMPF_REPLY_MORE=1))
        ev = types.SimpleNamespace(msg=types.SimpleNamespace(
            datapath=dp, xid=xid, flags=int(more), body=body))
        with patch.object(controller.time, 'monotonic', return_value=now):
            monitor._flow_stats_reply(ev)

    def test_rates_telemetry_exclusion_and_expired_cleanup(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.reply(monitor, [self.flow(), self.flow(port=9999)])
        tx, rx, _ = monitor._evaluate.call_args.args
        self.assertEqual(tx[IP], 2e6)
        self.assertEqual(rx['10.0.0.4'], 2e6)
        self.reply(monitor, [], xid=2, now=102)
        self.assertFalse(monitor.last_counter)

    def test_multipart_is_one_evaluation(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.reply(monitor, [self.flow()], more=True)
        monitor._evaluate.assert_not_called()
        self.reply(monitor, [self.flow(src='10.0.0.1')])
        monitor._evaluate.assert_called_once()
        self.assertEqual(monitor._evaluate.call_args.args[1]['10.0.0.4'], 4e6)
        self.assertFalse(monitor.pending_stats)

    def test_disconnect_clears_switch_state(self):
        monitor = make_monitor()
        self.reply(monitor, [self.flow()])
        monitor.mac_to_port[1] = {'mac': 1}
        monitor.pending_stats[1] = (2, [])
        monitor._state_change(types.SimpleNamespace(
            state='dead', datapath=types.SimpleNamespace(id=1)))
        self.assertFalse(monitor.last_counter)
        self.assertFalse(monitor.mac_to_port)
        self.assertFalse(monitor.pending_stats)

    def test_log_uses_epoch_timestamp(self):
        monitor = make_monitor()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'alerts.jsonl')
            with patch.object(controller, 'ALERT_FILE', path), \
                    patch.object(controller.time, 'time', return_value=1791900000):
                controller.NetworkMonitor._alert(monitor, IP, 'TEST', 'WARNING', 'detail', 2)
            self.assertEqual(json.loads(Path(path).read_text())['ts'], 1791900000)

    def test_log_failure_does_not_stop_detection(self):
        monitor = make_monitor()
        with patch('builtins.open', side_effect=PermissionError('read only')):
            controller.NetworkMonitor._alert(monitor, IP, 'TEST', 'WARNING', 'detail', 2)
        monitor.logger.exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()
