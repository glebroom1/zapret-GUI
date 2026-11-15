"""GitHub downloads and same-path Windows self-update, with backup/rollback."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from app_updates import version_key, PREFIX

REPO = 'glebroom1/zapret-GUI'
API = f'https://api.github.com/repos/{REPO}/releases?per_page=30'
DOWNLOAD = f'https://github.com/{REPO}/releases/download/'
MAX_EXE = 250 * 1024 * 1024


def allowed_download(url):
    try:
        u = urllib.parse.urlsplit(url)
        return u.scheme == 'https' and not u.username and not u.password and u.port in (None, 443) and u.hostname in (
            'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        if not allowed_download(url):
            raise ValueError('Недопустимый адрес перенаправления обновления')
        return super().redirect_request(request, fp, code, msg, headers, url)


def open_url(url):
    if url != API and not allowed_download(url):
        raise ValueError('Недопустимый адрес обновления')
    request = urllib.request.Request(url, headers={'User-Agent': 'zapret-GUI-updater', 'Accept': '*/*'})
    return urllib.request.build_opener(SafeRedirect()).open(request, timeout=15)


def json_url(url, limit=1024 * 1024):
    with open_url(url) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('Ответ сервера слишком большой')
    return json.loads(payload)


def release_asset(release, name):
    assets = [a for a in release.get('assets', []) if a.get('name') == name]
    if len(assets) != 1 or assets[0].get('browser_download_url') != DOWNLOAD + release['tag_name'] + '/' + name:
        raise ValueError('Не найден однозначный файл обновления в релизе')
    return assets[0]


def parse_manifest(release, manifest, current):
    tag = release['tag_name']
    version = tag.removeprefix('v')
    name, digest, size = (manifest.get(k, '') for k in ('asset', 'sha256', 'size'))
    if manifest.get('schema') != 1 or manifest.get('platform') != 'windows-x64' \
            or manifest.get('version') != version or version_key(version) <= version_key(current) \
            or not re.fullmatch(r'Zapret-GUI-[0-9A-Za-z.-]+\.exe', name) \
            or not re.fullmatch(r'[a-f0-9]{64}', digest) \
            or type(size) is not int or not 1024 <= size <= MAX_EXE:
        raise ValueError('Некорректный манифест обновления')
    asset = release_asset(release, name)
    if asset.get('size') != size or (asset.get('digest') and asset['digest'] != 'sha256:' + digest):
        raise ValueError('Размер или хеш файла не совпал с GitHub')
    return {'tag': tag, 'url': release['html_url'], 'download': asset['browser_download_url'],
            'sha256': digest, 'size': size, 'name': name}


def latest_release(current):
    releases = json_url(API)
    if not isinstance(releases, list):
        raise ValueError('Некорректный список релизов')
    candidates = []
    for release in releases:
        tag = release.get('tag_name', '') if isinstance(release, dict) else ''
        if not re.fullmatch(r'v?\d+\.\d+(?:\.\d+)?(?:-beta\.\d+)?', tag):
            continue  # Android has its own tag namespace.
        if release.get('draft') or (release.get('prerelease') and '-beta.' not in current):
            continue
        if version_key(tag) <= version_key(current):
            continue
        if release.get('html_url') != PREFIX + tag:
            raise ValueError('Неожиданный адрес релиза')
        candidates.append(release)
    if not candidates:
        return None
    release = max(candidates, key=lambda r: version_key(r['tag_name']))
    manifest = release_asset(release, 'update-windows.json')
    return parse_manifest(release, json_url(manifest['browser_download_url'], 65536), current)


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def download_release(release, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not allowed_download(release['download']) or not re.fullmatch(r'[a-f0-9]{64}', release['sha256']):
        raise ValueError('Некорректное обновление')
    stage = Path(tempfile.mkdtemp(prefix='release-', dir=directory)) / 'update.exe'
    part = stage.with_suffix('.part')
    deadline = time.monotonic() + 240
    try:
        with open_url(release['download']) as source, part.open('xb') as dest:
            total = 0
            while block := source.read(65536):
                total += len(block)
                if total > release['size'] or total > MAX_EXE or time.monotonic() > deadline:
                    raise ValueError('Превышен размер или срок загрузки')
                dest.write(block)
            dest.flush()
            os.fsync(dest.fileno())
        if total != release['size'] or sha256(part) != release['sha256']:
            raise ValueError('Проверка SHA256 обновления не пройдена')
        with part.open('rb') as source:
            if source.read(2) != b'MZ':
                raise ValueError('Файл обновления не является EXE')
        part.replace(stage)
        return stage
    finally:
        part.unlink(missing_ok=True)


def prepare_handoff(stage, release, target, *, tray=False):
    """Start the verified new EXE as a helper before the old GUI exits."""
    import psutil
    stage, target = Path(stage).resolve(), Path(target).resolve()
    if stage == target or not target.is_file() or sha256(stage) != release['sha256']:
        raise ValueError('Не удалось подтвердить пути обновления')
    job = stage.parent / 'job.json'
    data = {'schema': 1, 'pid': os.getpid(), 'created': psutil.Process().create_time(),
            'target': str(target), 'stage': str(stage), 'sha256': release['sha256'],
            'old_sha256': sha256(target), 'tray': bool(tray)}
    job.write_text(json.dumps(data), encoding='utf-8')
    subprocess.Popen([str(stage), '--apply-update', str(job)], cwd=stage.parent,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                     close_fds=True, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return job


def replace_transaction(stage, target, digest, old_digest, *, timeout=60):
    """Replace only the expected existing file; rollback if replacement fails."""
    stage, target = Path(stage).resolve(), Path(target).resolve()
    if not target.is_file() or stage == target or sha256(stage) != digest or sha256(target) != old_digest:
        raise ValueError('Файлы изменились после подготовки обновления')
    fd, name = tempfile.mkstemp(prefix='.' + target.stem + '-update-', suffix='.exe', dir=target.parent)
    os.close(fd)
    incoming = Path(name)
    backup = incoming.with_suffix('.previous.exe')
    deadline = time.monotonic() + timeout
    try:
        shutil.copyfile(stage, incoming)
        if sha256(incoming) != digest:
            raise ValueError('Ошибка копирования обновления')
        while True:
            try:
                if sha256(target) != old_digest:
                    raise ValueError('Исходный EXE изменился')
                os.replace(target, backup)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('EXE занят. Закройте другие копии и Telegram-прокси, затем повторите обновление.')
                time.sleep(.25)
        try:
            os.replace(incoming, target)
        except OSError:
            os.replace(backup, target)
            raise
        return backup
    finally:
        incoming.unlink(missing_ok=True)


def apply_update(job_path):
    """Entry point for the downloaded frozen helper; does not initialize Qt."""
    import psutil
    job_path = Path(job_path).resolve()
    status_path = job_path.parent / 'result.json'
    try:
        if job_path.stat().st_size > 65536:
            raise ValueError('job too large')
        job = json.loads(job_path.read_text(encoding='utf-8'))
        stage, target = Path(job['stage']).resolve(), Path(job['target']).resolve()
        if job.get('schema') != 1 or stage.parent != job_path.parent or stage != Path(sys.executable).resolve() \
                or target.suffix.lower() != '.exe' or stage == target \
                or type(job['pid']) is not int or not re.fullmatch(r'[a-f0-9]{64}', job['sha256']):
            raise ValueError('Некорректный план обновления')
        try:
            owner = psutil.Process(job['pid'])
            if abs(owner.create_time() - job['created']) > .01 or Path(owner.exe()).resolve() != target:
                raise ValueError('Не совпал процесс старой версии')
            owner.wait(timeout=90)
        except psutil.NoSuchProcess:
            pass
        backup = replace_transaction(stage, target, job['sha256'], job['old_sha256'])
        try:
            # Existing --smoke diagnostics can exercise the real frozen handoff
            # without opening a window or changing network/autostart settings.
            arguments = ['--smoke'] if '--smoke' in sys.argv else (['--tray'] if job.get('tray') else [])
            process = subprocess.Popen([str(target)] + arguments, cwd=target.parent, close_fds=True,
                                       env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
            time.sleep(4)
            if process.poll() not in (None, 0):
                raise RuntimeError('Новая версия завершилась с ошибкой')
        except Exception:
            os.replace(backup, target)
            subprocess.Popen([str(target)], cwd=target.parent, close_fds=True,
                             env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
            raise
        try:
            shutil.move(str(backup), str(stage.parent / 'previous.exe'))
        except OSError:
            pass
        status_path.write_text(json.dumps({'ok': True, 'target': str(target)}), encoding='utf-8')
        return 0
    except Exception as error:
        status_path.write_text(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), encoding='utf-8')
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'Обновление не применено. ' + str(error), 'zapret GUI', 0x10)
        except Exception:
            pass
        return 1
