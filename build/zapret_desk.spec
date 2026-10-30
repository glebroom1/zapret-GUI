# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для Zapret Desk: onefile, windowed, сборка zapret встроена как _zapret.
# Сборка: build\build.cmd (создаёт отдельный .venv-build, системный Python не меняет).

import os
from PyInstaller.utils.hooks import collect_submodules

BUILD_DIR = os.path.dirname(os.path.abspath(SPEC))          # ...\zapret-gui\build
PROJECT = os.path.dirname(BUILD_DIR)                        # ...\zapret-gui
ZAPRET = os.path.join(os.path.dirname(PROJECT),             # ...\vs code\zapret-discord-youtube-1.10.4
                      'zapret-discord-youtube-1.10.4')
ZAPRET_NAME = 'zapret-discord-youtube-1.10.4'

if not os.path.isfile(os.path.join(ZAPRET, 'bin', 'winws.exe')):
    raise SystemExit('Не найдена сборка zapret: ' + ZAPRET)

a = Analysis(
    [os.path.join(PROJECT, 'launch_runtime.py')],
    pathex=[PROJECT, BUILD_DIR],
    binaries=[],
    datas=[(ZAPRET, '_zapret')],                          # bat/bin/lists/utils целиком
    hiddenimports=['app', 'core', 'runtime_paths', 'zapret_info', 'zapret_features', 'ui_widgets'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'PIL', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ZapretDesk',
    debug=False,
    strip=False,
    upx=False,
    console=False,          # windowed: без консоли
    version=None,
    icon=None,
)
