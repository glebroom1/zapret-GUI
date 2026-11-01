import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from core import Engine, INDEPENDENT_ENGINE
from engine_session import record, recover


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'bin').mkdir()
        self.exe = self.root/'bin/winws.exe'
        shutil.copy2(sys.executable, self.exe)
        self.strategy = self.root/'general.bat'
        self.strategy.write_text('test')
        self.child = subprocess.Popen([str(self.exe), '-c', 'import time; time.sleep(60)'],
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.path = self.root/'session.json'
        record(self.path, self.child, self.strategy)

    def tearDown(self):
        if self.child.poll() is None:
            self.child.terminate()
        self.child.wait(timeout=5)
        self.temp.cleanup()

    def test_detach_recover_and_explicit_stop_real_process(self):
        first = Engine(lambda _: None)
        first.process = self.child
        first.detach()
        self.assertIsNone(self.child.poll())
        second = Engine(lambda _: None)
        second.session_file = self.path
        self.assertEqual(second.recover(), self.strategy)
        self.assertTrue(second.running())
        second.stop()
        self.child.wait(timeout=5)
        self.assertFalse(second.running())
        self.assertIsNone(recover(self.path))

    def test_reused_pid_or_changed_command_is_rejected(self):
        original = json.loads(self.path.read_text())
        for key, value in [('created', 0), ('exe', 'foreign.exe'),
                           ('args', ['foreign']), ('strategy', str(self.root/'other/general.bat'))]:
            with self.subTest(key=key):
                state = dict(original, **{key: value})
                self.path.write_text(json.dumps(state))
                self.assertIsNone(recover(self.path))
                self.assertIsNone(self.child.poll())

    def test_corrupt_state_is_rejected(self):
        self.path.write_text('{broken')
        self.assertIsNone(recover(self.path))

    def test_engine_survives_its_controller_process(self):
        script = '''
import subprocess, sys
from pathlib import Path
from core import INDEPENDENT_ENGINE
from engine_session import record
child = subprocess.Popen([sys.argv[1], '-c', 'import time; time.sleep(60)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=INDEPENDENT_ENGINE)
record(Path(sys.argv[2]), child, Path(sys.argv[3]))
'''
        other = self.root/'other.json'
        subprocess.run([sys.executable, '-c', script, str(self.exe), str(other),
                        str(self.strategy)], check=True, timeout=10)
        recovered = recover(other)
        self.assertIsNotNone(recovered)
        process, _ = recovered
        try:
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_gui_exit_and_reopen_restore_real_engine(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from app_qt import App
        qt = QApplication.instance() or QApplication([])
        self.path.replace(self.root/'engine-session.json')
        with patch.object(App, 'setup_tray'), patch.object(App, '_heal_autostart'):
            first = App(data_dir=self.root)
            self.assertEqual(first.state, 'connected')
            self.assertEqual(first.selected, 'general')
            first.request_exit()
            self.assertIsNone(self.child.poll())
            second = App(data_dir=self.root)
            self.assertEqual(second.state, 'connected')
            self.assertEqual(second.engine.process.pid, self.child.pid)
            second.engine.stop()
            second.request_exit()
            first.ticker.stop()
            second.ticker.stop()
