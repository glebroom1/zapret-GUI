# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для Zapret Desk Qt: onefile, windowed, сборка zapret встроена как _zapret.
# Сборка: build\build_qt.cmd (использует тот же .venv-build; системный Python не меняется).
# Tk-версия 0.4 собирается прежним build\build.cmd и остаётся для отката.

import os
from PyInstaller.utils.hooks import collect_submodules

BUILD_DIR = os.path.dirname(os.path.abspath(SPEC))          # ...\zapret-gui\build
PROJECT = os.path.dirname(BUILD_DIR)                        # ...\zapret-gui
ZAPRET = os.path.join(os.path.dirname(PROJECT),             # ...\vs code\zapret-discord-youtube-1.10.4
                      'zapret-discord-youtube-1.10.4')
ZAPRET = os.environ.get('ZAPRET_SOURCE', ZAPRET)
NAME = os.environ.get('EXE_NAME', 'Zapret-GUI-0.9.0')
TG_VENDOR = os.path.join(PROJECT, 'vendor', 'tg_ws_proxy')
import sys
sys.path.insert(0, TG_VENDOR)

if not os.path.isfile(os.path.join(ZAPRET, 'bin', 'winws.exe')):
    raise SystemExit('Не найдена сборка zapret: ' + ZAPRET)

a = Analysis(
    [os.path.join(PROJECT, 'launch_qt.py')],
    pathex=[PROJECT, BUILD_DIR, TG_VENDOR],
    binaries=[],
    datas=[(ZAPRET, '_zapret'), (os.path.join(PROJECT, 'assets'), 'assets'), (os.path.join(PROJECT, 'licenses'), 'licenses')],                          # bat/bin/lists/utils целиком
    hiddenimports=['app_qt', 'qt_common', 'core', 'runtime_paths', 'zapret_info',
                   'zapret_features', 'zapret_import', 'launch_qt',
                   'telegram_proxy', 'telegram_worker', 'telegram_network'] + collect_submodules('proxy'),
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'PIL', 'pytest', 'tkinter', 'matplotlib', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,          # windowed: без консоли
    version=None,
    icon=os.path.join(PROJECT, 'assets', 'app.ico'),
)
