"""Explicit local settings operations; no services, hosts or process changes."""
from __future__ import annotations
import ipaddress
import os
import re
import urllib.request
import uuid
from pathlib import Path

GAME_MODES = ('disabled', 'tcp', 'udp', 'all')
IPSET_MODES = ('loaded', 'none', 'any')
USER_LIST_KINDS = ('general', 'exclude', 'ipset_exclude')
IPSET_DEFAULT_LINE = '203.0.113.113/32'
IPSET_UPDATE_URL = 'https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/refs/heads/main/.service/ipset-service.txt'
IPSET_UPDATE_TIMEOUT = 10
IPSET_MAX_BYTES = 5 * 1024 * 1024
USER_LIST_FILES = {'general': 'list-general-user.txt', 'exclude': 'list-exclude-user.txt', 'ipset_exclude': 'ipset-exclude-user.txt'}
USER_LIST_DEFAULTS = {'general': '# Never leave this file empty\ndomain.example.abc\n', 'exclude': 'domain.example.abc\n', 'ipset_exclude': IPSET_DEFAULT_LINE+'\n'}

class SettingsError(ValueError):
    pass

def game_filter_path(root): return Path(root)/'utils/game_filter.enabled'
def ipset_path(root): return Path(root)/'lists/ipset-all.txt'
def user_list_path(root, kind):
    if kind not in USER_LIST_FILES: raise SettingsError('Неизвестный список: '+str(kind))
    return Path(root)/'lists'/USER_LIST_FILES[kind]

def _read_text(path, default=''):
    try: return Path(path).read_text(encoding='utf-8-sig')
    except FileNotFoundError: return default

def _validate_range(value, field='порты'):
    value = str(value).strip()
    if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
        raise SettingsError('Некорректные порты '+field+': например 1024-65535,80')
    for segment in value.split(','):
        numbers = [int(v) for v in segment.split('-')]
        if any(n < 1 or n > 65535 for n in numbers) or numbers[0] > numbers[-1]:
            raise SettingsError('Порты '+field+': допустим диапазон 1–65535, начало не больше конца')
    return value

def validate_user_text(kind, text):
    user_list_path(Path('.'), kind)
    if len(text.encode('utf-8')) > IPSET_MAX_BYTES: raise SettingsError('Список слишком большой')
    for number, line in enumerate(text.splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith('#'): continue
        if kind == 'ipset_exclude':
            try: ipaddress.ip_network(entry, strict=False)
            except ValueError: raise SettingsError(f'Строка {number}: нужен IP или CIDR') from None
        else:
            if len(entry) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', entry):
                raise SettingsError(f'Строка {number}: нужен домен без схемы, пути и пробелов')
            if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in entry.split('.')):
                raise SettingsError(f'Строка {number}: некорректное доменное имя')
            try: ipaddress.ip_address(entry)
            except ValueError: pass
            else: raise SettingsError(f'Строка {number}: IP следует добавлять в IPSet, здесь нужен домен')
    return text

def _ipset_mode(text):
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if not lines: return 'any'
    return 'none' if IPSET_DEFAULT_LINE in lines else 'loaded'

