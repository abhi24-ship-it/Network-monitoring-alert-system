"""Regression tests: actual controller methods, with Ryu transport stubbed out.

These checks do not emulate OpenFlow transport or validate Mininet integration.
Three expected failures document existing bugs; they are not passing checks.
The application remains the original four-file implementation.
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
    monitor.datapaths = {}
    monitor.mac_to_port = {}
    monitor.streak = defaultdict(int)
    monitor.last_alert = {}
    monitor.logger = Mock()
    monitor._alert = Mock()
    return monitor


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

    def test_cooldown(self):
        monitor = make_monitor(92, 9e6, now=100)
        for now in (100, 102, 104, 106, 108, 110, 112):
            monitor.telemetry[IP]['_rx_ts'] = now
            monitor._evaluate({IP: 9e6}, {}, now)
        self.assertEqual([c.args[-1] for c in monitor._alert.call_args_list], [102, 112])


class OriginalCodeTests(unittest.TestCase):
    def rate(self, monitor, count, now, age=2):
        flow = types.SimpleNamespace(match={'ipv4_src': IP, 'ipv4_dst': '10.0.0.4',
            'ip_proto': 17, 'udp_dst': 5001}, byte_count=count,
            duration_sec=age, duration_nsec=0)
        event = types.SimpleNamespace(msg=types.SimpleNamespace(
            datapath=types.SimpleNamespace(id=1), body=[flow]))
        with patch.object(controller.time, 'time', return_value=now):
            monitor._flow_stats_reply(event)
        return monitor._evaluate.call_args.args[0][IP]

    def test_initial_and_continuous_rate(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.assertEqual(self.rate(monitor, 500000, 100), 2e6)
        self.assertEqual(self.rate(monitor, 1000000, 102, 4), 2e6)

    def test_valid_report_after_invalid_json(self):
        monitor = make_monitor()
        sock = Mock()
        good = json.dumps({'cpu_pct': 20, 'tx_bps': 100, 'seq': 0}).encode()
        sock.recvfrom.side_effect = [(b'{', (IP, 1)), (good, (IP, 1)), KeyboardInterrupt()]
        with patch.object(controller.socket, 'socket', return_value=sock), \
                patch.object(controller.time, 'time', return_value=123), \
                self.assertRaises(KeyboardInterrupt):
            monitor._telemetry_loop()
        self.assertEqual(monitor.telemetry[IP]['_rx_ts'], 123)
        self.assertEqual(monitor.telemetry[IP]['seq'], 0)

    def test_alert_appends_json(self):
        monitor = make_monitor()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'alerts.jsonl'
            with patch.object(controller, 'ALERT_FILE', str(path)):
                for now in (100, 110):
                    controller.NetworkMonitor._alert(monitor, IP, 'TEST', 'WARNING', 'detail', now)
            self.assertEqual([json.loads(line)['ts'] for line in path.read_text().splitlines()], [100, 110])

    @unittest.expectedFailure
    def test_known_bug_recreated_flow_should_use_new_lifetime(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.rate(monitor, 1000000, 100, 4)
        self.assertEqual(self.rate(monitor, 500000, 200, 2), 2e6)

    @unittest.expectedFailure
    def test_known_bug_collector_should_reject_json_list(self):
        monitor = make_monitor()
        sock = Mock()
        sock.recvfrom.side_effect = [(b'[]', (IP, 1)), KeyboardInterrupt()]
        with patch.object(controller.socket, 'socket', return_value=sock), \
                self.assertRaises(KeyboardInterrupt):
            monitor._telemetry_loop()

    @unittest.expectedFailure
    def test_known_bug_incomplete_report_should_not_break_evaluation(self):
        monitor = make_monitor()
        del monitor.telemetry[IP]['tx_bps']
        monitor._evaluate({IP: 1e6}, {}, 100)


if __name__ == '__main__':
    unittest.main()
class CollectorTests(unittest.TestCase):
    def test_invalid_json_skipped_and_valid_report_stored_by_sender(self):
        monitor = make_monitor()
        monitor.telemetry = {}
        sock = Mock()
        good = json.dumps({'host': 'untrusted-name', 'cpu_pct': 20,
                           'tx_bps': 100, 'seq': 1}).encode()
        sock.recvfrom.side_effect = [(b'{', (IP, 1)), (b'\xff', (IP, 1)),
                                    (good, (IP, 1)), KeyboardInterrupt()]
        with patch.object(controller.socket, 'socket', return_value=sock), \
                patch.object(controller.time, 'time', return_value=123), \
                self.assertRaises(KeyboardInterrupt):
            monitor._telemetry_loop()
        self.assertEqual(monitor.telemetry[IP]['_rx_ts'], 123)
        self.assertEqual(monitor.telemetry[IP]['seq'], 1)
        self.assertNotIn('untrusted-name', monitor.telemetry)

    @unittest.expectedFailure
    def test_known_limit_non_object_json_should_not_stop_collector(self):
        """Original collector raises TypeError for valid JSON arrays."""
        monitor = make_monitor()
        sock = Mock()
        sock.recvfrom.side_effect = [(b'[]', (IP, 1)), KeyboardInterrupt()]
        with patch.object(controller.socket, 'socket', return_value=sock), \
                self.assertRaises(KeyboardInterrupt):
            monitor._telemetry_loop()

    @unittest.expectedFailure
    def test_known_limit_incomplete_report_should_not_break_evaluation(self):
        """Original evaluator raises KeyError when tx_bps is missing."""
        monitor = make_monitor()
        del monitor.telemetry[IP]['tx_bps']
        monitor._evaluate({IP: 1e6}, {}, 100)


class StatsTests(unittest.TestCase):
    def flow(self, port=5001, count=500000, age=2, src=IP):
        return types.SimpleNamespace(
            match={'ipv4_src': src, 'ipv4_dst': '10.0.0.4',
                   'ip_proto': 17, 'udp_dst': port}, byte_count=count,
            duration_sec=age, duration_nsec=0)

    def reply(self, monitor, body, now=100):
        ev = types.SimpleNamespace(msg=types.SimpleNamespace(
            datapath=types.SimpleNamespace(id=1), body=body))
        with patch.object(controller.time, 'time', return_value=now):
            monitor._flow_stats_reply(ev)

    def test_first_sample_and_telemetry_exclusion(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        table_miss = types.SimpleNamespace(match={})
        self.reply(monitor, [self.flow(), self.flow(port=9999), table_miss])
        tx, rx, now = monitor._evaluate.call_args.args
        self.assertEqual(tx[IP], 2e6)
        self.assertEqual(rx['10.0.0.4'], 2e6)
        self.assertEqual(now, 100)

    def test_continuous_flow_rate(self):
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.reply(monitor, [self.flow()])
        self.reply(monitor, [self.flow(count=1000000, age=4)], now=102)
        self.assertEqual(monitor._evaluate.call_args.args[0][IP], 2e6)

    @unittest.expectedFailure
    def test_known_limit_recreated_flow_should_use_new_lifetime(self):
        """Original code uses the old timestamp: 0.04 Mbps instead of 2 Mbps."""
        monitor = make_monitor()
        monitor._evaluate = Mock()
        self.reply(monitor, [self.flow(count=1000000, age=4)], now=102)
        self.reply(monitor, [], now=104)
        self.reply(monitor, [self.flow(count=500000, age=2)], now=202)
        self.assertEqual(monitor._evaluate.call_args.args[0][IP], 2e6)

    def test_switch_registration_and_disconnect(self):
        monitor = make_monitor()
        dp = types.SimpleNamespace(id=1)
        monitor._state_change(types.SimpleNamespace(state='main', datapath=dp))
        self.assertIs(monitor.datapaths[1], dp)
        monitor._state_change(types.SimpleNamespace(state='dead', datapath=dp))
        self.assertNotIn(1, monitor.datapaths)

    def test_alerts_append_json_lines(self):
        monitor = make_monitor()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'alerts.jsonl'
            with patch.object(controller, 'ALERT_FILE', str(path)):
                for now in (100, 110):
                    controller.NetworkMonitor._alert(
                        monitor, IP, 'HOST_FLOOD', 'CRITICAL', 'detail', now)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([r['ts'] for r in records], [100, 110])
        self.assertTrue(all(r['host'] == IP and r['rule'] == 'HOST_FLOOD'
                            and r['severity'] == 'CRITICAL' for r in records))


if __name__ == '__main__':
    unittest.main()
