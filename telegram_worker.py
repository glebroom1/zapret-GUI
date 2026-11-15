"""Headless TG worker: loopback only, independent of Qt and WinDivert."""
from __future__ import annotations
import asyncio
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import psutil
from telegram_proxy import atomic_json, read_json, unprotect, validate

VENDOR = Path(__file__).resolve().parent / 'vendor' / 'tg_ws_proxy'


class BackendRecovery:
    """Count backend exits, resetting after 60s of observed stable listening.

    A sleep/monitor gap is not evidence of continuous healthy operation.
    Ordinary upstream retries do not call exited().
    """
    def __init__(self):
        self.failures = 0
        self.since = None
        self.previous = None

    def observe(self, now, listening):
        if not listening:
            self.since = self.previous = None
            return
        if self.previous is None or not 0 <= now - self.previous <= 20:
            self.since = now
        self.previous = now
        if now - self.since >= 60:
            self.failures = 0

    def exited(self):
        self.observe(0, False)
        self.failures += 1
        if self.failures > 3:
            raise RuntimeError('Telegram backend repeatedly stopped')
        return min(2 ** self.failures, 30)


def import_backend():
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from proxy import tg_ws_proxy
    return tg_ws_proxy


class DirectoryLock:
    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            self.handle.close()


def redact(message, *values):
    for value in values:
        if value:
            message = message.replace(value, '[hidden]')
    return message


def setup_logging(directory, *values):
    class SecretFormatter(logging.Formatter):
        def format(self, record):
            return redact(super().format(record), *values)

    handler = RotatingFileHandler(directory / 'proxy.log', maxBytes=1024*1024,
                                  backupCount=1, encoding='utf-8')
    handler.setFormatter(SecretFormatter('%(asctime)s %(levelname)s %(message)s'))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


