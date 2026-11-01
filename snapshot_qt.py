"""Снимок окна Qt-версии в offscreen-режиме (без показа на общем рабочем столе)."""
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT/'preview_qt.png'
app = QApplication(sys.argv)
from PySide6.QtGui import QFont, QFontDatabase
# Windows offscreen plugin does not enumerate installed fonts.
font_file = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/segoeui.ttf'
if font_file.is_file():
    QFontDatabase.addApplicationFont(str(font_file))
font = QFont('Segoe UI', 10)
font.setStyleHint(QFont.SansSerif)
app.setFont(font)
import app_qt  # noqa: E402
app.setStyleSheet(app_qt.stylesheet())
window = app_qt.App(data_dir=Path(os.environ['LOCALAPPDATA']))
# Найти локальную сборку рядом с проектом для реалистичного списка стратегий
candidates = core.discover(PROJECT.parent)
if candidates:
    window.root_path = candidates[0]
    window.paths = core.strategies(candidates[0])
    window.strategy_combo.blockSignals(True)
    window.strategy_combo.clear()
    window.strategy_combo.addItems([p.stem for p in window.paths])
    window.strategy_combo.setCurrentText(window.paths[0].stem)
    window.strategy_combo.blockSignals(False)
    window.folder_view.setText('Сборка: '+str(window.root_path))
window.state = 'idle'
window.stage = ''
window.show()
height = int(sys.argv[2]) if len(sys.argv) > 2 else 900
width = int(sys.argv[3]) if len(sys.argv) > 3 else 680
window.resize(width, height)
window.setMinimumHeight(height)
app.processEvents()
window.log('Найдено стратегий: '+str(len(window.paths))+' · '+window.root_path.name)
app.processEvents()
pixmap = window.grab()
pixmap.save(str(out), 'PNG', 100)
print('saved', out, pixmap.width(), 'x', pixmap.height())
