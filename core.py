"""Zapret Desk engine. No shell execution of strategy files."""
from __future__ import annotations
import concurrent.futures
import ctypes
from ctypes import wintypes
import http.client
import ipaddress
import json
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The engine must outlive the GUI, its console and any kill-on-close job.
INDEPENDENT_ENGINE = (getattr(subprocess, 'DETACHED_PROCESS', 0) |
                      getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) |
                      getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0))
TARGETS = {'YouTube': 'https://www.youtube.com/generate_204',
           'Discord': 'https://discord.com/api/v10/gateway'}
CONTROLS = {'Internet 1': 'https://www.microsoft.com/favicon.ico',
            'Internet 2': 'https://ya.ru/'}
FAKE_IP_NET = ipaddress.ip_network('198.18.0.0/15')
DNS_HINT = ('Тогда как браузер может открывать сайт своим DNS, кэшем или QUIC; '
            'подробности в журнале (строки «DNS»).')

class ProcessInspectionError(RuntimeError):
    """Cannot safely establish whether another engine is running."""


class ForeignEngineError(RuntimeError):
    pass

def connect_profiles(engine, profiles, targets, cancel, emit, grace=4, offline_delay=15,
                     check=None, keep_running=False):
    """One bounded sweep. Network outage pauses on the current ready profile.

    No GUI access; emit(kind, payload) and cancellation belong to the caller.
    Returns (status, profile, results). 'connected' and 'running' keep the engine.
    keep_running separates engine activation from diagnostic success for the GUI.
    """
    check = check or check_network
    last_results = {}
    current = None
    success = False
    try:
        for number, current in enumerate(profiles, 1):
            if cancel.is_set():
                return 'cancelled', current, last_results
            emit('attempt', (number, len(profiles), current.stem))
            try:
                ready = engine.start(current, engine.session_log, timeout=20, cancel=cancel.is_set)
                if ready == 'cancelled' or cancel.is_set():
                    return 'cancelled', current, last_results
                if ready != 'ready' or not engine.ready:
                    raise RuntimeError('Не получен маркер готовности WinDivert за 20 с')
                emit('ready', current.stem)
                if cancel.wait(grace):
                    return 'cancelled', current, last_results
                while not cancel.is_set():
                    if not engine.running():
                        raise RuntimeError('Движок завершился после запуска')
                    last_results = check(targets)
                    if cancel.is_set():
                        return 'cancelled', current, last_results
                    emit('probe', last_results)
                    healthy = bool(targets) and all(last_results.get(n, (False,))[0] for n in targets)
                    if healthy and engine.running() and engine.ready:
                        success = True
                        return 'connected', current, last_results
                    if keep_running and engine.running() and engine.ready and (
                            number == len(profiles) or not any(v[0] for v in last_results.values())):
                        success = True
                        return 'running', current, last_results
                    if any(v[0] for v in last_results.values()):
                        break
                    emit('offline', 'Нет ответов ни от сервисов, ни от контрольных узлов. Ожидание сети…')
                    if cancel.wait(offline_delay):
                        return 'cancelled', current, last_results
            except (PermissionError, ForeignEngineError, ProcessInspectionError):
                raise
            except Exception as exc:
                emit('profile_error', str(exc))
            engine.stop()
        return 'exhausted', current, last_results
    finally:
        if not success:
            engine.stop()

