import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from single_instance import SingleInstance
from telegram_proxy import TelegramProxyManager, atomic_json, read_json, protect
from test_telegram_proxy import free_port


class InstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_background_duplicate_does_not_show_window(self):
        with tempfile.TemporaryDirectory() as folder, patch(
                'single_instance.QStandardPaths.writableLocation', return_value=folder):
            first = SingleInstance()
            self.assertTrue(first.acquire())
            restored = Mock()
            try:
                first.listen(restored)
                code = '''
import sys
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from single_instance import notify_existing
app = QCoreApplication([])
folder = sys.argv[1]
sys.argv = ['gui.exe', '--tray', '--connect']
with patch('single_instance.QStandardPaths.writableLocation', return_value=folder):
    sys.exit(0 if notify_existing() else 1)
'''
                child = subprocess.Popen([sys.executable, '-c', code, folder])
                deadline = time.monotonic() + 3
                while child.poll() is None and time.monotonic() < deadline:
                    QCoreApplication.processEvents()
                    time.sleep(.005)
                self.assertEqual(child.wait(timeout=2), 0)
                for _ in range(10):
                    QCoreApplication.processEvents()
                restored.assert_not_called()
            finally:
                first.server.close()
                first.lock.unlock()

    def test_dead_owner_lock_recovers_without_time_based_stealing(self):
        with tempfile.TemporaryDirectory() as folder, patch(
                'single_instance.QStandardPaths.writableLocation', return_value=folder):
            code = '''
import os, sys
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from single_instance import SingleInstance
app = QCoreApplication([])
with patch('single_instance.QStandardPaths.writableLocation', return_value=sys.argv[1]):
    owner = SingleInstance()
    os._exit(0 if owner.acquire() else 1)
'''
            subprocess.run([sys.executable, '-c', code, folder], check=True, timeout=5)
            self.assertTrue((Path(folder) / 'ZapretDesk/gui.lock').exists())
            next_owner = SingleInstance()
            try:
                self.assertTrue(next_owner.acquire())
            finally:
                next_owner.lock.unlock()

    def test_duplicate_probe_precedes_elevation(self):
        import app_qt
        with patch('single_instance.notify_existing', return_value=True), \
                patch('startup_admin.ensure_admin') as elevate:
            self.assertEqual(app_qt.main(), 0)
            elevate.assert_not_called()


class AutostartTests(unittest.TestCase):
    def test_reads_registered_task_path_instead_of_current_executable(self):
        import autostart
        xml = '''<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Actions>
<Exec><Command>C:\\old folder\\gui.exe</Command><Arguments>--tray --connect</Arguments></Exec>
</Actions></Task>'''.encode('utf-16')
        with patch.object(autostart, '_has_scheduled_task', return_value=True), \
                patch.object(autostart.subprocess, 'run', return_value=Mock(returncode=0, stdout=xml)):
            self.assertEqual(autostart.current_value(), '"C:\\old folder\\gui.exe" --tray --connect')

    def test_apostrophe_is_literal_in_task_registration(self):
        import autostart
        with patch.object(autostart, 'target_binary', return_value="C:\\O'Brien\\gui.exe"), \
                patch.object(autostart.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertTrue(autostart._create_scheduled_task())
            self.assertIn("C:\\O''Brien\\gui.exe", run.call_args.args[0][-1])


class WorkerHardening(unittest.TestCase):
    def test_forced_stop_checks_handle_identity_and_worker_command(self):
        from process_control import terminate_worker
        import psutil
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                live = psutil.Process(child.pid)
                state = dict(pid=child.pid, created=live.create_time(), exe=live.exe())
                with self.assertRaisesRegex(RuntimeError, 'Идентичность'):
                    terminate_worker(dict(state, created=0), folder)
                with self.assertRaisesRegex(RuntimeError, 'не является'):
                    terminate_worker(state, folder)
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_hung_owned_worker_can_be_stopped_without_killing_by_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = TelegramProxyManager(folder)
            manager.start(port=free_port(), direct=False, cfproxy=False)
            try:
                with patch.object(manager, '_request', side_effect=OSError('no response')):
                    manager.stop(force=True)
                self.assertFalse(manager.status()['running'])
                self.assertFalse(manager.state_file.exists())
            finally:
                manager.stop()

    def test_startup_phase_does_not_contact_port_zero_or_spawn_duplicate(self):
        import psutil
        with tempfile.TemporaryDirectory() as folder:
            manager = TelegramProxyManager(folder)
            process = psutil.Process()
            atomic_json(manager.state_file, dict(pid=process.pid, created=process.create_time(),
                exe=process.exe(), phase='starting', control_port=0))
            with patch.object(manager, '_request') as request, patch('telegram_proxy.subprocess.Popen') as spawn:
                self.assertEqual(manager.status(), dict(running=False, starting=True))
                with self.assertRaises(RuntimeError):
                    manager.start()
                request.assert_not_called()
                spawn.assert_not_called()

    def test_startup_error_and_traceback_redact_both_credentials(self):
        import telegram_worker
        first, second = 'a' * 32, 'b' * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            atomic_json(root/'config.json', dict(secret=protect(first), control_key=protect(second)))
            async def fail(*args):
                telegram_worker.setup_logging(root, first, second)
                raise RuntimeError(first + ' ' + second)
            logger = logging.getLogger()
            original, level = logger.handlers[:], logger.level
            try:
                with patch.object(telegram_worker, 'serve', fail):
                    self.assertEqual(telegram_worker.main(root), 1)
                for name in ('startup-error.txt', 'proxy.log'):
                    text = (root/name).read_text(encoding='utf-8')
                    self.assertNotIn(first, text)
                    self.assertNotIn(second, text)
                    self.assertIn('[hidden]', text)
            finally:
                for handler in logger.handlers:
                    if handler not in original:
                        handler.close()
                logger.handlers[:] = original
                logger.setLevel(level)
