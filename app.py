"""Zapret Desk · одностраничный тёмный GUI без вкладок.

Кнопка: Подключить → Отмена (идёт запуск и проверка) → Отключить.
Статус «Подключено» показывается только после того, как движок запущен,
продержался заданное время и сетевая проверка выбранных сервисов прошла.
Долгие операции выполняются вне GUI-потока; результаты устаревших запусков
отбрасываются по номеру поколения (generation).
"""
from __future__ import annotations
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from core import (discover, strategies, Rotation, Engine, check_network,
                  is_admin, foreign_engine, ForeignEngineError, connect_profiles, TARGETS)
import runtime_paths as _rp
import ui_widgets as uw

BASE = _rp.exe_dir()
DATA = _rp.data_dir()
BG, PANEL, BORDER, FG, MUTED = '#1e1e1e', '#252526', '#3c3c3c', '#d4d4d4', '#969696'
BLUE, GREEN, AMBER, RED, INPUT = '#007acc', '#89d185', '#e5b567', '#f48771', '#333337'
LIST_LABELS = uw.LIST_LABELS

ENGINE_GRACE = 4  # секунд паузы между стартом движка и первой проверкой


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Zapret Desk')
        self.geometry('680x860')
        self.minsize(640, 620)
        self.configure(bg=BG)
        self.after(150, self.dark_titlebar)
        self.option_add('*Font', ('Segoe UI', 10))
        self.events = queue.Queue()
        self.engine = Engine(lambda msg: self.events.put(('log', msg)))
        self.busy = False            # выполняется сетевая проверка
        self.operation_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.operation_active = False
        self.feature_busy = False
        self.closing = False
        self.state = 'idle'          # idle | connecting | connected
        self.generation = 0          # защита от устаревших результатов
        self.next_check = 0
        self.stage = ''
        self.log_open = False
        self.attempt = 0             # номер текущей попытки автоперебора
        self.settings = self.load()
        bundled = _rp.bootstrap_bundled()
        found = []
        for root in _rp.source_roots():
            found.extend(discover(root))
        self.root_path = Path(self.settings.get('root', str(bundled or (found[0] if found else ''))))
        if _rp.is_frozen() and (not _rp.is_valid_build(self.root_path) or self.root_path not in found):
            self.root_path = bundled or (found[0] if found else Path(''))
        self.paths = strategies(self.root_path) if self.root_path.is_dir() else []
        self.selected = tk.StringVar(value=self.settings.get('strategy', 'general'))
        if self.selected.get() not in [p.stem for p in self.paths]:
            self.selected.set(self.paths[0].stem if self.paths else '')
        self.auto = tk.BooleanVar(value=self.settings.get('auto', True))
        self.youtube = tk.BooleanVar(value=self.settings.get('youtube', True))
        self.discord = tk.BooleanVar(value=self.settings.get('discord', True))
        self.interval = tk.StringVar(value=str(self.settings.get('interval', 30)))
        self.rotation = Rotation([p.stem for p in self.paths])
        self.last_check = 'ещё не выполнялась'
        self.health = {}
        self.active_targets = {}
        # функции сборки (Game Filter / IPSet / списки): только чтение при запуске
        self.gf_mode = tk.StringVar(value=self.settings.get('game_mode', 'disabled'))
        self.gf_tcp = tk.StringVar(value=self.settings.get('gf_tcp', '1024-65535'))
        self.gf_udp = tk.StringVar(value=self.settings.get('gf_udp', '1024-65535'))
        self.ipset_mode = tk.StringVar(value=self.settings.get('ipset_mode', 'loaded'))
        self.ulist_kind = 'general'
        self.feature_notes = {'gf': '', 'ipset': '', 'list': '', 'diag': ''}
        self.last_diag = None
        self._features_module = 'init'   # 'init' — ещё не пробовали импортировать
        self.style()
        self.build()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(150, self.tick)
        self.after(450, self.load_filters)      # только чтение; запись — по кнопке
        if self.paths:
            self.log('Найдено стратегий: '+str(len(self.paths))+' · '+self.root_path.name)
        else:
            self.log('Сборка не найдена — укажите папку кнопкой «Обзор…»')
        if '--connect' in sys.argv:
            self.after(300, self.on_main_button)

    # ─ служебное ─────────────────────────────────────────────────────────────

    def dark_titlebar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)
            color = ctypes.c_int(0x00252525)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)
        except OSError:
            pass

    def load(self):
        try:
            return json.loads((DATA/'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save(self):
        interval = int(self.interval.get())
        if not 15 <= interval <= 300:
            raise ValueError('Интервал должен быть от 15 до 300 секунд')
        settings = dict(root=str(self.root_path), strategy=self.selected.get(), auto=self.auto.get(),
                        youtube=self.youtube.get(), discord=self.discord.get(), interval=interval,
                        game_mode=self.gf_mode.get(), gf_tcp=self.gf_tcp.get().strip(),
                        gf_udp=self.gf_udp.get().strip(), ipset_mode=self.ipset_mode.get())
        temp = DATA/'settings.tmp'
        temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(DATA/'settings.json')

    def settings_changed(self):
        try:
            self.save()
            self.refresh()
        except Exception as exc:
            self.log('Настройки: '+str(exc))

    def style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('.', background=BG, foreground=FG, bordercolor=BORDER)
        s.configure('TFrame', background=BG)
        s.configure('TLabel', background=BG, foreground=FG)
        s.configure('TButton', background=INPUT, foreground=FG, padding=(13, 6), borderwidth=1)
        s.map('TButton', background=[('active', '#45454a')], disabledforeground=[('background', FG)])
        s.configure('Main.TButton', font=('Segoe UI', 12, 'bold'), padding=(18, 11))
        s.configure('Accent.TButton', background=BLUE, foreground='white')
        s.map('Accent.TButton', background=[('active', '#168bd2')])
        s.configure('Cancel.TButton', background='#5a2323', foreground='#f2b8b8')
        s.map('Cancel.TButton', background=[('active', '#7a2a2a')])
        s.configure('TCheckbutton', background=PANEL, foreground=FG, padding=4)
        s.map('TCheckbutton', background=[('active', PANEL)])
        s.configure('TCombobox', fieldbackground=INPUT, background=INPUT, foreground=FG, padding=6)
        s.map('TCombobox', fieldbackground=[('readonly', INPUT)], foreground=[('readonly', FG)])
        self.option_add('*TCombobox*Listbox.background', PANEL)
        self.option_add('*TCombobox*Listbox.foreground', FG)
        s.configure('Thin.TProgressbar', background=BLUE, troughcolor=INPUT, thickness=6)
        try:  # Tk 9: производный стиль требует явного layout
            s.layout('Thin.TProgressbar', s.layout('Horizontal.TProgressbar'))
        except Exception:
            pass

    # ─ один прокручиваемый экран ─────────────────────────────────────────────

    def build(self):
        top = tk.Frame(self, bg='#2b2b2c', height=34)
        top.pack(fill='x')
        top.pack_propagate(False)
        tk.Label(top, text='  ZAPRET DESK', bg='#2b2b2c', fg=FG, font=('Segoe UI', 9, 'bold')).pack(side='left')
        tk.Label(top, text=' zapret-discord-youtube · одно окно', bg='#2b2b2c', fg=MUTED,
                 font=('Segoe UI', 8)).pack(side='left')
        tk.Label(top, text='0.4', bg='#2b2b2c', fg=MUTED).pack(side='right', padx=14)
        scroll = uw.ScrollFrame(self, bg=BG)
        scroll.pack(fill='both', expand=True)
        body = scroll.inner
        body.configure(padx=16, pady=12)

        # блок 1: статус подключения (синяя рамка) + кнопка
        sec = uw.Section(body, 'ПОДКЛЮЧЕНИЕ', accent=BLUE, bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.status = tk.Label(f, text='●  Остановлено', bg=PANEL, fg=FG,
                               font=('Segoe UI', 14, 'bold'), anchor='w')
        self.status.pack(fill='x')
        self.progress = ttk.Progressbar(f, style='Thin.TProgressbar', mode='indeterminate')
        self.details = tk.Label(f, text='', bg=PANEL, fg=MUTED, anchor='w', justify='left', wraplength=590)
        self.details.pack(fill='x', pady=(8, 10))
        self.main_button = ttk.Button(f, text='Подключить', style='Accent.TButton',
                                      width=17, command=self.on_main_button)
        self.main_button.pack(anchor='w')
        rows = tk.Frame(f, bg=PANEL)
        rows.pack(fill='x', pady=(10, 0))
        for name in ('YouTube', 'Discord', 'Интернет'):
            line = tk.Frame(rows, bg=PANEL)
            line.pack(fill='x', pady=2)
            tk.Label(line, text=name, bg=PANEL, fg=MUTED, width=10, anchor='w').pack(side='left')
            self.health[name] = tk.Label(line, text='—', bg=PANEL, fg=MUTED, anchor='w')
            self.health[name].pack(side='left')

        # блок 2: стратегия и автопереключение
        sec = uw.Section(body, 'СТРАТЕГИЯ И АВТОПЕРЕКЛЮЧЕНИЕ', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.strategy_combo = ttk.Combobox(f, textvariable=self.selected, state='readonly',
                                           values=[p.stem for p in self.paths], width=42)
        self.strategy_combo.pack(anchor='w', pady=7)
        self.strategy_combo.bind('<<ComboboxSelected>>', lambda e: self.strategy_changed())
        uw.Toggle(f, text='Автоматически переключаться при сбоях',
                        variable=self.auto, command=self.settings_changed).pack(anchor='w')
        tk.Label(f, text='При подключении — подбор до успеха · после подключения — смена после 3 сбоев · при потере сети пауза',
                 bg=PANEL, fg=MUTED, anchor='w', wraplength=590, justify='left').pack(fill='x')

        # блок 3: настройки проверок
        sec = uw.Section(body, 'НАСТРОЙКИ ПРОВЕРОК', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill='x', pady=7)
        uw.Toggle(row, text='Проверять YouTube', variable=self.youtube,
                        command=self.settings_changed).pack(side='left')
        uw.Toggle(row, text='Проверять Discord', variable=self.discord,
                        command=self.settings_changed).pack(side='left', padx=18)
        interval_row = tk.Frame(f, bg=PANEL)
        interval_row.pack(fill='x', pady=(0, 5))
        tk.Label(interval_row, text='Интервал, с (15–300)', bg=PANEL, fg=MUTED).pack(side='left', padx=(0, 6))
        entry = ttk.Entry(interval_row, textvariable=self.interval, width=6)
        entry.pack(side='left')
        entry.bind('<FocusOut>', lambda e: self.settings_changed())
        self.folder_view = tk.Label(f, text='Сборка: '+str(self.root_path), bg=PANEL, fg=MUTED,
                                    anchor='w', wraplength=590, justify='left')
        self.folder_view.pack(fill='x', pady=(4, 0))
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x', pady=(8, 0))
        ttk.Button(btns, text='Обзор…', command=self.choose_root).pack(side='left')
        ttk.Button(btns, text='Сохранить настройки', command=self.settings_changed).pack(side='left', padx=8)

        # блок 4: Game Filter (запись — только по кнопке «Применить»)
        sec = uw.Section(body, 'GAME FILTER', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.gf_section = f
        self.gf_note = tk.Label(f, text='', bg=PANEL, fg=MUTED, anchor='w', justify='left', wraplength=590)
        self.gf_note.pack(fill='x')
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(fill='x', pady=(8, 0))
        tk.Label(grid, text='Фильтрация портов:', bg=PANEL, fg=FG).grid(row=0, column=0, sticky='w', pady=3)
        self.gf_mode_box = ttk.Combobox(grid, textvariable=self.gf_mode, state='readonly', width=10,
                                        values=['disabled', 'tcp', 'udp', 'all'])
        self.gf_mode_box.grid(row=0, column=1, sticky='w', padx=(10, 0))
        tk.Label(grid, text='TCP:', bg=PANEL, fg=FG).grid(row=1, column=0, sticky='w', pady=3)
        self.gf_tcp_entry = ttk.Entry(grid, textvariable=self.gf_tcp, width=22)
        self.gf_tcp_entry.grid(row=1, column=1, sticky='w', padx=(10, 0))
        tk.Label(grid, text='UDP:', bg=PANEL, fg=FG).grid(row=2, column=0, sticky='w', pady=3)
        self.gf_udp_entry = ttk.Entry(grid, textvariable=self.gf_udp, width=22)
        self.gf_udp_entry.grid(row=2, column=1, sticky='w', padx=(10, 0))
        tk.Label(grid, text='Формат портов: 1024-65535,80 (диапазоны через запятую)',
                 bg=PANEL, fg=MUTED, justify='left', wraplength=380).grid(row=3, column=0, columnspan=2, sticky='w')
        grid.columnconfigure(1, weight=1)
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x', pady=(10, 0))
        ttk.Button(btns, text='Применить фильтр', command=self.save_filters).pack(side='left')
        ttk.Button(btns, text='Загрузить из сборки', command=self.load_filters).pack(side='left', padx=8)

        # блок 5: IPSet-фильтр + ручное обновление
        sec = uw.Section(body, 'IPSET-ФИЛЬТР', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.ipset_section = f
        self.ipset_note = tk.Label(f, text='', bg=PANEL, fg=MUTED, anchor='w', justify='left', wraplength=590)
        self.ipset_note.pack(fill='x')
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(fill='x', pady=(8, 0))
        tk.Label(grid, text='Режим списка:', bg=PANEL, fg=FG).grid(row=0, column=0, sticky='w', pady=3)
        self.ipset_mode_box = ttk.Combobox(grid, textvariable=self.ipset_mode, state='readonly', width=10,
                                           values=['loaded', 'none', 'any'])
        self.ipset_mode_box.grid(row=0, column=1, sticky='w', padx=(10, 0))
        tk.Label(grid, text='loaded — только адреса из списка · none — отключить IPSet-профили · any — все IP (широкая фильтрация)',
                 bg=PANEL, fg=MUTED, justify='left', wraplength=420).grid(row=1, column=0, columnspan=2, sticky='w')
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x', pady=(10, 0))
        ttk.Button(btns, text='Применить режим', command=self.save_filters).pack(side='left')
        ttk.Button(btns, text='Обновить IPSet', command=self.update_ipset).pack(side='left', padx=8)

        # блок 6: редактор пользовательских списков
        sec = uw.Section(body, 'ПОЛЬЗОВАТЕЛЬСКИЕ СПИСКИ', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.list_section = f
        self.list_note = tk.Label(f, text='Один адрес в строке. Запись — только по «Сохранить список», с резервной копией.',
                                  bg=PANEL, fg=MUTED, anchor='w', justify='left', wraplength=590)
        self.list_note.pack(fill='x')
        head = tk.Frame(f, bg=PANEL)
        head.pack(fill='x', pady=(8, 2))
        self.ulist_combo = ttk.Combobox(head, state='readonly', width=24,
                                        values=[LIST_LABELS[k] for k in uw.LIST_KINDS])
        self.ulist_combo.current(0)
        self.ulist_combo.pack(side='left')
        self.ulist_combo.bind('<<ComboboxSelected>>', lambda e: self.on_ulist_kind())
        self.list_view = tk.Text(f, bg=INPUT, fg=FG, insertbackground=FG, font=('Consolas', 9),
                                 relief='flat', wrap='none', height=7, padx=8, pady=6)
        self.list_view.pack(fill='x', pady=(4, 0))
        self.list_view.configure(state='disabled')
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x', pady=(8, 0))
        ttk.Button(btns, text='Загрузить список', command=self.load_ulist).pack(side='left')
        ttk.Button(btns, text='Сохранить список', command=self.save_ulist).pack(side='left', padx=8)

        # блок 7: диагностика и экспорт
        sec = uw.Section(body, 'ДИАГНОСТИКА', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        self.diag_note = tk.Label(f, text='', bg=PANEL, fg=MUTED, anchor='w', justify='left', wraplength=590)
        self.diag_note.pack(fill='x')
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x', pady=(8, 0))
        ttk.Button(btns, text='Проверить локально', command=self.run_diagnostics).pack(side='left')
        ttk.Button(btns, text='Экспорт отчёта…', command=self.export_diagnostics).pack(side='left', padx=8)

        # блок 8: сворачиваемый журнал
        sec = uw.Section(body, 'ЖУРНАЛ', bg=BG, panel=PANEL)
        sec.pack(fill='x')
        f = sec.panel
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill='x')
        self.log_toggle = ttk.Button(btns, text='Показать журнал', command=self.toggle_log)
        self.log_toggle.pack(side='left')
        ttk.Button(btns, text='Сохранить отчёт…', command=self.export).pack(side='left', padx=8)
        self.log_frame = tk.Frame(f, bg=PANEL)
        self.log_view = tk.Text(self.log_frame, bg='#181818', fg=FG, insertbackground=FG,
                                font=('Consolas', 9), relief='flat', wrap='word',
                                height=9, padx=10, pady=8)
        self.log_view.pack(fill='both', expand=True)
        self.log_view.configure(state='disabled')
        self.footer = tk.Label(self, text='', bg='#2b2b2c', fg=MUTED, anchor='w', padx=14, pady=4)
        self.footer.pack(side='bottom', fill='x')
        self.refresh()

    def toggle_log(self):
        self.log_open = not self.log_open
        self.log_toggle.configure(text='Скрыть журнал' if self.log_open else 'Показать журнал')
        if self.log_open:
            self.log_frame.pack(fill='both', expand=True, pady=(8, 0))
            path = DATA/'desk.log'
            self.log_view.configure(state='normal')
            self.log_view.delete('1.0', 'end')
            if path.exists():
                self.log_view.insert('end', path.read_text(encoding='utf-8', errors='replace')[-20000:])
            self.log_view.configure(state='disabled')
            self.log_view.see('end')
        else:
            self.log_frame.pack_forget()
        self.refresh()

    # ─ журнал ────────────────────────────────────────────────────────────────

    def log(self, message):
        line = time.strftime('%H:%M:%S')+'  '+message+'\n'
        try:
            with (DATA/'desk.log').open('a', encoding='utf-8') as f:
                f.write(line)
        except OSError:
            pass
        if self.log_open:
            self.log_view.configure(state='normal')
            self.log_view.insert('end', line)
            self.log_view.see('end')
            self.log_view.configure(state='disabled')

    # ─ выбор целей проверки ──────────────────────────────────────────────────

    def check_targets(self):
        return {name: url for name, url in TARGETS.items()
                if (self.youtube.get() if name == 'YouTube' else self.discord.get())}

    # ─ подключение: одна кнопка, один поток состояния ────────────────────────

    def on_main_button(self):
        if self.feature_busy:
            return
        if self.state == 'stopping':
            return
        if self.state == 'connecting':
            self.cancel_connection()
        elif self.state == 'connected' or self.engine.running():
            self.stop_engine()
        elif not is_admin():
            # Кнопка сама предлагает повышение прав; UAC подтверждает пользователь.
            self.offer_elevation('Для драйвера WinDivert нужны права администратора.')
        else:
            self.start(auto=False)

    def start(self, auto):
        """Запуск стратегия→движок→ожидание→проверка. auto=True — из автопереключения.

        Всё долгое выполняется в connect_worker; события несут номер поколения,
        устаревшие отбрасываются.
        """
        if self.feature_busy or self.operation_active or self.state in ('connecting', 'stopping'):
            return
        gen = self.generation + 1
        try:
            self.save()
            if not self.check_targets():
                raise ValueError('Включите проверку хотя бы одного сервиса.')
        except Exception as exc:
            messagebox.showerror('Настройки', str(exc))
            return
        path = next((p for p in self.paths if p.stem == self.selected.get()), None)
        if path is None:
            messagebox.showerror('Стратегия', 'Выберите папку сборки и стратегию')
            return
        self.generation = gen
        self.busy = False
        self.cancel_event = threading.Event()
        self.operation_active = True
        self.attempt = 1
        self.rotation.reset(path.stem)
        self.set_state('connecting', 'Подготовка…')
        self.log('Подключение: '+path.stem+(' · автопереключение' if auto else ''))
        targets = self.check_targets()
        self.active_targets = dict(targets)
        index = self.paths.index(path)
        profiles = self.paths[index:]+self.paths[:index] if self.auto.get() else [path]
        self.engine.session_log = DATA/'engine.log'
        threading.Thread(target=self.connect_worker, args=(tuple(profiles), gen, targets, self.cancel_event), daemon=True).start()

    def connect_worker(self, profiles, gen, targets, cancel):
        with self.operation_lock:
            self._connect_worker(profiles, gen, targets, cancel)

    def _connect_worker(self, profiles, gen, targets, cancel):
        try:
            if cancel.is_set() or self.stale(gen):
                return
            if not is_admin():
                self.post_failed(gen, 'admin', 'Для WinDivert нужны права администратора.')
                return
            def emit(kind, payload):
                self.events.put(('selection_event', (gen, kind, payload)))
            status, profile, results = connect_profiles(self.engine, profiles, targets, cancel, emit,
                                                        grace=ENGINE_GRACE)
            if cancel.is_set() or self.stale(gen):
                return
            if status == 'connected':
                self.events.put(('connect_done', (gen, results)))
            elif status == 'exhausted':
                self.post_failed(gen, 'exhausted', 'Проверены все выбранные стратегии. Подключение не подтверждено. Смотрите журнал.')
        except Exception as exc:
            self.engine.stop()
            code = 'foreign' if isinstance(exc, ForeignEngineError) else 'admin' if isinstance(exc, PermissionError) else 'error'
            self.post_failed(gen, code, str(exc))
        finally:
            if cancel.is_set() or self.stale(gen):
                self.engine.stop()
            self.events.put(('operation_done', gen))

    def stale(self, gen):
        return gen != self.generation

    def post_stage(self, gen, text):
        self.events.put(('stage', (gen, text)))

    def post_failed(self, gen, code, message):
        self.events.put(('connect_failed', (gen, code, message)))

    def cancel_connection(self):
        self.stop_engine()
        self.log('Отменено во время запуска')

    def apply_connect_failed(self, gen, code, message):
        if self.stale(gen):
            return
        self.busy = False
        self.set_state('idle', message)
        self.log('Не подключено: '+message)
        if code == 'admin':
            self.offer_elevation(message)

    def apply_connect_done(self, gen, results):
        if self.stale(gen):
            return
        names = self.active_targets
        healthy = bool(names) and all(results.get(name, (False,))[0] for name in names)
        if not healthy or not self.engine.running() or not self.engine.ready:
            self.set_state('idle', 'Подключение не подтверждено. Смотрите журнал.')
            return
        self.last_check = time.strftime('%H:%M:%S')
        self.show_health(results)
        self.rotation.reset(self.selected.get())
        self.next_check = time.monotonic()+self.interval_seconds()
        self.set_state('connected', 'Подключено · '+self.selected.get())
        self.log('Подключение подтверждено: WinDivert готов, выбранные HTTPS-проверки пройдены')
        self.settings_changed()

    def handle_rotation(self, action):
        if action in [p.stem for p in self.paths]:
            self.log('Восстановление подключения: начинаем новый подбор с '+action)
            self.selected.set(action)
            self.start(auto=True)
        elif action == 'offline':
            self.set_stage('Нет ответов контрольных узлов. Ожидание сети; перебор приостановлен.')

    def stop_engine(self):
        if self.state == 'stopping':
            return
        self.generation += 1
        self.cancel_event.set()
        self.set_state('stopping', 'Остановка движка…')
        self.log('Отключение…')
        threading.Thread(target=self.stop_worker, daemon=True).start()

    def stop_worker(self):
        try:
            with self.operation_lock:
                self.engine.stop()
        except Exception as exc:
            self.events.put(('log', 'Ошибка остановки: '+str(exc)))
        finally:
            self.events.put(('stopped', self.generation))

    # ─ периодические проверки и автопереключение ─────────────────────────────

    def check(self):
        if self.busy:
            return
        targets = dict(self.active_targets)
        if not targets:
            return
        self.busy = True
        gen = self.generation
        def worker():
            try:
                self.events.put(('checks', (gen, check_network(targets))))
            except Exception as exc:
                self.events.put(('check_error', (gen, str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def apply_checks(self, gen, results):
        if self.stale(gen):
            return
        self.busy = False
        self.last_check = time.strftime('%H:%M:%S')
        names = self.active_targets
        online = any(value[0] for value in results.values())
        healthy = bool(names) and all(results.get(name, (False,))[0] for name in names)
        self.log('Проверка: '+', '.join(k+(' OK' if v[0] else ' FAIL: '+v[2]) for k, v in results.items()))
        self.show_health(results)
        # статус всегда отражает последнюю проверку
        if self.state == 'connected':
            stage = ('Подключено' if healthy else 'Движок работает, проверка не пройдена')+' · '+self.selected.get()
            self.set_stage(stage)
        if self.auto.get() and self.engine.running() and self.state == 'connected':
            self.handle_rotation(self.rotation.observe(online, healthy))
        self.next_check = time.monotonic()+self.interval_seconds()
        self.refresh()

    def interval_seconds(self):
        try:
            value = int(self.interval.get())
            return value if 15 <= value <= 300 else 30
        except ValueError:
            return 30

    def show_health(self, results):
        for name in ('YouTube', 'Discord'):
            value = results.get(name)
            if not (self.active_targets or self.check_targets()).get(name):
                text, color = 'Отключено', MUTED
            elif value and value[0]:
                text, color = 'Доступен · '+str(value[1])+' мс', GREEN
            else:
                text, color = 'Нет ответа', AMBER
            self.health[name].configure(text=text, fg=color)
        online = any(value[0] for value in results.values())
        self.health['Интернет'].configure(text='Доступен' if online else 'Нет ответа',
                                          fg=GREEN if online else RED)

    # ─ состояние интерфейса ──────────────────────────────────────────────────

    def set_stage(self, stage):
        self.stage = stage
        self.refresh()

    def set_state(self, state, stage):
        self.state = state
        self.stage = stage
        self.refresh()

    def refresh(self):
        running = self.engine.running()
        self.strategy_combo.configure(state='disabled' if self.state in ('connecting', 'stopping') else 'readonly')
        self.main_button.configure(state='disabled' if self.state == 'stopping' or self.feature_busy else 'normal')
        if self.state == 'connecting':
            text, color = '●  Подключение…', AMBER
            if not self.progress.winfo_ismapped():
                self.progress.pack(fill='x', before=self.main_button, pady=(10, 8))
            self.progress.start(12)
        else:
            self.progress.stop()
            if self.progress.winfo_ismapped():
                self.progress.pack_forget()
            if self.state == 'stopping':
                text, color = '●  Остановка…', AMBER
            elif not running:
                text, color = '●  Остановлено', FG
            elif self.stage.startswith('Подключено'):
                text, color = '●  Подключено', GREEN
            else:
                text, color = '●  Движок работает · подключение не подтверждено', AMBER
        self.status.configure(text=text, fg=color)
        self.main_button.configure(
            text='Отмена' if self.state == 'connecting' else
                 'Отключить' if running else 'Подключить',
            style=('Cancel.TButton' if self.state == 'connecting' else 'Accent.TButton'))
        parts = []
        if self.stage:
            parts.append(self.stage)
        parts.append('Стратегия: '+(self.selected.get() or '—'))
        if running and self.engine.process:
            parts.append('PID: '+str(self.engine.process.pid))
        parts.append('Последняя проверка: '+self.last_check+(' · проверяется…' if self.busy else ''))
        if not is_admin():
            parts.append('Права: обычные · при подключении будет предложен перезапуск с UAC')
        self.details.configure(text='\n'.join(parts))
        self.footer.configure(text='  '+('Движок работает' if running else 'Остановлено')+'    |    '+
                              str(len(self.paths))+' стратегий    |    '+
                              ('Администратор' if is_admin() else 'Обычные права')+'    |    '+
                              self.root_path.name)
        self.folder_view.configure(text='Сборка: '+str(self.root_path))
        # блокировка настроек сборки во время операций и работы движка
        locked, reason = uw.feature_lock(self.state, self.operation_active or self.feature_busy, running)
        if hasattr(self, 'gf_section'):
            self.gf_note.configure(text=reason if locked else self.feature_notes['gf'])
            self.ipset_note.configure(text=reason if locked else self.feature_notes['ipset'])
            self.list_note.configure(text=reason if locked else
                                     (self.feature_notes['list'] or
                                      'Один адрес в строке. Запись — по кнопке, с резервной копией.'))
            for section in (self.gf_section, self.ipset_section, self.list_section):
                uw.set_widgets_state(section, not locked,
                                     readonly={self.gf_mode_box, self.ipset_mode_box,
                                               self.ulist_combo})

    def offer_elevation(self, reason):
        """Предлагает перезапуск GUI с повышением прав. UAC подтверждает пользователь."""
        try:
            self.save()
            if not self.check_targets():
                raise ValueError('Включите проверку хотя бы одного сервиса.')
        except Exception as exc:
            messagebox.showerror('Настройки', str(exc))
            return
        self.elevate()

    def elevate(self):
        target, args, workdir = _rp.elevate_target()
        previous_reset = os.environ.get('PYINSTALLER_RESET_ENVIRONMENT')
        if _rp.is_frozen():
            os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        try:
            result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', target,
                        subprocess.list2cmdline(args), workdir, 1)
        finally:
            if previous_reset is None:
                os.environ.pop('PYINSTALLER_RESET_ENVIRONMENT', None)
            else:
                os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = previous_reset
        if result > 32:
            self.log('Перезапуск с правами администратора и продолжением подключения')
            self.destroy()
        else:
            self.set_state('idle', 'Повышение прав отменено или недоступно.')

    # ─ выбор стратегии и сборки ──────────────────────────────────────────────

    def strategy_changed(self):
        if self.state == 'connected' and self.engine.running():
            self.log('Смена стратегии: '+self.selected.get())
            self.start(auto=False)
        else:
            self.settings_changed()

    def choose_root(self):
        if self.feature_busy or self.operation_active or self.engine.running() or self.state == 'stopping':
            messagebox.showinfo('Сборка', 'Сначала отключите подключение и дождитесь остановки.')
            return
        folder = filedialog.askdirectory(title='Папка сборки (bin/winws.exe и general*.bat)')
        if not folder:
            return
        path = Path(folder)
        if not (path/'bin/winws.exe').exists() or not strategies(path):
            messagebox.showerror('Папка', 'Нужна папка с bin/winws.exe и general*.bat')
            return
        self.root_path = path
        self.paths = strategies(path)
        self.selected.set(self.paths[0].stem)
        self.rotation = Rotation([p.stem for p in self.paths])
        self.strategy_combo.configure(values=[p.stem for p in self.paths])
        self.settings_changed()
        self.log('Выбрана сборка '+path.name+' · стратегий: '+str(len(self.paths)))
        self.load_filters()

    # ─ функции сборки: Game Filter, IPSet, списки, диагностика ────────────────

    def features(self):
        """Backend zapret_features или None. Модуль не пишет ничего сам по себе."""
        if self._features_module == 'init':
            try:
                import zapret_features
                self._features_module = zapret_features
            except ImportError as exc:
                self._features_module = None
                self.log('Backend zapret_features недоступен ('+str(exc)+'): '
                         'элементы фильтров пока не смогут изменять сборку.')
        return self._features_module

    def feature_locked(self):
        return uw.feature_lock(self.state, self.operation_active or self.feature_busy, self.engine.running())

    def launch_feature(self, function, args, writing=False):
        if self.feature_busy:
            return
        self.feature_busy = True
        self.refresh()
        def work():
            try:
                if writing and foreign_engine():
                    self.events.put(('feature_error', ('gf', 'Другой winws.exe работает. Остановите его перед изменением сборки.')))
                    return
                function(*args)
            except Exception as exc:
                self.events.put(('feature_error', ('gf', str(exc))))
            finally:
                self.events.put(('feature_finished', None))
        threading.Thread(target=work, daemon=True).start()

    def require_backend(self, title):
        mod = self.features()
        if mod is None:
            self.log(title+': backend zapret_features ещё не подключён.')
        return mod

    def load_filters(self):
        """Чтение фильтров (без записи) и отображение в элементах."""
        mod = self.require_backend('Фильтры')
        if mod is None:
            return
        root = self.root_path
        self.launch_feature(self._load_filters_worker, (mod, root))

    def _load_filters_worker(self, mod, root):
        try:
            data = mod.read_filters(root)
        except Exception as exc:
            self.events.put(('feature_error', ('gf', 'Чтение фильтров не удалось: '+str(exc))))
            return
        self.events.put(('filters_loaded', data))

    def apply_filters_loaded(self, data):
        self.gf_mode.set(str(data.get('game_mode', self.gf_mode.get())))
        self.gf_tcp.set(str(data.get('tcp', self.gf_tcp.get())))
        self.gf_udp.set(str(data.get('udp', self.gf_udp.get())))
        self.ipset_mode.set(str(data.get('ipset_mode', self.ipset_mode.get())))
        self.feature_notes['gf'] = 'Значения прочитаны из сборки.'
        self.log('Фильтры сборки: '+str(data))
        self.refresh()

    def save_filters(self):
        """Запись Game Filter и режима IPSet. Только по кнопке пользователя."""
        mod = self.require_backend('Фильтры')
        if mod is None:
            return
        locked, reason = self.feature_locked()
        if locked:
            self.log('Фильтры не применены: '+reason)
            return
        try:
            payload = uw.game_filter_payload(self.gf_mode.get(), self.gf_tcp.get(),
                                             self.gf_udp.get(), self.ipset_mode.get())
        except ValueError as exc:
            self.log('Фильтры не применены: '+str(exc))
            return
        root = self.root_path
        self.launch_feature(self._save_filters_worker, (mod, root, payload), writing=True)

    def _save_filters_worker(self, mod, root, payload):
        try:
            mod.write_filters(root, **payload)
            data = mod.read_filters(root)   # показать фактический результат
        except Exception as exc:
            self.events.put(('feature_error', ('gf', 'Запись фильтров не удалась: '+str(exc))))
            return
        self.events.put(('filters_saved', data))

    def update_ipset(self):
        """Ручное обновление IPSet (network и запись — вне Tk-потока)."""
        mod = self.require_backend('IPSet')
        if mod is None:
            return
        locked, reason = self.feature_locked()
        if locked:
            self.log('Обновление IPSet отложено: '+reason)
            return
        root = self.root_path
        self.launch_feature(self._update_ipset_worker, (mod, root), writing=True)

    def _update_ipset_worker(self, mod, root):
        try:
            count = mod.update_ipset(root)
        except Exception as exc:
            self.events.put(('feature_error', ('ipset', 'Обновление IPSet не удалось: '+str(exc))))
            return
        self.events.put(('ipset_updated', count))

    def on_ulist_kind(self):
        labels = {v: k for k, v in LIST_LABELS.items()}
        self.ulist_kind = labels.get(self.ulist_combo.get(), 'general')
        self.load_ulist()

    def load_ulist(self):
        """Чтение выбранного пользовательского списка (без записи)."""
        mod = self.require_backend('Списки')
        if mod is None:
            return
        kind = self.ulist_kind
        self.launch_feature(self._load_ulist_worker, (mod, self.root_path, kind))

    def _load_ulist_worker(self, mod, root, kind):
        try:
            text = mod.read_user_list(root, kind)
        except Exception as exc:
            self.events.put(('feature_error', ('list', 'Чтение списка не удалось: '+str(exc))))
            return
        self.events.put(('ulist_loaded', (kind, text)))

    def apply_ulist_loaded(self, kind, text):
        if kind != self.ulist_kind:
            return
        self.list_view.configure(state='normal')
        self.list_view.delete('1.0', 'end')
        self.list_view.insert('1.0', text)
        self.feature_notes['list'] = 'Список прочитан из сборки; правки ещё не сохранены.'
        self.log('Список '+LIST_LABELS[kind]+' прочитан ('+str(len(text.splitlines()))+' строк).')
        self.refresh()

    def save_ulist(self):
        """Сохранение одного списка. Бэкенд создаёт резервную копию перед записью."""
        mod = self.require_backend('Списки')
        if mod is None:
            return
        locked, reason = self.feature_locked()
        if locked:
            self.log('Список не сохранён: '+reason)
            return
        kind = self.ulist_kind
        text = self.list_view.get('1.0', 'end').rstrip('\n')
        ok, reason = uw.validate_list(kind, text)
        if not ok:
            self.log('Список не сохранён: '+reason)
            return
        self.launch_feature(self._save_ulist_worker, (mod, self.root_path, kind, text), writing=True)

    def _save_ulist_worker(self, mod, root, kind, text):
        try:
            mod.write_user_list(root, kind, text)
            checked = mod.read_user_list(root, kind)
        except Exception as exc:
            self.events.put(('feature_error', ('list', 'Запись списка не удалась: '+str(exc))))
            return
        self.events.put(('ulist_saved', (kind, len(checked.splitlines()), checked.rstrip('\n') == text.rstrip('\n'))))

    def run_diagnostics(self):
        """Локальная диагностика без мутаций; результат — в блоке и в журнале."""
        mod = self.require_backend('Диагностика')
        if mod is None:
            return
        root = self.root_path
        self.launch_feature(self._run_diag_worker, (mod, root))

    def _run_diag_worker(self, mod, root):
        try:
            info = mod.diagnostics(root)
        except Exception as exc:
            self.events.put(('feature_error', ('diag', 'Диагностика не удалась: '+str(exc))))
            return
        self.events.put(('diag_done', info))

    def apply_diag(self, info):
        self.last_diag = info
        summary = []
        for key, value in (info.items() if isinstance(info, dict) else [('данные', info)]):
            summary.append(f'{key}: {value}')
            if len(summary) >= 6:
                break
        self.diag_note.configure(text='\n'.join(summary), fg=MUTED, justify='left')
        self.log('Диагностика: '+str(info))
        self.refresh()

    def export_diagnostics(self):
        """Диагностика + настройки + журналы в файл по выбору пользователя."""
        mod = self.features()
        if mod is None:
            self.log('Экспорт: backend zapret_features ещё не подключён; журнал доступен кнопкой «Сохранить отчёт…».')
            return
        filename = filedialog.asksaveasfilename(defaultextension='.json',
                                               initialfile='zapret-diagnostics.json')
        if not filename:
            return
        root, state_note, last_check, strategy = (self.root_path, self.state,
                                                  self.last_check, self.selected.get())
        self.launch_feature(self._export_diag_worker, (mod, root, Path(filename), state_note, last_check, strategy))

    def _export_diag_worker(self, mod, root, filename, state_note, last_check, strategy):
        try:
            info = mod.diagnostics(root)
            report = {'диагностика': info,
                      'состояние': {'статус': state_note, 'последняя_проверка': last_check,
                                    'стратегия': strategy},
                      'журналы': {name: path.read_text(encoding='utf-8', errors='replace')[-40000:]
                                  for name, path in
                                  (('desk.log', DATA/'desk.log'), ('engine.log', DATA/'engine.log'))
                                  if path.exists()}}
            filename.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                encoding='utf-8')
        except Exception as exc:
            self.events.put(('feature_error', ('diag', 'Экспорт не удался: '+str(exc))))
            return
        self.events.put(('diag_exported', str(filename)))

    # ─ главный цикл очереди событий ──────────────────────────────────────────

    def tick(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'log':
                    self.log(payload)
                elif kind == 'stage':
                    gen, text = payload
                    if not self.stale(gen) and self.state == 'connecting':
                        self.stage = text
                        self.refresh()
                elif kind == 'selection_event':
                    gen, event, value = payload
                    if self.stale(gen):
                        continue
                    if event == 'attempt':
                        number, total, stem = value
                        self.attempt = number
                        self.selected.set(stem)
                        self.set_stage(f'Подбор {number}/{total} · {stem} · ожидание готовности WinDivert (до 20 с)…')
                        self.log(self.stage)
                    elif event == 'ready':
                        self.set_stage(f'Подбор · {value} · WinDivert готов, стабилизация и проверка сети…')
                    elif event == 'offline':
                        self.set_stage(value)
                        self.log(value)
                    elif event == 'profile_error':
                        self.log('Стратегия не запустилась: '+value)
                    elif event == 'probe':
                        self.last_check = time.strftime('%H:%M:%S')
                        self.show_health(value)
                        self.log('Проверка: '+', '.join(k+(' OK' if v[0] else ' FAIL: '+v[2]) for k, v in value.items()))
                elif kind == 'checks':
                    self.apply_checks(*payload)
                elif kind == 'check_error':
                    gen, text = payload
                    if not self.stale(gen):
                        self.busy = False
                        self.log('Ошибка проверки: '+text)
                elif kind == 'connect_done':
                    self.apply_connect_done(*payload)
                elif kind == 'connect_failed':
                    self.apply_connect_failed(*payload)
                elif kind == 'filters_loaded':
                    self.apply_filters_loaded(payload)
                elif kind == 'feature_finished':
                    self.feature_busy = False
                    self.refresh()
                    if self.closing:
                        self.after_idle(self.close)
                elif kind == 'filters_saved':
                    self.apply_filters_loaded(payload)
                    self.feature_notes['gf'] = 'Фильтры применены и сохранены в сборке.'
                    self.log('Фильтры применены: '+str(payload))
                elif kind == 'ulist_loaded':
                    self.apply_ulist_loaded(*payload)
                elif kind == 'ulist_saved':
                    kind, strings, same = payload
                    self.feature_notes['list'] = ('Список сохранён ('+str(strings)+' строк); '
                                                  'резервная копия создана бэкендом.')
                    self.log('Список '+LIST_LABELS[kind]+' сохранён: '+str(strings)+' строк.'
                             + ('' if same else ' Прочитанный текст отличается — проверьте журналы бэкенда.'))
                    self.after(1, self.load_ulist)
                elif kind == 'ipset_updated':
                    self.feature_notes['ipset'] = 'IPSet обновлён вручную: '+(str(payload) or '0')+' записей.'
                    self.log('IPSet обновлён: '+str(payload)+' записей.')
                    self.after(1, self.load_filters)
                elif kind == 'diag_exported':
                    self.feature_notes['diag'] = 'Отчёт сохранён: '+str(payload)
                    self.log('Экспорт диагностики: '+str(payload))
                elif kind == 'feature_error':
                    section, message = payload
                    self.feature_notes[section] = message
                    self.log(message)
                    self.refresh()
                elif kind == 'operation_done':
                    self.operation_active = False
                elif kind == 'stopped':
                    self.operation_active = False
                    self.busy = False
                    self.set_state('idle', 'Остановлено')
                    if self.closing:
                        self.destroy()
                        return
        except queue.Empty:
            pass
        if self.state == 'connected' and not self.engine.running():
            if self.auto.get():
                self.start(auto=True)
            else:
                self.set_state('idle', 'Движок завершился · подробности в engine.log')
        if self.state == 'connected' and self.engine.running() and not self.busy \
                and time.monotonic() >= self.next_check:
            self.check()
        self.after(250, self.tick)

    # ─ экспорт отчёта ────────────────────────────────────────────────────────

    def export(self):
        filename = filedialog.asksaveasfilename(defaultextension='.txt', initialfile='zapret-report.txt')
        if not filename:
            return
        report = 'Zapret Desk 0.4\nСборка: '+str(self.root_path)+'\nСтратегия: '+self.selected.get()+'\n\n'
        for name in ('desk.log', 'engine.log'):
            path = DATA/name
            if path.exists():
                report += '\n--- '+name+' ---\n'+path.read_text(encoding='utf-8', errors='replace')[-60000:]
        try:
            Path(filename).write_text(report, encoding='utf-8')
        except OSError as exc:
            messagebox.showerror('Отчёт', str(exc))

    # ─ завершение ────────────────────────────────────────────────────────────

    def close(self):
        if self.feature_busy:
            self.closing = True
            return
        try:
            self.save()
        except Exception:
            pass
        if self.engine.running() or self.operation_active or self.state == 'stopping':
            self.closing = True
            self.stop_engine()
        else:
            self.destroy()


if __name__ == '__main__':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        App().mainloop()
    except Exception:
        import traceback
        (DATA/'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise
