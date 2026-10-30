"""Точка входа собранного EXE Zapret Desk.

Запуск EXE:     ZapretDesk.exe                — GUI
Автоподключение: ZapretDesk.exe --connect     — GUI сразу предложит UAC и начнёт подключение
Тех. проверка:   ZapretDesk.exe --smoke        — без GUI и без прав: пишет smoke.json и завершается

В исходном режиме используется app.py напрямую; launch_runtime.py нужен только
PyInstaller. Запуск исходников через «python launch_runtime.py» тоже возможен.
"""
from __future__ import annotations
import ctypes
import json
import os
import sys
from pathlib import Path

import runtime_paths


def main() -> int:
    argv = sys.argv[1:]
    if '--smoke' in argv:
        return smoke()
    zapret = runtime_paths.bootstrap_bundled()
    if zapret is None and runtime_paths.is_frozen() and runtime_paths.bundled_zapret() is not None:
        # Развернуть сборку не удалось — работа возможна только со сборкой рядом с EXE;
        # app.py покажет «Сборка не найдена», детали в runtime-error.log / startup-error.log.
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    import app
    app.App().mainloop()
    return 0


def smoke() -> int:
    """Техническая проверка без открытия GUI: что EXE видит и умеет развернуть."""
    result: dict = {'frozen': runtime_paths.is_frozen(),
                    'exe': str(Path(sys.executable).resolve()),
                    'bundle_dir': str(runtime_paths.bundle_dir()),
                    'data_dir': str(runtime_paths.data_dir()),
                    'errors': []}
    bundled = runtime_paths.bundled_zapret()
    result['bundled_zapret'] = str(bundled) if bundled else None
    marker = runtime_paths.bundle_dir() / runtime_paths.BUNDLE_PREFIX / 'BUNDLE_NAME.txt'
    try:
        raw = marker.read_bytes()
    except OSError:
        raw = None
    result['bundle_name_marker'] = 'есть' if marker.is_file() else 'нет'
    if raw is not None:
        result['bundle_name_bytes'] = raw[:64].hex()
    try:
        result['bundle_name'] = runtime_paths.bundle_name()
    except Exception as exc:
        result['bundle_name'] = 'ОШИБКА: '+repr(exc)
    dest = None
    if bundled is not None:
        dest = runtime_paths.bootstrap_bundled()
        if dest is None:
            result['errors'].append('не удалось развернуть встроенную сборку (runtime-error.log)')
    result['bootstrap_dest'] = str(dest) if dest else None
    try:
        from core import strategies, discover
        roots = runtime_paths.source_roots()
        result['source_roots'] = [str(r) for r in roots]
        builds = []
        for r in roots:
            try:
                builds.extend(discover(r))
            except OSError as exc:
                result['errors'].append('discover(%s): %s' % (r, exc))
        builds = sorted(set(builds), key=lambda p: p.stat().st_mtime, reverse=True)
        result['builds_found'] = [str(b) for b in builds]
        if builds:
            result['strategies'] = [p.stem for p in strategies(builds[0])]
            from core import parse_strategy, prepare_lists, validate_files
            prepare_lists(builds[0])
            for profile in strategies(builds[0]):
                validate_files(builds[0], parse_strategy(profile))
        else:
            result['errors'].append('не найдено встроенных стратегий')
        from app import App
        result['app_import'] = App.__name__ == 'App'
        import zapret_features as features
        import ui_widgets
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            test_root = Path(folder)
            features.write_filters(test_root, 'all', '80,443', '50000-50100', 'none')
            features.write_user_list(test_root, 'general', 'example.com')
            state = features.read_filters(test_root)
            result['features'] = {'game_mode': state['game_mode'], 'ipset_mode': state['ipset_mode'],
                                  'user_list_roundtrip': features.read_user_list(test_root, 'general').strip() == 'example.com'}
            if state['game_mode'] != 'all' or state['ipset_mode'] != 'none' or not result['features']['user_list_roundtrip']:
                result['errors'].append('ошибка внутренней проверки настроек')
        target, args, workdir = runtime_paths.elevate_target()
        result['elevation_target'] = target
        result['elevation_args'] = args
        # Exercise the real frozen controller without network or process mutations.
        import threading
        from core import connect_profiles
        class SmokeEngine:
            session_log = None
            ready = False
            alive = False
            attempts = 0
            def start(self, *args, **kwargs):
                self.attempts += 1
                self.ready = self.alive = True
                return 'ready'
            def running(self): return self.alive
            def stop(self): self.ready = self.alive = False
        engine = SmokeEngine()
        replies = iter([{'service': (False, None, 'simulated'), 'control': (True, 1, '')},
                        {'service': (True, 1, '')}])
        status, _, _ = connect_profiles(engine, [Path('A.bat'), Path('B.bat')], {'service': 'simulated'},
            threading.Event(), lambda *args: None, grace=0, check=lambda _: next(replies))
        engine.stop()
        result['simulated_rotation'] = {'status': status, 'attempts': engine.attempts}
        if status != 'connected' or engine.attempts != 2:
            result['errors'].append('ошибка внутренней проверки автоподбора')
    except Exception as exc:
        result['errors'].append(f'core: {exc!r}')
    try:
        import tkinter
        result['tkinter'] = str(tkinter.TkVersion)
    except Exception as exc:
        result['errors'].append(f'tkinter: {exc!r}')
    out = runtime_paths.data_dir() / 'smoke.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
