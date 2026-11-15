"""Check frozen Python code against these sources and check embedded user lists."""
import hashlib
import json
import marshal
from pathlib import Path
import types
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / 'dist' / 'Zapret-GUI-0.9.0.exe'
archive = CArchiveReader(str(EXE))
pyz = archive.open_embedded_archive('PYZ.pyz')


def normalized(value):
    if isinstance(value, types.CodeType):
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
                  'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names',
                  'co_varnames', 'co_freevars', 'co_cellvars', 'co_name', 'co_qualname',
                  'co_firstlineno', 'co_linetable', 'co_exceptiontable')
        return tuple((name, normalized(getattr(value, name))) for name in fields)
    if isinstance(value, tuple):
        return tuple(normalized(v) for v in value)
    return value


modules = ['connection_health', 'self_update', 'update_ui', 'app_qt', 'qt_common', 'core', 'runtime_paths', 'engine_session', 'autostart',
           'single_instance', 'startup_admin', 'app_updates', 'strategy_history',
           'zapret_import', 'zapret_features', 'telegram_proxy', 'telegram_worker',
           'telegram_network', 'telegram_ui', 'process_control']
checks = {}
for name in modules:
    path = ROOT / (name + '.py')
    expected = compile(path.read_bytes(), path.name, 'exec')
    actual = pyz.extract(name)
    assert normalized(actual) == normalized(expected), name
    checks[name] = hashlib.sha256(path.read_bytes()).hexdigest()
expected = compile((ROOT / 'launch_qt.py').read_bytes(), 'launch_qt.py', 'exec')
assert normalized(marshal.loads(archive.extract('launch_qt'))) == normalized(expected)
checks['launch_qt'] = hashlib.sha256((ROOT / 'launch_qt.py').read_bytes()).hexdigest()
for path in (ROOT / 'vendor' / 'tg_ws_proxy' / 'proxy').glob('*.py'):
    name = 'proxy' if path.stem == '__init__' else 'proxy.' + path.stem
    assert normalized(pyz.extract(name)) == normalized(compile(path.read_bytes(), path.name, 'exec')), name
    checks[name] = hashlib.sha256(path.read_bytes()).hexdigest()
for base in ('list-general-user.txt', 'list-exclude-user.txt', 'ipset-exclude-user.txt'):
    names = [n for n in archive.toc if n.replace('\\', '/').endswith('_zapret/lists/' + base)]
    assert len(names) == 1 and archive.extract(names[0]) == b'', base
result = dict(ok=True, source_modules=len(checks), modules=checks,
              exe_sha256=hashlib.sha256(EXE.read_bytes()).hexdigest(),
              embedded_personal_lists_empty=True)
(ROOT / 'build' / 'tg-build-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in result.items() if key != 'modules'}))
