import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import core
from connection_health import assessment, verified, SingleProbe
from strategy_history import StrategyHistory
from test_app import Engine, PROFILES

class EvidenceTests(unittest.TestCase):
    def test_short_https_never_verifies_video(self):
        results={'YouTube':(True,1,''),'Discord':(True,2,'')}
        self.assertEqual(assessment(core.TARGETS,results),'partial')
        self.assertFalse(verified(core.TARGETS,results))
        self.assertTrue(verified({'Discord':'test'},results))

    def test_auto_partial_retains_first_eligible_engine_without_verification(self):
        e=Engine(); results={'YouTube':(True,1,'')}
        state,_,_=core.connect_profiles(e,PROFILES,{'YouTube':'test'},threading.Event(),Mock(),
                                        grace=0,check=lambda _:results)
        self.assertEqual(state,'provisional'); self.assertEqual(e.started,PROFILES[:1])
        self.assertTrue(e.running()); self.assertFalse(verified({'YouTube':'test'},results))

    def test_manual_partial_retains_control_without_success(self):
        e=Engine()
        state,_,_=core.connect_profiles(e,PROFILES[:1],{'YouTube':'test'},threading.Event(),Mock(),
                 grace=0,check=lambda _:{'YouTube':(True,1,'')},keep_running=True)
        self.assertEqual(state,'provisional'); self.assertTrue(e.running())

    def test_route_changed_before_result_prevents_success(self):
        e=Engine()
        with patch('core.network_key',side_effect=['a','b']):
            state,_,_=core.connect_profiles(e,PROFILES,{'Discord':'test'},threading.Event(),Mock(),
                      grace=0,check=lambda _:{'Discord':(True,1,'')})
        self.assertEqual(state,'network_changed'); self.assertFalse(e.running())

    def test_budget_does_not_claim_exhaustion(self):
        e=Engine()
        state,_,_=core.connect_profiles(e,PROFILES,{},threading.Event(),Mock(),budget=0)
        self.assertEqual(state,'limited'); self.assertEqual(e.started,[])

    def test_stalled_probe_cancel_is_prompt_and_singleflight(self):
        gate=SingleProbe(); release=threading.Event(); cancel=threading.Event()
        threading.Timer(.1,cancel.set).start()
        started=time.monotonic()
        try:
            with self.assertRaises(InterruptedError):
                gate.run(lambda _:release.wait(10),{},cancel)
            self.assertLess(time.monotonic()-started,1)
            with self.assertRaises(TimeoutError): gate.run(lambda _:None,{})
        finally: release.set()
        time.sleep(.05)
        self.assertEqual(gate.run(lambda _:42,{}),42)

    def test_stalled_probe_timeout(self):
        gate=SingleProbe(); release=threading.Event()
        try:
            with self.assertRaises(TimeoutError):gate.run(lambda _:release.wait(5),{},timeout=.05)
        finally:release.set()

    def test_history_network_engine_criterion_and_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'general.bat';p.write_text('test')
            (p.parent/'bin').mkdir();engine=p.parent/'bin/winws.exe';engine.write_bytes(b'old')
            h=StrategyHistory(p.parent/'history.json');targets={'Discord':'https://test'}
            with patch('strategy_history.network_key',return_value='network-a'):
                h.record_success(p,targets); self.assertEqual(h.preferred([p],targets),p)
                with patch('strategy_history.CRITERION','new'):
                    self.assertIsNone(h.preferred([p],targets))
                engine.write_bytes(b'new');self.assertIsNone(h.preferred([p],targets))
                engine.write_bytes(b'old')
                with patch('strategy_history.time.time',return_value=time.time()+86401):
                    self.assertIsNone(h.preferred([p],targets))
            with patch('strategy_history.network_key',return_value='network-b'):
                self.assertIsNone(h.preferred([p],targets))

    def test_missing_physical_interface_never_uses_default(self):
        from telegram_network import PhysicalRoute
        with self.assertRaises(OSError):PhysicalRoute(None).socket()

    def test_upstream_backoff_bounded_and_success_clears_it(self):
        import asyncio
        from telegram_worker import import_backend
        import_backend()
        from proxy.pool import _WsPool
        from proxy.config import proxy_config
        async def run():
            pool=_WsPool();key=(2,False,False)
            async def unavailable(*a):return None
            pool._connect_one=unavailable
            with patch.object(proxy_config,'pool_size',1):
                for _ in range(15):
                    pool._refilling[key]=asyncio.current_task()
                    await pool._refill(key,'127.0.0.1',[])
                self.assertLessEqual(pool._refill_after[key]-time.monotonic(),30)
                self.assertGreater(pool._refill_failures[key],10)
                ws=Mock()
                async def available(*a):return ws
                pool._connect_one=available
                pool._refilling[key]=asyncio.current_task()
                await pool._refill(key,'127.0.0.1',[])
                self.assertNotIn(key,pool._refill_after)
                pool.reset()
        asyncio.run(run())