async def serve(directory, saved):
    config = validate(**{key: saved[key] for key in ('port', 'direct', 'cfproxy', 'domain')})
    secret, token = unprotect(saved['secret']), unprotect(saved['control_key'])
    setup_logging(directory, secret, token)
    interface = None
    route = None
    original_open = asyncio.open_connection
    if config['direct']:
        from telegram_network import PhysicalRoute, physical_interface
        interface = await asyncio.to_thread(physical_interface)
        route = PhysicalRoute(interface)
        asyncio.open_connection = route.open_connection
    backend = import_backend()
    from proxy.config import proxy_config, CFPROXY_DEFAULT_DOMAINS
    from proxy import raw_websocket
    raw_websocket._ssl_ctx_fronting.check_hostname = True
    proxy_config.host = '127.0.0.1'
    proxy_config.port = config['port']
    proxy_config.secret = secret
    proxy_config.fallback_cfproxy = config['cfproxy']
    proxy_config.disable_secure = False
    proxy_config.cfproxy_user_domains = ([config['domain']] if config['domain']
                                          else list(CFPROXY_DEFAULT_DOMAINS))
    # Shipped pool is pinned; no automatic GitHub refresh thread.
    proxy_config.cfproxy_worker_domains = []
    proxy_config.fake_tls_domain = ''
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    instance = secrets.token_hex(16)
    task = asyncio.create_task(backend._run(stop))
    server = None
    heartbeat = time.monotonic()
    phase = 'starting'
    restarts = 0
    try:
        deadline = time.monotonic() + 12
        while backend._server_instance is None:
            if task.done():
                await task
                raise RuntimeError('Слушатель Telegram-прокси не запустился')
            if time.monotonic() > deadline:
                raise RuntimeError('Превышено время запуска Telegram-прокси')
            await asyncio.sleep(.03)
        from proxy.stats import stats

        def status():
            return dict(instance=instance, pid=os.getpid(), port=config['port'],
                        direct=config['direct'], cfproxy=config['cfproxy'],
                        interface_index=interface['index'] if interface else None,
                        listener_ready=backend._server_instance is not None,
                        loop_age=round(time.monotonic()-heartbeat, 2),
                        phase=phase, upstream='unverified', restarts=restarts,
                        connections=stats.connections_active,
                        ws=stats.connections_ws, cf=stats.connections_cfproxy,
                        tcp=stats.connections_tcp_fallback)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply(False)

            def do_POST(self):
                self.reply(True)

            def reply(self, stopping):
                import json
                if (not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)
                        or not hmac.compare_digest(self.headers.get('X-Worker-ID', ''), instance)):
                    self.send_error(403)
                    return
                if (self.path != ('/stop' if stopping else '/status')
                        or self.headers.get('Content-Length', '0') != '0'
                        or self.headers.get('Transfer-Encoding')):
                    self.send_error(400)
                    return
                body = json.dumps(status()).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                if stopping:
                    loop.call_soon_threadsafe(stop.set)

        class ControlServer(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                sock, address = super().get_request()
                sock.settimeout(2)
                return sock, address

        server = ControlServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True, name='TG-control').start()
        process = psutil.Process()
        atomic_json(directory / 'worker.json', dict(
            pid=process.pid, created=process.create_time(), exe=process.exe(),
            instance=instance, control_port=server.server_port))
        phase = 'listening'
        previous = time.time()
        next_route = 0
        recovery = BackendRecovery()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
                break
            except TimeoutError:
                pass
            heartbeat = time.monotonic()
            now = time.time()
            restart = now - previous > 20 or now < previous
            previous = now
            if route is not None and (restart or now >= next_route):
                next_route = now + 30
                try:
                    updated = await asyncio.to_thread(physical_interface)
                except Exception:
                    updated = None
                if updated != interface:
                    interface = updated
                    route.interface = updated
                    route.cache.clear()
                    restart = True
            if stop.is_set():
                break
            recovery.observe(heartbeat, not restart and not task.done()
                             and backend._server_instance is not None)
            if task.done():
                # Retrieve the exception, but never write credentials from it.
                await asyncio.gather(task, return_exceptions=True)
                if stop.is_set():
                    break
                delay = recovery.exited()
                phase = 'reconnecting'
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    break
                except TimeoutError:
                    restart = True
            if restart and not stop.is_set():
                recovery.observe(heartbeat, False)
                phase = 'reconnecting'
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if stop.is_set():
                    break
                task = asyncio.create_task(backend._run(stop))
                restarts += 1
                logging.info('Backend reconnect: network/resume/exit; attempt %d', restarts)
            phase = 'listening' if backend._server_instance is not None else 'reconnecting'
    finally:
        asyncio.open_connection = original_open
        stop.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        state = read_json(directory / 'worker.json')
        if state.get('instance') == instance:
            (directory / 'worker.json').unlink(missing_ok=True)


def main(directory):
    directory = Path(directory).resolve()
    try:
        lock = DirectoryLock(directory / 'worker.lock')
        lock.__enter__()
    except OSError:
        return 2
    try:
        process = psutil.Process()
        atomic_json(directory / 'worker.json', dict(
            pid=process.pid, created=process.create_time(), exe=process.exe(),
            instance=secrets.token_hex(16), control_port=0, phase='starting'))
        asyncio.run(serve(directory, read_json(directory / 'config.json')))
        return 0
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, OSError) and (getattr(exc, 'winerror', None) == 10048
                                         or exc.errno in (10048, 98)):
            message = 'Локальный порт занят другой программой. Выберите другой порт Telegram.'
        saved = read_json(directory / 'config.json')
        sensitive = []
        for name in ('secret', 'control_key'):
            try:
                sensitive.append(unprotect(saved[name]))
            except (KeyError, ValueError, OSError):
                pass
        message = redact(message, *sensitive)
        (directory / 'startup-error.txt').write_text(message[:2000], encoding='utf-8')
        logging.exception('Telegram worker stopped: %s', message)
        return 1
    finally:
        state = read_json(directory / 'worker.json')
        if state.get('pid') == os.getpid():
            (directory / 'worker.json').unlink(missing_ok=True)
        lock.__exit__()
