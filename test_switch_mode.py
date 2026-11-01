"""Engine activation, explicit selection and hidden-window shutdown regressions."""
import os
import queue
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from core import connect_profiles
from test_app import Engine, PROFILES, TARGETS, BAD, OFFLINE
from app_qt import App


class SwitchMode(unittest.TestCase):
    def test_failed_diagnostics_keep_manual_engine_running(self):
        for results in (BAD, OFFLINE, {}):
            engine = Engine()
            status, profile, _ = connect_profiles(
                engine, PROFILES[:1], TARGETS, threading.Event(), lambda *e: None,
                grace=0, check=lambda targets: results, keep_running=True)
            self.assertEqual(status, 'running')
            self.assertTrue(engine.running())
            self.assertEqual(profile, PROFILES[0])

    def test_auto_sweep_keeps_last_ready_strategy_after_failed_checks(self):
        engine = Engine()
        status, profile, _ = connect_profiles(
            engine, PROFILES, TARGETS, threading.Event(), lambda *e: None,
            grace=0, check=lambda targets: BAD, keep_running=True)
        self.assertEqual(status, 'running')
        self.assertEqual(profile, PROFILES[-1])
        self.assertTrue(engine.running())

    def test_selection_while_checking_cancels_then_queues_latest_choice(self):
        window = Mock(closing=False, exit_requested=False, operation_active=True,
                      state='connecting')
        App.on_strategy_select(window, 'general2')
        self.assertEqual(window.pending_strategy, 'general2')
        window.stop_engine.assert_called_once()
        window.start.assert_not_called()

    def test_manual_selection_bypasses_auto_history(self):
        window = Mock(closing=False, exit_requested=False, operation_active=False,
                      state='connected', auto=True)
        window.engine.running.return_value = True
        App.on_strategy_select(window, 'general2')
        window.start.assert_called_once_with(auto=False)

    def test_stopped_event_starts_latest_queued_strategy(self):
        window = Mock(closing=False, state='stopping', pending_strategy='general3')
        window.events = queue.Queue()
        window.events.put(('stopped', 2))
        App.tick(window)
        self.assertEqual(window.selected, 'general3')
        self.assertIsNone(window.pending_strategy)
        window.start.assert_called_once_with(auto=False)

    def test_hidden_window_keeps_scheduling_checks(self):
        window = Mock(state='connected', busy=False, next_check=0)
        window.engine.running.return_value = True
        window.events = queue.Queue()
        window.isVisible.return_value = False
        App.tick(window)
        window.check.assert_called_once()

    def test_hidden_window_exit_terminates_event_loop(self):
        code = '''
import tempfile
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app_qt import App
app = QApplication([])
with tempfile.TemporaryDirectory() as folder, patch.object(App, 'setup_tray'), patch.object(App, '_heal_autostart'):
    window = App(data_dir=folder)
    window.tray = None
    assert not window.isVisible()
    QTimer.singleShot(0, window.request_exit)
    app.exec()
    assert window.exit_requested
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)
