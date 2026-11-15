"""Release regressions: no real engine, user settings or external network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import queue
import subprocess
import sys
import threading
import time
import unittest
import asyncio
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from functools import partial

from app_qt import App
from connection_health import MEDIA_RESULT, assessment, eligible, verified
from core import connect_profiles, TARGETS
from telegram_worker import BackendRecovery
from test_app import Engine, PROFILES, GOOD


class SelectionEvidence(unittest.TestCase):
    def select(self, results, **kwargs):
        engine = Engine()
        state, profile, _ = connect_profiles(engine, PROFILES[:2], TARGETS,
            kwargs.pop('cancel', threading.Event()), kwargs.pop('emit', Mock()),
            grace=0, check=lambda _: results, **kwargs)
        return engine, state, profile

    def test_two_ready_profiles_select_first_partial(self):
        engine, state, profile = self.select(GOOD)
        self.assertEqual((state, profile), ('provisional', PROFILES[0]))
        self.assertEqual(engine.started, PROFILES[:1])
        self.assertTrue(engine.running())
        self.assertEqual(assessment(TARGETS, GOOD), 'partial')
        self.assertFalse(verified(TARGETS, GOOD))

    def test_failed_or_missing_target_and_failed_media_never_provisional(self):
        variants = [{**GOOD, name: (False, 0, 'failed')} for name in TARGETS]
        variants += [{k: v for k, v in GOOD.items() if k != 'YouTube'},
                     {**GOOD, MEDIA_RESULT: (False, 0, 'segment failed')}]
        for results in variants:
            with self.subTest(results=results):
                engine, state, _ = self.select(results)
                self.assertEqual(state, 'exhausted')
                self.assertFalse(engine.running())
                self.assertFalse(eligible(TARGETS, results))

    def test_stop_from_probe_beats_partial(self):
        cancel = threading.Event()
        engine, state, _ = self.select(GOOD, cancel=cancel,
            emit=lambda kind, value: cancel.set() if kind == 'probe' else None)
        self.assertEqual(state, 'cancelled')
        self.assertFalse(engine.running())

    def test_probe_over_budget_cannot_retain_partial(self):
        with patch('core.time.monotonic', side_effect=[0, 0, 0, 2]):
            engine, state, _ = self.select(GOOD, budget=1)
        self.assertEqual(state, 'limited')
        self.assertFalse(engine.running())

    def test_crashed_engine_cannot_retain_partial(self):
        e = Engine()
        def check(_):
            e.stop()
            return GOOD
        state, _, _ = connect_profiles(e, PROFILES[:2], TARGETS,
            threading.Event(), Mock(), grace=0, check=check)
        self.assertEqual(state, 'exhausted')
        self.assertFalse(e.running())


class Callbacks(unittest.TestCase):
    def window(self):
        w = Mock(generation=2, network_context='old', health_state='https',
                 busy=True, state='connecting', active_targets=TARGETS,
                 selected='general', next_network_poll=float('inf'))
        w.stale.side_effect = lambda gen: gen != w.generation
        w.engine.ready = True
        w.engine.running.return_value = True
        w.interval_seconds.return_value = 30
        return w

    def test_stale_new_network_does_not_touch_new_session(self):
        for method in (App.apply_connect_done, App.apply_checks):
            w = self.window()
            with patch('app_qt.network_key', return_value='new') as key:
                method(w, 1, GOOD)
            w.stale.assert_called_once_with(1)
            key.assert_not_called()
            self.assertEqual(w.health_state, 'https')
            self.assertTrue(w.busy)
            w.stop_engine.assert_not_called()
            w.set_state.assert_not_called()

    def test_current_new_network_stops_asynchronously_then_idle(self):
        w = self.window()
        w.cancel_event = threading.Event()
        w.operation_lock = threading.Lock()
        w.events = queue.Queue()
        w.closing = False
        w.pending_strategy = 'do-not-restart'
        w.set_state.side_effect = lambda state, text: setattr(w, 'state', state)
        w.stop_engine = partial(App.stop_engine, w)
        w.stop_worker = partial(App.stop_worker, w)
        threads = []
        thread_type = threading.Thread
        def thread(**kw):
            t = thread_type(**kw)
            threads.append(t)
            return t
        w.operation_lock.acquire()
        try:
            with patch('app_qt.network_key', return_value='new'), patch('app_qt.threading.Thread', side_effect=thread):
                App.apply_connect_done(w, 2, GOOD)
            self.assertEqual(w.state, 'stopping')
            self.assertEqual(w.generation, 3)
            self.assertTrue(w.cancel_event.is_set())
            w.engine.stop.assert_not_called()
            self.assertIsNone(w.pending_strategy)
        finally:
            w.operation_lock.release()
            for t in threads: t.join(2)
        self.assertTrue(all(not t.is_alive() for t in threads))
        w.engine.stop.assert_called_once()
        self.assertEqual(w.network_context, 'new')
        # A second interface change while stop is queued cannot invalidate it.
        w.next_network_poll = 0
        with patch('app_qt.network_key', return_value='newer'):
            App.tick(w)
        self.assertEqual(w.state, 'idle')
        self.assertFalse(w.busy)
        self.assertFalse(w.operation_active)
        w.start.assert_not_called()

    def test_partial_has_explicit_text_without_good_history(self):
        w = self.window()
        with patch('app_qt.network_key', return_value='old'):
            App.apply_connect_done(w, 2, GOOD)
        self.assertEqual(w.health_state, 'partial')
        self.assertIn('Стратегия выбрана по HTTPS · видео не проверено', w.set_state.call_args.args[1])
        w.record_history_success.assert_not_called()

    def test_periodic_partial_is_not_rotation_failure(self):
        w = self.window()
        w.state = 'connected'
        w.selection_auto = True
        w._installing = False
        with patch('app_qt.network_key', return_value='old'):
            App.apply_checks(w, 2, GOOD)
        w.rotation.observe.assert_called_once_with(True, True)
        self.assertEqual(w.health_state, 'partial')
        self.assertIn('видео не проверено', w.stage)
        w.record_history_success.assert_not_called()

    def test_worker_delivers_provisional_completion(self):
        w = self.window()
        w.events = queue.Queue()
        w.selection_auto = True
        with patch('app_qt.is_admin', return_value=True), patch('app_qt.connect_profiles', return_value=('provisional', PROFILES[0], GOOD)):
            App._connect_worker(w, PROFILES, 2, TARGETS, threading.Event())
        self.assertEqual(w.events.get_nowait(), ('connect_done', (2, GOOD)))
        self.assertEqual(w.events.get_nowait(), ('operation_done', 2))
        w.engine.stop.assert_not_called()


class RecoveryCounter(unittest.TestCase):
    def test_four_spaced_exits_are_recoverable(self):
        r = BackendRecovery()
        for run in range(4):
            for now in range(run * 100, run * 100 + 65, 2):
                r.observe(now, True)
            self.assertEqual(r.exited(), 2)

    def test_four_fast_exits_are_bounded(self):
        r = BackendRecovery()
        for run, delay in enumerate((2, 4, 8)):
            r.observe(run * 10, True)
            self.assertEqual(r.exited(), delay)
        with self.assertRaisesRegex(RuntimeError, 'repeatedly stopped'):
            r.exited()

    def test_sleep_gap_and_unready_do_not_reset_failures(self):
        r = BackendRecovery()
        r.exited()
        r.observe(0, True)
        r.observe(120, True)
        self.assertEqual(r.failures, 1)
        r.observe(122, False)
        r.observe(200, True)
        self.assertEqual(r.failures, 1)

    def test_network_retry_without_exit_does_not_count_as_crash(self):
        r = BackendRecovery()
        for now in range(500): r.observe(now, now % 10 != 0)
        self.assertEqual(r.failures, 0)


class WorkerLoop(unittest.TestCase):
    def run_worker(self, uptime, stop_on_fourth=False, network_retries=False):
        import telegram_worker as worker
        worker.import_backend()  # Load pinned backend config/stats, no listener yet.
        clock = SimpleNamespace(now=0.0)
        backend = SimpleNamespace(_server_instance=None, starts=0, exits=0, retries=0)
        async def run(stop):
            backend.starts += 1
            backend._server_instance = object()
            start = clock.now
            try:
                while not stop.is_set():
                    await asyncio.sleep(0)
                    if network_retries:
                        backend.retries += 1
                        if clock.now >= 100: stop.set()
                    elif clock.now - start >= uptime:
                        backend.exits += 1
                        if stop_on_fourth and backend.exits == 4: stop.set()
                        raise RuntimeError('injected backend exit')
                    if backend.starts == 5: stop.set()
            finally:
                backend._server_instance = None
        backend._run = run
        class VirtualAsync:
            def __getattr__(self, key): return getattr(asyncio, key)
            async def sleep(self, delay):
                clock.now += delay
                await asyncio.sleep(0)
            async def wait_for(self, coro, timeout):
                # Advance the supervisor clock, never block for virtual minutes.
                coro.close()
                clock.now += timeout
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                raise TimeoutError
        config = dict(port=12345, direct=False, cfproxy=False, domain='')
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(worker, 'import_backend', return_value=backend), \
             patch.object(worker, 'setup_logging'), \
             patch.object(worker, 'unprotect', return_value='fixture'), \
             patch.object(worker, 'asyncio', VirtualAsync()), \
             patch.object(worker, 'time', SimpleNamespace(monotonic=lambda:clock.now, time=lambda:clock.now)):
            saved = dict(config, secret='fixture', control_key='fixture')
            error = None
            try:
                asyncio.run(worker.serve(Path(folder), saved))
            except RuntimeError as exc:
                error = str(exc)
            self.assertFalse((Path(folder)/'worker.json').exists())
        return backend, error

    def test_actual_supervisor_recovers_four_stable_exits(self):
        b, error = self.run_worker(70)
        self.assertIsNone(error)
        self.assertEqual((b.starts, b.exits), (5, 4))

    def test_actual_supervisor_limits_four_fast_exits(self):
        b, error = self.run_worker(4)
        self.assertEqual(error, 'Telegram backend repeatedly stopped')
        self.assertEqual((b.starts, b.exits), (4, 4))

    def test_stop_wins_over_fourth_exit(self):
        b, error = self.run_worker(4, stop_on_fourth=True)
        self.assertIsNone(error)
        self.assertEqual((b.starts, b.exits), (4, 4))

    def test_upstream_retries_do_not_restart_backend(self):
        b, error = self.run_worker(4, network_retries=True)
        self.assertIsNone(error)
        self.assertEqual((b.starts, b.exits), (1, 0))
        self.assertGreater(b.retries, 4)


class TrayContract(unittest.TestCase):
    def test_default_close_hides_and_explicit_exit_quits(self):
        code = '''
import ast
from pathlib import Path
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer
tree = ast.parse(Path('app_qt.py').read_text(encoding='utf-8'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'App')
methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('closeEvent', 'request_exit')]
ns = {'QApplication': QApplication}
exec(compile(ast.Module(body=methods, type_ignores=[]), 'app_qt.py', 'exec'), ns)
class Window(QWidget):
    closeEvent = ns['closeEvent']
    request_exit = ns['request_exit']
app = QApplication([])
assert app.quitOnLastWindowClosed()
w = Window()
w.tray = Mock()
w.exit_requested = w.feature_busy = w.tg_busy = w.operation_active = False
w.state = 'idle'
w.save = Mock()
w.engine = Mock()
observed = []
def after_close():
    assert not w.isVisible()
    w.engine.detach.assert_not_called()
    observed.append('alive after close')
    w.request_exit()
w.show()
QTimer.singleShot(0, w.close)
QTimer.singleShot(100, after_close)
QTimer.singleShot(2000, app.quit)
app.exec()
assert observed == ['alive after close']
assert w.exit_requested
w.engine.detach.assert_called_once()
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
