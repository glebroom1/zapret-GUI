"""Actual selection-loop integration tests. No desktop or network changes."""
import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from core import connect_profiles, ForeignEngineError, ProcessInspectionError, check_network
from app import App

GOOD = {'YouTube': (True, 10, ''), 'Discord': (True, 10, ''), 'Control': (True, 10, '')}
BAD = {'YouTube': (False, None, 'timeout'), 'Discord': (False, None, 'timeout'), 'Control': (True, 10, '')}
OFFLINE = {name: (False, None, 'timeout') for name in GOOD}
TARGETS = {'YouTube': 'test', 'Discord': 'test'}
PROFILES = [Path('general.bat'), Path('general (ALT).bat'), Path('general (ALT2).bat')]

class Engine:
    def __init__(self, outcomes=None):
        self.ready = False
        self.alive = False
        self.started = []
        self.session_log = Path('test.log')
        self.outcomes = list(outcomes or ['ready']*3)
    def start(self, profile, logfile, **kw):
        self.stop()
        self.started.append(profile)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception): raise outcome
        self.ready = outcome == 'ready'
        self.alive = self.ready
        return outcome
    def stop(self): self.alive = self.ready = False
    def running(self): return self.alive

class SelectionTests(unittest.TestCase):
    def test_tls_failures_with_working_route_do_not_force_offline_pause(self):
        with patch('core.probe', return_value=(False, None, 'TLS failure')), patch('core.probe_tcp', return_value=(True, 1, '')):
            results = check_network(TARGETS)
        self.assertTrue(results['Internet TCP'][0])
        self.assertFalse(results['YouTube'][0])

    def run_selection(self, replies, outcomes=None, cancel=None, emit=None):
        engine = Engine(outcomes)
        events = []
        result = connect_profiles(engine, PROFILES, TARGETS, cancel or threading.Event(),
                                  emit or (lambda *e: events.append(e)), grace=0,
                                  check=Mock(side_effect=replies))
        return engine, events, result

    def test_first_failed_reply_advances_without_second_click(self):
        engine, events, result = self.run_selection([BAD, GOOD])
        self.assertEqual(engine.started, PROFILES[:2])
        self.assertEqual(result[:2], ('connected', PROFILES[1]))
        self.assertTrue(engine.running())

    def test_early_exit_and_timeout_do_not_block_remaining_profiles(self):
        engine, _, result = self.run_selection([GOOD], [RuntimeError('early exit'), 'timeout', 'ready'])
        self.assertEqual(engine.started, PROFILES)
        self.assertEqual(result[0], 'connected')

    def test_full_sweep_stops_and_reports_exhausted(self):
        engine, _, result = self.run_selection([BAD]*3)
        self.assertEqual(result[0], 'exhausted')
        self.assertEqual(engine.started, PROFILES)
        self.assertFalse(engine.running())

    def test_missing_discord_result_never_counts_as_success(self):
        engine, _, result = self.run_selection([{'YouTube': GOOD['YouTube']}]*3)
        self.assertEqual(result[0], 'exhausted')

    def test_offline_pause_rechecks_same_profile(self):
        class NoWait:
            def is_set(self): return False
            def wait(self, n): return False
        engine, events, result = self.run_selection([OFFLINE, GOOD], cancel=NoWait())
        self.assertEqual(engine.started, PROFILES[:1])
        self.assertIn('offline', [e[0] for e in events])
        self.assertEqual(result[0], 'connected')

    def test_cancel_offline_pause_stops_process(self):
        cancel = threading.Event()
        def emit(kind, value):
            if kind == 'offline': cancel.set()
        engine, _, result = self.run_selection([OFFLINE], cancel=cancel, emit=emit)
        self.assertEqual(result[0], 'cancelled')
        self.assertFalse(engine.running())
        self.assertEqual(len(engine.started), 1)

    def test_cancel_after_failure_prevents_next_spawn(self):
        cancel = threading.Event()
        def emit(kind, value):
            if kind == 'probe': cancel.set()
        engine, _, result = self.run_selection([BAD], cancel=cancel, emit=emit)
        self.assertEqual(result[0], 'cancelled')
        self.assertEqual(len(engine.started), 1)
        self.assertFalse(engine.running())

    def test_foreign_engine_and_missing_rights_abort_sweep(self):
        for exc in (ForeignEngineError('foreign'), PermissionError('rights'), ProcessInspectionError('inspection')):
            engine = Engine([exc])
            with self.assertRaises(type(exc)):
                connect_profiles(engine, PROFILES, TARGETS, threading.Event(), lambda *e: None, grace=0)
            self.assertEqual(len(engine.started), 1)

    def test_attempt_progress_has_real_numbers(self):
        _, events, _ = self.run_selection([BAD, BAD, GOOD])
        self.assertEqual([e[1][:2] for e in events if e[0] == 'attempt'], [(1, 3), (2, 3), (3, 3)])

class WorkerHarness:
    stale = App.stale
    post_failed = App.post_failed
    _connect_worker = App._connect_worker
    def __init__(self):
        self.generation = 1
        self.events = queue.Queue()
        self.operation_lock = threading.Lock()
        self.engine = Engine()

class GuiBridgeTests(unittest.TestCase):
    def test_real_worker_runs_two_profiles_before_sending_success(self):
        h = WorkerHarness()
        with patch('app.is_admin', return_value=True), patch('core.check_network', side_effect=[BAD, GOOD]), patch('app.ENGINE_GRACE', 0):
            App.connect_worker(h, PROFILES, 1, TARGETS, threading.Event())
        self.assertEqual(h.engine.started, PROFILES[:2])
        messages = list(h.events.queue)
        self.assertEqual([m[0] for m in messages][-2:], ['connect_done', 'operation_done'])

    def test_stale_result_is_ignored(self):
        h = WorkerHarness(); h.generation = 2; h.set_state = Mock()
        App.apply_connect_done(h, 1, GOOD)
        h.set_state.assert_not_called()

    def test_recovery_starts_next_profile(self):
        h = WorkerHarness(); h.paths = PROFILES; h.log = Mock(); h.selected = Mock(); h.start = Mock()
        App.handle_rotation(h, PROFILES[1].stem)
        h.selected.set.assert_called_once_with(PROFILES[1].stem)
        h.start.assert_called_once_with(auto=True)

if __name__ == '__main__': unittest.main()
