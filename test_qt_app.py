"""Проверки Qt-версии Zapret Desk без открытия окна на общем рабочем столе.

QT_QPA_PLATFORM=offscreen, отдельные временные пользовательские данные,
имитированный controller (без сети и процессов), временные копии сборки
для операций фильтров. Реальная сборка и текущее подключение не затрагиваются.
"""
from __future__ import annotations
import ctypes
import json
import os
import sys
import tempfile
import time
from unittest import mock
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

import core  # noqa: E402
import qt_common as C  # noqa: E402
import zapret_features as features  # noqa: E402
import zapret_import as _zi  # noqa: E402


def fake_build(base: Path, count=2) -> Path:
    """Временная «сборка»: bin/winws.exe (заглушка), general bat, utils, lists."""
    root = base/'zapret-test'
    (root/'bin').mkdir(parents=True, exist_ok=True)
    (root/'utils').mkdir(parents=True, exist_ok=True)
    (root/'lists').mkdir(parents=True, exist_ok=True)
    (root/'bin'/'winws.exe').write_bytes(b'MZ')
    for name in ('WinDivert.dll', 'WinDivert64.sys', 'cygwin1.dll'):
        (root/'bin'/name).write_bytes(b'test fixture')
    for i in range(1, count+1):
        name = 'general'+('%d' % i if i > 1 else '')+'.bat'
        (root/name).write_text(
            'start "zapret" /min "%BIN%winws.exe" '
            f'--wf-tcp=443\n --wf-tcp-out=443\n --wf-udp=443\n --wf-icmp-blk=3\n',
            encoding='utf-8')
    features._commit({root/'utils'/'game_filter.enabled': 'mode=disabled\ntcp=1024-65535\nudp=1024-65535\n',
                      root/'lists'/'ipset-all.txt': '1.1.1.1/32\n1.0.0.1/32\n'})
    return root


class QtCase(unittest.TestCase):
    """Общий контекст: offscreen QApplication и временные пользовательские данные."""

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory()
        cls._saved_localappdata = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = cls._tmp.name

    @classmethod
    def tearDownClass(cls):
        if cls._saved_localappdata is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = cls._saved_localappdata
        cls._tmp.cleanup()

    def drain(self, app, cycles=6, wait=0.02):
        for _ in range(cycles):
            time.sleep(wait)
            app.tick()
            self._app.processEvents()

    def make_app(self, argv=None):
        import app_qt
        old_argv = sys.argv
        sys.argv = ([old_argv[0]]+list(argv)) if argv else [old_argv[0]]
        try:
            return app_qt.App(data_dir=Path(self._tmp.name))
        finally:
            sys.argv = old_argv


