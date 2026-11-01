"""Zapret Desk Qt · чистая логика и тема в стиле Antigravity Unlocker.

Без tkinter и без Qt: только цвета, проверки и решение о блокировке.
Взято из ui_widgets.py (проверенные функции), палитра — из theme.rs
референса https://github.com/confeden/Antigravity (src/gui/theme.rs).
"""
from __future__ import annotations
import re

# ─ палитра референса (src/gui/theme.rs) ──────────────────────────────────────
BG = '#14161a'        # окно
CARD = '#1c1f26'      # карточка
SUNKEN = '#242831'    # поле ввода, дорожка выключателя
LINE = '#2e333d'      # тонкая линия/рамка
TEXT = '#e6e9ef'
MUTED = '#8b93a3'
ACCENT = '#4c8dff'
ACCENT_HOVER = '#6aa1ff'
OK = '#3fc17c'
WARN = '#e8b339'
BAD = '#e5635f'
RADIUS = 10
RADIUS_SMALL = 6
VERSION = '0.8'

# ─ чистая логика (совместимо с ui_widgets) ───────────────────────────────────

PORT_RE = re.compile(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*')
GAME_MODES = {'disabled', 'tcp', 'udp', 'all'}
IPSET_MODES = {'loaded', 'none', 'any'}
LIST_KINDS = ('general', 'exclude', 'ipset_exclude')
LIST_LABELS = {'general': 'Общий список', 'exclude': 'Исключения доменов',
               'ipset_exclude': 'Исключения IP/CIDR'}

DOMAIN_RE = re.compile(r'^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
                       r'|^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$', re.IGNORECASE)


def validate_ports(text: str):
    """(True, '') либо (False, причина). Формат: 1024-65535,80."""
    value = (text or '').strip()
    if not value:
        return False, 'Укажите порт или диапазон, например 1024-65535.'
    if re.search(r'\s', value):
        return False, 'Пробелы не допускаются: 1024-65535,80.'
    if not PORT_RE.fullmatch(value):
        return False, 'Разрешены цифры, «-» и «,», пример: 1024-65535,80.'
    for part in value.split(','):
        lo, _, hi = part.partition('-')
        lo, hi = int(lo), int(hi or lo)
        if not 1 <= lo <= 65535 or not 1 <= hi <= 65535:
            return False, f'Порты от 1 до 65535: {part}.'
        if lo > hi:
            return False, f'Начало диапазона больше конца: {part}.'
    return True, ''


def game_filter_payload(mode: str, tcp: str, udp: str, ipset_mode: str):
    """Проверяет значения и возвращает kwargs для write_filters. ValueError при ошибке."""
    if mode not in GAME_MODES:
        raise ValueError('Неизвестный режим Game Filter: '+str(mode))
    if ipset_mode not in IPSET_MODES:
        raise ValueError('Неизвестный режим IPSet: '+str(ipset_mode))
    ok, reason = validate_ports(tcp)
    if not ok:
        raise ValueError('TCP-порты: '+reason)
    ok, reason = validate_ports(udp)
    if not ok:
        raise ValueError('UDP-порты: '+reason)
    return dict(game_mode=mode, tcp=tcp.strip(), udp=udp.strip(), ipset_mode=ipset_mode)


def validate_list(kind: str, text: str):
    """Проверка текста пользовательского списка. (True, '') либо (False, причина)."""
    from zapret_features import validate_user_text, SettingsError
    try:
        validate_user_text(kind, text)
        return True, ''
    except SettingsError as exc:
        return False, str(exc)


def feature_lock(state: str, operation_active: bool, engine_running: bool):
    """Единое решение о блокировке настроек сборки. (заблокировано, причина)."""
    if operation_active:
        return True, 'Выполняется операция подключения; настройки сборки заблокированы.'
    if state == 'stopping':
        return True, 'Идёт остановка движка; подождите завершения.'
    if state == 'connecting':
        return True, 'Идёт подбор стратегии; настройки сборки заблокированы.'
    if engine_running:
        return True, 'Движок работает. Отключите его, чтобы менять настройки сборки.'
    return False, ''
