"""Ask for elevation before opening the Qt controller; preserve startup intent."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
from core import is_admin


def target(arguments):
    if getattr(sys, 'frozen', False):
        return sys.executable, list(arguments)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    executable = str(pythonw if pythonw.exists() else Path(sys.executable))
    return executable, [str(Path(__file__).with_name('app_qt.py')), *arguments]


def ensure_admin():
    if is_admin():
        return True
    executable, arguments = target(sys.argv[1:])
    previous = os.environ.get('PYINSTALLER_RESET_ENVIRONMENT')
    if getattr(sys, 'frozen', False):
        os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', executable, subprocess.list2cmdline(arguments),
            str(Path(executable).parent), 1)
    finally:
        if previous is None:
            os.environ.pop('PYINSTALLER_RESET_ENVIRONMENT', None)
        else:
            os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = previous
    if result <= 32:
        ctypes.windll.user32.MessageBoxW(None,
            'Для управления zapret нужны права администратора. Запуск отменён.',
            'zapret GUI', 0x30)
    return False