class TestPureLogic(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(C.validate_ports('1024-65535,80'), (True, ''))
        self.assertFalse(C.validate_ports('0')[0])
        self.assertFalse(C.validate_ports('65536')[0])
        self.assertFalse(C.validate_ports('100-90')[0])
        self.assertFalse(C.validate_ports('')[0])

    def test_payload(self):
        with self.assertRaises(ValueError):
            C.game_filter_payload('bad', '80', '80', 'loaded')
        with self.assertRaises(ValueError):
            C.game_filter_payload('tcp', '80', '80', 'bad')
        self.assertEqual(C.game_filter_payload('udp', ' 1024-65535 ', '80', 'any'),
                         dict(game_mode='udp', tcp='1024-65535', udp='80', ipset_mode='any'))

    def test_feature_lock_matches_tk(self):
        self.assertFalse(C.feature_lock('idle', False, False)[0])
        self.assertTrue(C.feature_lock('idle', True, False)[0])
        self.assertTrue(C.feature_lock('connecting', False, False)[0])
        self.assertTrue(C.feature_lock('stopping', False, False)[0])
        self.assertTrue(C.feature_lock('connected', False, True)[0])


class TestAutostart(unittest.TestCase):
    """Модуль автозапуска: реальный HKCU, но с тестовым именем значения."""

    NAME = 'ZapretGUI-test'

    def setUp(self):
        import autostart
        self.mod = autostart
        autostart.update(False, self.NAME)

    def tearDown(self):
        self.mod.update(False, self.NAME)

    def test_off_by_default_and_roundtrip(self):
        self.assertFalse(self.mod.read(self.NAME))
        self.assertTrue(self.mod.update(True, self.NAME))
        self.assertTrue(self.mod.read(self.NAME))
        self.assertTrue(self.mod.update(False, self.NAME))
        self.assertFalse(self.mod.read(self.NAME))

    def test_double_enable_and_disable_are_idempotent(self):
        self.assertTrue(self.mod.update(True, self.NAME))
        self.assertTrue(self.mod.update(True, self.NAME))
        self.assertTrue(self.mod.update(False, self.NAME))
        # Повторное удаление после удаления: состояние уже совпадает — ошибки нет.
        self.assertTrue(self.mod.update(False, self.NAME))
        self.assertFalse(self.mod.read(self.NAME))

    def test_command_carries_hidden_flag_and_update_mirrors_it(self):
        self.assertIn(self.mod.START_HIDDEN, self.mod.command())
        self.mod.update(True, self.NAME)
        stored = self.mod.current_value(self.NAME)
        self.assertEqual(stored, self.mod.command())
        self.assertIn(self.mod.START_HIDDEN, stored)


class TestAppCore(QtCase):
    def drain(self, app, cycles=6, wait=0.02):
        for _ in range(cycles):
            time.sleep(wait)
            app.tick()
            self._app.processEvents()

    def test_instantiation_reads_only(self):
        root = fake_build(Path(self._tmp.name))
        app = self.make_app(argv=['--pick-build', str(root)])
        app.root_path = root
        app.paths = core.strategies(root)
        before = {p: p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}
        app.refresh()
        app.load_filters()
        self.drain(app)
        after = {p: p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}
        self.assertEqual(before, after,
                         'открытие окна и чтение фильтров не должны менять файлы сборки')
        app.close()

    def _stub_autostart(self, update_returns=None):
        """Подмена модуля автозапуска: ни один тест не трогает реальный HKCU."""
        import app_qt
        calls = []
        allowed = bool(update_returns) if update_returns is not None else None

        class Stub:
            START_HIDDEN = '--tray'
            state = [False]           # фактическое состояние ключа, как в реестре

            @staticmethod
            def read():
                return Stub.state[0]

            @staticmethod
            def current_value():
                return 'exe --tray' if Stub.state[0] else ''

            @staticmethod
            def command():
                return 'exe --tray'

            @staticmethod
            def update(enabled):
                calls.append(enabled)
                if allowed is None or allowed:
                    Stub.state[0] = enabled      # запись удалась
                # при allowed=False реестр не изменился — состояние прежнее
                return Stub.state[0] == enabled

        return mock.patch.object(app_qt, '_autostart', Stub), calls

    def test_autostart_toggle_roundtrip(self):
        patcher, calls = self._stub_autostart()
        with patcher:
            app = self.make_app()
            self.assertFalse(app.autostart_check.isChecked())
            app.autostart_check.setChecked(True)
            self.assertEqual(calls, [True])
            self.assertTrue(app.autostart_check.isChecked())
            self.assertEqual(app.autostart_note.text(), '')
            app.autostart_check.setChecked(False)
            self.assertEqual(calls, [True, False])
            app.close()

    def test_autostart_toggle_missing_key_reverts_switch(self):
        patcher, _calls = self._stub_autostart(update_returns=False)
        with patcher:
            app = self.make_app()
            self.assertFalse(app.autostart_check.isChecked())
            warnings = []
            with mock.patch.object(app, 'show_warning',
                                   lambda title, message: warnings.append(title)):
                app.autostart_check.setChecked(True)
            self.assertTrue(warnings, 'ошибка записи должна показывать предупреждение')
            self.assertFalse(app.autostart_check.isChecked(),
                             'после неудачи переключатель возвращается в выключенное состояние')
            app.close()

    def test_settings_roundtrip(self):
        root = fake_build(Path(self._tmp.name))
        app = self.make_app()
        app.root_path = root
        app.youtube = False
        app.interval = '45'
        app.save()
        raw = json.loads((app.DATA/'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(raw['interval'], 45)
        self.assertFalse(raw['youtube'])
        app.close()

    def test_bad_interval_rejected(self):
        app = self.make_app()
        app.interval = '5'
        with self.assertRaises(ValueError):
            app.save()
        app.interval = '30'
        app.save()
        app.close()

    def test_filters_loaded_via_events(self):
        root = fake_build(Path(self._tmp.name))
        app = self.make_app()
        app.root_path = root
        self.assertIn(app.root_path.name, str(root) or '')
        app.load_filters()
        self.drain(app)
        self.assertEqual(app.gf_mode, 'disabled')
        self.assertEqual(app.ipset_mode, 'loaded')
        self.assertEqual(app.gf_note.text(), 'Значения прочитаны из сборки.')
        app.close()

    def test_save_filters_blocked_while_connecting(self):
        root = fake_build(Path(self._tmp.name))
        app = self.make_app()
        app.root_path = root
        app.state = 'connected'
        app.operation_active = True
        app.save_filters()
        app.refresh()
        self.drain(app)
        self.assertEqual(features.read_filters(root)['game_mode'], 'disabled')
        self.assertIn('заблокированы', app.gf_note.text())
        app.close()

    def test_locked_ui_widgets_disabled(self):
        root = fake_build(Path(self._tmp.name))
        app = self.make_app()
        app.state = 'stopping'
        app.refresh()
        self.assertFalse(app.gf_buttons.isEnabled())
        self.assertFalse(app.list_view.isEnabled())
        app.state = 'idle'
        app.refresh()
        self.assertTrue(app.gf_buttons.isEnabled())
        app.close()

    def test_health(self):
        app = self.make_app()
        app.active_targets = {'YouTube': 'x'}
        app.check_targets = lambda: {'YouTube': 'x'}
        app.show_health({'YouTube': (True, 12, ''), 'Discord': (True, 5, '')})
        self.assertIn('Доступен', app.health['YouTube'].text())
        app.show_health({'YouTube': (False, None, 'x')})
        self.assertEqual(app.health['YouTube'].text(), 'Нет ответа')
        app.close()

    def test_connect_rotation_two_attempts(self):
        root = fake_build(Path(self._tmp.name))
        import app_qt
        app = self.make_app()

        class FakeEngine:
            session_log = None
            ready = True
            process = None

            def running(self):
                return True

            def detach(self):
                pass

            def stop(self):
                pass
        original = app_qt.connect_profiles
        # Ключи имитации — как у core.check_network: имена выбранных целей.
        first = {'YouTube': (False, None, 'sim'), 'Discord': (False, None, 'sim'),
                 'Internet 1': (True, 1, ''), 'Internet 2': (True, 1, '')}
        second = {'YouTube': (True, 9, ''), 'Discord': (True, 9, ''),
                  'Internet 1': (True, 1, ''), 'Internet 2': (True, 2, '')}
        replies = iter([first, second])

        def fake_connect(engine, profiles, targets, cancel, emit, grace=0, **kw):
            for number, profile in enumerate(profiles, 1):
                emit('attempt', (number, len(profiles), profile.stem))
                service = next(replies)
                emit('probe', service)
                if all(value[0] for value in service.values()):
                    return 'connected', profile, service
            return 'exhausted', profiles[-1], {}

        app.paths = core.strategies(root)
        app.selected = app.paths[0].stem
        app.engine = FakeEngine()
        app_qt.connect_profiles = fake_connect
        saved_admin = app_qt.is_admin
        app_qt.is_admin = lambda: True
        try:
            app.start(auto=True)
            self.assertEqual(app.state, 'connecting')
            self.drain(app, cycles=10)
            self.assertEqual(app.state, 'connected')
            self.assertIn('Подключено', app.stage)
            self.assertIn('general2', app.selected)   # вторая попытка после тихой первой
        finally:
            app_qt.connect_profiles = original
            app_qt.is_admin = saved_admin
        app.close()

    def test_admin_missing_is_failure(self):
        import app_qt
        root = fake_build(Path(self._tmp.name))
        app = self.make_app()

        class FakeEngine:
            session_log = None
            ready = False
            process = None
            stopped = False

            def running(self):
                return False

            def detach(self):
                pass

            def stop(self):
                self.stopped = True
        saved_admin = app_qt.is_admin
        app_qt.is_admin = lambda: False
        saved_shell = app_qt.ctypes.windll.shell32.ShellExecuteW
        app_qt.ctypes.windll.shell32.ShellExecuteW = lambda *a, **kw: 0  # никакого реального UAC
        app.paths = core.strategies(root)
        app.selected = app.paths[0].stem
        fake_engine = FakeEngine()
        app.engine = fake_engine
        try:
            app.start(auto=False)
            self.drain(app)
            self.assertEqual(app.state, 'idle')
            # admin-код предлагает UAC; без подтверждения сообщение про права замещается отказом
            self.assertIn('Повышение прав отменено', app.stage)
            self.assertTrue(fake_engine.stopped or not app.engine.running())
        finally:
            app_qt.is_admin = saved_admin
            app_qt.ctypes.windll.shell32.ShellExecuteW = saved_shell
        app.close()

    def test_elevate_declined(self):
        import app_qt
        app = self.make_app()
        saved_target = app_qt.ctypes.windll.shell32.ShellExecuteW
        calls = []

        def fake_shell(*args):
            calls.append(args)
            return 0  # пользователь отменил UAC
        app_qt.ctypes.windll.shell32.ShellExecuteW = fake_shell
        try:
            app.elevate()
            self.assertEqual(len(calls), 1)
            self.assertEqual(app.state, 'idle')
            self.assertIn('Повышение прав отменено', app.stage)
        finally:
            app_qt.ctypes.windll.shell32.ShellExecuteW = saved_target
        app.close()

    def test_style(self):
        import app_qt
        text = app_qt.stylesheet()
        self.assertIn(C.ACCENT, text)
        self.assertIn(C.BG, text)
        self.assertIn('border-radius', text)

    def test_two_builds_strategy_list(self):
        root = fake_build(Path(self._tmp.name), count=3)
        app = self.make_app()
        app.root_path = root
        app.paths = core.strategies(root)
        self.assertEqual([p.stem for p in app.paths], ['general', 'general2', 'general3'])
        combo_items = [app.strategy_combo.itemText(i) for i in range(app.strategy_combo.count())]
        self.assertEqual(combo_items, ['general', 'general2', 'general3'])
        app.close()


class TestZipImportFlow(QtCase):
    """Импорт ZIP через события GUI: подтверждение, установка, блокировки, отказ."""

    def setUp(self):
        base = Path(self._tmp.name)
        import uuid
        self.dest = base/('import-dest-'+uuid.uuid4().hex)
        saved_open = getattr(self, '_saved_open', None)
        # Никаких модальных диалогов: файл всегда готов, подтверждение задаётся тестом.
        import app_qt
        self._saved_dialog = app_qt.QFileDialog.getOpenFileName
        app_qt.QFileDialog.getOpenFileName = lambda *a, **k: ('', '')
        self.addCleanup(setattr, app_qt.QFileDialog, 'getOpenFileName', self._saved_dialog)
        self.previous = fake_build(base/'previous-build')
        (self.previous/'lists'/'list-general-user.txt').write_text('local-user.example\n',
                                                                   encoding='utf-8')
        import zipfile
        self.zip_path = base/'incoming.zip'
        root = 'zapret-incoming/'
        with zipfile.ZipFile(self.zip_path, 'w') as z:
            z.writestr(root+'bin/winws.exe', b'MZ')
            z.writestr(root+'bin/WinDivert.dll', b'MZ')
            z.writestr(root+'bin/WinDivert64.sys', b'MZ')
            z.writestr(root+'bin/cygwin1.dll', b'MZ')
            z.writestr(root+'general.bat',
                       'start "zapret" /min "%BIN%winws.exe" --wf-tcp=443\n --wf-udp=443\n')
            z.writestr(root+'lists/list-general-user.txt', 'zip-user.example\n')

    def drain(self, app, cycles=6, wait=0.02):
        for _ in range(cycles):
            time.sleep(wait)
            app.tick()
            self._app.processEvents()

    def make_confirming_app(self, answer):
        import app_qt
        app = self.make_app()

        def fake_question(*args, **kwargs):
            return answer
        saved = app_qt.QMessageBox.question
        app_qt.QMessageBox.question = fake_question
        self.addCleanup(setattr, app_qt.QMessageBox, 'question', saved)
        return app

    def offer(self, app):
        app.events.put(('import_inspected',
                        (self.zip_path, _zi.inspect_zip(self.zip_path), self.dest,
                         self.previous, '')))

    def test_import_confirmed_installs_and_switches(self):
        from PySide6.QtWidgets import QMessageBox
        app = self.make_confirming_app(QMessageBox.Yes)
        self.offer(app)
        self.drain(app, cycles=10)
        install = self.dest/'zapret-incoming'
        self.assertTrue((install/'bin/winws.exe').is_file(), 'ZIP установлен в dest')
        self.assertEqual(app.root_path, install, 'сборка после импорта — активная')
        self.assertEqual((install/'lists/list-general-user.txt').read_text(encoding='utf-8'),
                         'local-user.example\n', 'список пользователя перевешивает архив')
        self.assertIn('Импортирована сборка', app.feature_notes['import'])
        combo_items = [app.strategy_combo.itemText(i) for i in range(app.strategy_combo.count())]
        self.assertEqual(combo_items, ['general'])
        self.assertFalse(app.feature_busy)
        app.close()

    def test_import_declined_touches_nothing(self):
        from PySide6.QtWidgets import QMessageBox
        app = self.make_confirming_app(QMessageBox.No)
        self.offer(app)
        self.drain(app, cycles=6)
        self.assertFalse(self.dest.exists(), 'отказ не распаковывает архив')
        self.assertFalse(app.feature_busy)
        app.close()

    def test_bad_archive_reported_without_changes(self):
        app = self.make_app()
        app.events.put(('import_rejected', 'Архив не принят: это не ZIP-архив'))
        self.drain(app, cycles=4)
        self.assertFalse(self.dest.exists())
        self.assertIn('не ZIP', app.feature_notes['import'])
        self.assertFalse(app.feature_busy)
        app.close()

    def test_import_blocked_while_engine_running(self):
        import app_qt
        app = self.make_app()
        saved_open = app_qt.QFileDialog.getOpenFileName
        calls = []

        def fake_open(*args, **kwargs):
            calls.append(args)
            return ('', '')
        app_qt.QFileDialog.getOpenFileName = fake_open

        class Locked:
            running = True
            process = None

            def running(self):
                return True
        saved_engine = app.engine
        app.engine = Locked()
        try:
            app.pick_zip_import()
            self.assertEqual(calls, [], 'диалог файла не открывается при работающем движке')
            self.assertFalse(app.feature_busy)
            self.assertEqual(app.state, 'idle')
            self.assertIn('остановите движок', app.feature_notes['import'])
        finally:
            app.engine = saved_engine
            app_qt.QFileDialog.getOpenFileName = saved_open
        app.close()

class TestImportCard07(QtCase):
    """0.7: заголовок, описание, кнопка папки, версия."""

    def test_labels_and_description(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        app = self.make_app()
        titles = [w.text() for w in app.import_section.findChildren(QLabel)
                  if w.property('cardTitle')]
        self.assertIn('ОБНОВЛЕНИЕ ZAPRET', titles)
        self.assertEqual(app.import_note.text(),
                         'Импортируйте сюда архив с GitHub или папку с zapret с пользовательскими доменами')
        buttons = [b.text() for b in app.import_buttons.findChildren(QPushButton)]
        self.assertIn('Импорт ZIP…', buttons)
        self.assertIn('Импорт папки…', buttons)
        app.close()

    def test_version_label(self):
        from PySide6.QtWidgets import QLabel
        app = self.make_app()
        self.assertEqual(C.VERSION, '0.8.0-beta.6')
        self.assertIn(C.VERSION, [w.text() for w in app.findChildren(QLabel)])
        app.close()


class TestNoWheelCombo(QtCase):
    """0.7: колесо не меняет значение combo даже с фокусом."""

    def wheel_on(self, combo, delta=120):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent
        center = QPointF(combo.rect().center())
        event = QWheelEvent(center, center, QPoint(0, 0), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollUpdate, False)
        QApplication.sendEvent(combo, event)
        return event

    def test_wheel_does_not_change_value(self):
        app = self.make_app()
        self.assertEqual(type(app.ulist_combo).__name__, 'NoWheelComboBox')
        self.assertEqual(type(app.gf_mode_box).__name__, 'NoWheelComboBox')
        self.assertEqual(type(app.ipset_mode_box).__name__, 'NoWheelComboBox')
        for combo in app.findChildren(QComboBox):
            combo.setFocus()
            index = combo.currentIndex()
            event = self.wheel_on(combo, 120)
            self.assertEqual(combo.currentIndex(), index,
                             'колесо не должно менять значение списка')
            self.assertFalse(event.isAccepted(), 'колесо должно быть отклонено combo')
        app.close()

    def test_plain_combo_has_no_guard(self):
        """Контроль: у обычного QComboBox колесо меняет значение (значит guard осмысленен)."""
        plain = QComboBox()
        for i in range(3):
            plain.addItem('item'+str(i))
        plain.setCurrentIndex(1)
        self.wheel_on(plain, 120)
        self.assertNotEqual(plain.currentIndex(), 1)


class TestCheckboxPaint(QtCase):
    """0.7: явная белая галочка на синей заливке, отрисованная QPainter."""

    def counts(self, check):
        image = check.grab().toImage()
        white = blue = 0
        for y in range(image.height()):
            for x in range(image.width()):
                r, g, b, _ = image.pixelColor(x, y).getRgb()
                if r >= 250 and g >= 250 and b >= 250:
                    white += 1
                elif abs(r-76) < 40 and abs(g-141) < 40 and abs(b-255) < 40:
                    blue += 1
        return white, blue

    def test_label_pixels_do_not_overlap_indicator(self):
        import app_qt
        from PySide6.QtGui import QColor
        check = app_qt.PaintedCheckBox('Readable checkbox label')
        check.setStyleSheet(app_qt.stylesheet())
        check.resize(check.sizeHint())
        for checked in (False, True):
            check.setChecked(checked)
            image = check.grab().toImage()
            text_pixels = [x for x in range(image.width()) for y in range(image.height())
                           if image.pixelColor(x, y) == QColor(C.TEXT)]
            self.assertTrue(text_pixels)
            self.assertGreaterEqual(min(text_pixels), 24)
        check.close()

    def test_checked_shows_white_check_on_blue(self):
        app = self.make_app()
        check = app.auto_check
        self._app.processEvents()
        check.setChecked(False)
        self._app.processEvents()
        white0, blue0 = self.counts(check)
        self.assertEqual(blue0, 0, 'выключенный чекбокс без синей заливки')
        self.assertEqual(white0, 0, 'выключенный чекбокс без белой галочки')
        check.setChecked(True)
        self._app.processEvents()
        white1, blue1 = self.counts(check)
        self.assertGreater(blue1, 100, 'включённый чекбокс с синей заливкой')
        self.assertGreater(white1, 5, 'явная белая галочка внутри')
        app.close()


class TestHistoryIntegration(QtCase):
    """0.7: память удачных стратегий (StrategyHistory, автор Maradigma)."""

    TARGETS = None

    def setUp(self):
        (Path(self._tmp.name)/'strategy-history.json').unlink(missing_ok=True)
        self.targets = dict(core.TARGETS)
        import uuid
        self.root = fake_build(Path(self._tmp.name)/('build-'+uuid.uuid4().hex), count=2)
        self.paths = sorted(core.strategies(self.root))

    def write_settings(self, **kw):
        settings = dict(root=str(self.root), auto=True, strategy='general',
                        youtube=True, discord=True, interval=30)
        settings.update(kw)
        (Path(self._tmp.name)/'settings.json').write_text(
            json.dumps(settings, ensure_ascii=False), encoding='utf-8')

    def seed_history(self, successes):
        from strategy_history import StrategyHistory
        history = StrategyHistory(Path(self._tmp.name)/'strategy-history.json')
        for stem in successes:
            history.record_success(next(p for p in self.paths if p.stem == stem),
                                   self.targets)

    def test_default_selection_prefers_last_success(self):
        self.write_settings(strategy=self.paths[0].stem)
        self.seed_history([self.paths[0].stem, self.paths[1].stem])
        app = self.make_app()
        self.assertEqual(app.selected, self.paths[1].stem,
                         'последняя удачная стратегия — выбор по умолчанию при auto')
        self.assertEqual(app.strategy_combo.currentText(), self.paths[1].stem)
        app.close()

    def test_manual_selection_is_kept_without_auto(self):
        self.write_settings(strategy=self.paths[0].stem, auto=False)
        self.seed_history([self.paths[1].stem])
        app = self.make_app()
        self.assertEqual(app.selected, self.paths[0].stem,
                         'manual выбор сохраняется при выключенном автоподборе')
        app.close()

    def test_connect_records_success_and_updates_memory(self):
        import app_qt
        self.write_settings(strategy=self.paths[0].stem)
        app = self.make_app()

        class FakeEngine:
            session_log = None
            ready = True
            process = None

            def running(self):
                return True

            def detach(self):
                pass

            def stop(self):
                pass
        ok = {'YouTube': (True, 9, ''), 'Discord': (True, 9, ''),
              'Internet 1': (True, 1, ''), 'Internet 2': (True, 2, '')}

        def fake_connect(engine, profiles, targets, cancel, emit, grace=0, **kw):
            emit('attempt', (1, len(profiles), profiles[0].stem))
            emit('probe', ok)
            return 'connected', profiles[0], ok
        saved_connect = app_qt.connect_profiles
        saved_admin = app_qt.is_admin
        app_qt.connect_profiles = fake_connect
        app_qt.is_admin = lambda: True
        app.paths = list(self.paths)
        app.engine = FakeEngine()
        try:
            app.start(auto=False)
            self.drain(app, cycles=10)
            self.assertEqual(app.state, 'connected')
        finally:
            app_qt.connect_profiles = saved_connect
            app_qt.is_admin = saved_admin
        history = json.loads((app.DATA/'strategy-history.json').read_text(encoding='utf-8'))
        self.assertEqual(len(history), 1)
        self.assertEqual(next(iter(history.values()))['strategy'], self.paths[0].stem)
        self.assertEqual(app.history.order(list(reversed(self.paths)), self.targets)[0],
                         self.paths[0], 'записанный удачный поднимается в порядке перебора')
        self.assertEqual(app.strategy_combo.currentText(), self.paths[0].stem)
        app.close()

    def test_auto_order_raises_known_first_without_gaps(self):
        import uuid as uuid_module
        root = fake_build(Path(self._tmp.name)/('build-'+uuid_module.uuid4().hex), count=3)
        paths = sorted(core.strategies(root))
        self.write_settings(root=str(root), strategy=paths[0].stem)
        from strategy_history import StrategyHistory
        history = StrategyHistory(Path(self._tmp.name)/'strategy-history.json')
        history.record_success(paths[1], self.targets)   # раньше
        history.record_success(paths[2], self.targets)   # позже — последняя удачная
        app = self.make_app()
        app.paths = list(paths)
        self.assertEqual(app.selected, paths[2].stem)
        order = app.history_profiles(paths[0], self.targets)
        self.assertEqual(order, [paths[2], paths[1], paths[0]],
                         'известные удачные первыми, остальные без пропусков')
        app.auto = False
        self.assertEqual(app.history_profiles(paths[0], self.targets), [paths[0]],
                         'manual порядок — только выбранная стратегия')
        app.close()

    def test_failed_or_stale_checks_do_not_record_success(self):
        self.write_settings()
        app = self.make_app()
        app.active_targets = dict(self.targets)
        calls = []
        app.history.record_success = lambda *args: calls.append(args)
        app.apply_connect_done(app.generation, {})
        app.apply_connect_done(app.generation - 1, {name: (True, 1, '') for name in self.targets})
        self.assertEqual(calls, [])
        self.assertNotEqual(app.state, 'connected')
        app.close()

    def test_corrupt_history_file_falls_back(self):
        self.write_settings()
        (Path(self._tmp.name)/'strategy-history.json').write_text('не-json')
        app = self.make_app()
        self.assertEqual(app.selected, 'general', 'битый файл истории не роняет запуск')
        self.assertEqual(app.history.records, {})
        app.close()

    def test_record_failure_does_not_break_connected(self):
        import app_qt
        self.write_settings(strategy=self.paths[0].stem)
        app = self.make_app()

        class FakeEngine:
            session_log = None
            ready = True
            process = None

            def running(self):
                return True

            def detach(self):
                pass

            def stop(self):
                pass
        ok = {'YouTube': (True, 9, ''), 'Discord': (True, 9, ''),
              'Internet 1': (True, 1, ''), 'Internet 2': (True, 2, '')}

        def fake_connect(engine, profiles, targets, cancel, emit, grace=0, **kw):
            emit('attempt', (1, 1, profiles[0].stem))
            emit('probe', ok)
            return 'connected', profiles[0], ok
        saved_connect = app_qt.connect_profiles
        saved_admin = app_qt.is_admin
        app_qt.connect_profiles = fake_connect
        app_qt.is_admin = lambda: True

        def broken_record(path, targets):
            raise OSError('диск переполнен')
        app.history.record_success = broken_record
        app.paths = list(self.paths)
        app.engine = FakeEngine()
        try:
            app.start(auto=False)
            self.drain(app, cycles=10)
        finally:
            app_qt.connect_profiles = saved_connect
            app_qt.is_admin = saved_admin
        self.assertEqual(app.state, 'connected',
                         'сбой записи истории не ломает рабочее подключение')
        self.assertIn('не сохранено', (app.DATA/'desk.log').read_text(encoding='utf-8'))
        app.close()


class TestFolderImportFlow(QtCase):
    """0.7: кнопка импорта папки; backend (Agatha, задача d3488a3f) — заглушки."""

    def setUp(self):
        import uuid
        base = Path(self._tmp.name)
        self.dest = base/('folder-dest-'+uuid.uuid4().hex)
        import app_qt
        self._saved_dialog = app_qt.QFileDialog.getExistingDirectory
        app_qt.QFileDialog.getExistingDirectory = lambda *a, **k: ''
        self.addCleanup(setattr, app_qt.QFileDialog, 'getExistingDirectory',
                        self._saved_dialog)

    def drain(self, app, cycles=6, wait=0.02):
        for _ in range(cycles):
            time.sleep(wait)
            app.tick()
            self._app.processEvents()

    def source_folder(self, name):
        base = Path(self._tmp.name)
        folder = base/name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def patch_import_folder(self, function):
        """Подмена только бэкенда установки для случая ошибки (остальное — реальный API)."""
        saved = _zi.import_folder
        _zi.import_folder = function
        self.addCleanup(setattr, _zi, 'import_folder', saved)

    def choose(self, app, folder, answer):
        import app_qt
        from PySide6.QtWidgets import QMessageBox
        app.import_dest_root = lambda: self.dest
        saved_dialog = app_qt.QFileDialog.getExistingDirectory
        saved_question = app_qt.QMessageBox.question
        app_qt.QFileDialog.getExistingDirectory = lambda *a, **k: str(folder)

        def fake_question(parent, title, text, *args, **kwargs):
            return answer
        app_qt.QMessageBox.question = fake_question
        self.addCleanup(setattr, app_qt.QFileDialog, 'getExistingDirectory', saved_dialog)
        self.addCleanup(setattr, app_qt.QMessageBox, 'question', saved_question)

    def log_text(self, app):
        return (app.DATA/'desk.log').read_text(encoding='utf-8', errors='replace')

    def test_api_ready_and_default_note(self):
        app = self.make_app()
        self.assertTrue(app.folder_api_ready())
        self.assertIn('папку с zapret с пользовательскими доменами', app.import_note.text())
        app.close()

    def test_button_blocked_while_engine_running(self):
        app = self.make_app()
        calls = []

        class Locked:
            process = None

            def running(self):
                return True
        saved = app.engine
        app.engine = Locked()
        try:
            app.pick_folder_import()
            self.assertFalse(app.feature_busy)
            self.assertIn('остановите движок', app.feature_notes['import'])
        finally:
            app.engine = saved
        app.close()

    def test_build_folder_confirmed_installs_and_switches(self):
        from PySide6.QtWidgets import QMessageBox
        source = fake_build(self.source_folder('zapret-07build'))
        app = self.make_app()
        self.choose(app, source, QMessageBox.Yes)
        app.pick_folder_import()
        self.drain(app, cycles=10)
        install = self.dest/source.name
        self.assertTrue((install/'bin'/'winws.exe').is_file(), 'папка установлена в dest')
        self.assertEqual(app.root_path, install, 'сборка после импорта папки — активная')
        self.assertIn('Импортирована папка', app.feature_notes['import'])
        self.assertEqual(app.strategy_combo.count(), 2)
        self.assertFalse(app.feature_busy)
        self.assertIn(source.name, self.log_text(app), 'имя установленной папки в журнале')
        app.close()

    def test_build_folder_declined_touches_nothing(self):
        from PySide6.QtWidgets import QMessageBox
        source = fake_build(self.source_folder('zapret-decline'))
        app = self.make_app()
        before = app.root_path
        self.choose(app, source, QMessageBox.No)
        app.pick_folder_import()
        self.drain(app, cycles=6)
        self.assertFalse(self.dest.exists(), 'отказ не копирует папку')
        self.assertEqual(app.root_path, before)
        self.assertFalse(app.feature_busy)
        app.close()

    def test_unknown_folder_reported_without_changes(self):
        from PySide6.QtWidgets import QMessageBox
        source = self.source_folder('mystery')   # без winws.exe и списков
        (source/'readme.md').write_text('x', encoding='utf-8')
        app = self.make_app()
        self.choose(app, source, QMessageBox.Yes)
        app.pick_folder_import()
        self.drain(app, cycles=6)
        self.assertFalse(self.dest.exists())
        self.assertIn('не принята', app.feature_notes['import'])
        self.assertFalse(app.feature_busy)
        app.close()

    def test_user_folder_confirmed_imports_lists(self):
        from PySide6.QtWidgets import QMessageBox
        source = self.source_folder('user-domains')
        (source/'list-general-user.txt').write_text('user-import.example\n',
                                                    encoding='utf-8')
        root_before = fake_build(Path(self._tmp.name)/'user-target-root')
        app = self.make_app()
        app.root_path = root_before
        self.choose(app, source, QMessageBox.Yes)
        app.pick_folder_import()
        self.drain(app, cycles=8)
        target = root_before/'lists'/'list-general-user.txt'
        self.assertTrue(target.is_file(), 'список записан в lists/ текущей сборки')
        self.assertIn('user-import.example', target.read_text(encoding='utf-8'))
        log = self.log_text(app)
        self.assertIn('Импорт папки завершён: пользовательские файлы из user-domains', log)
        self.assertIn('moved=1', log, 'сводка результата backend попадает в журнал')
        self.assertIn('Пользовательские файлы импортированы', app.feature_notes['import'])
        self.assertFalse(app.feature_busy)
        app.close()

    def test_build_folder_user_files_carry_over(self):
        from PySide6.QtWidgets import QMessageBox
        source = fake_build(self.source_folder('zapret-07users'))
        previous = fake_build(self.source_folder('previous-root'))
        user_file = previous/'lists'/'list-general-user.txt'
        user_file.write_text('previous-user.example\n', encoding='utf-8')
        app = self.make_app()
        app.root_path = previous
        app.paths = core.strategies(previous)
        self.choose(app, source, QMessageBox.Yes)
        app.pick_folder_import()
        self.drain(app, cycles=10)
        install = self.dest/source.name
        carried = install/'lists'/'list-general-user.txt'
        self.assertTrue(carried.is_file() and 'previous-user.example' in
                        carried.read_text(encoding='utf-8'),
                        'пользовательский файл из прежней сборки перенесён backend')
        self.assertIn(str(len(core.strategies(install))),
                      app.feature_notes['import'], 'число стратегий в заметке')
        app.close()

    def test_backend_error_shown_without_crash(self):
        from PySide6.QtWidgets import QMessageBox
        source = fake_build(self.source_folder('zapret-error'))
        app = self.make_app()
        self.choose(app, source, QMessageBox.Yes)

        def broken(source, destination, previous=None, log=None):
            raise OSError('диск переполнен')
        self.patch_import_folder(broken)
        app.pick_folder_import()
        self.drain(app, cycles=8)
        self.assertIn('Импорт папки не выполнен', app.feature_notes['import'])
        self.assertFalse(app.feature_busy)
        app.close()


def tearDownModule():
    # Dispose native Qt objects while Python callbacks/modules are still alive.
    # close() hides windows but does not delete them or their queued timers.
    import gc
    import shiboken6
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if shiboken6.isValid(widget):
            ticker = getattr(widget, 'ticker', None)
            if ticker is not None:
                ticker.stop()
            shiboken6.delete(widget)
    gc.collect()
    shiboken6.delete(app)


if __name__ == '__main__':
    unittest.main()
