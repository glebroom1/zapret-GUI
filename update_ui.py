"""Update controls; network and helper preparation stay outside the Qt thread."""
import json
from pathlib import Path
import sys
import threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
import runtime_paths
import self_update
import qt_common as C


class UpdateUI:
    def init_update_ui(self, frame):
        self._update_busy = self._installing = False
        self._ready_update = None
        self.auto_update = bool(self.settings.get('auto_update', True))
        self.update_auto = self.toggle('Автоматически проверять и скачивать обновления', self.auto_update,
                                       self.set_auto_update)
        frame.layout().addWidget(self.update_auto)
        self.update_note = self.note(frame)
        self.update_note.setText('После загрузки нажмите «Установить»: EXE заменится по текущему пути и перезапустится. '
                                 'Настройки сохраняются; предыдущая версия остаётся в папке обновлений.')
        self.update_button = self.button('Проверить обновление приложения', self.check_app_update)
        frame.layout().addWidget(self.update_button)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(6 * 3600000)
        self.update_timer.timeout.connect(self.automatic_app_update)
        self.update_timer.start()

    def set_auto_update(self, value):
        self.auto_update = value
        self.settings_changed()

    def automatic_app_update(self):
        if self.auto_update and not self._ready_update:
            self.check_app_update()

    def check_app_update(self):
        if self._update_busy or self._installing:
            return
        if self._ready_update:
            self.install_app_update()
            return
        self._update_busy = True
        self.update_button.setEnabled(False)
        self.update_note.setText('Проверяем релизы GitHub…')

        def work():
            try:
                release = self_update.latest_release(C.VERSION)
                if release is None:
                    self.events.put(('app_update_none', None))
                    return
                self.events.put(('app_update_progress', 'Загружаем ' + release['tag'] + ' и проверяем SHA256…'))
                cache = self.DATA / 'updates/ready.json'
                stage = None
                try:
                    cached = json.loads(cache.read_text(encoding='utf-8'))
                    candidate = Path(cached['stage']).resolve()
                    if candidate.is_relative_to(cache.parent.resolve()) and candidate.is_file() \
                            and cached['release']['sha256'] == release['sha256'] \
                            and self_update.sha256(candidate) == release['sha256']:
                        stage = candidate
                except (OSError, ValueError, KeyError):
                    pass
                if stage is None:
                    stage = self_update.download_release(release, self.DATA / 'updates')
                    temporary = cache.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'stage': str(stage), 'release': release}), encoding='utf-8')
                    temporary.replace(cache)
                self.events.put(('app_update_ready', (stage, release)))
            except Exception as error:
                self.events.put(('app_update_error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def handle_update_event(self, kind, value):
        if kind == 'app_update_progress':
            self.update_note.setText(value)
            return
        self._update_busy = False
        self.update_button.setEnabled(True)
        if kind == 'app_update_error':
            self._installing = False
            self.setEnabled(True)
            self.tg_timer.start()
            self.update_note.setText('Обновление не применено: ' + value)
        elif kind == 'app_update_none':
            self.update_note.setText('Новых версий для этого канала нет. Установлена ' + C.VERSION)
        elif kind == 'app_update_ready':
            self._ready_update = value
            self.update_button.setText('Установить ' + value[1]['tag'] + ' и перезапустить')
            self.update_note.setText('Обновление скачано и проверено. Замена по текущему пути — после нажатия «Установить».')
        elif kind == 'app_update_handoff':
            self.close_and_delete()

    def install_app_update(self):
        if not runtime_paths.is_frozen():
            self.update_note.setText('Обновление скачано: ' + str(self._ready_update[0]) + '. Самозамена доступна в собранном EXE.')
            return
        if self.feature_busy or self.operation_active or self.tg_busy or self.state in ('connecting', 'stopping'):
            self.update_note.setText('Дождитесь завершения текущей операции и повторите установку.')
            return
        if QMessageBox.question(self, 'Установить обновление',
                'Заменить ' + str(Path(sys.executable).resolve()) + ' и перезапустить приложение?\n'
                'Telegram-прокси на время обновления остановится; его можно снова включить после запуска. '
                'Настройки и текущий путь EXE сохранятся.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.save()
        except Exception as error:
            self.update_note.setText('Не удалось сохранить настройки: ' + str(error))
            return
        self._installing = True
        self.setEnabled(False)
        self.tg_timer.stop()
        self.update_note.setText('Подготавливаем замену EXE…')
        stage, release = self._ready_update
        hidden = not self.isVisible()

        def work():
            try:
                status = self.tg_manager.status()
                if status.get('unresponsive') or status.get('starting'):
                    raise RuntimeError('Telegram-прокси не отвечает. Сначала остановите его в карточке Telegram.')
                if status['running']:
                    self.tg_manager.stop()
                self_update.prepare_handoff(stage, release, sys.executable, tray=hidden)
                self.events.put(('app_update_handoff', None))
            except Exception as error:
                self.events.put(('app_update_error', str(error)))
        threading.Thread(target=work, daemon=True).start()
