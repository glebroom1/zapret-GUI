"""Lifecycle and authenticated loopback control for an independent TG worker."""
from __future__ import annotations

import base64
import ctypes
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode

import psutil

DEFAULTS = dict(port=1443, direct=True, cfproxy=True, domain='')
WORKER_FLAG = '--telegram-worker'


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _dpapi(data, encrypt):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = api.CryptProtectData if encrypt else api.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        kernel.LocalFree(dest.data)


def protect(value):
    raw = value.encode('ascii')
    return ('dpapi:' if os.name == 'nt' else 'local:') + base64.b64encode(
        _dpapi(raw, True) if os.name == 'nt' else raw).decode('ascii')


def unprotect(value):
    prefix, encoded = value.split(':', 1)
    raw = base64.b64decode(encoded, validate=True)
    if prefix == 'dpapi' and os.name == 'nt':
        raw = _dpapi(raw, False)
    elif prefix != 'local' or os.name == 'nt':
        raise ValueError('Настройки прокси принадлежат другому пользователю Windows')
    return raw.decode('ascii')


def validate(port=1443, direct=True, cfproxy=True, domain=''):
    if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('Порт Telegram должен быть от 1 до 65535')
    domain = str(domain).strip().lower()
    if domain and (len(domain) > 253 or not re.fullmatch(
            r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain)):
        raise ValueError('CF-домен указывается без https://, пути и префикса kws')
    return dict(port=int(port), direct=bool(direct), cfproxy=bool(cfproxy), domain=domain)


class TelegramProxyManager:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.config_file = self.directory / 'config.json'
        self.state_file = self.directory / 'worker.json'

    def settings(self):
        raw = read_json(self.config_file)
        return validate(**{key: raw.get(key, value) for key, value in DEFAULTS.items()})

    def _request(self, state, method='GET', path='/status', timeout=.6):
        config = read_json(self.config_file)
        token = unprotect(config['control_key'])
        conn = http.client.HTTPConnection('127.0.0.1', int(state['control_port']), timeout=timeout)
        try:
            conn.request(method, path, body=b'', headers={
                'Authorization': 'Bearer ' + token, 'X-Worker-ID': state['instance'],
                'Connection': 'close'})
            response = conn.getresponse()
            data = json.loads(response.read(8192))
            if response.status != 200 or data.get('instance') != state['instance']:
                raise OSError('Не подтверждено управление процессом Telegram-прокси')
            return data
        finally:
            conn.close()

    def status(self):
        state = read_json(self.state_file)
        try:
            live = psutil.Process(int(state['pid']))
            if (live.create_time() != state['created'] or
                    os.path.normcase(live.exe()) != os.path.normcase(state['exe'])):
                return dict(running=False)
        except (OSError, ValueError, KeyError, TypeError, psutil.Error,
                http.client.HTTPException):
            return dict(running=False)
        if state.get('phase') == 'starting' and state.get('control_port') == 0:
            # A worker has a bounded startup period before control is available.
            if time.time() - state['created'] < 30:
                return dict(running=False, starting=True)
            return dict(running=False, unresponsive=True)
        try:
            result = self._request(state)
            result['running'] = True
            return result
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            return dict(running=False, unresponsive=True)

    def start(self, **options):
        current = self.status()
        if current['running']:
            return current
        if current.get('unresponsive') or current.get('starting'):
            raise RuntimeError('Работающий Telegram-прокси не отвечает. Второй процесс не запущен.')
        values = validate(**options)
        saved = read_json(self.config_file)
        # Preserve the Telegram link across restarts. Never put keys in argv or logs.
        for name in ('secret', 'control_key'):
            if name in saved:
                value = unprotect(saved[name])
                length = 32 if name == 'secret' else 64
                if not re.fullmatch(r'[0-9a-f]{' + str(length) + '}', value):
                    raise ValueError('Повреждены настройки Telegram-прокси')
            else:
                value = secrets.token_hex(16 if name == 'secret' else 32)
            values[name] = protect(value)
        self.directory.mkdir(parents=True, exist_ok=True)
        error_file = self.directory / 'startup-error.txt'
        error_file.write_text('', encoding='utf-8')
        atomic_json(self.config_file, values)
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command.append(str(Path(__file__).with_name('launch_qt.py')))
        command.extend([WORKER_FLAG, '--telegram-data', str(self.directory)])
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
        flags = 0
        if os.name == 'nt':
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                     | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, cwd=str(self.directory), env=env)
        try:
            child = subprocess.Popen(command, creationflags=flags, **kwargs)
        except OSError as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) != 5:
                raise
            child = subprocess.Popen(command, creationflags=flags & ~subprocess.CREATE_BREAKAWAY_FROM_JOB,
                                     **kwargs)
        # Reap the launcher without tying its lifetime to the GUI or leaking a handle.
        threading.Thread(target=child.wait, daemon=True, name='TG-launcher-wait').start()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = self.status()
            if state['running']:
                return state
            if child.poll() is not None:
                break
            time.sleep(.1)
        message = error_file.read_text(encoding='utf-8').strip()
        raise RuntimeError(message or 'Прокси не подтвердил запуск. Подробности: telegram-proxy/proxy.log')

    def stop(self, force=False):
        state = read_json(self.state_file)
        status = self.status()
        wrappers = []
        # Venv and PyInstaller bootloaders can outlive the actual worker for a
        # short time and still hold its EXE/current directory. Only wait for
        # parents whose command line names this exact worker directory.
        try:
            live = psutil.Process(int(state['pid']))
            if live.create_time() == state['created'] and os.path.normcase(live.exe()) == os.path.normcase(state['exe']):
                parent = live.parent()
                for _ in range(2):
                    if parent is None:
                        break
                    args = parent.cmdline()
                    index = args.index('--telegram-data')
                    if WORKER_FLAG not in args or Path(args[index + 1]).resolve() != self.directory:
                        break
                    wrappers.append(parent)
                    parent = parent.parent()
        except (psutil.Error, KeyError, ValueError, IndexError, OSError):
            pass

        def wait_wrappers():
            for wrapper in wrappers:
                try:
                    wrapper.wait(timeout=15)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    raise RuntimeError('Оболочка Telegram-прокси ещё завершает процесс') from None

        if status.get('unresponsive') and force:
            from process_control import terminate_worker
            terminate_worker(state, self.directory)
            if read_json(self.state_file).get('instance') == state.get('instance'):
                self.state_file.unlink(missing_ok=True)
            wait_wrappers()
            return
        if status.get('unresponsive') or status.get('starting'):
            raise RuntimeError('Процесс Telegram-прокси не отвечает; состояние не подтверждено')
        if not status['running']:
            return
        self._request(state, 'POST', '/stop', timeout=2)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self.status()
            if not status['running'] and not status.get('unresponsive'):
                try:
                    live = psutil.Process(int(state['pid']))
                    if live.create_time() == state['created']:
                        live.wait(timeout=15)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    raise RuntimeError('Прокси закрыл порт, но ещё завершает процесс') from None
                wait_wrappers()
                return
            time.sleep(.1)
        raise RuntimeError('Прокси ещё завершает соединения; повторите остановку')

    def link(self):
        status = self.status()
        if not status['running']:
            raise RuntimeError('Сначала запустите Telegram-прокси')
        secret = unprotect(read_json(self.config_file)['secret'])
        return 'tg://proxy?' + urlencode(dict(server='127.0.0.1', port=status['port'], secret='dd' + secret))
