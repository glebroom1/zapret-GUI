"""Telegram card, separate from zapret's controller and main connection button."""
import threading
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox
from telegram_proxy import TelegramProxyManager, DEFAULTS, validate


class TelegramProxyUI:
    def init_telegram(self):
        self.tg_manager = TelegramProxyManager(self.DATA / 'telegram-proxy')
        self.tg_status = dict(running=False)
        self.tg_busy = self.tg_poll_busy = False
        self.tg_generation = 0
        self.tg_settings_error = ''
        try:
            self.tg_settings = self.tg_manager.settings()
        except Exception as exc:
            self.tg_settings = dict(DEFAULTS)
            self.tg_settings_error = str(exc)

    def build_telegram_card(self):
        frame = self.card('TELEGRAM · БЕТА')
        self.tg_status_label = QLabel('Проверка локального прокси…')
        self.tg_status_label.setWordWrap(True)
        frame.layout().addWidget(self.tg_status_label)
        self.tg_port = QLineEdit(str(self.tg_settings['port']))
        self.tg_port.setMaximumWidth(105)
        frame.layout().addWidget(self.field_row('Локальный порт', self.tg_port))
        self.tg_direct = self.toggle('Напрямую через Wi-Fi / Ethernet, без VPN',
                                     self.tg_settings['direct'], lambda _: None)
        frame.layout().addWidget(self.tg_direct)
        self.tg_cf = self.toggle('Запасной маршрут через Cloudflare',
                                 self.tg_settings['cfproxy'], lambda _: None)
        frame.layout().addWidget(self.tg_cf)
        self.tg_domain = QLineEdit(self.tg_settings['domain'])
        self.tg_domain.setPlaceholderText('Пусто — публичный пул Flowseal')
        frame.layout().addWidget(self.field_row('Свой CF-домен', self.tg_domain))
        self.tg_button = self.button('Запустить прокси', self.toggle_telegram)
        self.tg_open_button = self.button('Подключить в Telegram', lambda: self.telegram_link('open'))
        self.tg_copy_button = self.button('Скопировать ссылку', lambda: self.telegram_link('copy'))
        frame.layout().addWidget(self.button_row(self.tg_button, self.tg_open_button))
        frame.layout().addWidget(self.button_row(self.tg_copy_button))
        frame.layout().addWidget(self.hint(
            'Один раз добавьте прокси в Telegram Desktop на этом компьютере. '
            'После выхода из GUI прокси продолжит работать. '
            'Публичный CF-пул может быть недоступен; выключение галочки оставляет только прямой маршрут.'))
        self.tg_note = self.note(frame)
        self.tg_note.setText(self.tg_settings_error)
        self.apply_telegram_status(dict(running=False))
        self.tg_timer = QTimer(self)
        self.tg_timer.setInterval(2500)
        self.tg_timer.timeout.connect(self.poll_telegram)
        self.tg_timer.start()
        QTimer.singleShot(100, self.poll_telegram)

    def poll_telegram(self):
        if self.tg_busy or self.tg_poll_busy:
            return
        self.tg_poll_busy = True
        generation = self.tg_generation

        def work():
            try:
                status = self.tg_manager.status()
            except Exception:
                status = dict(running=False, unresponsive=True)
            self.events.put(('telegram_status', (generation, status)))
        threading.Thread(target=work, daemon=True).start()

    def apply_telegram_status(self, status):
        self.tg_status = status
        running = status['running']
        uncertain = bool(status.get('unresponsive'))
        starting = bool(status.get('starting'))
        if running and status.get('loop_age', 0) > 10:
            self.tg_status_label.setText('Процесс отвечает, сетевой цикл задерживается')
        elif running and status.get('phase') == 'reconnecting':
            self.tg_status_label.setText('Прокси восстанавливает соединения…')
        elif running:
            route = 'напрямую' if status['direct'] else 'системный маршрут'
            self.tg_status_label.setText(
                f"Локальный прокси · 127.0.0.1:{status['port']} · {route}\n"
                f"Соединений: {status['connections']} · WS: {status['ws']} · CF: {status['cf']}")
        elif starting:
            self.tg_status_label.setText('Прокси запускается…')
        elif uncertain:
            self.tg_status_label.setText('Процесс найден, но не отвечает. Управление пока не подтверждено.')
        else:
            self.tg_status_label.setText('Остановлен')
        self.tg_button.setText('Операция…' if self.tg_busy else
                               ('Завершить зависший прокси' if uncertain else
                                'Остановить прокси' if running else 'Запустить прокси'))
        self.tg_button.setEnabled(not self.tg_busy and not starting)
        self.tg_open_button.setEnabled(running and not self.tg_busy)
        self.tg_copy_button.setEnabled(running and not self.tg_busy)
        for widget in (self.tg_port, self.tg_direct, self.tg_cf, self.tg_domain):
            widget.setEnabled(not running and not self.tg_busy and not uncertain and not starting)

    def toggle_telegram(self):
        if self.tg_busy:
            return
        force = bool(self.tg_status.get('unresponsive'))
        if force and QMessageBox.question(self, 'Telegram-прокси',
                'Прокси не отвечает. Принудительно завершить его? Текущие соединения прервутся.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        stopping = self.tg_status['running'] or force
        try:
            options = validate(port=self.tg_port.text().strip(), direct=self.tg_direct.isChecked(),
                               cfproxy=self.tg_cf.isChecked(), domain=self.tg_domain.text())
        except ValueError as exc:
            self.tg_note.setText(str(exc))
            return
        self.tg_busy = True
        self.tg_generation += 1
        self.tg_note.setText('Остановка…' if stopping else 'Запуск отдельного процесса…')
        self.apply_telegram_status(self.tg_status)

        def work():
            error = ''
            try:
                if stopping:
                    self.tg_manager.stop(force=force)
                else:
                    self.tg_manager.start(**options)
            except Exception as exc:
                error = str(exc)
            self.events.put(('telegram_done', (self.tg_manager.status(), error, stopping)))
        threading.Thread(target=work, daemon=True).start()

    def handle_telegram_event(self, kind, payload):
        if kind == 'telegram_status':
            self.tg_poll_busy = False
            generation, payload = payload
            # An older poll must not override a more recent start/stop operation.
            if not self.tg_busy and generation == self.tg_generation:
                self.apply_telegram_status(payload)
        elif kind == 'telegram_done':
            status, error, stopping = payload
            self.tg_busy = False
            self.apply_telegram_status(status)
            self.tg_note.setText(error or ('Прокси остановлен.' if stopping else
                'Прокси слушает локальный порт. Доступность проверит Telegram при подключении.'))
            self.log('Telegram: ' + (error or ('прокси остановлен' if stopping else 'прокси запущен')))
            if self.closing:
                self.close()
        elif kind == 'telegram_link':
            action, link = payload
            if action == 'copy':
                QApplication.clipboard().setText(link)
                self.tg_note.setText('Ссылка скопирована. Добавьте её в Telegram Desktop на этом ПК.')
            elif not QDesktopServices.openUrl(QUrl(link)):
                self.tg_note.setText('Telegram не открылся. Скопируйте ссылку и добавьте прокси вручную.')
        elif kind == 'telegram_note':
            self.tg_note.setText(payload)

    def telegram_link(self, action):
        def work():
            try:
                self.events.put(('telegram_link', (action, self.tg_manager.link())))
            except Exception as exc:
                self.events.put(('telegram_note', str(exc)))
        threading.Thread(target=work, daemon=True).start()
