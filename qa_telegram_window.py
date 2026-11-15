"""Isolated source GUI for visual QA; no autostart changes or UAC needed.
Launched and operated only through cua-driver. Closing the window exits QA.
"""
import json
from pathlib import Path
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import app_qt

ROOT = Path(__file__).parent / 'build' / 'tg-window-qa'
ROOT.mkdir(parents=True, exist_ok=True)
qt = QApplication([])
qt.setFont(QFont('Segoe UI', 10))
qt.setStyleSheet(app_qt.stylesheet())
app_qt.App._heal_autostart = lambda self: None
app_qt.App.setup_tray = lambda self: None
window = app_qt.App(data_dir=ROOT)
window.setWindowTitle(window.windowTitle() + ' · проверка TG')
window.show()
raise SystemExit(qt.exec())