def discover(parent: Path):
    return sorted((p for p in parent.iterdir() if p.is_dir() and (p/'bin/winws.exe').is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)

def strategies(root: Path):
    def key(p):
        return (p.name != 'general.bat', [int(x) if x.isdigit() else x.lower()
                 for x in re.split(r'(\d+)', p.stem)])
    return sorted(root.glob('general*.bat'), key=key)

def game_ports(root):
    values = {'mode': 'disabled', 'tcp': '1024-65535', 'udp': '1024-65535'}
    flag = root/'utils/game_filter.enabled'
    if flag.exists():
        for line in flag.read_text(encoding='utf-8-sig').splitlines():
            k, _, v = line.partition('=')
            if k in ('tcp', 'udp') and not v:
                values['mode'] = k
            elif k in values:
                values[k] = v
            elif k == 'all':
                values['mode'] = 'all'
        if values['mode'] not in ('disabled', 'all', 'tcp', 'udp'):
            raise ValueError('Неизвестный режим игрового фильтра')
    result = {}
    for proto in ('tcp', 'udp'):
        value = values[proto]
        if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
            raise ValueError('Некорректные порты игрового фильтра')
        for segment in value.split(','):
            bounds = [int(n) for n in segment.split('-')]
            if any(n < 1 or n > 65535 for n in bounds) or bounds[0] > bounds[-1]:
                raise ValueError('Порты должны быть в диапазоне 1–65535')
        result['GameFilter'+proto.upper()] = value if values['mode'] in ('all', proto) else '12'
    return result

def parse_strategy(path: Path):
    root = path.parent.resolve()
    source = path.read_text(encoding='utf-8-sig')
    match = re.search(r'^start\s+"[^"\n]*"\s+/min\s+"%BIN%winws.exe"\s+(.+)',
                      source, re.M | re.S | re.I)
    if not match:
        raise ValueError('Формат стратегии не поддерживается: '+path.name)
    command = re.sub(r'\^\r?\n', ' ', match[1]).strip().replace('^!', '!')
    variables = {'BIN': str(root/'bin')+'\\', 'LISTS': str(root/'lists')+'\\', **game_ports(root)}
    command = re.sub(r'%([^%]+)%', lambda m: variables[m[1]], command)
    # The shipped strategies consist solely of whitespace-separated --options;
    # quoted path fragments may contain spaces. Do not invoke cmd.exe.
    args = re.findall(r'(?:[^\s"]|"[^"]*")+', command)
    args = [a.replace('"', '') for a in args]
    if not args or any(not a.startswith('--') for a in args):
        raise ValueError('В стратегии обнаружены неподдерживаемые команды')
    return args

def prepare_lists(root):
    defaults = {'ipset-exclude-user.txt': '203.0.113.113/32\n',
                'list-general-user.txt': '# Never leave this file empty\ndomain.example.abc\n',
                'list-exclude-user.txt': 'domain.example.abc\n'}
    for name, content in defaults.items():
        path = root/'lists'/name
        if not path.exists():
            path.write_text(content, encoding='utf-8')

def validate_files(root, args):
    for arg in args:
        value = arg.partition('=')[2]
        if value.startswith(str(root)) and not Path(value).is_file():
            raise FileNotFoundError(value)

def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


def _process_api():
    # Toolhelp does not depend on tasklist, WMI, PATH or localized command output.
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(api, name)
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        fn.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _inspection_error(operation):
    code = ctypes.get_last_error()
    return ProcessInspectionError(
        f'Не удалось проверить запущенные процессы zapret: {operation}; '
        f'Windows {code}: {ctypes.FormatError(code).strip()}. Подключение остановлено.')


def foreign_engine():
    api = _process_api()
    snapshot = api.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise _inspection_error('CreateToolhelp32Snapshot')
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not api.Process32FirstW(snapshot, ctypes.byref(entry)):
            if ctypes.get_last_error() == 18:  # ERROR_NO_MORE_FILES: empty snapshot
                return False
            raise _inspection_error('Process32FirstW')
        while True:
            if entry.szExeFile.casefold() == 'winws.exe':
                return True
            if not api.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() == 18:
                    return False
                raise _inspection_error('Process32NextW')
    finally:
        api.CloseHandle(snapshot)


def probe(url, timeout=7):
    started = time.monotonic()
    try:
        # Direct HTTPS, verified certificates; system proxy is intentionally bypassed.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'ZapretDesk/0.6'}),
                         timeout=timeout) as response:
            response.read(256)
            if not 200 <= response.status < 300:
                raise OSError('HTTP '+str(response.status))
        return True, round((time.monotonic()-started)*1000), ''
    except Exception as exc:
        return False, None, str(exc)


