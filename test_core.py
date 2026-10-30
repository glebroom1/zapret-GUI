import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from core import Rotation, Engine, parse_strategy, strategies, discover, validate_files

class RotationTests(unittest.TestCase):
    def test_three_failures_and_cooldown(self):
        r = Rotation(['A', 'B']); r.last_switch = 0
        self.assertEqual(r.observe(True, False, 10), 'retry')
        self.assertEqual(r.observe(True, False, 20), 'retry')
        self.assertEqual(r.observe(True, False, 30), 'retry')
        self.assertEqual(r.observe(True, False, 46), 'B')

    def test_offline_resets_failures(self):
        r = Rotation(['A', 'B']); r.last_switch = 0
        r.observe(True, False, 60); r.observe(True, False, 61)
        self.assertEqual(r.observe(False, False, 62), 'offline')
        self.assertEqual(r.observe(True, False, 63), 'retry')

    def test_sweep_is_bounded_and_recovers(self):
        r = Rotation(['A', 'B'], threshold=1, cooldown=0)
        self.assertEqual(r.observe(True, False), 'B')
        self.assertEqual(r.observe(True, False), 'exhausted')
        self.assertEqual(r.observe(True, False), 'exhausted')
        self.assertEqual(r.observe(True, True), 'healthy')
        self.assertEqual(r.observe(True, False), 'A')

    def test_shipped_strategies(self):
        root = (Path(__file__).parent.parent/'zapret-discord-youtube-1.10.4')
        files = strategies(root)
        self.assertEqual(len(files), 22)
        for path in files:
            with self.subTest(path=path.name):
                args = parse_strategy(path)
                self.assertTrue(args[0].startswith('--wf-tcp='))
                self.assertFalse(any('%' in a or '^' in a for a in args))
                self.assertFalse(any('"' in a for a in args))
                for a in args:
                    value = a.partition('=')[2]
                    if value.startswith(str(root)) and not value.endswith(('-user.txt',)):
                        self.assertTrue(Path(value).is_file(), value)

    def test_escaped_bang_and_paths_with_spaces(self):
        root = (Path(__file__).parent.parent/'zapret-discord-youtube-1.10.4')
        args = parse_strategy(root/'general (FAKE TLS AUTO).bat')
        self.assertIn('--dpi-desync-fake-tls=!', args)
        self.assertIn('--hostlist='+str(root/'lists/list-general.txt'), args)

    def test_unknown_variables_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'general.bat'
            path.write_text('start "zapret" /min "%BIN%winws.exe" --x=%UNKNOWN%', encoding='utf-8')
            with self.assertRaises(KeyError): parse_strategy(path)

    def test_foreign_engine_is_never_terminated(self):
        engine = Engine(lambda msg: None)
        with patch('core.is_admin', return_value=True), patch('core.foreign_engine', return_value=True), patch('core.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'Другой winws'):
                engine.start(Path('general.bat'), Path('test.log'))
            spawn.assert_not_called()

    def test_stop_only_owned_process(self):
        engine = Engine(lambda msg: None)
        process = Mock()
        process.poll.return_value = None
        engine.process = process
        engine.stop()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        self.assertIsNone(engine.process)

class EngineStartTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(lambda msg: None)
        self.folder = tempfile.TemporaryDirectory()
        self.logfile = Path(self.folder.name)/'engine.log'

    def tearDown(self):
        self.engine.stop()
        self.folder.cleanup()

    def strategy(self):
        return (Path(__file__).parent.parent/'zapret-discord-youtube-1.10.4')/'general (ALT).bat'

    def test_start_waits_until_winws_reports_ready(self):
        process = Mock(); process.poll.return_value = None
        with patch('core.is_admin', return_value=True), \
             patch('core.foreign_engine', return_value=False), \
             patch('core.subprocess.Popen', return_value=process) as spawn, \
             patch.object(self.engine, '_read_new', side_effect=[b'windivert initialized. capture is started.', b'']):
            status = self.engine.start(self.strategy(), self.logfile, timeout=10)
        self.assertEqual(status, 'ready')
        self.assertTrue(self.engine.ready)
        self.assertTrue(self.engine.running())
        spawn.assert_called_once()

    def test_start_raises_on_early_exit_before_ready(self):
        process = Mock(); process.poll.return_value = 1
        with patch('core.is_admin', return_value=True), \
             patch('core.foreign_engine', return_value=False), \
             patch('core.subprocess.Popen', return_value=process), \
             patch.object(self.engine, '_read_new', return_value=b''):
            with self.assertRaisesRegex(RuntimeError, 'winws.exe завершился'):
                self.engine.start(self.strategy(), self.logfile, timeout=10)
        self.assertFalse(self.engine.ready)
        self.assertIsNone(self.engine.process)

    def test_start_cancelled_returns_status(self):
        process = Mock(); process.poll.return_value = None
        with patch('core.is_admin', return_value=True), \
             patch('core.foreign_engine', return_value=False), \
             patch('core.subprocess.Popen', return_value=process), \
             patch.object(self.engine, '_read_new', return_value=b''):
            status = self.engine.start(self.strategy(), self.logfile, timeout=10, cancel=lambda: True)
        self.assertEqual(status, 'cancelled')
        self.assertFalse(self.engine.ready)

    def test_start_times_out_when_engine_never_ready(self):
        process = Mock(); process.poll.return_value = None
        with patch('core.is_admin', return_value=True), \
             patch('core.foreign_engine', return_value=False), \
             patch('core.subprocess.Popen', return_value=process), \
             patch.object(self.engine, '_read_new', return_value=b''):
            status = self.engine.start(self.strategy(), self.logfile, timeout=0)
        self.assertEqual(status, 'timeout')
        self.assertFalse(self.engine.ready)
        self.assertFalse(self.engine.running())
        process.terminate.assert_called_once()

    def test_immediate_output_is_not_skipped(self):
        process = Mock(); process.poll.return_value = None
        def spawn(*args, **kwargs):
            with self.logfile.open('ab') as f:
                f.write(b'windivert initialized. capture is started.\n')
            return process
        with patch('core.is_admin', return_value=True), patch('core.foreign_engine', return_value=False), patch('core.subprocess.Popen', side_effect=spawn):
            self.assertEqual(self.engine.start(self.strategy(), self.logfile, timeout=0.2), 'ready')

    def test_split_readiness_marker(self):
        process = Mock(); process.poll.return_value = None
        with patch('core.is_admin', return_value=True), patch('core.foreign_engine', return_value=False), patch('core.subprocess.Popen', return_value=process), patch.object(self.engine, '_read_new', side_effect=[b'windivert initial', b'ized. capture is ', b'started.']):
            self.assertEqual(self.engine.start(self.strategy(), self.logfile, timeout=1), 'ready')

    def test_old_readiness_log_is_not_reused(self):
        self.logfile.write_bytes(b'windivert initialized. capture is started.\n')
        process = Mock(); process.poll.return_value = None
        with patch('core.is_admin', return_value=True), patch('core.foreign_engine', return_value=False), patch('core.subprocess.Popen', return_value=process):
            self.assertEqual(self.engine.start(self.strategy(), self.logfile, timeout=0.1), 'timeout')

if __name__ == '__main__': unittest.main()
