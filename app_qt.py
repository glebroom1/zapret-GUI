"""Zapret Desk 0.5 · Qt/PySide6 · одностраничный тёмный GUI без вкладок.

Скин по референсу https://github.com/confeden/Antigravity (src/gui/theme.rs):
тёмные карточки на #14161a, радиус 10, постоянная тонкая полоса прокрутки,
синий акцент, зелёный/жёлтый/красный только для статусов.

Логика controller полностью соответствует app.py 0.4 (Tk): единый цикл
connect_profiles, поколения событий для отмены устаревших результатов,
одна кнопка Подключить → Отмена → Отключить, статус только после проверки.
Долгие операции вне GUI-потока; запись настроек — только по кнопке.
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
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPen, QTextCursor,
                           QWheelEvent)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QStyleOptionButton, QPushButton, QPlainTextEdit, QScrollArea, QSizePolicy,
    QStyle, QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QSpacerItem,
)

from core import (discover, strategies, Rotation, Engine, check_network,
                  is_admin, foreign_engine, ForeignEngineError, connect_profiles, TARGETS)
from strategy_history import StrategyHistory
import runtime_paths as _rp
import autostart as _autostart
import zapret_import as _zi
import qt_common as C

ENGINE_GRACE = 4  # пауза старта движка перед первой проверкой
IMPORT_HINT = 'Импортируйте сюда архив с GitHub или папку с zapret с пользовательскими доменами'
FOLDER_API = ('inspect_folder', 'import_folder', 'import_user_folder')


class NoWheelComboBox(QComboBox):
    """Выбор только мышью: список открывается кликом, колесо не меняет значение."""

    def wheelEvent(self, event):
        event.ignore()


class CheckStyle:
    """Индикатор чекбокса, нарисованный QPainter: синяя заливка и явная белая
    галочка. Без файлов ресурсов — одинаково надёжно в проекте и в frozen EXE."""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PE_IndicatorCheckBox:
            return
        rect = QRectF(option.rect)
        side = min(rect.width(), rect.height())
        rect = QRectF(rect.center().x() - side / 2 + 0.5,
                      rect.center().y() - side / 2 + 0.5, side - 1, side - 1)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        checked = bool(option.state & QStyle.State_On)
        partial = bool(option.state & QStyle.State_NoChange)
        enabled = bool(option.state & QStyle.State_Enabled)
        radius = 4.0
        if checked or partial:
            painter.setPen(QPen(QColor(C.ACCENT), 1))
            painter.setBrush(QBrush(QColor(C.ACCENT)))
        else:
            border = QColor(C.ACCENT) if option.state & QStyle.State_MouseOver else QColor(C.LINE)
            painter.setPen(QPen(border, 1))
            painter.setBrush(QBrush(QColor(C.SUNKEN)))
        painter.drawRoundedRect(rect, radius, radius)
        if checked or partial:
            alpha = 255 if enabled else 130
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 2.4,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            width, height = rect.width(), rect.height()
            left, top = rect.left(), rect.top()
            if checked:
                painter.drawPolyline([QPointF(left + width * 0.24, top + height * 0.54),
                                      QPointF(left + width * 0.42, top + height * 0.73),
                                      QPointF(left + width * 0.77, top + height * 0.29)])
            else:
                painter.drawLine(QPointF(left + width * 0.28, top + height * 0.5),
                                 QPointF(left + width * 0.72, top + height * 0.5))
        painter.restore()


class PaintedCheckBox(QCheckBox):
    """Paint the indicator directly; no QObject style ownership at shutdown."""

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_CheckBox, option, painter, self)
        option.rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        CheckStyle().drawPrimitive(QStyle.PE_IndicatorCheckBox, option, painter, self)
        painter.end()


class App(QMainWindow):
    def __init__(self, data_dir=None):
        super().__init__()
        self.setWindowTitle('zapret GUI — 0.8')
        self.exit_requested = False
        self.pending_strategy = None
        self.tray = None
        self.setWindowIcon(QIcon(str(_rp.bundle_dir() / 'assets' / 'app.png')))
        self.resize(680, 900)
        self.setMinimumSize(640, 620)
        self.DATA = Path(data_dir) if data_dir else _rp.data_dir()
        self.events = queue.Queue()
        self.engine = Engine(lambda msg: self.events.put(('log', msg)))
        self.engine.session_file = self.DATA/'engine-session.json'
        self.busy = False
        self.operation_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.operation_active = False
        self.feature_busy = False
        self.closing = False
        self.state = 'idle'      # idle | connecting | connected | stopping
        self.generation = 0
        self.next_check = 0
        self.stage = ''
        self.log_open = False
        self.attempt = 0
        self.settings = self.load()
        self._heal_autostart()          # путь EXE переехал — обновить ключ Run
        bundled = _rp.bootstrap_bundled()
        found = []
        for root in _rp.source_roots():
            found.extend(discover(root))
        self.root_path = Path(self.settings.get('root', str(bundled or (found[0] if found else ''))))
        if _rp.is_frozen() and (not _rp.is_valid_build(self.root_path) or self.root_path not in found):
            self.root_path = bundled or (found[0] if found else Path(''))
        self.paths = strategies(self.root_path) if self.root_path.is_dir() else []
        self.history = StrategyHistory(self.DATA/'strategy-history.json')
        self.auto = bool(self.settings.get('auto', True))
        self.youtube = bool(self.settings.get('youtube', True))
        self.discord = bool(self.settings.get('discord', True))
        self._history_default = False
        self.selected = str(self.settings.get('strategy', 'general'))
        if self.selected not in [p.stem for p in self.paths]:
            self.selected = self.paths[0].stem if self.paths else ''
        if self.auto and self.selected and self.paths:
            # Память удачных стратегий: последняя успешная — выбор по умолчанию.
            # Ключ истории привязан к пути, содержимому стратегии и целям, поэтому
            # обновление файлов или смена целей не дают ложного приоритета.
            try:
                preferred = self.history.preferred(self.paths, self.check_targets())
            except Exception:
                preferred = None
            if preferred is not None and preferred.stem != self.selected:
                self.selected = preferred.stem
                self._history_default = True
        self.interval = str(self.settings.get('interval', 30))
        self.rotation = Rotation([p.stem for p in self.paths])
        self.last_check = 'ещё не выполнялась'
        self.health = {}
        self.active_targets = {}
        self.gf_mode = str(self.settings.get('game_mode', 'disabled'))
        self.gf_tcp = str(self.settings.get('gf_tcp', '1024-65535'))
        self.gf_udp = str(self.settings.get('gf_udp', '1024-65535'))
        self.ipset_mode = str(self.settings.get('ipset_mode', 'loaded'))
        self.ulist_kind = 'general'
        self.feature_notes = {'gf': '', 'ipset': '', 'list': '', 'diag': '', 'import': ''}
        self.last_diag = None
        self._features_module = 'init'
        recovered = self.engine.recover()
        if recovered is not None:
            self.root_path = recovered.parent
            self.paths = strategies(self.root_path)
            self.selected = recovered.stem
            self.rotation = Rotation([p.stem for p in self.paths])
            self.rotation.reset(self.selected)
            self.state = 'connected'
            self.stage = 'Движок работает · управление восстановлено'
            self.active_targets = dict(self.check_targets())
        self.build_ui()
        self.setup_tray()
        self.apply_titlebar_theme()
        self.refresh()
        self.ticker = QTimer(self)
        self.ticker.setInterval(250)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start()
        QTimer.singleShot(450, self.load_filters)      # только чтение; запись — по кнопке
        if self.paths:
            self.log('Найдено стратегий: '+str(len(self.paths))+' · '+self.root_path.name)
            if self._history_default:
                self.log('Память стратегий: по умолчанию выбрана '+self.selected)
        else:
            self.log('Сборка не найдена — укажите папку кнопкой «Обзор…»')
        requested_strategy = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--select-strategy=')), None)
        if requested_strategy in [p.stem for p in self.paths]:
            QTimer.singleShot(300, lambda: self.on_strategy_select(requested_strategy))
        if '--disconnect' in sys.argv and self.engine.running():
            QTimer.singleShot(300, self.stop_engine)
        if '--connect' in sys.argv and self.state != 'connected':
            QTimer.singleShot(300, self.on_main_button)

    # ─ служебное ─────────────────────────────────────────────────────────────

    def apply_titlebar_theme(self):
        """Тёмная полоса заголовка Windows (DwmSetWindowAttribute, как в app.py)."""
        try:
            value = ctypes.c_int(1)
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)
            color = ctypes.c_int(0x001a1614)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)
        except OSError:
            pass

    def load(self):
        try:
            return json.loads((self.DATA/'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save(self):
        interval = int(self.interval)
        if not 15 <= interval <= 300:
            raise ValueError('Интервал должен быть от 15 до 300 секунд')
        settings = dict(root=str(self.root_path), strategy=self.selected, auto=self.auto,
                        youtube=self.youtube, discord=self.discord, interval=interval,
                        game_mode=self.gf_mode, gf_tcp=self.gf_tcp.strip(),
                        gf_udp=self.gf_udp.strip(), ipset_mode=self.ipset_mode)
        temp = self.DATA/'settings.tmp'
        temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.DATA/'settings.json')

    def settings_changed(self):
        try:
            self.save()
        except Exception as exc:
            self.log('Настройки: '+str(exc))

    def _heal_autostart(self):
        """Ключ Run хранит абсолютный путь: после переезда EXE обновляем его."""
        try:
            if _autostart.read() and _autostart.current_value() != _autostart.command():
                _autostart.update(True)
        except Exception:
            pass

    # ─ построение интерфейса ─────────────────────────────────────────────────

    def build_ui(self):
        self.setCentralWidget(self.build_scroll())
        self.statusBar().hide()

    def build_topbar(self):
        top = QFrame()
        top.setObjectName('topbar')
        top.setFixedHeight(34)
        row = QHBoxLayout(top)
        row.setContentsMargins(14, 0, 14, 0)
        title = QLabel(' zapret GUI')
        small = QLabel(' zapret-discord-youtube')
        version = QLabel(C.VERSION)
        small.setProperty('muted', True)
        version.setProperty('muted', True)
        row.addWidget(title)
        row.addWidget(small)
        row.addStretch(1)
        row.addWidget(version)
        return top

    def build_footer(self):
        footer = QFrame()
        footer.setObjectName('topbar')
        footer.setFixedHeight(26)
        row = QHBoxLayout(footer)
        row.setContentsMargins(14, 0, 14, 0)
        self.footer_label = QLabel('')
        self.footer_label.setProperty('muted', True)
        row.addWidget(self.footer_label)
        return footer

    def build_scroll(self):
        page = QWidget()
        page.setObjectName('windowHolder')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.build_topbar())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        body = QWidget()
        body.setObjectName('scrollBody')
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        column = QVBoxLayout(body)
        column.setContentsMargins(16, 12, 8, 12)
        column.setSpacing(12)
        self.body = column
        self.build_connect_card()
        self.build_strategy_card()
        self.build_checks_card()
        self.build_autostart_card()
        self.build_import_card()
        self.build_gamefilter_card()
        self.build_ipset_card()
        self.build_lists_card()
        self.build_diagnostics_card()
        self.build_log_card()
        column.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.build_footer())
        return page

    def card(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setProperty('card', True)
        column = QVBoxLayout(frame)
        column.setContentsMargins(14, 10, 14, 12)
        column.setSpacing(6)
        heading = QLabel(title)
        heading.setProperty('cardTitle', True)
        column.addWidget(heading)
        self.body.addWidget(frame)
        return frame

    def note(self, panel: QFrame) -> QLabel:
        label = QLabel('')
        label.setProperty('muted', True)
        label.setWordWrap(True)
        panel.layout().addWidget(label)
        return label

    def button(self, text, handler, kind='plain') -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty('kind', kind)
        button.clicked.connect(handler)
        return button

    def button_row(self, *buttons) -> QWidget:
        row = QWidget()
        row.setProperty('buttonRow', True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(8)
        for button in buttons:
            layout.addWidget(button)
        layout.addStretch(1)
        return row

    def field_row(self, caption: str, widget) -> QWidget:
        """Строка «подпись + поле»: поле забирает всё свободное место строки."""
        row = QWidget()
        row.setProperty('fieldRow', True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        label = QLabel(caption)
        label.setMinimumWidth(104)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        trailing = QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addSpacerItem(trailing)
        return row

    def hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty('muted', True)
        label.setWordWrap(True)
        return label

    # блок 1: подключение
    def build_connect_card(self):
        frame = self.card('ПОДКЛЮЧЕНИЕ')
        frame.setObjectName('statusCard')
        self.status_line = QLabel('●  Остановлено')
        self.status_line.setObjectName('statusLine')
        frame.layout().addWidget(self.status_line)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        frame.layout().addWidget(self.progress)
        self.details = QLabel('')
        self.details.setProperty('muted', True)
        self.details.setWordWrap(True)
        frame.layout().addWidget(self.details)
        main_button = self.button('Подключить', self.on_main_button, kind='accent')
        main_button.setFixedHeight(40)
        self.main_button = main_button
        frame.layout().addSpacing(4)
        frame.layout().addWidget(main_button)
        frame.layout().addSpacing(2)
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setVerticalSpacing(2)
        grid.setColumnMinimumWidth(0, 82)
        for row, name in enumerate(('YouTube', 'Discord', 'Интернет')):
            caption = QLabel(name)
            caption.setProperty('muted', True)
            value = QLabel('—')
            self.health[name] = value
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
        frame.layout().addLayout(grid)

    def combo(self) -> NoWheelComboBox:
        """Все выпадающие списки приложения: без реакции на колесо мыши."""
        return NoWheelComboBox()

    # блок 2: стратегия
    def build_strategy_card(self):
        frame = self.card('СТРАТЕГИЯ И АВТОПЕРЕКЛЮЧЕНИЕ')
        combo = self.combo()
        combo.addItems([p.stem for p in self.paths])
        if self.selected:
            combo.setCurrentIndex(max(combo.findText(self.selected), 0))
        combo.currentTextChanged.connect(self.on_strategy_select)
        self.strategy_combo = combo
        frame.layout().addWidget(combo)
        self.auto_check = self.toggle('Автоматически переключаться при сбоях', self.auto,
                                      self.on_auto_changed)
        frame.layout().addWidget(self.auto_check)
        hint = QLabel('При подключении — подбор до успеха · после подключения — смена '
                      'после 3 сбоев · при потере сети пауза')
        hint.setProperty('muted', True)
        hint.setWordWrap(True)
        frame.layout().addWidget(hint)

    def toggle(self, text, checked, handler) -> QCheckBox:
        check = PaintedCheckBox(text)
        check.setChecked(checked)
        check.toggled.connect(handler)
        return check

    # блок 3: настройки проверок
    def build_checks_card(self):
        frame = self.card('НАСТРОЙКИ ПРОВЕРОК')
        checks = QHBoxLayout()
        checks.setContentsMargins(0, 0, 0, 0)
        self.youtube_check = self.toggle('Проверять YouTube', self.youtube, self.on_youtube_changed)
        self.discord_check = self.toggle('Проверять Discord', self.discord, self.on_discord_changed)
        checks.addWidget(self.youtube_check)
        checks.addSpacing(18)
        checks.addWidget(self.discord_check)
        checks.addStretch(1)
        frame.layout().addLayout(checks)
        self.interval_edit = QLineEdit(self.interval)
        self.interval_edit.setFixedWidth(70)
        self.interval_edit.editingFinished.connect(self.on_interval_changed)
        interval_row = self.field_row('Интервал, с (15–300)', self.interval_edit)
        self.interval_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        frame.layout().addWidget(interval_row)
        frame.layout().addWidget(self.button_row(self.button('Сохранить настройки', self.settings_changed)))

    # блок 3c: автозапуск в Windows
    def build_autostart_card(self):
        frame = self.card('АВТОЗАПУСК')
        self.autostart_note = self.note(frame)
        self.autostart_check = self.toggle(
            'Запускать при входе в Windows', _autostart.read(), self.on_autostart_changed)
        frame.layout().addWidget(self.autostart_check)
        hint = self.hint('При входе приложение откроется сразу свёрнутым в трей и продолжит '
                         'работать в фоне. Ключ пишется в реестр вашего пользователя '
                         '(HKCU\\…\\Run), поэтому права администратора не нужны, а сам '
                         'автозапуск выключается там же, в «Параметры → Приложения → Автозагрузка».')
        frame.layout().addWidget(hint)

    # блок 3b: обновление zapret
    def build_import_card(self):
        frame = self.import_section = self.card('ОБНОВЛЕНИЕ ZAPRET')
        self.folder_view = QLabel('Сборка: '+str(self.root_path))
        self.folder_view.setWordWrap(True)
        self.folder_view.setProperty('muted', True)
        frame.layout().addWidget(self.folder_view)
        self.import_note = self.note(frame)
        self.import_buttons = self.button_row(self.button('Импорт ZIP…', self.pick_zip_import),
                                              self.button('Импорт папки…', self.pick_folder_import),
                                              self.button('Использовать папку без копирования…', self.choose_root))
        frame.layout().addWidget(self.import_buttons)
        self.update_note = self.note(frame)
        self.update_note.setText('Автопроверка обновлений приложения; ручной импорт zapret сохранён.')
        self.update_button = self.button('Проверить обновление приложения', self.check_app_update)
        frame.layout().addWidget(self.update_button)

    # блок 4: game filter
    def build_gamefilter_card(self):
        frame = self.gf_section = self.card('GAME FILTER')
        self.gf_note = self.note(frame)
        rows = QVBoxLayout()
        rows.setContentsMargins(0, 6, 0, 0)
        rows.setSpacing(6)
        self.gf_mode_box = self.combo()
        rows.addWidget(self.field_row('Фильтрация портов:', self.gf_mode_box))
        self.gf_mode_box.addItems(['disabled', 'tcp', 'udp', 'all'])
        self.gf_mode_box.setCurrentText(self.gf_mode)
        self.gf_mode_box.currentTextChanged.connect(lambda v: setattr(self, 'gf_mode', v))
        self.gf_tcp_entry = QLineEdit(self.gf_tcp)
        self.gf_tcp_entry.editingFinished.connect(lambda: setattr(self, 'gf_tcp', self.gf_tcp_entry.text()))
        rows.addWidget(self.field_row('TCP:', self.gf_tcp_entry))
        self.gf_udp_entry = QLineEdit(self.gf_udp)
        self.gf_udp_entry.editingFinished.connect(lambda: setattr(self, 'gf_udp', self.gf_udp_entry.text()))
        rows.addWidget(self.field_row('UDP:', self.gf_udp_entry))
        hint = self.hint('Формат: 1024-65535,80. Широкая фильтрация UDP может мешать играм, '
                         'поэтому по умолчанию стоит tcp.')
        rows.addWidget(hint)
        frame.layout().addLayout(rows)
        self.gf_buttons = self.button_row(self.button('Применить фильтр', self.save_filters),
                                          self.button('Загрузить из сборки', self.load_filters))
        frame.layout().addWidget(self.gf_buttons)

    # блок 5: ipset
    def build_ipset_card(self):
        frame = self.ipset_section = self.card('IPSET-ФИЛЬТР')
        self.ipset_note = self.note(frame)
        rows = QVBoxLayout()
        rows.setContentsMargins(0, 6, 0, 0)
        rows.setSpacing(6)
        self.ipset_mode_box = self.combo()
        rows.addWidget(self.field_row('Режим списка:', self.ipset_mode_box))
        self.ipset_mode_box.addItems(['loaded', 'none', 'any'])
        self.ipset_mode_box.setCurrentText(self.ipset_mode)
        self.ipset_mode_box.currentTextChanged.connect(lambda v: setattr(self, 'ipset_mode', v))
        hint = self.hint('loaded — только адреса из списка · none — отключить IPSet-профили · '
                         'any — все IP (широкая фильтрация)')
        rows.addWidget(hint)
        frame.layout().addLayout(rows)
        self.ipset_buttons = self.button_row(self.button('Применить режим', self.save_filters),
                                             self.button('Обновить IPSet', self.update_ipset))
        frame.layout().addWidget(self.ipset_buttons)

    # блок 6: пользовательские списки
    def build_lists_card(self):
        frame = self.list_section = self.card('ПОЛЬЗОВАТЕЛЬСКИЕ СПИСКИ')
        self.list_note = self.note(frame)
        self.list_note.setText('Один адрес в строке. Запись — только по «Сохранить список», '
                               'с резервной копией.')
        head = QHBoxLayout()
        head.setContentsMargins(0, 4, 0, 0)
        self.ulist_combo = self.combo()
        self.ulist_combo.addItems([C.LIST_LABELS[k] for k in C.LIST_KINDS])
        self.ulist_combo.currentIndexChanged.connect(self.on_ulist_kind)
        head.addWidget(self.ulist_combo)
        head.addStretch(1)
        frame.layout().addLayout(head)
        self.list_view = QPlainTextEdit()
        self.list_view.setObjectName('listView')
        self.list_view.setFixedHeight(130)
        self.list_view.setReadOnly(True)
        frame.layout().addWidget(self.list_view)
        self.list_buttons = self.button_row(self.button('Загрузить список', self.load_ulist),
                                            self.button('Сохранить список', self.save_ulist))
        frame.layout().addWidget(self.list_buttons)

    # блок 7: диагностика
    def build_diagnostics_card(self):
        frame = self.diag_section = self.card('ДИАГНОСТИКА')
        self.diag_note = self.note(frame)
        self.diag_buttons = self.button_row(self.button('Проверить локально', self.run_diagnostics),
                                            self.button('Экспорт отчёта…', self.export_diagnostics))
        frame.layout().addWidget(self.diag_buttons)

    # блок 8: журнал
    def build_log_card(self):
        frame = self.log_section = self.card('ЖУРНАЛ')
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.log_toggle = self.button('Показать журнал', self.toggle_log)
        buttons.addWidget(self.log_toggle)
        buttons.addSpacing(8)
        buttons.addWidget(self.button('Сохранить отчёт…', self.export))
        buttons.addStretch(1)
        self.log_frame = QPlainTextEdit()
        self.log_frame.setObjectName('logView')
        self.log_frame.setReadOnly(True)
        self.log_frame.setFixedHeight(200)
        self.log_frame.hide()
        frame.layout().addWidget(self.log_frame)
        frame.layout().addLayout(buttons)

    def toggle_log(self):
        self.log_open = not self.log_open
        self.log_toggle.setText('Скрыть журнал' if self.log_open else 'Показать журнал')
        self.log_frame.setVisible(self.log_open)
        if self.log_open:
            path = self.DATA/'desk.log'
            self.log_frame.setPlainText(
                path.read_text(encoding='utf-8', errors='replace')[-20000:] if path.exists() else '')
        self.refresh()

    def refresh_footer(self):
        running = self.engine.running()
        self.footer_label.setText('  '+('Движок работает' if running else 'Остановлено')+'    |    '
                                  + str(len(self.paths))+' стратегий    |    '
                                  + ('Администратор' if is_admin() else 'Обычные права')+'    |    '
                                  + self.root_path.name)

    # ─ журнал ────────────────────────────────────────────────────────────────

    def log(self, message):
        line = time.strftime('%H:%M:%S')+'  '+message+'\n'
        try:
            with (self.DATA/'desk.log').open('a', encoding='utf-8') as f:
                f.write(line)
        except OSError:
            pass
        if self.log_open:
            self.log_frame.moveCursor(QTextCursor.End)
            self.log_frame.insertPlainText(line)
            self.log_frame.verticalScrollBar().setValue(
                self.log_frame.verticalScrollBar().maximum())

    # ─ выбор целей проверки ──────────────────────────────────────────────────

    def check_targets(self):
        return {name: url for name, url in TARGETS.items()
                if (self.youtube if name == 'YouTube' else self.discord)}

    # ─ подключение: одна кнопка, один поток состояния ────────────────────────

    def on_main_button(self):
        if self.feature_busy or self.state == 'stopping':
            return
        if self.state == 'connecting':
            self.cancel_connection()
        elif self.state == 'connected' or self.engine.running():
            if not is_admin():
                self.elevate(disconnect=True)
            else:
                self.stop_engine()
        elif not is_admin():
            self.offer_elevation('Для драйвера WinDivert нужны права администратора.')
        else:
            self.start(auto=self.auto)

    def start(self, auto):
        """Запуск стратегия→движок→ожидание→проверка. Долгое — в connect_worker."""
        if self.feature_busy or self.operation_active or self.state in ('connecting', 'stopping'):
            return
        if not is_admin() and self.engine.running():
            self.save()
            self.elevate(strategy=self.selected)
            return
        gen = self.generation + 1
        try:
            self.save()
        except Exception as exc:
            self.show_warning('Настройки', str(exc))
            return
        path = next((p for p in self.paths if p.stem == self.selected), None)
        if path is None:
            self.show_warning('Стратегия', 'Выберите папку сборки и стратегию')
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
        profiles = self.history_profiles(path, targets) if auto else [path]
        self.engine.session_log = self.DATA/'engine.log'
        threading.Thread(target=self.connect_worker,
                         args=(tuple(profiles), gen, targets, self.cancel_event),
                         daemon=True).start()

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

            status, profile, results = connect_profiles(self.engine, profiles, targets, cancel,
                                                        emit, grace=ENGINE_GRACE, keep_running=True)
            if cancel.is_set() or self.stale(gen):
                return
            if status in ('connected', 'running'):
                self.events.put(('connect_done', (gen, results)))
            elif status == 'exhausted':
                self.post_failed(gen, 'exhausted', 'Проверены все выбранные стратегии. '
                                                   'Подключение не подтверждено. Смотрите журнал.'
                                                   ' Если браузер при этом открывал сайты — браузер '
                                                   'может использовать свой DNS, кэш или QUIC; '
                                                   'пометки «DNS» в журнале объясняют расхождение.')
        except Exception as exc:
            self.engine.stop()
            code = ('foreign' if isinstance(exc, ForeignEngineError)
                    else 'admin' if isinstance(exc, PermissionError) else 'error')
            self.post_failed(gen, code, str(exc))
        finally:
            if cancel.is_set() or self.stale(gen):
                self.engine.stop()
            self.events.put(('operation_done', gen))

    def stale(self, gen):
        return gen != self.generation

    def post_failed(self, gen, code, message):
        self.events.put(('connect_failed', (gen, code, message)))

    def history_profiles(self, path, targets):
        """Порядок перебора стратегий. Manual (auto выключен) — только выбранная.
        Auto — выбранная первой среди неизвестных, известные удачные поднимаются
        вперёд по памяти; перебор полный, без пропусков."""
        if not self.auto:
            return [path]
        rest = [p for p in self.paths if p != path]
        candidates = [path]+rest
        try:
            return self.history.order(candidates, targets)
        except Exception:
            return candidates

    def record_history_success(self, stem):
        """Память удачных стратегий. Запись только по фактическому connected после
        всех выбранных проверок; сбой записи не влияет на подключение."""
        path = next((p for p in self.paths if p.stem == stem), None)
        if path is None:
            return
        try:
            self.history.record_success(path, dict(self.active_targets))
            self.log('Память стратегий: '+stem+' записан как удачный.')
        except Exception as exc:
            self.log('Память стратегий: не сохранено ('+str(exc)
                     +') — подключение не затронуто.')

    def cancel_connection(self):
        self.pending_strategy = None
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
        if not self.engine.running() or not self.engine.ready:
            self.set_state('idle', 'Подключение не подтверждено. Смотрите журнал.')
            return
        self.last_check = time.strftime('%H:%M:%S')
        self.show_health(results)
        self.rotation.reset(self.selected)
        self.next_check = time.monotonic()+self.interval_seconds()
        self.set_state('connected', ('Подключено' if healthy else 'Движок работает, проверка не пройдена')+' · '+self.selected)
        self.log('Zapret включён: WinDivert готов. '+('HTTPS-проверки пройдены' if healthy else 'Доступность сервисов не подтверждена'))
        if healthy:
            self.record_history_success(self.selected)
        self.settings_changed()

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

    # ─ главный цикл очереди ──────────────────────────────────────────────────

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
                    self.apply_selection_event(*payload)
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
                elif kind in ('app_update', 'app_update_error'):
                    self._update_busy = False
                    self.update_button.setEnabled(True)
                    if kind == 'app_update_error':
                        self.update_note.setText('Не удалось проверить обновления: '+payload)
                    elif payload is None:
                        self.update_note.setText('Новых стабильных версий нет. Установлена '+C.VERSION)
                    else:
                        self.update_note.setText('Доступна версия '+payload['tag'])
                        if QMessageBox.question(self, 'Обновление zapret GUI',
                                'Доступна '+payload['tag']+'. Открыть страницу загрузки?') == QMessageBox.Yes:
                            from PySide6.QtGui import QDesktopServices
                            from PySide6.QtCore import QUrl
                            QDesktopServices.openUrl(QUrl(payload['url']))
                elif kind == 'filters_loaded':
                    self.apply_filters_loaded(payload)
                elif kind == 'feature_finished':
                    self.feature_busy = False
                    self.refresh()
                    if self.closing:
                        self.close()
                elif kind == 'filters_saved':
                    self.apply_filters_loaded(payload)
                    self.feature_notes['gf'] = 'Фильтры применены и сохранены в сборке.'
                    self.log('Фильтры применены: '+str(payload))
                elif kind == 'ulist_loaded':
                    self.apply_ulist_loaded(*payload)
                elif kind == 'diag_done':
                    self.apply_diag(payload)
                elif kind == 'ulist_saved':
                    kind_name, strings, same = payload
                    self.feature_notes['list'] = ('Список сохранён ('+str(strings)+' строк); '
                                                  'резервная копия создана бэкендом.')
                    self.log('Список '+C.LIST_LABELS[kind_name]+' сохранён: '+str(strings)+' строк.'
                             + ('' if same else ' Прочитанный текст отличается — проверьте журналы бэкенда.'))
                    QTimer.singleShot(1, self.load_ulist)
                elif kind == 'ipset_updated':
                    self.feature_notes['ipset'] = 'IPSet обновлён вручную: '+(str(payload) or '0')+' записей.'
                    self.log('IPSet обновлён: '+str(payload)+' записей.')
                    QTimer.singleShot(1, self.load_filters)
                elif kind == 'diag_exported':
                    self.feature_notes['diag'] = 'Отчёт сохранён: '+str(payload)
                    self.log('Экспорт диагностики: '+str(payload))
                elif kind == 'feature_error':
                    section, message = payload
                    self.feature_notes[section] = message
                    self.log(message)
                    self.refresh()
                elif kind == 'operation_done':
                    if not self.stale(payload):
                        self.operation_active = False
                        if self.closing:
                            self.close()
                            return
                elif kind == 'import_inspected':
                    zip_path, report, dest_root, previous, initial_note = payload
                    self.feature_busy = False
                    self.feature_notes['import'] = initial_note
                    self.refresh()
                    if self.ask_import_confirm(report):
                        self.log('Импорт: '+str(zip_path))
                        self.start_import_install(zip_path, dest_root, previous, report,
                                                  initial_note)
                elif kind == 'import_rejected':
                    self.feature_busy = False
                    self.feature_notes['import'] = payload
                    self.log(payload)
                    self.refresh()
                elif kind == 'import_done':
                    self.apply_import_done(*payload)
                elif kind == 'folder_inspected':
                    self.apply_folder_inspected(*payload)
                elif kind == 'folder_user_done':
                    self.feature_busy = False
                    self.apply_folder_user_done(*payload)
                    self.refresh()
                elif kind == 'folder_build_done':
                    self.feature_busy = False
                    self.apply_folder_build_done(*payload)
                elif kind == 'stopped':
                    self.operation_active = False
                    self.busy = False
                    self.set_state('idle', 'Остановлено')
                    if self.closing:
                        self.close()
                        return
                    if self.pending_strategy:
                        self.selected = self.pending_strategy
                        self.pending_strategy = None
                        self.start(auto=False)
        except queue.Empty:
            pass
        if self.state == 'connected' and not self.engine.running():
            # Движок завершился сам: как в app.py — рестарт при авто или честный статус.
            if self.auto:
                self.start(auto=True)
            else:
                self.set_state('idle', 'Движок завершился · подробности в engine.log')
        if self.state == 'connected' and self.engine.running() and not self.busy \
                and time.monotonic() >= self.next_check:
            self.check()
        self.refresh_footer()

    def apply_selection_event(self, gen, event, value):
        if self.stale(gen) or self.state != 'connecting':
            return
        if event == 'attempt':
            number, total, stem = value
            self.attempt = number
            self.selected = stem
            combo = self.strategy_combo
            if combo.findText(stem) >= 0:
                combo.blockSignals(True)
                combo.setCurrentText(stem)
                combo.blockSignals(False)
            self.set_stage(f'Подбор {number}/{total} · {stem} · ожидание готовности WinDivert (до 20 с)…')
            self.log(self.stage)
        elif event == 'ready':
            self.set_stage('Подбор · '+value+' · WinDivert готов, стабилизация и проверка сети…')
        elif event == 'offline':
            self.set_stage(value)
            self.log(value)
        elif event == 'profile_error':
            self.log('Стратегия не запустилась: '+value)
        elif event == 'probe':
            self.last_check = time.strftime('%H:%M:%S')
            self.show_health(value)
            self.log('Проверка: '+', '.join(k+(' OK' if v[0] else ' FAIL: '+v[2]) for k, v in value.items()))

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
        if self.state == 'connected':
            stage = ('Подключено' if healthy else 'Движок работает, проверка не пройдена')+' · '+self.selected
            self.stage = stage
        if self.auto and self.engine.running() and self.state == 'connected':
            self.handle_rotation(self.rotation.observe(online, healthy))
        self.next_check = time.monotonic()+self.interval_seconds()
        self.refresh()

    def handle_rotation(self, action):
        if action in [p.stem for p in self.paths]:
            self.log('Восстановление подключения: начинаем новый подбор с '+action)
            self.selected = action
            combo = self.strategy_combo
            if combo.findText(action) >= 0:
                combo.blockSignals(True)
                combo.setCurrentText(action)
                combo.blockSignals(False)
            self.start(auto=True)
        elif action == 'offline':
            self.set_stage('Нет ответов контрольных узлов. Ожидание сети; перебор приостановлен.')

    def interval_seconds(self):
        try:
            value = int(self.interval)
            return value if 15 <= value <= 300 else 30
        except ValueError:
            return 30

    def show_health(self, results):
        for name in ('YouTube', 'Discord'):
            value = results.get(name)
            if not (self.active_targets or self.check_targets()).get(name):
                text, color = 'Отключено', C.MUTED
            elif value and value[0]:
                text, color = 'Доступен · '+str(value[1])+' мс', C.OK
            else:
                text, color = 'Нет ответа', C.WARN
            health = self.health[name]
            health.setText(text)
            health.setStyleSheet('color: '+color)
        online = any(value[0] for value in results.values())
        internet = self.health['Интернет']
        internet.setText('Доступен' if online else 'Нет ответа')
        internet.setStyleSheet('color: '+(C.OK if online else C.BAD))

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
        self.strategy_combo.setEnabled(not (self.feature_busy or self.closing))
        self.main_button.setEnabled(not (self.state == 'stopping' or self.feature_busy))
        if self.state == 'connecting':
            self.progress.show()
            text, color = '●  Подключение…', C.WARN
        else:
            self.progress.hide()
            if self.state == 'stopping':
                text, color = '●  Остановка…', C.WARN
            elif not running:
                text, color = '●  Остановлено', C.TEXT
            elif self.stage.startswith('Подключено'):
                text, color = '●  Подключено', C.OK
            else:
                text, color = '●  Движок работает · подключение не подтверждено', C.WARN
        self.status_line.setText(text)
        self.status_line.setStyleSheet('color: '+color)
        self.main_button.setText(
            'Отмена' if self.state == 'connecting' else
            'Отключить' if running else 'Подключить')
        self.main_button.setProperty('kind', 'cancel' if self.state == 'connecting' else 'accent')
        self.main_button.style().unpolish(self.main_button)
        self.main_button.style().polish(self.main_button)
        parts = []
        if self.stage:
            parts.append(self.stage)
        parts.append('Стратегия: '+(self.selected or '—'))
        if running and self.engine.process:
            parts.append('PID: '+str(self.engine.process.pid))
        parts.append('Последняя проверка: '+self.last_check+(' · проверяется…' if self.busy else ''))
        if not is_admin():
            parts.append('Права: обычные · при подключении будет предложен перезапуск с UAC')
        self.details.setText('\n'.join(parts))
        self.folder_view.setText('Сборка: '+str(self.root_path))
        self.sync_feature_lock()
        self.refresh_footer()

    def sync_feature_lock(self):
        """Отблокировка/блокировка настроек сборки (как ui_widgets.feature_lock в Tk)."""
        locked, reason = C.feature_lock(self.state, self.operation_active or self.feature_busy,
                                        self.engine.running())
        self.gf_note.setText(reason if locked else self.feature_notes['gf'])
        self.ipset_note.setText(reason if locked else self.feature_notes['ipset'])
        self.list_note.setText(reason if locked else
                               (self.feature_notes['list'] or
                                'Один адрес в строке. Запись — по кнопке, с резервной копией.'))
        editable = not locked
        for widget in (self.gf_tcp_entry, self.gf_udp_entry, self.gf_buttons,
                       self.ipset_buttons, self.list_buttons, self.list_view,
                       self.import_buttons):
            widget.setEnabled(editable)
        self.import_note.setText(reason if locked else
                                 (self.feature_notes['import'] or IMPORT_HINT))

    def offer_elevation(self, reason):
        """Предлагает перезапуск GUI с повышением прав. UAC подтверждает пользователь."""
        try:
            self.save()
        except Exception as exc:
            self.show_warning('Настройки', str(exc))
            return
        self.elevate()

    def elevate(self, disconnect=False, strategy=None):
        target, args, workdir = _rp.elevate_target()
        if strategy:
            args = [arg for arg in args if arg != '--connect'] + ['--select-strategy='+strategy]
        if disconnect:
            args = [arg for arg in args if arg != '--connect'] + ['--disconnect']
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
            self.close_and_delete()
        else:
            self.set_state('idle', 'Повышение прав отменено или недоступно.')

    def close_and_delete(self):
        self.exit_requested = True
        self.ticker.stop()
        self.close()
        self.deleteLater()

    # ─ выбор стратегии и сборки ──────────────────────────────────────────────

    def on_strategy_select(self, value):
        if not value or self.closing or self.exit_requested:
            return
        self.selected = value
        if self.operation_active or self.state in ('connecting', 'stopping'):
            self.pending_strategy = value
            self.stop_engine()
        elif self.engine.running():
            self.log('Смена стратегии: '+value)
            self.start(auto=False)
        else:
            self.settings_changed()

    def on_auto_changed(self, checked):
        self.auto = checked
        self.settings_changed()

    def on_youtube_changed(self, checked):
        self.youtube = checked
        self.settings_changed()

    def on_discord_changed(self, checked):
        self.discord = checked
        self.settings_changed()

    def on_autostart_changed(self, checked):
        # update() возвращает факт: соответствует ли состояние реестра желаемому.
        if _autostart.update(checked):
            self.log('Автозапуск: включён — запуск при входе в Windows (сразу в трей)'
                     if checked else 'Автозапуск: выключен')
            return
        # Реестр не поддался — вернуть переключатель в фактическое состояние.
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(_autostart.read())
        self.autostart_check.blockSignals(False)
        self.show_warning('Автозапуск', 'Не удалось обновить запись автозапуска в реестре '
                          r'(HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run)')

    def on_interval_changed(self):
        self.interval = self.interval_edit.text().strip()
        self.settings_changed()

    def choose_root(self):
        if self.feature_busy or self.operation_active or self.engine.running() or self.state == 'stopping':
            self.show_info('Сборка', 'Сначала отключите подключение и дождитесь остановки.')
            return
        folder = QFileDialog.getExistingDirectory(self, 'Папка сборки (bin/winws.exe и general*.bat)')
        if not folder:
            return
        path = Path(folder)
        if not (path/'bin/winws.exe').exists() or not strategies(path):
            self.show_warning('Папка', 'Нужна папка с bin/winws.exe и general*.bat')
            return
        self.root_path = path
        self.paths = strategies(path)
        self.selected = self.paths[0].stem
        self.rotation = Rotation([p.stem for p in self.paths])
        combo = self.strategy_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([p.stem for p in self.paths])
        combo.setCurrentText(self.selected)
        combo.blockSignals(False)
        self.settings_changed()
        self.log('Выбрана сборка '+path.name+' · стратегий: '+str(len(self.paths)))
        self.load_filters()

    # ─ импорт ZIP со сборкой zapret ──────────────────────────────────────────

    def import_dest_root(self):
        """Куда ставить импортированные сборки: runtime (EXE) или папка проекта."""
        if _rp.is_frozen():
            return _rp.runtime_root()
        return _rp.exe_dir().parent

    def pick_zip_import(self):
        if self.feature_busy or self.operation_active or self.engine.running() \
                or self.state in ('connecting', 'stopping'):
            self.feature_notes['import'] = 'Импорт недоступен: остановите движок и операции.'
            self.log('Импорт не начат: движок или операция ещё активны.')
            self.refresh()
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Архив zapret (ZIP)', '',
                                                  'ZIP (*.zip);;Все файлы (*)')
        if not filename:
            return
        initial_note = self.feature_notes['import']
        self.feature_busy = True
        self.refresh()

        def work():
            try:
                report = _zi.inspect_zip(Path(filename))
                self.events.put(('import_inspected',
                                 (Path(filename), report, self.import_dest_root(),
                                  self.root_path if self.root_path.is_dir() else None,
                                  initial_note)))
            except Exception as exc:
                self.events.put(('import_rejected', 'Архив не принят: '+str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def ask_import_confirm(self, report):
        lines = [f'Распакованный объём: {report["unpacked_bytes"]/1048576:.0f} МБ, '
                 f'записей: {report["entry_count"]}.',
                 f'Стратегий: {len(report["strategies"])}.',
                 'Пользовательские списки и фильтры будут сохранены.',
                 'Движок должен быть остановлен; текущая сборка не изменяется '
                 'до успешной проверки архива.']
        answer = QMessageBox.question(self, 'Импорт сборки: '+report['build_name'],
                                      '\n'.join(lines),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def start_import_install(self, zip_path, dest_root, previous, report, initial_note):
        self.feature_busy = True
        self.refresh()

        def work():
            try:
                install = _zi.import_zip(zip_path, dest_root, previous=previous,
                                         log=lambda m: self.events.put(('log', m)))
                strategies_ok = strategies(install)
                self.events.put(('import_done', (install, report, strategies_ok)))
            except Exception as exc:
                self.events.put(('feature_error',
                                 ('import', 'Импорт не выполнен: '+(str(exc) or repr(exc)))))
            finally:
                self.events.put(('feature_finished', None))
        threading.Thread(target=work, daemon=True).start()

    def apply_import_done(self, install, report, strategies_ok):
        self.apply_build_change(install, report)
        self.feature_notes['import'] = ('Импортирована сборка '+install.name
                                        + f' (стратегий {len(strategies_ok)}); '
                                          'пользовательских файлов перенесено: '
                                          + str(report.get('moved', 0))+'.')
        self.refresh()

    def apply_build_change(self, install, report=None):
        """Пересборка списка стратегий по новой сборке (как после «Обзор…»)."""
        kept = self.selected
        self.root_path = install
        self.paths = strategies(install)
        self.selected = kept if kept in [p.stem for p in self.paths] \
            else (self.paths[0].stem if self.paths else '')
        self.rotation = Rotation([p.stem for p in self.paths])
        combo = self.strategy_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([p.stem for p in self.paths])
        if self.selected:
            combo.setCurrentText(self.selected)
        combo.blockSignals(False)
        self.settings_changed()
        if report:
            self.log('Импорт завершён: '+report['build_name'])
        else:
            self.log('Сборка выбрана: '+install.name+' · стратегий: '+str(len(self.paths)))
        self.load_filters()

    # ─ импорт папки (backend zapret_import, задача d3488a3f) ──────────────────

    def folder_api_ready(self):
        """Backend импорта папки делает Agatha; кнопка включается, как только
        API появится в zapret_import. Backend здесь не дублируется."""
        return all(hasattr(_zi, name) for name in FOLDER_API)

    def pick_folder_import(self):
        if self.feature_busy or self.operation_active or self.engine.running() \
                or self.state in ('connecting', 'stopping'):
            self.feature_notes['import'] = 'Импорт недоступен: остановите движок и операции.'
            self.log('Импорт не начат: движок или операция ещё активны.')
            self.refresh()
            return
        if not self.folder_api_ready():
            self.feature_notes['import'] = ('Импорт папки: backend импорта папки ещё не '
                                            'подключён; импорт ZIP уже работает.')
            self.log('Импорт папки не начат: API (inspect_folder/import_folder/'
                     'import_user_folder) ещё не появился в zapret_import.')
            self.refresh()
            return
        folder = QFileDialog.getExistingDirectory(self, 'Папка с zapret или '
                                                        'пользовательскими доменами')
        if not folder:
            return
        initial_note = self.feature_notes['import']
        self.feature_busy = True
        self.refresh()

        def work():
            try:
                report = _zi.inspect_folder(Path(folder))
                self.events.put(('folder_inspected',
                                 (Path(folder), report, self.import_dest_root(),
                                  self.root_path if self.root_path.is_dir() else None,
                                  initial_note)))
            except Exception as exc:
                self.events.put(('import_rejected', 'Папка не принята: '+str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def apply_folder_inspected(self, source, report, dest_root, previous, initial_note):
        self.feature_busy = False
        kind = report.get('type') if isinstance(report, dict) else None
        self.refresh()
        if kind not in ('user', 'build'):
            self.feature_notes['import'] = ('Импорт папки отменён: тип папки не распознан '
                                            'backend (ожидались build или user).')
            self.log('Импорт папки отменён: backend не распознал тип папки '
                     +source.name+' ('+str(report)+').')
            return
        if kind == 'user':
            prompt = ('Папка со списками пользователя: '+source.name+'.\n\n'
                      'Распознанные файлы: '+', '.join(report.get('kinds', []))
                      +'.\nПроверка доменов/IP, резервная копия и откат выполняются '
                       'backend; списки будут записаны в текущую сборку.')
        else:
            prompt = ('Папка полной сборки zapret: '+source.name+' (стратегий: '
                      +str(len(report.get('strategies', [])))+').\n\n'
                      'Проверка структуры и безопасности, резервное переименование и '
                      'откат выполняются backend. Движок должен быть остановлен; '
                      'текущая сборка не меняется до подтверждения; пользовательские '
                      'списки и фильтры переносятся из текущей сборки.')
        if QMessageBox.question(self, 'Импорт папки: '+report.get('build_name', source.name),
                                prompt,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.log('Импорт: '+str(source))
        self.feature_busy = True
        self.refresh()

        if kind == 'user':
            def work():
                try:
                    result = _zi.import_user_folder(source, self.root_path,
                                                    log=lambda m: self.events.put(('log', m)))
                    self.events.put(('folder_user_done', (source, result)))
                except Exception as exc:
                    self.events.put(('feature_error',
                                     ('import', 'Импорт папки не выполнен: '
                                                +(str(exc) or repr(exc)))))
                finally:
                    self.events.put(('feature_finished', None))
        else:
            def work():
                try:
                    install = _zi.import_folder(source, dest_root, previous=previous,
                                                log=lambda m: self.events.put(('log', m)))
                    strategies_ok = strategies(install)
                    self.events.put(('folder_build_done', (install, dict(report), strategies_ok)))
                except Exception as exc:
                    self.events.put(('feature_error',
                                     ('import', 'Импорт папки не выполнен: '
                                                +(str(exc) or repr(exc)))))
                finally:
                    self.events.put(('feature_finished', None))
        threading.Thread(target=work, daemon=True).start()

    def folder_summary(self, result):
        if isinstance(result, dict):
            parts = []
            for key in ('build_name', 'name', 'moved', 'kinds', 'strategies'):
                if key in result:
                    parts.append(str(key)+'='+str(result[key]))
            return ', '.join(parts) if parts else ''
        return ''

    def apply_folder_user_done(self, source, result):
        detail = self.folder_summary(result)
        self.feature_notes['import'] = ('Пользовательские файлы импортированы из '
                                        +source.name+((' ('+detail+')') if detail else '')
                                        +'; резервная копия создана backend.')
        self.log('Импорт папки завершён: пользовательские файлы из '+source.name
                 +((' · '+detail) if detail else ''))
        QTimer.singleShot(1, self.load_filters)

    def apply_folder_build_done(self, install, report, strategies_ok):
        self.apply_build_change(install, report=None)
        moved = report.get('moved', 0)
        self.feature_notes['import'] = ('Импортирована папка '+install.name
                                        + f' (стратегий {len(strategies_ok)}); '
                                          'пользовательских файлов перенесено: '+str(moved)+'.')
        self.refresh()

    def show_warning(self, title, message):
        QMessageBox.warning(self, title, message)
        self.log(title+': '+message)

    def show_info(self, title, message):
        QMessageBox.information(self, title, message)

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
        return C.feature_lock(self.state, self.operation_active or self.feature_busy,
                              self.engine.running())

    def launch_feature(self, function, args, writing=False):
        if self.feature_busy:
            return
        self.feature_busy = True
        self.refresh()
        def work():
            try:
                if writing and foreign_engine():
                    self.events.put(('feature_error',
                                     ('gf', 'Другой winws.exe работает. Остановите его '
                                            'перед изменением сборки.')))
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
        self.launch_feature(self._load_filters_worker, (mod, self.root_path))

    def _load_filters_worker(self, mod, root):
        try:
            data = mod.read_filters(root)
        except Exception as exc:
            self.events.put(('feature_error', ('gf', 'Чтение фильтров не удалось: '+str(exc))))
            return
        self.events.put(('filters_loaded', data))

    def apply_filters_loaded(self, data):
        self.gf_mode = str(data.get('game_mode', self.gf_mode))
        self.gf_tcp = str(data.get('tcp', self.gf_tcp))
        self.gf_udp = str(data.get('udp', self.gf_udp))
        self.ipset_mode = str(data.get('ipset_mode', self.ipset_mode))
        for box, value in ((self.gf_mode_box, self.gf_mode), (self.ipset_mode_box, self.ipset_mode)):
            if box.findText(value) >= 0:
                box.blockSignals(True)
                box.setCurrentText(value)
                box.blockSignals(False)
        self.gf_tcp_entry.setText(self.gf_tcp)
        self.gf_udp_entry.setText(self.gf_udp)
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
            payload = C.game_filter_payload(self.gf_mode_box.currentText(), self.gf_tcp_entry.text(),
                                            self.gf_udp_entry.text(), self.ipset_mode_box.currentText())
        except ValueError as exc:
            self.log('Фильтры не применены: '+str(exc))
            return
        if self.gf_mode_box.currentText() != self.gf_mode:
            self.gf_mode = self.gf_mode_box.currentText()
        if self.ipset_mode_box.currentText() != self.ipset_mode:
            self.ipset_mode = self.ipset_mode_box.currentText()
        self.launch_feature(self._save_filters_worker, (mod, self.root_path, payload), writing=True)

    def _save_filters_worker(self, mod, root, payload):
        try:
            mod.write_filters(root, **payload)
            data = mod.read_filters(root)   # показать фактический результат
        except Exception as exc:
            self.events.put(('feature_error', ('gf', 'Запись фильтров не удалась: '+str(exc))))
            return
        self.events.put(('filters_saved', data))

    def update_ipset(self):
        """Ручное обновление IPSet (сеть и запись — вне GUI-потока)."""
        mod = self.require_backend('IPSet')
        if mod is None:
            return
        locked, reason = self.feature_locked()
        if locked:
            self.log('Обновление IPSet отложено: '+reason)
            return
        self.launch_feature(self._update_ipset_worker, (mod, self.root_path), writing=True)

    def _update_ipset_worker(self, mod, root):
        try:
            count = mod.update_ipset(root)
        except Exception as exc:
            self.events.put(('feature_error', ('ipset', 'Обновление IPSet не удалось: '+str(exc))))
            return
        self.events.put(('ipset_updated', count))

    def on_ulist_kind(self, index):
        self.ulist_kind = C.LIST_KINDS[index]
        self.load_ulist()

    def load_ulist(self):
        """Чтение выбранного пользовательского списка (без записи)."""
        mod = self.require_backend('Списки')
        if mod is None:
            return
        self.launch_feature(self._load_ulist_worker, (mod, self.root_path, self.ulist_kind))

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
        self.list_view.setPlainText(text)
        self.feature_notes['list'] = 'Список прочитан из сборки; правки ещё не сохранены.'
        self.log('Список '+C.LIST_LABELS[kind]+' прочитан ('+str(len(text.splitlines()))+' строк).')
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
        text = self.list_view.toPlainText().rstrip('\n')
        ok, reason = C.validate_list(kind, text)
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
        self.events.put(('ulist_saved', (kind, len(checked.splitlines()),
                                         checked.rstrip('\n') == text.rstrip('\n'))))

    def run_diagnostics(self):
        """Локальная диагностика без мутаций; результат — в блоке и в журнале."""
        mod = self.require_backend('Диагностика')
        if mod is None:
            return
        self.launch_feature(self._run_diag_worker, (mod, self.root_path))

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
        self.diag_note.setText('\n'.join(summary))
        self.log('Диагностика: '+str(info))
        self.refresh()

    def export_diagnostics(self):
        """Диагностика + настройки + журналы в файл по выбору пользователя."""
        mod = self.features()
        if mod is None:
            self.log('Экспорт: backend zapret_features ещё не подключён; журнал доступен '
                     'кнопкой «Сохранить отчёт…».')
            return
        filename, _ = QFileDialog.getSaveFileName(self, 'Экспорт отчёта', 'zapret-diagnostics.json',
                                                  'JSON (*.json)')
        if not filename:
            return
        self.launch_feature(self._export_diag_worker,
                            (mod, self.root_path, Path(filename), self.state,
                             self.last_check, self.selected))

    def _export_diag_worker(self, mod, root, filename, state_note, last_check, strategy):
        try:
            info = mod.diagnostics(root)
            report = {'диагностика': info,
                      'состояние': {'статус': state_note, 'последняя_проверка': last_check,
                                    'стратегия': strategy},
                      'журналы': {name: path.read_text(encoding='utf-8', errors='replace')[-40000:]
                                  for name, path in
                                  (('desk.log', self.DATA/'desk.log'),
                                   ('engine.log', self.DATA/'engine.log'))
                                  if path.exists()}}
            filename.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as exc:
            self.events.put(('feature_error', ('diag', 'Экспорт не удался: '+str(exc))))
            return
        self.events.put(('diag_exported', str(filename)))

    # ─ экспорт отчёта ────────────────────────────────────────────────────────

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self, 'Отчёт', 'zapret-report.txt', 'Text (*.txt)')
        if not filename:
            return
        report = ('zapret GUI '+C.VERSION+' Qt\nСборка: '+str(self.root_path)
                  +'\nСтратегия: '+self.selected+'\n\n')
        for name in ('desk.log', 'engine.log'):
            path = self.DATA/name
            if path.exists():
                report += '\n--- '+name+' ---\n'+path.read_text(encoding='utf-8', errors='replace')[-60000:]
        try:
            Path(filename).write_text(report, encoding='utf-8')
        except OSError as exc:
            self.show_warning('Отчёт', str(exc))

    # ─ завершение ────────────────────────────────────────────────────────────

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('zapret GUI — работает в фоне')
        menu = QMenu(self)
        menu.addAction('Открыть zapret GUI', self.restore_window)
        menu.addAction('Выйти — оставить zapret включённым', self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_window()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_exit(self):
        self.exit_requested = True
        self.pending_strategy = None
        self.close()

    def check_app_update(self):
        if getattr(self, '_update_busy', False):
            return
        self._update_busy = True
        self.update_button.setEnabled(False)
        self.update_note.setText('Проверка обновлений…')
        def worker():
            try:
                from app_updates import latest_release
                result = latest_release(C.VERSION)
                self.events.put(('app_update', result))
            except Exception as exc:
                self.events.put(('app_update_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def closeEvent(self, event):
        if self.tray is not None and not self.exit_requested:
            self.hide()
            event.ignore()
            return
        if self.feature_busy:
            self.closing = True
            event.ignore()
            return
        try:
            self.save()
        except Exception:
            pass
        if self.operation_active or self.state == 'stopping':
            self.closing = True
            event.ignore()
            # Finish the current bounded launch/check before detaching.
        else:
            self.engine.detach()
            event.accept()
            if self.tray is not None:
                self.tray.hide()
            # Closing an already hidden window does not emit lastWindowClosed.
            # Quit explicitly after all engine/file operations have finished.
            if self.exit_requested:
                QApplication.instance().quit()


# ─ стили ──────────────────────────────────────────────────────────────────────

def stylesheet():
    return f"""
