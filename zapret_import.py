"""Импорт архива ZIP со сборкой zapret-discord-youtube.

Только явный пользовательский импорт: выбор ZIP → проверка структуры →
безопасная распаковка → перенос пользовательских списков → установка рядом
с текущими сборками. При любой ошибке состояние dest_root не меняется:
распаковка идёт во временную папку, замена существующей сборки — через
резервное переименование с откатом. Движок здесь не запускается и не
останавливается: импорт выполняется при остановленном собственном движке.

`inspect_folder` определяет тип источника (папка полной сборки, папка
списков пользователя, ZIP); `import_folder` ставит полную сборку из папки
или ZIP; `import_user_folder` ставит пользовательские списки из папки.
"""
from __future__ import annotations
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from pathlib import Path

import zapret_features as _zf

# Лимиты защиты от архивных бомб и мусорных структур.
MAX_ZIP_BYTES = 400 * 1024 * 1024        # сам архив
MAX_TOTAL_BYTES = 900 * 1024 * 1024      # распакованный объём
MAX_ENTRIES = 20000
MAX_NAME_LEN = 240
WINWS = 'bin/winws.exe'
REQUIRED_BIN = ('bin/winws.exe', 'bin/WinDivert.dll', 'bin/WinDivert64.sys', 'bin/cygwin1.dll')
PRESERVE_DIRS = {'lists': ('list-general-user.txt', 'list-exclude-user.txt',
                           'ipset-exclude-user.txt'),
                 'utils': ('game_filter.enabled',)}
S_DOS_DIR_ATTR = 0x10


class ImportError_(ValueError):
    pass


def _safe_relative(name: str) -> str:
    """Нормализованный относительный путь без выхода из корня. ValueError при нарушении."""
    if not name:
        raise ImportError_('пустой путь в архиве')
    if name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ImportError_('абсолютный путь в архиве: '+repr(name))
    if '\\' in name:
        raise ImportError_('обратные слэши в имени: '+repr(name))
    parts = []
    reserved = {'con', 'prn', 'aux', 'nul'}
    reserved |= {f'com{n}' for n in range(1, 10)} | {f'lpt{n}' for n in range(1, 10)}
    for part in name.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ImportError_('выход из корня в архиве: '+repr(name))
        if part.split('.')[0].lower() in reserved:
            raise ImportError_('зарезервированное имя устройства: '+repr(name))
        if any(ch in part for ch in '<>:"|?*') or re.search(r'[\x00-\x1f]', part):
            raise ImportError_('недопустимые символы в имени: '+repr(name))
        if not part.strip():
            raise ImportError_('пустая часть пути: '+repr(name))
        parts.append(part)
    result = '/'.join(parts)
    if not result or len(result) > MAX_NAME_LEN:
        raise ImportError_('слишком длинный или пустой путь: '+repr(name[:64]))
    return result


def _entry_kind(info: zipfile.ZipInfo) -> str:
    """'file' | 'dir' | 'link'. Символические ссылки запрещены."""
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return 'link'
    if info.is_dir() or info.filename.endswith('/') or stat.S_ISDIR(mode):
        return 'dir'
    return 'file'


def _find_build_root(names: list[str]) -> str:
    """Общий корень сборки внутри ZIP: '' когда bin/winws.exe в корне архива."""
    prefix = None
    for name in names:
        if name == WINWS:
            return ''
        if name.endswith('/'+WINWS):
            root = name[:-len(WINWS)]
            if prefix is None or len(root) < len(prefix):
                prefix = root
    if prefix is None:
        raise ImportError_('В архиве нет bin/winws.exe.')
    return prefix


def _user_list_file(source: Path, kind: str):
    """Файл пользовательского списка в папке source: lists/<name> или <name> в корне."""
    filename = _zf.USER_LIST_FILES.get(kind)
    if not filename:
        return None
    for candidate in (source / 'lists' / filename, source / filename):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _user_kinds(source: Path) -> list[str]:
    return [kind for kind in sorted(_zf.USER_LIST_KINDS) if _user_list_file(source, kind) is not None]


