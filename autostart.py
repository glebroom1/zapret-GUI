"""Автозапуск в Windows по желанию пользователя (с поддержкой Task Scheduler и Run).

Для работы без окна UAC при старте Windows (с правами администратора):
создаётся задача в Планировщике Windows с флагом RunLevel Highest,
запуском от батареи на ноутбуках и без ограничения по времени.
При отсутствии прав администратора или в тестах используется ключ
реестра HKCU Software\\Microsoft\\Windows\\CurrentVersion\\Run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TASK_NAME = 'ZapretGUI'
VALUE_NAME = 'ZapretGUI'
SUB_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
START_HIDDEN = '--tray --connect'      # флаги автозапуска: трей + подключение


def target_binary() -> str:
    if getattr(sys, 'frozen', False):
        return str(Path(sys.executable).resolve())
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if pythonw.is_file():
        return str(pythonw.resolve())
    return str(Path(sys.executable).resolve())


def target_arguments() -> str:
    if getattr(sys, 'frozen', False):
        return START_HIDDEN
    script = Path(__file__).resolve().with_name('app_qt.py')
    return f'"{script}" {START_HIDDEN}'


def command() -> str:
    """Что записывать в автозапуск: EXE или pythonw + скрипт + флаги --tray --connect."""
    bin_path = target_binary()
    args = target_arguments()
    return f'"{bin_path}" {args}'


def _has_scheduled_task(task_name: str = TASK_NAME) -> bool:
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        res = subprocess.run(['schtasks', '/query', '/tn', task_name],
                             capture_output=True, timeout=2, creationflags=flags)
        return res.returncode == 0
    except Exception:
        return False


def _delete_scheduled_task(task_name: str = TASK_NAME) -> bool:
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        res = subprocess.run(['schtasks', '/delete', '/tn', task_name, '/f'],
                             capture_output=True, timeout=3, creationflags=flags)
        return res.returncode == 0
    except Exception:
        return False


def _create_scheduled_task(task_name: str = TASK_NAME) -> bool:
    exe_path = target_binary()
    args = target_arguments()
    work_dir = str(Path(exe_path).parent)
    # PowerShell single-quoted literals escape an apostrophe by doubling it.
    exe_path_ps, args_ps, work_dir_ps, task_ps = (
        value.replace("'", "''") for value in (exe_path, args, work_dir, task_name))
    ps_cmd = (
        "$ErrorActionPreference = 'Stop'; "
        f"$Action = New-ScheduledTaskAction -Execute '{exe_path_ps}' -Argument '{args_ps}' -WorkingDirectory '{work_dir_ps}'; "
        f"$Trigger = New-ScheduledTaskTrigger -AtLogOn; "
        f"$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; "
        f"$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); "
        f"Register-ScheduledTask -TaskName '{task_ps}' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force"
    )
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        res = subprocess.run(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps_cmd],
                             capture_output=True, timeout=5, creationflags=flags)
        if res.returncode == 0:
            return True
    except Exception:
        pass
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        cmd_str = f'"{exe_path}" {args}'
        res = subprocess.run(['schtasks', '/create', '/tn', task_name, '/tr', cmd_str,
                              '/sc', 'ONLOGON', '/rl', 'HIGHEST', '/f'],
                             capture_output=True, timeout=4, creationflags=flags)
        return res.returncode == 0
    except Exception:
        return False


def _remove_run_key(value_name: str) -> None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except OSError:
        pass


def _update_run_key(enabled: bool, value_name: str) -> bool:
    try:
        import winreg
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SUB_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command())
        else:
            _remove_run_key(value_name)
    except OSError:
        pass
    return read(value_name) == enabled


def read(value_name: str = VALUE_NAME) -> bool:
    """Есть ли автозапуск прямо сейчас (задача в Планировщике или ключ HKCU Run)."""
    if value_name == VALUE_NAME and _has_scheduled_task(TASK_NAME):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY) as key:
            value, _type = winreg.QueryValueEx(key, value_name)
        return bool(value)
    except OSError:
        return False


def current_value(value_name: str = VALUE_NAME) -> str:
    """Точная строка команды для проверки переезда пути EXE."""
    if value_name == VALUE_NAME and _has_scheduled_task(TASK_NAME):
        try:
            result = subprocess.run(['schtasks', '/query', '/tn', TASK_NAME, '/xml'],
                capture_output=True, timeout=3,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0:
                return ''
            # schtasks emits UTF-16 on Windows; bytes preserve XML encoding/BOM.
            root = ET.fromstring(result.stdout)
            action = root.find('.//{*}Actions/{*}Exec')
            if action is None:
                return ''
            executable = action.findtext('{*}Command', '')
            arguments = action.findtext('{*}Arguments', '')
            return f'"{executable}" {arguments}'.strip()
        except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError):
            return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUB_KEY) as key:
            value, _type = winreg.QueryValueEx(key, value_name)
        if value:
            return str(value)
    except OSError:
        pass
    return ''


def update(enabled: bool, value_name: str = VALUE_NAME) -> bool:
    """Записать или удалить автозапуск; возвращает фактическое состояние после записи."""
    if value_name == VALUE_NAME:
        if enabled:
            if _create_scheduled_task(TASK_NAME):
                _remove_run_key(value_name)
                return True
            return _update_run_key(True, value_name)
        else:
            _delete_scheduled_task(TASK_NAME)
            _remove_run_key(value_name)
            return not read(value_name)
    return _update_run_key(enabled, value_name)
