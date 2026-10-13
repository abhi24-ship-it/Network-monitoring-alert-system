"""Scenario orchestration checks with Mininet boundaries stubbed."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_topology():
    names = ['mininet', 'mininet.cli', 'mininet.link', 'mininet.log',
             'mininet.net', 'mininet.node']
    modules = {name: types.ModuleType(name) for name in names}
    for module, attrs in {
        'cli': ['CLI'], 'link': ['TCLink'], 'log': ['setLogLevel', 'info'],
        'net': ['Mininet'], 'node': ['Node', 'OVSSwitch', 'RemoteController'],
    }.items():
        for attr in attrs:
            setattr(modules['mininet.' + module], attr, Mock())
    spec = importlib.util.spec_from_file_location(
        'tested_topology', Path(__file__).resolve().parents[1] / 'topology.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


topology = load_topology()


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.net = Mock()
        self.hosts = {name: Mock() for name in ('h1', 'h2', 'h3', 'h4')}
        self.net.get.side_effect = self.hosts.__getitem__
        self.jobs = Mock()

    def test_normal_starts_all_reports_and_two_senders(self):
        topology.run_normal(self.net, self.jobs)
        calls = self.jobs.start.call_args_list
        self.assertEqual(len(calls), 7)
        telemetry = [c for c in calls if c.args[0].endswith('-telemetry')]
        self.assertEqual(len(telemetry), 4)
        clients = [c for c in calls if '-c' in c.args[2]]
        self.assertEqual(len(clients), 2)
        for call in clients:
            self.assertIn('1M', call.args[2])
            self.assertFalse(call.kwargs['persistent'])
        self.jobs.wait.assert_any_call(45)

    def test_abnormal_restarts_only_h3_then_adds_burst(self):
        topology.run_abnormal(self.net, self.jobs)
        self.jobs.stop.assert_called_once_with('h3-telemetry')
        reports = [c for c in self.jobs.start.call_args_list if c.args[0] == 'h3-telemetry']
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[-1].args[2][-2:], ['--cpu-override', '92'])
        attack = self.jobs.start.call_args_list[-1]
        self.assertEqual(attack.args[0], 'h3-iperf')
        self.assertEqual(attack.args[2][-4:], ['-b', '9M', '-t', '30'])
        self.jobs.wait.assert_any_call(15)
        self.jobs.wait.assert_any_call(35)

    def test_failed_connectivity_aborts_and_cleans_up(self):
        root = Mock()
        self.net.pingAll.return_value = 100
        with patch.object(topology, 'build', return_value=(self.net, root)), \
                patch.object(topology, 'ProcessManager', return_value=self.jobs), \
                patch.object(sys, 'argv', ['topology.py', '--scenario', 'normal']), \
                self.assertRaisesRegex(RuntimeError, 'Connectivity'):
            topology.main()
        self.jobs.start.assert_not_called()
        self.jobs.close.assert_called_once()
        self.net.stop.assert_called_once()
        root.terminate.assert_called_once()

    def test_cleanup_continues_if_process_shutdown_fails(self):
        root = Mock()
        self.net.pingAll.return_value = 0
        self.jobs.close.side_effect = RuntimeError('shutdown failed')
        with patch.object(topology, 'build', return_value=(self.net, root)), \
                patch.object(topology, 'ProcessManager', return_value=self.jobs), \
                patch.object(sys, 'argv', ['topology.py', '--scenario', 'cli']), \
                self.assertRaisesRegex(RuntimeError, 'shutdown failed'):
            topology.main()
        self.net.stop.assert_called_once()
        root.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
