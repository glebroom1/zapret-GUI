"""Точка входа собранного EXE Zapret Desk Qt.

Запуск EXE:     ZapretDeskQt.exe               — GUI (Qt)
Автоподключение: ZapretDeskQt.exe --connect    — сразу предложение UAC и подключение
Тех. проверка:   ZapretDeskQt.exe --smoke      — без GUI и без прав: smoke_qt.json и завершение

Аналог launch_runtime.py (Tk 0.4); Qt-версия не заменяет её: обе версии
используют один и тот же runtime_paths, но их EXE собираются отдельно.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import runtime_paths


def smoke() -> int:
    """Техническая проверка без открытия GUI: что EXE видит и умеет развернуть."""
    result: dict = {'frozen': runtime_paths.is_frozen(),
                    'exe': str(Path(sys.executable).resolve()),
                    'bundle_dir': str(runtime_paths.bundle_dir()),
                    'data_dir': str(runtime_paths.data_dir()),
                    'errors': []}
    bundled = runtime_paths.bundled_zapret()
    result['bundled_zapret'] = str(bundled) if bundled else None
    if bundled is not None:
        dest = runtime_paths.bootstrap_bundled()
        if dest is None:
            result['errors'].append('не удалось развернуть встроенную сборку (runtime-error.log)')
        result['bootstrap_dest'] = str(dest) if dest else None
    try:
        from core import strategies, discover, parse_strategy, prepare_lists, validate_files
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
            prepare_lists(builds[0])
            for profile in strategies(builds[0]):
                validate_files(builds[0], parse_strategy(profile))
        else:
            result['errors'].append('не найдено стратегий')
    except Exception as exc:
        result['errors'].append(f'core: {exc!r}')
    try:
        import PySide6
        result['pyside6'] = PySide6.__version__
        import app_qt
        result['app_import'] = app_qt.App.__name__ == 'App'
        import qt_common
        result['theme'] = qt_common.ACCENT
    except Exception as exc:
        result['errors'].append(f'qt: {exc!r}')
    try:
        import zapret_features as features
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
    except Exception as exc:
        result['errors'].append(f'features: {exc!r}')
    # Импорт ZIP: синтетический архив, перенос пользовательских файлов, отказ паразитам.
    try:
        import tempfile
        import zipfile
        import zapret_import as zi

        def write_build(z, root='', *, good=True):
            files = ['bin/winws.exe', 'bin/WinDivert.dll', 'bin/WinDivert64.sys',
                     'bin/cygwin1.dll', 'general.bat', 'lists/list-general.txt']
            for name in files:
                z.writestr(root + name, b'smoke-mock')
            z.writestr(root + 'lists/list-general-user.txt', 'user.example\n')

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            good = base / 'good.zip'
            with zipfile.ZipFile(good, 'w') as z:
                write_build(z, 'zapret-smoke/')
            bad = base / 'bad.zip'
            with zipfile.ZipFile(bad, 'w') as z:
                write_build(z, 'zapret-smoke/')
                z.writestr('../escape.txt', b'no')
            dest = base / 'dest'
            installed = zi.import_zip(good, dest)
            report = zi.inspect_zip(good)
            bad_rejected = False
            try:
                zi.inspect_zip(bad)
            except zi.ImportError_:
                bad_rejected = True
            result['import_zip'] = {
                'install': installed.name,
                'strategies': len(report['strategies']),
                'baddies_rejected': bad_rejected,
            }
            if not bad_rejected or installed.name != 'zapret-smoke' \
                    or not report['strategies']:
                result['errors'].append('ошибка внутренней проверки импорта ZIP')
    except Exception as exc:
        result['errors'].append(f'import: {exc!r}')
    # Проверка контроллера без сети и процессов, как у Tk-версии.
    try:
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

            def running(self):
                return self.alive

            def stop(self):
                self.ready = self.alive = False
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
        result['errors'].append(f'controller: {exc!r}')
    target, args, workdir = runtime_paths.elevate_target()
    result['elevation_target'] = target
    result['elevation_args'] = args
    out = runtime_paths.data_dir() / 'smoke_qt.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


def main() -> int:
    argv = sys.argv[1:]
    if '--smoke' in argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        return smoke()
    import runtime_paths as rp
    rp.bootstrap_bundled()
    import app_qt
    return app_qt.main()


if __name__ == '__main__':
    sys.exit(main())
