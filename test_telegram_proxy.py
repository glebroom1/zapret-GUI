"""Local worker lifecycle/control tests. No user account or real Telegram client."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from telegram_proxy import TelegramProxyManager, atomic_json, read_json, protect, unprotect, validate


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = TelegramProxyManager(self.root)

    def tearDown(self):
        self.manager.stop()
        self.temp.cleanup()

    def start(self):
        return self.manager.start(port=free_port(), direct=False, cfproxy=False, domain='')

    def test_empty_status_does_not_create_config(self):
        self.assertEqual(self.manager.status(), dict(running=False))
        self.assertFalse(self.manager.config_file.exists())

    def test_start_recover_duplicate_restart_and_stop(self):
        first = self.start()
        self.assertTrue(first['running'])
        second = TelegramProxyManager(self.root)
        self.assertEqual(second.status()['pid'], first['pid'])
        self.assertEqual(second.start(port=free_port())['pid'], first['pid'])
        link = second.link()
        secret = unprotect(read_json(second.config_file)['secret'])
        self.assertIn('secret=dd' + secret, link)
        raw = second.config_file.read_text()
        if os.name == 'nt':
            self.assertIn('dpapi:', raw)
            self.assertNotIn(secret, raw)
        process = __import__('psutil').Process(first['pid'])
        self.assertFalse(any(secret in arg for arg in process.cmdline()))
        self.assertNotIn(secret, (self.root / 'proxy.log').read_text(encoding='utf-8'))
        second.stop()
        self.assertFalse(second.status()['running'])
        restarted = self.manager.start(port=first['port'], direct=False, cfproxy=False)
        self.assertTrue(restarted['running'])
        self.assertEqual(self.manager.link(), link)

    def test_reused_identity_never_attaches_or_stops_process(self):
        first = self.start()
        state = read_json(self.manager.state_file)
        try:
            atomic_json(self.manager.state_file, dict(state, created=0))
            self.assertFalse(self.manager.status()['running'])
            self.manager.stop()
            self.assertTrue(__import__('psutil').pid_exists(first['pid']))
        finally:
            atomic_json(self.manager.state_file, state)

    def test_control_rejects_unauthenticated_requests(self):
        self.start()
        state = read_json(self.manager.state_file)
        conn = http.client.HTTPConnection('127.0.0.1', state['control_port'], timeout=2)
        conn.request('POST', '/stop')
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.close()
        self.assertTrue(self.manager.status()['running'])

    def test_unresponsive_worker_is_not_replaced(self):
        first = self.start()
        with patch.object(self.manager, '_request', side_effect=OSError('temporary failure')):
            self.assertTrue(self.manager.status()['unresponsive'])
            with patch('telegram_proxy.subprocess.Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'Второй процесс'):
                    self.manager.start()
                popen.assert_not_called()
        self.assertEqual(self.manager.status()['pid'], first['pid'])

    def test_second_worker_is_refused_by_process_lock(self):
        first = self.start()
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('launch_qt.py')),
                                 '--telegram-worker', '--telegram-data', str(self.root)],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.manager.status()['pid'], first['pid'])

    def test_survives_controller_process_exit(self):
        code = ('from telegram_proxy import TelegramProxyManager; import sys,json; '
                'm=TelegramProxyManager(sys.argv[1]); '
                'print(json.dumps(m.start(port=int(sys.argv[2]),direct=False,cfproxy=False)))')
        result = subprocess.run([sys.executable, '-c', code, str(self.root), str(free_port())],
                                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=35)
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = json.loads(result.stdout)
        self.assertEqual(self.manager.status()['pid'], previous['pid'])
        self.assertTrue(self.manager.status()['running'])

    def test_port_conflict_does_not_take_over_listener(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            with self.assertRaisesRegex(RuntimeError, 'порт занят'):
                self.manager.start(port=sock.getsockname()[1], direct=False, cfproxy=False)
            self.assertNotEqual(sock.fileno(), -1)
            self.assertFalse(self.manager.status()['running'])


class ConfigurationTests(unittest.TestCase):
    def test_corrupt_saved_secret_never_launches_worker(self):
        with tempfile.TemporaryDirectory() as root:
            manager = TelegramProxyManager(root)
            atomic_json(manager.config_file, dict(secret=protect('0' * 64)))
            with patch('telegram_proxy.subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'Повреждены'):
                    manager.start()
                popen.assert_not_called()

    def test_invalid_options(self):
        for options in [dict(port=0), dict(port=65536), dict(port=True),
                        dict(port='443x'), dict(domain='https://example.com'),
                        dict(domain='example.com/path'), dict(domain='-bad.example.com')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate(**options)

    def test_worker_dispatch_precedes_admin_and_qt(self):
        import launch_qt
        with patch.object(sys, 'argv', ['app', '--telegram-worker', '--telegram-data', 'test-data']), \
                patch('telegram_worker.main', return_value=42) as worker, \
                patch('startup_admin.ensure_admin', side_effect=AssertionError('unexpected elevation')):
            self.assertEqual(launch_qt.main(), 42)
            worker.assert_called_once_with('test-data')


class TelegramCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def test_recovered_proxy_does_not_replace_main_button_or_stop_on_exit(self):
        from app_qt import App
        with tempfile.TemporaryDirectory() as root, patch.object(App, 'setup_tray'), \
                patch.object(App, '_heal_autostart'):
            window = App(data_dir=Path(root))
            original = window.main_button.text() if hasattr(window, 'main_button') else window.connect_button.text()
            state = dict(running=True, port=1443, direct=True, connections=1, ws=0, cf=1)
            window.apply_telegram_status(state)
            self.assertEqual(window.tg_button.text(), 'Остановить прокси')
            self.assertFalse(window.tg_port.isEnabled())
            self.assertTrue(window.tg_copy_button.isEnabled())
            self.assertEqual(window.main_button.text() if hasattr(window, 'main_button') else window.connect_button.text(), original)
            with patch.object(window.tg_manager, 'stop') as stop:
                window.request_exit()
                stop.assert_not_called()
            window.ticker.stop()
            window.tg_timer.stop()

    def test_stale_poll_cannot_override_started_proxy(self):
        from app_qt import App
        with tempfile.TemporaryDirectory() as root, patch.object(App, 'setup_tray'), \
                patch.object(App, '_heal_autostart'):
            window = App(data_dir=Path(root))
            window.tg_generation = 1
            state = dict(running=True, port=1443, direct=True, connections=0, ws=0, cf=0)
            window.apply_telegram_status(state)
            window.handle_telegram_event('telegram_status', (0, dict(running=False)))
            self.assertTrue(window.tg_status['running'])
            window.request_exit()
            window.ticker.stop()
            window.tg_timer.stop()