* {{
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QMainWindow {{ background: {C.BG}; }}
#windowHolder {{ background: {C.BG}; }}
#scrollBody {{ background: {C.BG}; }}
QFrame#topbar {{ background: {C.CARD}; }}
QLabel {{ background: transparent; color: {C.TEXT}; }}
QLabel#statusLine {{
    background: transparent; color: {C.TEXT};
    font-size: 12.5pt; font-weight: 600;
}}
QLabel[cardTitle="true"], QLabel[cardTitle="1"] {{
    color: {C.MUTED}; font-size: 8.5pt; font-weight: bold; letter-spacing: 1px;
}}
QLabel[muted="true"] {{ color: {C.MUTED}; }}

QFrame[card="true"], QFrame[card="1"] {{
    background: {C.CARD};
    border: 1px solid {C.LINE};
    border-radius: {C.RADIUS}px;
}}
QFrame#statusCard {{
    background: {C.CARD};
    border: 1px solid {C.ACCENT};
    border-radius: {C.RADIUS}px;
}}

QPushButton {{
    background: {C.SUNKEN}; color: {C.TEXT};
    border: 1px solid {C.LINE};
    border-radius: {C.RADIUS_SMALL}px;
    padding: 8px 14px;
}}
QPushButton:hover {{ background: {C.LINE}; border-color: {C.ACCENT}; }}
QPushButton:disabled {{ color: rgba(230,233,239,0.35); background: {C.SUNKEN}; border-color: {C.LINE}; }}
QPushButton[kind="accent"] {{
    background: {C.ACCENT}; color: #ffffff; font-weight: 600; border: none;
    padding: 10px 22px;
}}
QPushButton[kind="accent"]:hover {{ background: {C.ACCENT_HOVER}; }}
QPushButton[kind="accent"]:disabled {{ background: {C.ACCENT}; color: rgba(255,255,255,0.5); }}
QPushButton[kind="cancel"] {{
    background: #5c2322; color: #f4b9b4; font-weight: 600; border: none;
    padding: 10px 22px;
}}
QPushButton[kind="cancel"]:hover {{ background: #7a2d2b; }}
QPushButton[kind="cancel"]:disabled {{ color: rgba(244,185,180,0.5); }}

QComboBox {{
    background: {C.SUNKEN}; color: {C.TEXT};
    border: 1px solid {C.LINE};
    border-radius: {C.RADIUS_SMALL}px;
    padding: 6px 10px;
    min-width: 210px;
}}
QComboBox:hover {{ border-color: {C.ACCENT}; }}
QComboBox:disabled {{ color: rgba(230,233,239,0.35); }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid {C.MUTED}; margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {C.CARD}; color: {C.TEXT};
    border: 1px solid {C.LINE};
    selection-background-color: {C.ACCENT};
    selection-color: white;
    outline: none;
}}

QLineEdit, QPlainTextEdit {{
    background: {C.SUNKEN}; color: {C.TEXT};
    border: 1px solid {C.LINE};
    border-radius: {C.RADIUS_SMALL}px;
    padding: 6px 8px;
    selection-background-color: {C.ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {C.ACCENT}; }}
QLineEdit:disabled {{ color: rgba(230,233,239,0.35); }}
QPlainTextEdit#listView, QPlainTextEdit#logView {{
    font-family: 'Consolas'; font-size: 9pt;
    background: #181b21;
}}
QPlainTextEdit:disabled {{ color: rgba(230,233,239,0.35); }}

