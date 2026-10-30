"""Zapret Desk · разрешение путей в исходном и собранном (frozen) режимах.

Используется launch_runtime.py при запуске из EXE и (после интеграции
главным агентом — см. PACKAGING.md) app.py в исходном режиме.

Гарантии:
- пользовательские данные (runtime-копия сборки zapret, настройки, журналы)
  живут в %%LOCALAPPDATA%%\\ZapretDesk и не зависят от временной распаковки
  onefile (_MEIPASS удаляется при выходе);
- в исходном режиме поведение в точности прежнее: discover(BASE.parent).
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

BUNDLE_PREFIX = '_zapret'          # имя встроенной папки сборки внутри EXE
RUNTIME_DIRNAME = 'runtime'        # %LOCALAPPDATA%\ZapretDesk\runtime



def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))



def bundle_dir() -> Path:
    """Папка, где лежат ресурсы приложения (onefile: временная распаковка)."""
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    """Папка EXE в собранном режиме, папка проекта — в исходном."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Постоянные пользовательские данные: настройки, журналы, runtime-сборка."""
    d = Path(os.environ.get('LOCALAPPDATA', str(exe_dir()))) / 'ZapretDesk'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def runtime_root() -> Path:
    """Постоянная папка для копий встроенных сборок zapret."""
    d = data_dir() / RUNTIME_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_zapret() -> Path | None:
    """Встроенная сборка zapret внутри EXE, если она есть."""
    if not is_frozen():
        return None
    d = bundle_dir() / BUNDLE_PREFIX
    return d if (d / 'bin' / 'winws.exe').is_file() else None


def is_valid_build(path: Path) -> bool:
    return path.is_dir() and (path / 'bin' / 'winws.exe').is_file()


def source_roots() -> list[Path]:
    """Папки, в которых искать сборки zapret (аргументы для discover()).

    Исходный режим: только родитель папки проекта (прежнее поведение).
    Frozen: постоянный runtime и папка рядом с EXE.
    """
    roots: list[Path] = []
    if is_frozen():
        roots.append(runtime_root())
        roots.append(exe_dir())
    else:
        roots.append(exe_dir().parent)   # == BASE.parent в прежнем коде
    unique: list[Path] = []
    for r in roots:
        if r.is_dir() and r not in unique:
            unique.append(r)
    return unique


def bundle_name() -> str:
    """Имя встроенной сборки (например zapret-discord-youtube-1.10.4) для runtime-папки.

    Имя пишется модулем zapret_info.py при сборке EXE (build.cmd); в исходном
    режиме модуля нет — используется имя-заглушка.
    """
    try:
        import zapret_info
        name = str(getattr(zapret_info, 'BUNDLE_NAME', ''))
    except ImportError:
        return 'zapret-bundled'
    if name and all(ch not in name for ch in '\\/:*?"<>|'):
        return name
    return 'zapret-bundled'


def bootstrap_bundled() -> Path | None:
    """Копирует встроенную сборку в постоянную папку (один раз на версию).

    Возврат: путь постоянной сборки или None (нет встроенной / не удалось).
    Существующие пользовательские списки не перезаписываются: копируем в новую
    папку, а старые версии остаются на месте; discover() выберет свежую по mtime.
    """
    src = bundled_zapret()
    if src is None:
        return None
    name = bundle_name()
    dest = runtime_root() / name
    stamp = runtime_root() / (name + '.copied')
    if stamp.is_file() and is_valid_build(dest):
        return dest
    try:
        import shutil
        # Never remove or replace an existing runtime/user list during repair.
        dest.mkdir(parents=True, exist_ok=True)
        for original in src.rglob('*'):
            relative = original.relative_to(src)
            target = dest / relative
            if original.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with original.open('rb') as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
        stamp.write_text('copied from EXE bundle', encoding='utf-8')
        return dest
    except OSError as exc:
        log = data_dir() / 'runtime-error.log'
        try:
            log.write_text(f'Не удалось развернуть встроенную сборку в {dest}: {exc}\n', encoding='utf-8')
        except OSError:
            pass
        return None


def elevate_target() -> tuple[str, list[str], str]:
    """(программа, аргументы, рабочая папка) для перезапуска с повышением прав."""
    if is_frozen():
        exe = str(Path(sys.executable).resolve())
        return exe, ['--connect'], str(exe_dir())
    base = bundle_dir()
    pyw = Path(sys.executable).with_name('pythonw.exe')
    return str(pyw), [str(base / 'app.py'), '--connect'], str(base)
