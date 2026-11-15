"""Evidence levels, local network identity and bounded cancellable probe execution."""
import concurrent.futures
import hashlib
import json
import socket
import threading
import time
import psutil

CRITERION = 'https-only-v3-provisional'
MEDIA_RESULT = 'YouTube media'

def network_key():
    # No packets sent, DNS changed or external identity service contacted.
    source = ''
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('1.1.1.1', 443))
            source = sock.getsockname()[0]
    except OSError:
        pass
    stats = psutil.net_if_stats()
    rows = [(name, sorted(a.address for a in addresses
                         if a.family in (socket.AF_INET, socket.AF_INET6)))
            for name, addresses in psutil.net_if_addrs().items()
            if name in stats and stats[name].isup]
    return hashlib.sha256(json.dumps([source, sorted(rows)]).encode()).hexdigest()

def assessment(targets, results):
    if not targets:
        return 'unverified'
    if not all(results.get(n, (False,))[0] for n in targets):
        return 'degraded'
    # A supplied media failure must veto HTTPS eligibility. No validated media
    # probe is shipped, so even an extra positive result cannot verify playback.
    if 'YouTube' in targets and results.get(MEDIA_RESULT, (None,))[0] is False:
        return 'degraded'
    return 'partial' if 'YouTube' in targets else 'https'

def verified(targets, results):
    return assessment(targets, results) == 'https'

def eligible(targets, results):
    """Can retain this ready strategy; deliberately weaker than verification."""
    return assessment(targets, results) in ('https', 'partial')

def evidence_text(targets, results):
    level = assessment(targets, results)
    if level == 'partial':
        return 'Стратегия выбрана по HTTPS · видео не проверено'
    if level == 'https':
        return 'HTTPS доступен'
    return 'Движок работает · доступность не подтверждена'

class DaemonPool:
    """Small fixed fanout; no interpreter-exit join of stalled OS DNS calls.

    Only used inside SingleProbe, which admits one batch until it really ends.
    """
    def __init__(self, max_workers):
        self.limit = max_workers
        self.count = 0
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, fn, *args):
        self.count += 1
        # check_network also submits two TCP controls after the HTTPS batch.
        if self.count > self.limit + 2:
            raise RuntimeError('Probe fanout exceeded')
        future = concurrent.futures.Future()
        def run():
            try: future.set_result(fn(*args))
            except BaseException as exc: future.set_exception(exc)
        threading.Thread(target=run, daemon=True, name='health-probe').start()
        return future

class SingleProbe:
    def __init__(self): self.lock = threading.Lock()
    def run(self, fn, targets, cancel=None, timeout=20):
        if not self.lock.acquire(False):
            raise TimeoutError('Предыдущая сетевая проверка ещё завершается')
        done = threading.Event()
        outcome = []
        def work():
            try: outcome.append((True, fn(targets)))
            except BaseException as exc: outcome.append((False, exc))
            finally:
                self.lock.release()
                done.set()
        threading.Thread(target=work, daemon=True, name='health-batch').start()
        deadline = time.monotonic() + timeout
        while not done.wait(.05):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Проверка отменена')
            if time.monotonic() >= deadline:
                raise TimeoutError('Истёк бюджет сетевой проверки')
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Проверка отменена')
        if not outcome[0][0]: raise outcome[0][1]
        return outcome[0][1]
