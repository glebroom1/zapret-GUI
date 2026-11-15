"""Opt-in live validation. Only initial MTProto packets, no user account or client.

Run with the build environment: python qa_telegram_live.py [--exe path]
The GUI parent exit and the proxy worker's actual byte forwarding are exercised.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from telegram_proxy import TelegramProxyManager, read_json, unprotect


def read_exact(sock, count):
    out = b''
    while len(out) < count:
        chunk = sock.recv(count - len(out))
        if not chunk:
            raise EOFError('Proxy closed the connection')
        out += chunk
    return out


def initial_exchange(manager, dc):
    config = read_json(manager.config_file)
    secret = bytes.fromhex(unprotect(config['secret']))
    init = bytearray(os.urandom(64))
    init[0] = 0x42
    init[56:60] = b'\xdd' * 4
    init[60:62] = struct.pack('<h', dc)
    keys = bytes(init[8:56])
    import hashlib
    up = Cipher(algorithms.AES(hashlib.sha256(keys[:32] + secret).digest()),
                modes.CTR(keys[32:])).encryptor()
    reverse = keys[::-1]
    down = Cipher(algorithms.AES(hashlib.sha256(reverse[:32] + secret).digest()),
                  modes.CTR(reverse[32:])).encryptor()
    encrypted = up.update(bytes(init))
    nonce = os.urandom(16)
    body = struct.pack('<I', 0xbe7e8ef1) + nonce
    message_id = int(time.time() * 2**32) & ~3
    message = struct.pack('<QQI', 0, message_id, len(body)) + body + os.urandom(4)
    start = time.monotonic()
    with socket.create_connection(('127.0.0.1', config['port']), timeout=30) as sock:
        sock.sendall(bytes(init[:56]) + encrypted[56:] +
                     up.update(struct.pack('<I', len(message)) + message))
        length = struct.unpack('<I', down.update(read_exact(sock, 4)))[0]
        if not 20 <= length <= 65536:
            raise RuntimeError('Invalid transport length ' + str(length))
        response = down.update(read_exact(sock, length))
    auth_key_id, server_message_id, body_len = struct.unpack_from('<QQI', response)
    body = response[20:20 + body_len]
    if (auth_key_id != 0 or body[:4] != struct.pack('<I', 0x05162463)
            or body[4:20] != nonce):
        raise RuntimeError('Initial MTProto response not verified')
    return dict(dc=dc, response='resPQ', nonce_verified=True,
                ms=round((time.monotonic() - start)*1000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe')
    args = parser.parse_args()
    directory = Path(__file__).parent / 'build' / ('tg-live-' + secrets.token_hex(4))
    directory.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    root = directory / 'data'
    manager = TelegramProxyManager(root / 'telegram-proxy')
    result = dict(account_used=False, data_dir=str(root), checks=[])
    try:
        if args.exe:
            # A short-lived source controller launches the real frozen worker.
            parent = '''
import json,sys
from pathlib import Path
from telegram_proxy import TelegramProxyManager
import telegram_proxy
telegram_proxy.sys.executable = sys.argv[3]
telegram_proxy.sys.frozen = True
m=TelegramProxyManager(Path(sys.argv[1])/'telegram-proxy')
print(json.dumps(m.start(port=int(sys.argv[2]),direct=True,cfproxy=True)))
'''
        else:
            parent = '''
import json,sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app_qt import App
qt=QApplication([])
with patch.object(App,'setup_tray'), patch.object(App,'_heal_autostart'):
    window=App(data_dir=Path(sys.argv[1]))
    state=window.tg_manager.start(port=int(sys.argv[2]),direct=True,cfproxy=True)
    window.apply_telegram_status(state)
    window.request_exit()
    print(json.dumps(state))
'''
        command = [sys.executable, '-c', parent, str(root), str(port)]
        if args.exe:
            command.append(str(Path(args.exe).resolve()))
        child = subprocess.run(command, cwd=Path(__file__).parent, capture_output=True,
                               text=True, timeout=45, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
        if child.returncode:
            raise RuntimeError(child.stderr[-4000:])
        previous = json.loads(child.stdout)
        status = manager.status()
        if not status['running'] or status['pid'] != previous['pid'] or not status['direct']:
            raise RuntimeError('Worker did not survive controller exit')
        result['controller_exit_survived'] = True
        result['interface_index'] = status['interface_index']
        result['frozen_worker'] = bool(args.exe)
        # Positive IDs exercise normal WS; negative IDs exercise the H2 media route.
        for dc in (2, 4, -2, -4):
            result['checks'].append(initial_exchange(manager, dc))
        result['final_status'] = manager.status()
        manager.stop()
        result['stopped'] = not manager.status()['running']
        result['ok'] = result['stopped'] and len(result['checks']) == 4
    except Exception as exc:
        result['ok'] = False
        result['error'] = str(exc)
    finally:
        try:
            manager.stop()
        except Exception as exc:
            result['cleanup_error'] = str(exc)
            result['ok'] = False
        (directory / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
