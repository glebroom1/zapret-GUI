"""Exercise control/forced-stop with the built worker and isolated loopback config."""
import json
import hashlib
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from telegram_proxy import TelegramProxyManager, atomic_json, protect

EXE = ROOT/'dist/Zapret-GUI-0.9.0.exe'
with tempfile.TemporaryDirectory(prefix='zapret-beta3-qa-') as temp:
    folder = Path(temp)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    secret, token = secrets.token_hex(16), secrets.token_hex(32)
    atomic_json(folder/'config.json', dict(port=port, direct=False, cfproxy=False,
        domain='', secret=protect(secret), control_key=protect(token)))
    child = subprocess.Popen([str(EXE), '--telegram-worker', '--telegram-data', str(folder)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    manager = TelegramProxyManager(folder)
    try:
        deadline = time.monotonic()+25
        while time.monotonic()<deadline:
            state = manager.status()
            if state['running']:
                break
            if child.poll() is not None:
                raise AssertionError('Frozen worker exited before ready')
            time.sleep(.05)
        assert state['running']
        # Force path is used only when authenticated control is unresponsive.
        from unittest.mock import patch
        with patch.object(manager, '_request', side_effect=OSError('simulated hung control')):
            manager.stop(force=True)
        child.wait(timeout=10)
        assert not manager.status()['running']
        assert secret not in (folder/'proxy.log').read_text(encoding='utf-8')
        assert token not in (folder/'proxy.log').read_text(encoding='utf-8')
        result = dict(frozen_worker_start=True, verified_force_stop=True,
            control_status=True, log_redaction=True, isolated_data=True,
            exe_sha256=hashlib.sha256(EXE.read_bytes()).hexdigest())
        (ROOT/'build/frozen-beta3-qa.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
    finally:
        if child.poll() is None:
            manager.stop()
            child.wait(timeout=20)