def inspect_zip(zip_path: Path) -> dict:
    """Проверка архива без распаковки. Возвращает отчёт; ImportError_ при отказе."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ImportError_('Файл не найден: '+str(zip_path))
    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise ImportError_(f'Архив {size/1048576:.0f} МБ превышает лимит '
                           f'{MAX_ZIP_BYTES//1048576} МБ.')
    if not zipfile.is_zipfile(zip_path):
        raise ImportError_('Это не ZIP-архив (или он повреждён).')
    result = {'name': zip_path.name, 'archive_bytes': size}
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ImportError_(f'Слишком много записей в архиве: {len(infos)} > {MAX_ENTRIES}.')
        total = 0
        for info in infos:
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ImportError_(f'Распакованный объём превысит лимит '
                                   f'{MAX_TOTAL_BYTES//1048576} МБ.')
        if any(i.flag_bits & 0x1 for i in infos):
            raise ImportError_('Архив защищён паролем — не поддерживается.')
        entries = []
        for info in infos:
            kind = _entry_kind(info)
            if kind == 'link':
                raise ImportError_('Символические ссылки в архиве запрещены: '+info.filename)
            entries.append((kind, _safe_relative(info.filename if kind != 'dir'
                                                 else info.filename.rstrip('/'))))
        names = [clean for _, clean in entries]
        root = _find_build_root(names)
        strategy_names = [clean[len(root):] for kind, clean in entries
                          if kind == 'file' and clean.startswith(root)
                          and re.fullmatch(r'general[^/]*\.bat', clean[len(root):].lower())]
        lowered = {clean.lower() for _, clean in entries}
        missing = [root+path for path in REQUIRED_BIN if (root+path).lower() not in lowered]
        if missing:
            raise ImportError_('В сборке отсутствуют: '+', '.join(missing))
        if not strategy_names:
            raise ImportError_('В корне сборки нет файлов general*.bat.')
        result.update(root=root,
                      build_name=root.rstrip('/') or Path(zip_path.stem).name,
                      entry_count=len(infos),
                      unpacked_bytes=total,
                      strategies=sorted(strategy_names))
        return result


def inspect_folder(source) -> dict:
    """Определить тип источника: 'build' (папка/ ZIP полной сборки), 'user' (папка списков).

    Папка полной сборки — с bin/winws.exe и general*.bat; папка со списками
    пользователя — с files list-*-user.txt (возможно внутри lists). ZIP
    делегирует проверке inspect_zip. Отчёт содержит тип, путь и признаки.
    """
    source = Path(source)
    if source.is_file():
        report = inspect_zip(source)
        report['type'] = 'build'
        report['source'] = 'zip'
        return report
    if not source.is_dir():
        raise ImportError_('Папка не найдена: '+str(source))
    if (source / WINWS).is_file():
        missing = [rel for rel in REQUIRED_BIN if not (source / rel).is_file()]
        if missing:
            raise ImportError_('В папке полной сборки отсутствуют: '+', '.join(missing)+'. '+str(source))
        strategies = sorted(p.name for p in source.glob('general*.bat') if p.is_file())
        if not strategies:
            raise ImportError_('В папке нет файлов general*.bat: '+str(source))
        return {'type': 'build', 'source': 'folder', 'path': str(source),
                'build_name': source.name, 'strategies': strategies}
    kinds = _user_kinds(source)
    if kinds:
        return {'type': 'user', 'source': 'folder', 'path': str(source),
                'kinds': kinds}
    raise ImportError_('Не удалось определить тип папки (нет bin/winws.exe и списков): '+str(source))


def _copy_preserve(previous: Path | None, target: Path, log) -> int:
    """Перенести пользовательские списки/флаг фильтра из прежней сборки. Число файлов."""
    if previous is None or not previous.is_dir() or previous == target:
        return 0
    moved = 0
    for subdir, names in PRESERVE_DIRS.items():
        source_dir = previous/subdir
        if not source_dir.is_dir():
            continue
        (target/subdir).mkdir(parents=True, exist_ok=True)
        for name in names:
            source = source_dir/name
            destination = target/subdir/name
            if source.is_file():
                # Файлы текущей сборки перевешивают файлы архива: список пользователя
                # собран на машине; в свежем ZIP он может быть заглушкой.
                shutil.copy2(source, destination)
                moved += 1
                log(f'Перенесён пользовательский файл: {subdir}/{name}')
        for backup in source_dir.glob('*-user.txt.bak-*'):
            destination = target/subdir/backup.name
            shutil.copy2(backup, destination)
            moved += 1
    return moved


def import_zip(zip_path: Path, dest_root: Path, previous: Path | None = None,
               log=lambda *_: None) -> Path:
    """Установить сборку из ZIP в dest_root. Возврат — путь установленной сборки.

    Порядок: проверка → распаковка во временную папку → перенос пользовательских
    файлов из previous → замена сборки с тем же именем через резервное
    переименование. При ошибке временное удаляется, прежнее имя восстанавливается
    со своими файлами.
    """
    zip_path, dest_root = Path(zip_path), Path(dest_root)
    report = inspect_zip(zip_path)
    name = report['build_name']
    dest_root.mkdir(parents=True, exist_ok=True)
    install = dest_root/name
    temp = dest_root / ('.import-'+uuid.uuid4().hex)
    saved = None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temp)   # допустимо: все пути проверены в inspect_zip
        extracted = temp/report['root'].rstrip('/') if report['root'] else temp
        if not extracted.is_dir() or not (extracted/WINWS).is_file():
            raise ImportError_('После распаковки структура сборки не подтвердилась.')
        moved = _copy_preserve(previous, extracted, log)
        if install.exists():
            saved = dest_root/(name+'.prev-'+uuid.uuid4().hex)
            shutil.move(str(install), str(saved))
            log(f'Существующая сборка сохранена как {saved.name}')
        shutil.move(str(extracted), str(install))
        if temp.is_dir():
            shutil.rmtree(temp, ignore_errors=True)
        report['moved'] = moved
        log(f'Установлена сборка {name}: стратегий {len(report["strategies"])}, '
            f'пользовательских файлов перенесено {moved}.')
        return install
    except Exception as exc:
        if saved is not None:
            if install.exists():
                shutil.rmtree(install, ignore_errors=True)
            shutil.move(str(saved), str(install))
        if temp.is_dir():
            shutil.rmtree(temp, ignore_errors=True)
        log('Импорт отменён, прежняя сборка не изменена: '+(str(exc) if isinstance(exc, ImportError_)
                                                          else repr(exc)))
        if isinstance(exc, ImportError_):
            raise
        raise ImportError_('Импорт не удался: '+(str(exc) or repr(exc))) from exc


def _reject_symlinks(root: Path) -> None:
    """Запрет root-папки и дочерних ссылок/junction/reparse без обхода."""
    def reject(path):
        try:
            attributes = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & 0x400:
            raise ImportError_('Ссылка/junction/reparse запрещена: '+str(path))
    root = root.absolute()
    for path in (root, *root.parents):
        reject(path)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for entry in list(dirnames) + list(filenames):
            candidate = Path(dirpath) / entry
            reject(candidate)


def import_folder(source, destination, previous: Path | None = None,
                  log=lambda *_: None) -> Path:
    """Установить полную сборку zapret из папки (или ZIP) в destination.

    source — папка с bin/winws.exe и general*.bat (тип определяется через
    inspect_folder; ZIP тонет в импорте_zip). destination — родительская
    папка для установленной сборки. previous — текущая сборка, пользовательские
    файлы из которой переносятся. При ошибке состояние destination не меняется.
    """
    source, destination = Path(source), Path(destination)
    if source.is_file():
        return import_zip(source, destination, previous=previous, log=log)
    report = inspect_folder(source)
    if report.get('type') != 'build':
        raise ImportError_('Это не папка полной сборки: '+str(source))
    source_res, destination_res = source.resolve(), destination.resolve()
    if source_res == destination_res or source_res in destination_res.parents \
            or destination_res in source_res.parents:
        raise ImportError_('Папка источника и назначение не должны совпадать '
                           'или находиться внутри друг друга: '+str(source)+' / '+str(destination))
    _reject_symlinks(source)
    _reject_symlinks(destination)
    if previous is not None:
        _reject_symlinks(Path(previous))
    entries, unpacked = 0, 0
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            entries += 1
            unpacked += path.stat().st_size
            if entries > MAX_ENTRIES or unpacked > MAX_TOTAL_BYTES:
                raise ImportError_(f'Папка превышает лимиты: файлов {entries}/{MAX_ENTRIES}, '
                                   f'объём {unpacked/1048576:.0f} МБ/{MAX_TOTAL_BYTES//1048576} МБ.')
    install = destination / source.name
    temp = destination / ('.import-'+uuid.uuid4().hex)
    saved = None
    try:
        _reject_symlinks(source)
        shutil.copytree(source, temp, symlinks=False)
        moved = _copy_preserve(previous, temp, log)
        if install.exists():
            saved = destination/(source.name+'.prev-'+uuid.uuid4().hex)
            shutil.move(str(install), str(saved))
            log(f'Существующая сборка сохранена как {saved.name}')
        shutil.move(str(temp), str(install))
        if temp.is_dir():
            shutil.rmtree(temp, ignore_errors=True)
        report['moved'] = moved
        log(f'Установлена сборка {source.name}: стратегий {len(report["strategies"])}, '
            f'пользовательских файлов перенесено {moved}.')
        return install
    except Exception as exc:
        if saved is not None:
            if install.exists():
                shutil.rmtree(install, ignore_errors=True)
            shutil.move(str(saved), str(install))
        if temp.is_dir():
            shutil.rmtree(temp, ignore_errors=True)
        log('Импорт отменён, прежняя сборка не изменена: '+(str(exc) if isinstance(exc, ImportError_)
                                                          else repr(exc)))
        if isinstance(exc, ImportError_):
            raise
        raise ImportError_('Импорт не удался: '+(str(exc) or repr(exc))) from exc


def _commit_user_lists(root: Path, updates: dict[str, bytes], log) -> int:
    """Stage replacements and attempt every rollback on failure.

    Successful imports keep backups. Failed restoration keeps the old copy for
    manual recovery. This does not provide atomicity across a power loss.
    """
    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    committed: list[str] = []

    def cleanup(paths, errors):
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except Exception as exc:
                errors.append(f'Cleanup {path}: {exc!r}')

    try:
        for rel, data in updates.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
            staged[rel] = temp
            temp.write_bytes(data)
            if target.exists():
                backup = target.with_name(target.name + '.bak-' + uuid.uuid4().hex)
                backups[rel] = backup
                shutil.copy2(target, backup)
        for rel, temp in staged.items():
            os.replace(temp, root / rel)
            committed.append(rel)
    except Exception as original:
        errors = []
        recovery = set()
        for rel in reversed(committed):
            target = root / rel
            backup = backups.get(rel)
            try:
                if backup is not None:
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except Exception as exc:
                if backup is not None:
                    recovery.add(backup)
                    errors.append(f'Rollback {target}: {exc!r}; recovery backup: {backup}')
                else:
                    errors.append(f'Rollback {target}: {exc!r}; remove newly created file manually')
        cleanup(staged.values(), errors)
        cleanup((p for p in backups.values() if p not in recovery), errors)
        message = f'User-list import failed: {original!r}'
        if errors:
            message += '; ' + '; '.join(errors)
        try:
            log(message)
        except Exception:
            pass  # A failing logger must not hide the commit/rollback failures.
        raise ImportError_(message) from original
    errors = []
    cleanup(staged.values(), errors)
    if errors:
        raise ImportError_('Lists committed; backups retained; ' + '; '.join(errors))
    return len(committed)


def import_user_folder(source, root, log=lambda *_: None) -> dict:
    """Установить пользовательские списки из папки source в lists/ сборки root.

    source — папка со списками пользователя (files list-*-user.txt, можно внутри lists).
    root — корень сборки, куда пишутся lists/. Запись через zapret_features:
    валидация доменов/IP, резервная копия и откат при ошибке. BAT не выполняется.
    """
    source, root = Path(source), Path(root)
    _reject_symlinks(source)
    report = inspect_folder(source)
    if report.get('type') != 'user':
        raise ImportError_('Это не папка со списками: '+str(source))
    _reject_symlinks(root)
    updates: dict[str, bytes] = {}
    touched: list[str] = []
    for kind in report['kinds']:
        path = _user_list_file(source, kind)
        if path is None:
            continue
        text = path.read_text(encoding='utf-8-sig')
        try:
            _zf.validate_user_text(kind, text)  # общая предвалидация всех списков до записи
        except _zf.SettingsError as exc:
            raise ImportError_('Пользовательский список не прошёл валидацию: '+str(exc)) from exc
        if not any(s.strip() and not s.lstrip().startswith('#') for s in text.splitlines()):
            text = _zf.USER_LIST_DEFAULTS[kind]
        if not text.endswith('\n'):
            text += '\n'
        updates[str(Path('lists') / _zf.USER_LIST_FILES[kind])] = text.encode('utf-8')
        touched.append(kind)
    try:
        moved = _commit_user_lists(root, updates, log)
    except ImportError_:
        raise
    except Exception as exc:
        raise ImportError_('Импорт списков не удался: '+(str(exc) or repr(exc))) from exc
    for kind in touched:
        log(f'Пользовательский список обновлён: {kind}')
    return {'moved': moved, 'kinds': touched}