class _HTTPSByIP(http.client.HTTPSConnection):
    """HTTPS to a literal IP with SNI and certificate verification of the hostname."""

    def __init__(self, ip, host, timeout):
        super().__init__(host, timeout=timeout)
        self.target_ip = ip

    def connect(self):
        sock = socket.create_connection((self.target_ip, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def doh_resolve(host, timeout=5):
    """A-answers via DoH (1.1.1.1, then 8.8.8.8) without the system DNS.
    Returns (ips, ms, server); RuntimeError when every resolver failed."""
    last = ''
    for server, template in (('1.1.1.1', 'https://1.1.1.1/dns-query?name={}&type=A'),
                             ('8.8.8.8', 'https://8.8.8.8/resolve?name={}&type=A')):
        started = time.monotonic()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                template.format(host),
                headers={'accept': 'application/dns-json', 'User-Agent': 'ZapretDesk/0.6'})
            with opener.open(request, timeout=timeout) as response:
                if response.status != 200:
                    last = 'HTTPS '+str(response.status)
                    continue
                data = json.loads(response.read(8192))
            answers = sorted({a['data'] for a in data.get('Answer', []) if a.get('type') == 1})
            if answers:
                return answers, round((time.monotonic()-started)*1000), server
            last = 'нет A-записей'
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(last or 'резолверы недоступны')


def probe_via_doh(host, url, timeout=7):
    """HTTPS over a DoH-resolved IP with SNI and certificate check: the channel,
    not DNS. Never raises; returns a (ok, ms, сноска) tuple like probe()."""
    started = time.monotonic()
    try:
        ips, _ms, server = doh_resolve(host, timeout=max(2, min(timeout, 5)))
        parts = urllib.parse.urlparse(url)
        path = parts.path + ('?'+parts.query if parts.query else '') or '/'
        conn = _HTTPSByIP(ips[0], host, timeout)
        try:
            conn.request('GET', path, headers={'User-Agent': 'ZapretDesk/0.6'})
            response = conn.getresponse()
            response.read(256)
            status = response.status
        finally:
            conn.close()
        ok = 200 <= status < 300
        note = ('HTTPS '+str(status)+' по IP DoH-'+server+' '+ips[0]) if not ok else ''
        return ok, round((time.monotonic()-started)*1000), note
    except Exception as exc:
        return False, None, 'DoH: '+str(exc)


def _dns_recovery(urls, results):
    """Диагностика расхождения «проверка ≠ браузер»: цель падает на getaddrinfo,
    контрольные живы → измеряем канал через DoH-IP и честно объясняем слои."""
    controls_ok = any(results[name][0] for name in CONTROLS)
    notes = {}
    if not controls_ok:
        return notes
    hosts = {name: urllib.parse.urlparse(url).netloc for name, url in urls.items()}
    doomed = [name for name in TARGETS if name in urls and
              not results[name][0] and 'getaddrinfo' in results[name][2]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(doomed), 1)) as pool:
        jobs = {name: pool.submit(probe_via_doh, hosts[name], urls[name]) for name in doomed}
        for name, job in jobs.items():
            ok, ms, note = job.result()
            if ok:
                results[name] = (True, ms, '')
            notes[name] = ('Системный DNS не ответил (getaddrinfo) при живых контрольных. '
                           + ('Канал сервиса подтверждён по IP через DoH, проверка засчитана. '
                              if ok else 'Резолв через DoH не удался: '+note+' ')
                           + DNS_HINT)
    # Fake-IP у успешных целей: маршрут определяет роутер, а не zapret.
    for name in TARGETS:
        if name not in urls or name in doomed or not results[name][0]:
            continue
        try:
            infos = socket.getaddrinfo(hosts[name], 443, type=socket.SOCK_STREAM)
            ip = infos[0][4][0]
        except OSError:
            continue
        if ipaddress.ip_address(ip) in FAKE_IP_NET:
            notes[name] = ('Системный DNS отдаёт fake-IP '+ip+' (режим типа Podkop на роутере): '
                           'данные к сервису идут маршрутом роутера; проверка не отделяет '
                           'zapret от маршрута роутера.')
    return notes


