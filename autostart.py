"""Автозапуск в Windows по желанию пользователя (идея — друг пользователя).

Обычная строка ключа HKCU …\Run: пишется из-под текущего пользователя,
поэтому права администратора не нужны, и такой автозапуск виден в
«Параметры → Приложения → Автозагрузка». Без планировщика и
без ярлыка в папке автозагрузки.
"""
from __future__ import annotations

import sys
from pathlib import Path

VALUE_NAME = 'ZapretGUI'
SUB_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
START_HIDDEN = '--tray'      # флаг автозапуска: сразу свернуть в трей, без окна


def command() -> str:
    """Что записывать в Run: EXE сборки или pythonw + скрипт проекта.

    START_HIDDEN в конце носит смысловой груз: без него автозапуск открывал
    бы окно поверх прочих программ при каждом входе в Windows.
    """
    script = Path(__file__).resolve().with_name('app_qt.py')
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" {START_HIDDEN}'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if pythonw.is_file():
        return f'"{pythonw}" "{script}" {START_HIDDEN}'
    return f'"{sys.executable}" "{script}" {START_HIDDEN}'


def read(value_name: str = VALUE_NAME) -> bool:
    """Есть ли ключ автозапуска прямо сейчас (правда, если значение не пустое)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY) as key:
            value, _type = winreg.QueryValueEx(key, value_name)
        return bool(value)
    except OSError:
        return False


def current_value(value_name: str = VALUE_NAME) -> str:
    """Точная строка ключа — чтобы заметить переезд EXE и обновить путь."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY) as key:
            value, _type = winreg.QueryValueEx(key, value_name)
        return str(value)
    except OSError:
        return ''


def update(enabled: bool, value_name: str = VALUE_NAME) -> bool:
    """Записать или удалить ключ; возвращает фактическое состояние после записи."""
    try:
        import winreg
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SUB_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass          # ключа и так нет — цель достигнута
    except OSError:
        pass
    return read(value_name) == enabled
