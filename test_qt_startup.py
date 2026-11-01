"""Exercise the normal entry point, including font setup, in a separate process."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class StartupTests(unittest.TestCase):
    def test_normal_main_starts_and_exits(self):
        script = '''
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import app_qt
import startup_admin
startup_admin.ensure_admin = lambda: True
original_exec = QApplication.exec
def bounded_exec(self):
    QTimer.singleShot(300, self.quit)
    return original_exec()
QApplication.exec = bounded_exec
raise SystemExit(app_qt.main())
'''
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', LOCALAPPDATA=folder)
            result = subprocess.run([sys.executable, '-c', script],
                                    cwd=Path(__file__).parent, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