QCheckBox {{ color: {C.TEXT}; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
/* Заливку и галочку рисует CheckStyle (QPainter), чтобы белая галочка была
   явно видна и в проекте, и в frozen EXE без файлов ресурсов. */
QCheckBox:disabled {{ color: rgba(230,233,239,0.35); }}

QProgressBar {{
    background: {C.SUNKEN}; border: none; border-radius: 3px;
}}
QProgressBar::chunk {{ background: {C.ACCENT}; border-radius: 3px; }}

QScrollArea {{ background: {C.BG}; }}
QScrollBar:vertical {{
    background: {C.BG}; width: 8px; margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {C.LINE}; border-radius: 3px; min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: {C.MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal, QScrollBar::handle:horizontal {{ height: 0; }}

QMessageBox {{ background: {C.CARD}; }}
QMessageBox QLabel {{ color: {C.TEXT}; }}
QToolTip {{ background: {C.CARD}; color: {C.TEXT}; border: 1px solid {C.LINE}; }}
"""


def main():
    from startup_admin import ensure_admin
    if not ensure_admin():
        return 0
    app = QApplication(sys.argv)
    from single_instance import SingleInstance
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    app.setStyleSheet(stylesheet())
    font = QFont('Segoe UI', 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    window = App()
    instance.listen(window.restore_window)
    # Автозапуск (--tray): поднимаемся сразу в трей, окно не открываем.
    # Без трея прятать окно нельзя — приложение стало бы «невидимым».
    if '--tray' not in sys.argv or window.tray is None:
        window.show()
    QTimer.singleShot(3000, window.check_app_update)
    QTimer.singleShot(150, window.apply_titlebar_theme)
    app.exec()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        error = traceback.format_exc()
        try:
            (_rp.data_dir()/'startup-error.log').write_text(error, encoding='utf-8')
        except OSError:
            pass
        raise
