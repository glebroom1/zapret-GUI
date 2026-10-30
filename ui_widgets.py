"""Zapret Desk · переиспользуемая UI-логика и виджеты.

Чистые функции (валидация портов/списков, решение о блокировке, конструктор
payload) можно тестировать без GUI; виджеты создаются только в App.
"""
from __future__ import annotations
import ipaddress
import re
import tkinter as tk
from tkinter import ttk

PANEL = '#252526'
BORDER = '#3c3c3c'
MUTED = '#969696'

# ─ чистая логика ─────────────────────────────────────────────────────────────

PORT_RE = re.compile(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*')
GAME_MODES = {'disabled', 'tcp', 'udp', 'all'}
IPSET_MODES = {'loaded', 'none', 'any'}
LIST_KINDS = ('general', 'exclude', 'ipset_exclude')
LIST_LABELS = {'general': 'Общий список', 'exclude': 'Исключения доменов',
               'ipset_exclude': 'Исключения IP/CIDR'}

DOMAIN_RE = re.compile(r'^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
                       r'|^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$', re.IGNORECASE)


def validate_ports(text: str):
    """(True, '') либо (False, причина). Формат как у бэкенда: 1024-65535,80."""
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


def _entry_kind(entry: str) -> str:
    """domain | ip | cidr | '' (пусто/неверно)."""
    if not entry:
        return ''
    try:
        ipaddress.IPv4Address(entry)
        return 'ip'
    except ValueError:
        pass
    try:
        ipaddress.IPv6Address(entry)
        return 'ip'
    except ValueError:
        pass
    try:
        ipaddress.ip_network(entry, strict=False)
        return 'cidr'
    except ValueError:
        pass
    return 'domain' if DOMAIN_RE.match(entry) else ''


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


# ─ виджеты ───────────────────────────────────────────────────────────────────

class Toggle(tk.Checkbutton):
    """Native checkbutton semantics with an original small switch graphic."""
    def __init__(self, master, text='', variable=None, command=None, **kw):
        self.icons = []
        for enabled in (False, True):
            picture = tk.PhotoImage(master=master, width=42, height=24)
            for y in range(24):
                for x in range(42):
                    center = min(30, max(12, x))
                    color = PANEL
                    if (x-center)**2+(y-12)**2 <= 100:
                        color = '#478bff' if enabled else '#555b65'
                    if (x-(30 if enabled else 12))**2+(y-12)**2 <= 64:
                        color = '#e4e8ee'
                    picture.put(color, (x, y))
            self.icons.append(picture)
        super().__init__(master, text=text, variable=variable, command=command,
                         image=self.icons[0], selectimage=self.icons[1], compound='left',
                         indicatoron=False, relief='flat', bd=0, highlightthickness=0,
                         bg=PANEL, fg='#d4d4d4', activebackground=PANEL,
                         activeforeground='white', selectcolor=PANEL, padx=5, pady=3, **kw)

class ScrollFrame(tk.Frame):
    """Прокручиваемый контейнер: Canvas + внутренний Frame + полоса прокрутки."""

    def __init__(self, master, width=None, bg='#1e1e1e', **kw):
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                width=width or 660)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', self._on_inner_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self._wheel_job = None
        self.winfo_toplevel().bind('<MouseWheel>', self._on_wheel, add='+')

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _bind_wheel(self):
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

    def _unbind_wheel(self):
        self.canvas.unbind_all('<MouseWheel>')

    def _on_wheel(self, event):
        if event.widget.winfo_class() in ('Text', 'TCombobox'):
            return
        # положительный delta — прокрутка вверх в Windows
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')


class Section(tk.Frame):
    """Заголовок + внутренняя панель со скруглённой рамкой (Canvas)."""

    def __init__(self, master, title, accent=BORDER, radius=16,
                 bg='#1e1e1e', panel=PANEL):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=0)
        self.canvas.pack(fill='x', pady=(0, 12))
        self.panel = tk.Frame(self.canvas, bg=panel, padx=14, pady=10)
        self.win = self.canvas.create_window((0, 0), window=self.panel, anchor='nw')
        label = tk.Label(self.canvas, text=' '+title, bg=bg, fg=MUTED,
                         font=('Segoe UI', 8, 'bold'), anchor='w')
        self._label_item = self.canvas.create_window((14, 3), window=label, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self._fit())
        self.panel.bind('<Configure>', lambda e: self._fit())
        self._radius = radius
        self._accent = accent
        self._panel = panel

    def _fit(self, label=None):
        width = max(80, self.canvas.winfo_width()-16)
        self.canvas.itemconfigure(self.win, width=width)
        for child in self.panel.winfo_children():
            if isinstance(child, tk.Label) and child.cget('wraplength'):
                child.configure(wraplength=max(80, width-32))
        height = self.panel.winfo_reqheight() + 32
        self.canvas.coords(self.win, 8, 20)
        self.canvas.coords(self._label_item, 14, 3)
        self.canvas.configure(height=height)
        self._redraw(self._accent, self._radius, self._panel)

    def _redraw(self, accent, radius, panel):
        """Скруглённая рамка из точных четвертных дуг + прямые стороны.

        Сглаженный полигон в Tk рисуется «волнами» без сглаживания — поэтому
        углы вычерчиваются настоящими дугами create_arc.
        """
        c = self.canvas
        c.delete('frame')
        box = c.bbox(self.win)
        if not box:
            return
        margin = 4
        x1 = max(margin, box[0]-margin)
        y1 = max(margin, box[1]-margin)
        x2 = box[2]+margin
        y2 = box[3]+margin
        r = max(2, min(radius, (x2-x1)//4, (y2-y1)//4))

        def arc(bbox, start):
            c.create_arc(*bbox, start=start, extent=90, style='arc',
                         outline=accent, width=1, tags='frame')

        arc((x1, y1, x1+2*r, y1+2*r), 90)        # левый верх
        arc((x2-2*r, y1, x2, y1+2*r), 0)         # правый верх
        arc((x2-2*r, y2-2*r, x2, y2), 270)       # правый низ
        arc((x1, y2-2*r, x1+2*r, y2), 180)       # левый низ
        c.create_line(x1+r, y1, x2-r, y1, fill=accent, width=1, tags='frame')
        c.create_line(x2, y1+r, x2, y2-r, fill=accent, width=1, tags='frame')
        c.create_line(x1+r, y2, x2-r, y2, fill=accent, width=1, tags='frame')
        c.create_line(x1, y1+r, x1, y2-r, fill=accent, width=1, tags='frame')


def set_widgets_state(widget, enabled: bool, readonly=None):
    """Рекурсивно включает/отключает интерактивные виджеты.

    readonly: множество ttk-виджетов, которые в рабочем состоянии 'readonly'.
    """
    if readonly is None:
        readonly = frozenset()
    for child in widget.winfo_children():
        set_widgets_state(child, enabled, readonly)
    name = widget.winfo_class()
    if name in ('TButton', 'TEntry', 'TCheckbutton'):
        widget.configure(state='disabled' if not enabled else 'normal')
    elif name == 'TCombobox':
        widget.configure(state='disabled' if not enabled else
                         ('readonly' if widget in readonly else 'normal'))
    elif isinstance(widget, tk.Checkbutton):
        widget.configure(state='disabled' if not enabled else 'normal')
    elif isinstance(widget, (tk.Entry, tk.Button)):
        widget.configure(state='disabled' if not enabled else 'normal')
    elif isinstance(widget, tk.Text):
        widget.configure(state='normal' if enabled else 'disabled')
