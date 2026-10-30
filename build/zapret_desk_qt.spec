# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для Zapret Desk Qt: onefile, windowed, сборка zapret встроена как _zapret.
# Сборка: build\build_qt.cmd (использует тот же .venv-build; системный Python не меняется).
# Tk-версия 0.4 собирается прежним build\build.cmd и остаётся для отката.

import os

BUILD_DIR = os.path.dirname(os.path.abspath(SPEC))          # ...\zapret-gui\build
PROJECT = os.path.dirname(BUILD_DIR)                        # ...\zapret-gui
ZAPRET = os.path.join(os.path.dirname(PROJECT),             # ...\vs code\zapret-discord-youtube-1.10.4
                      'zapret-discord-youtube-1.10.4')
NAME = 'ZapretDeskQt'

if not os.path.isfile(os.path.join(ZAPRET, 'bin', 'winws.exe')):
    raise SystemExit('Не найдена сборка zapret: ' + ZAPRET)

a = Analysis(
    [os.path.join(PROJECT, 'launch_qt.py')],
    pathex=[PROJECT, BUILD_DIR],
    binaries=[],
    datas=[(ZAPRET, '_zapret')],                          # bat/bin/lists/utils целиком
    hiddenimports=['app_qt', 'qt_common', 'core', 'runtime_paths', 'zapret_info',
                   'zapret_features', 'zapret_import', 'launch_qt'],
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
    icon=None,
)