def _valid_loaded(text):
    if _ipset_mode(text) != 'loaded': return False
    entries = [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
    if not entries: return False
    try:
        for entry in entries: ipaddress.ip_network(entry, strict=False)
    except ValueError: return False
    return True

def _commit(changes):
    """Stage/validate first; preserve unique backups and roll back partial commits."""
    changes = {Path(p): text.encode('utf-8') for p, text in changes.items()}
    before = {p: p.read_bytes() if p.exists() else None for p in changes}
    changes = {p: data for p, data in changes.items() if data != before[p]}
    staged, committed = {}, []
    try:
        for p, data in changes.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            temp = p.with_name(p.name+'.tmp-'+uuid.uuid4().hex)
            staged[p] = temp
            with temp.open('xb') as f: f.write(data)
            if before[p] is not None:
                backup = p.with_name(p.name+'.bak-'+uuid.uuid4().hex)
                with backup.open('xb') as f: f.write(before[p])
        for p, temp in staged.items():
            os.replace(temp, p)
            committed.append(p)
    except Exception:
        for p in reversed(committed):
            if before[p] is None: p.unlink(missing_ok=True)
            else:
                rollback = p.with_name(p.name+'.rollback-'+uuid.uuid4().hex)
                rollback.write_bytes(before[p])
                os.replace(rollback, p)
        raise
    finally:
        for temp in staged.values(): temp.unlink(missing_ok=True)

def read_filters(root):
    mode, tcp, udp = 'disabled', '1024-65535', '1024-65535'
    for line in _read_text(game_filter_path(root)).splitlines():
        key, _, value = line.partition('='); key = key.strip().lower(); value = value.strip()
        if key == 'mode': mode = value.lower()
        elif key in ('all', 'tcp', 'udp') and not value: mode = key
        elif key == 'tcp': tcp = value
        elif key == 'udp': udp = value
    if mode not in GAME_MODES: raise SettingsError('Неизвестный режим Game Filter: '+mode)
    return dict(game_mode=mode, tcp=_validate_range(tcp, 'TCP'), udp=_validate_range(udp, 'UDP'),
                ipset_mode=_ipset_mode(_read_text(ipset_path(root))))

def write_filters(root, game_mode, tcp, udp, ipset_mode):
    if game_mode not in GAME_MODES or ipset_mode not in IPSET_MODES:
        raise SettingsError('Неизвестный режим фильтра')
    tcp, udp = _validate_range(tcp, 'TCP'), _validate_range(udp, 'UDP')
    ipfile = ipset_path(root); current = _read_text(ipfile)
    if ipset_mode == 'none': desired = IPSET_DEFAULT_LINE+'\n'
    elif ipset_mode == 'any': desired = ''
    elif _valid_loaded(current): desired = current
    else:
        backups = list(ipfile.parent.glob(ipfile.name+'.bak-*'))
        legacy = ipfile.with_name(ipfile.name+'.backup')
        if legacy.exists(): backups.append(legacy)
        backups.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        desired = next((_read_text(p) for p in backups if _valid_loaded(_read_text(p))), None)
        if desired is None: raise SettingsError('Нет рабочего IPSet для восстановления. Сначала обновите IPSet.')
    _commit({game_filter_path(root): f'mode={game_mode}\ntcp={tcp}\nudp={udp}\n', ipfile: desired})

def read_user_list(root, kind):
    return _read_text(user_list_path(root, kind), USER_LIST_DEFAULTS[kind])

def write_user_list(root, kind, text):
    validate_user_text(kind, text)
    # An empty hostlist must not accidentally mean all hosts to the engine.
    if not any(s.strip() and not s.lstrip().startswith('#') for s in text.splitlines()):
        text = text.rstrip('\n')+'\n'+USER_LIST_DEFAULTS[kind]
    if not text.endswith('\n'): text += '\n'
    _commit({user_list_path(root, kind): text})

def update_ipset(root):
    try:
        request = urllib.request.Request(IPSET_UPDATE_URL, headers={'User-Agent': 'ZapretDesk/0.4'})
        response = urllib.request.urlopen(request, timeout=IPSET_UPDATE_TIMEOUT)
        try:
            if response.status != 200: raise SettingsError('Сервер вернул HTTP '+str(response.status))
            data = response.read(IPSET_MAX_BYTES+1)
        finally: response.close()
        if len(data) > IPSET_MAX_BYTES: raise SettingsError('IPSet превышает лимит 5 МБ')
        text = data.decode('utf-8-sig')
        entries = [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
        if not entries: raise SettingsError('Сервер вернул пустой IPSet; текущий список сохранён')
        for entry in entries: ipaddress.ip_network(entry, strict=False)
        _commit({ipset_path(root): text.rstrip('\n')+'\n'})
        return len(entries)
    except Exception as exc:
        raise SettingsError('Не удалось обновить IPSet: '+str(exc)) from exc

def diagnostics(root):
    result = {'game_filter': {}, 'ipset': {}, 'user_lists': {}}
    try: result['game_filter'] = read_filters(root)
    except (ValueError, OSError) as exc: result['game_filter'] = {'error': str(exc)}
    try: result['ipset'] = {'mode': _ipset_mode(_read_text(ipset_path(root)))}
    except (ValueError, OSError) as exc: result['ipset'] = {'error': str(exc)}
    for kind in USER_LIST_KINDS:
        try: result['user_lists'][kind] = {'lines': len(read_user_list(root, kind).splitlines())}
        except (ValueError, OSError) as exc: result['user_lists'][kind] = {'error': str(exc)}
    return result
