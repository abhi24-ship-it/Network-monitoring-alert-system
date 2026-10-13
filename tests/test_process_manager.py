import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from process_manager import ProcessManager


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = ProcessManager()
        self.addCleanup(self.manager.close)
        self.host = Mock()
        self.process = self.host.popen.return_value
        self.process.poll.return_value = None
        self.path = str(Path(self.directory.name) / 'host.log')

    def start(self, persistent=True):
        with patch.object(self.manager, 'wait'):
            self.manager.start('host', self.host, ['python3', '/path with spaces/client.py'],
                               self.path, persistent)

    def test_uses_argv_and_tracks_log(self):
        self.start()
        self.assertEqual(self.host.popen.call_args.args[0][1], '/path with spaces/client.py')
        log = self.host.popen.call_args.kwargs['stdout']
        self.manager.close()
        self.assertTrue(log.closed)
        self.process.terminate.assert_called_once()

    def test_missing_executable_closes_log(self):
        self.host.popen.side_effect = FileNotFoundError('missing')
        with self.assertRaises(FileNotFoundError):
            self.manager.start('host', self.host, ['missing'], self.path)
        self.assertTrue(self.host.popen.call_args.kwargs['stdout'].closed)
        self.assertFalse(self.manager.jobs)

    def test_persistent_process_exiting_zero_is_failure(self):
        self.start()
        self.process.poll.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'host.*status 0.*host.log'):
            self.manager.check()

    def test_finished_client_is_allowed_but_failure_is_not(self):
        self.start(persistent=False)
        self.process.poll.return_value = 0
        self.manager.check()
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'status 1'):
            self.manager.check()

    def test_terminate_timeout_escalates_to_kill(self):
        self.start()
        self.process.wait.side_effect = [subprocess.TimeoutExpired('test', 2), 0]
        self.manager.stop('host')
        self.process.kill.assert_called_once()
        self.assertFalse(self.manager.jobs)

    def test_failed_start_is_reported_immediately(self):
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'host.log'):
            self.manager.start('host', self.host, ['bad'], self.path)

    def test_stop_one_job_does_not_signal_other_jobs(self):
        self.start()
        other_host = Mock()
        other_host.popen.return_value.poll.return_value = None
        with patch.object(self.manager, 'wait'):
            self.manager.start('other', other_host, ['other'], self.path + '.other')
        self.manager.stop('host')
        other_host.popen.return_value.terminate.assert_not_called()
        self.assertIn('other', self.manager.jobs)


if __name__ == '__main__':
    unittest.main()