def check_network(targets=None):
    urls = {**(targets or TARGETS), **CONTROLS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {name: pool.submit(probe, url) for name, url in urls.items()}
        results = {name: f.result() for name, f in futures.items()}
        if not any(value[0] for value in results.values()):
            # TLS failures at controls must not be mistaken for a missing route.
            checks = [pool.submit(probe_tcp, host) for host in ('1.1.1.1', '8.8.8.8')]
            tcp = [f.result() for f in checks]
            results['Internet TCP'] = next((v for v in tcp if v[0]), tcp[0])
        for name, note in _dns_recovery(urls, results).items():
            results[name+' DNS'] = (results[name][0], None, note)
        return results

def probe_tcp(host):
    started = time.monotonic()
    try:
        with socket.create_connection((host, 443), timeout=3):
            return True, round((time.monotonic()-started)*1000), ''
    except OSError as exc:
        return False, None, str(exc)

class Rotation:
    """Bounded sweep with consecutive failures, grace time, and no offline rotation."""
    def __init__(self, names, threshold=3, cooldown=45):
        self.names = list(names)
        self.threshold = threshold
        self.cooldown = cooldown
        self.reset(self.names[0] if self.names else '')

    def reset(self, current):
        self.current = current
        self.tried = {current}
        self.failures = 0
        self.last_switch = time.monotonic()
        self.exhausted = False

    def observe(self, online, healthy, now=None):
        now = time.monotonic() if now is None else now
        if not online:
            self.failures = 0
            return 'offline'
        if healthy:
            self.failures = 0
            self.tried = {self.current}
            self.exhausted = False
            return 'healthy'
        if self.exhausted:
            return 'exhausted'
        self.failures += 1
        if self.failures < self.threshold or now-self.last_switch < self.cooldown:
            return 'retry'
        start = self.names.index(self.current) if self.current in self.names else -1
        for offset in range(1, len(self.names)+1):
            name = self.names[(start+offset) % len(self.names)]
            if name not in self.tried:
                self.current = name
                self.tried.add(name)
                self.failures = 0
                self.last_switch = now
                return name
        self.exhausted = True
        return 'exhausted'

class Engine:
    """Lifecycle of a single winws.exe process: spawn, bounded readiness wait, cancel, diagnostics."""

    def __init__(self, log):
        self.process = None
        self.output = None
        self.log = log
        self.logfile = None
        self.status = 'idle'
        self.ready = False
        self.session_file = None

    def detach(self):
        self._close()

    def recover(self):
        from engine_session import recover
        result = recover(self.session_file) if self.session_file else None
        if result:
            self.process, strategy = result
            self.ready = True
            self.status = 'ready'
            return strategy
        return None

    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, strategy, logfile, timeout=20.0, cancel=None):
        """Start winws.exe for the strategy and wait for readiness with a bounded wait.
        Returns 'ready', 'timeout' or 'cancelled'. Raises RuntimeError when the process
        exits before readiness, PermissionError without rights, RuntimeError for a foreign engine.
        cancel: callable() -> bool to request cancellation of the wait."""
        self.stop()
        if not is_admin():
            raise PermissionError('Для WinDivert нужны права администратора. Перезапустите GUI с повышением прав.')
        if foreign_engine():
            raise ForeignEngineError('Другой winws.exe уже запущен. Остановите его через прежний способ запуска.')
        args = parse_strategy(strategy)
        prepare_lists(strategy.parent)
        validate_files(strategy.parent, args)
        self.logfile = Path(logfile)
        self.output = open(logfile, 'ab', buffering=0)
        # Record the boundary BEFORE Popen: winws may print readiness immediately.
        offset = self.logfile.stat().st_size
        try:
            self.process = subprocess.Popen([str(strategy.parent/'bin/winws.exe'), *args],
                cwd=str(strategy.parent/'bin'), stdin=subprocess.DEVNULL,
                stdout=self.output, stderr=subprocess.STDOUT, creationflags=INDEPENDENT_ENGINE)
        except Exception:
            self.output.close()
            self.output = None
            raise
        self.status = 'starting'
        self.ready = False
        self.log('Запущена стратегия '+strategy.stem+' · PID '+str(self.process.pid))
        should_cancel = cancel if cancel is not None else (lambda: self.process is None)
        deadline = time.monotonic() + timeout
        captured = b''
        try:
            while True:
                if should_cancel():
                    self.stop()
                    self.status = 'cancelled'
                    self.log('Запуск стратегии отменён')
                    return 'cancelled'
                if not self.running():
                    code = self.process.poll() if self.process else None
                    self.status = 'exited'
                    self._close()
                    raise RuntimeError('winws.exe завершился с кодом '+(str(code) if code is not None else '?')+
                                       ' до готовности. Подробности в журнале: '+str(self.logfile))
                fresh = self._read_new(offset)
                offset += len(fresh)
                captured = (captured + fresh)[-65536:]
                if b'windivert initialized' in captured.lower() and b'capture is started' in captured.lower():
                    self.status = 'ready'
                    self.ready = True
                    if self.session_file:
                        from engine_session import record
                        record(self.session_file, self.process, strategy)
                    self.log('WinDivert готов: инициализация завершена, перехват запущен')
                    return 'ready'
                if time.monotonic() >= deadline:
                    self.stop()
                    self.status = 'timeout'
                    return 'timeout'
                time.sleep(0.05)
        except BaseException:
            self.stop()
            raise

    def _read_new(self, offset, limit=65536):
        try:
            with open(self.logfile, 'rb') as handle:
                handle.seek(offset)
                return handle.read(limit)
        except OSError:
            return b''

    def _close(self):
        if self.output:
            try:
                self.output.close()
            except OSError:
                pass
            self.output = None
        self.process = None

    def stop(self):
        if self.running():
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.log('Процесс GUI остановлен')
        self.process = None
        if self.output:
            self.output.close()
            self.output = None
        self.ready = False
        self.status = 'idle'
