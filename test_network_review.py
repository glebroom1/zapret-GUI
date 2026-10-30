"""Network-free review regressions for DNS recovery and rotation."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import core

class NetworkReviewTests(unittest.TestCase):
    def test_doh_failure_retains_actual_reason(self):
        with patch('core.doh_resolve', side_effect=RuntimeError('TLS certificate failed')):
            ok, ms, reason = core.probe_via_doh('www.youtube.com', core.TARGETS['YouTube'])
        self.assertFalse(ok)
        self.assertIn('TLS certificate failed', reason)

    def engine(self):
        engine = Mock()
        engine.start.return_value = 'ready'
        engine.ready = True
        engine.running.return_value = True
        return engine

    def test_recovered_dns_stops_sweep_and_keeps_engine(self):
        engine = self.engine()
        cancel = threading.Event()
        targets = {'YouTube': core.TARGETS['YouTube']}
        def check(targets):
            results = {'YouTube': (False, None, 'getaddrinfo failed'),
                       **{n: (True, 1, '') for n in core.CONTROLS}}
            core._dns_recovery(targets, results)
            return results
        with patch('core.probe_via_doh', return_value=(True, 2, '')):
            status, _, _ = core.connect_profiles(engine, [Path('A.bat'), Path('B.bat')],
                targets, cancel, Mock(), grace=0, check=check)
        self.assertEqual(status, 'connected')
        engine.start.assert_called_once()
        engine.stop.assert_not_called()

    def test_cancel_during_probe_stops_owned_engine(self):
        engine = self.engine()
        cancel = threading.Event()
        def check(_):
            cancel.set()
            return {'YouTube': (True, 1, '')}
        status, _, _ = core.connect_profiles(engine, [Path('A.bat')],
            {'YouTube': core.TARGETS['YouTube']}, cancel, Mock(), grace=0, check=check)
        self.assertEqual(status, 'cancelled')
        engine.stop.assert_called_once()

    def test_exhaustion_stops_engine_and_is_bounded(self):
        engine = self.engine()
        status, _, _ = core.connect_profiles(engine, [Path('A.bat'), Path('B.bat')],
            {'YouTube': core.TARGETS['YouTube']}, threading.Event(), Mock(), grace=0,
            check=lambda _: {'YouTube': (False, None, 'DNS failed'), 'Internet 1': (True, 1, '')})
        self.assertEqual(status, 'exhausted')
        self.assertEqual(engine.start.call_count, 2)
        self.assertGreaterEqual(engine.stop.call_count, 2)