"""Backend импорта папки и определения типа источника (0.7).

zapret_import: inspect_folder / import_folder / import_user_folder.
Тесты на временных копиях; реальная локальная сборка zapret — как источник.
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import zapret_import as zi
import zapret_features as zf

REAL_BUILD = (Path(__file__).parent.parent / 'zapret-discord-youtube-1.10.4')


def make_build(root: Path, name: str = 'build') -> Path:
    build = root / name
    (build / 'bin').mkdir(parents=True, exist_ok=True)
    for rel in ('bin/winws.exe', 'bin/WinDivert.dll', 'bin/WinDivert64.sys', 'bin/cygwin1.dll'):
        (build / rel).write_bytes(b'mock')
    (build / 'general.bat').write_text(
        'start "zapret" /min "%BIN%winws.exe" --wf-tcp=443\n --wf-udp=443\n', encoding='utf-8')
    (build / 'lists').mkdir(parents=True, exist_ok=True)
    (build / 'lists' / 'list-general.txt').write_text('# standard\n', encoding='utf-8')
    return build


def make_user_dir(root: Path, name: str = 'userlists') -> Path:
    user = root / name
    (user / 'lists').mkdir(parents=True, exist_ok=True)
    (user / 'lists' / 'list-general-user.txt').write_text('user.example\n', encoding='utf-8')
    (user / 'lists' / 'list-exclude-user.txt').write_text('exclude.example\n', encoding='utf-8')
    (user / 'lists' / 'ipset-exclude-user.txt').write_text('203.0.113.113/32\n', encoding='utf-8')
    return user


def write_build_zip(path: Path, root: str = 'build/') -> Path:
    with zipfile.ZipFile(path, 'w') as z:
        for rel, data in (('bin/winws.exe', b'mock'), ('bin/WinDivert.dll', b'mock'),
                          ('bin/WinDivert64.sys', b'mock'), ('bin/cygwin1.dll', b'mock'),
                          ('general.bat', b'mock'), ('lists/list-general.txt', b'# standard\n')):
            z.writestr(root + rel, data)
        z.writestr(root + 'lists/list-general-user.txt', 'zip-user.example\n')
    return path


class InspectFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_folder_detected(self):
        build = make_build(self.base)
        report = zi.inspect_folder(build)
        self.assertEqual(report['type'], 'build')
        self.assertEqual(report['source'], 'folder')
        self.assertEqual(report['build_name'], 'build')
        self.assertEqual(report['strategies'], ['general.bat'])

    def test_user_folder_detected_with_lists_subdir(self):
        user = make_user_dir(self.base)
        report = zi.inspect_folder(user)
        self.assertEqual(report['type'], 'user')
        self.assertEqual(sorted(report['kinds']), ['exclude', 'general', 'ipset_exclude'])

    def test_unknown_folder_rejected(self):
        with self.assertRaises(zi.ImportError_):
            zi.inspect_folder(self.base)

    def test_missing_folder_rejected(self):
        with self.assertRaises(zi.ImportError_):
            zi.inspect_folder(self.base / 'ghost')

    def test_folder_with_bin_but_no_strategies_rejected(self):
        build = make_build(self.base)
        (build / 'general.bat').unlink()
        with self.assertRaises(zi.ImportError_):
            zi.inspect_folder(build)

    def test_zip_delegates_to_inspect_zip(self):
        zip_path = write_build_zip(self.base / 'good.zip')
        report = zi.inspect_folder(zip_path)
        self.assertEqual(report['type'], 'build')
        self.assertEqual(report['source'], 'zip')
        self.assertEqual(report['build_name'], 'build')

    def test_build_folder_missing_required_bin_rejected(self):
        build = self.base / 'broken-build'
        (build / 'bin').mkdir(parents=True)
        (build / 'bin' / 'winws.exe').write_bytes(b'mock')
        (build / 'general.bat').write_text('start "z" /min "%BIN%winws.exe" --x\n', encoding='utf-8')
        with self.assertRaises(zi.ImportError_):
            zi.inspect_folder(build)


class ImportUserFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_user_folder_writes_validated_lists_with_backup(self):
        user = make_user_dir(self.base, 'incoming-user')
        root = make_build(self.base, 'target-build')
        (root / 'lists' / 'list-general-user.txt').write_text('old.example\n', encoding='utf-8')
        result = zi.import_user_folder(user, root, log=lambda *_: None)
        self.assertEqual(result['moved'], 3)
        self.assertEqual((root / 'lists' / 'list-general-user.txt').read_text(encoding='utf-8'),
                         'user.example\n')
        backups = list((root / 'lists').glob('list-*-user.txt.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), 'old.example\n')

    def test_import_user_folder_rejects_invalid_domain_and_keeps_root(self):
        user = make_user_dir(self.base, 'incoming-user')
        (user / 'lists' / 'list-general-user.txt').write_text('bad host!!\n', encoding='utf-8')
        root = make_build(self.base, 'target-build')
        (root / 'lists' / 'list-general-user.txt').write_text('old.example\n', encoding='utf-8')
        with self.assertRaises(zi.ImportError_):
            zi.import_user_folder(user, root, log=lambda *_: None)
        self.assertEqual((root / 'lists' / 'list-general-user.txt').read_text(encoding='utf-8'),
                         'old.example\n')

    def test_import_user_folder_rejects_build_folder(self):
        build = make_build(self.base, 'the-build')
        with self.assertRaises(zi.ImportError_):
            zi.import_user_folder(build, self.base / 'target-build')

    def test_import_user_folder_rejects_unknown_folder(self):
        with self.assertRaises(zi.ImportError_):
            zi.import_user_folder(self.base, self.base / 'target-build')

    def test_invalid_later_list_blocks_all_writes(self):
        source = make_user_dir(self.base, 'incoming-user')
        (source / 'lists' / 'list-exclude-user.txt').write_text('bad host!!\n', encoding='utf-8')
        root = make_build(self.base, 'target-build')
        (root / 'lists' / 'list-general-user.txt').write_text('old.example\n', encoding='utf-8')
        with self.assertRaises(zi.ImportError_):
            zi.import_user_folder(source, root, log=lambda *_: None)
        self.assertEqual((root / 'lists' / 'list-general-user.txt').read_text(encoding='utf-8'),
                         'old.example\n', 'предвалидация всех списков — запись не начиналась')
        self.assertFalse((root / 'lists' / 'list-exclude-user.txt').exists())

    def test_commit_failure_rolls_back_committed_lists(self):
        source = make_user_dir(self.base, 'incoming-user')
        root = make_build(self.base, 'target-build')
        lists = root / 'lists'
        (lists / 'list-general-user.txt').write_text('old.example\n', encoding='utf-8')
        (lists / 'list-exclude-user.txt').write_text('old-exclude\n', encoding='utf-8')
        (lists / 'ipset-exclude-user.txt').write_text('old-ipset\n', encoding='utf-8')
        real_replace = os.replace
        blocked = lists / 'ipset-exclude-user.txt'
        def fake_replace(src, dst):
            if Path(dst) == blocked:
                raise OSError('simulated commit failure')
            return real_replace(src, dst)
        with patch('zapret_import.os.replace', side_effect=fake_replace):
            with self.assertRaises(zi.ImportError_):
                zi.import_user_folder(source, root, log=lambda *_: None)
        self.assertEqual((lists / 'list-general-user.txt').read_text(encoding='utf-8'), 'old.example\n')
        self.assertEqual((lists / 'list-exclude-user.txt').read_text(encoding='utf-8'), 'old-exclude\n')
        self.assertEqual((lists / 'ipset-exclude-user.txt').read_text(encoding='utf-8'), 'old-ipset\n')
        leftovers = [p.name for p in lists.iterdir() if '.tmp-' in p.name or '.bak-' in p.name]
        self.assertEqual(leftovers, [], 'нет временных и бэкапных остатков после отката')

    def test_symlinked_user_source_rejected(self):
        user = make_user_dir(self.base, 'user-sym')
        os.symlink(str(user / 'lists' / 'list-general-user.txt'), str(user / 'lists' / 'list-link.txt'),
                   target_is_directory=False)
        root = make_build(self.base, 'target-build')
        with self.assertRaises(zi.ImportError_):
            zi.import_user_folder(user, root, log=lambda *_: None)
        self.assertEqual(list((root / 'lists').glob('list-*-user.txt')), [],
                         'символическая ссылка в источнике — отказ, запись не шла')


class ImportFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_folder_from_build_dir_copies_with_user_preserve(self):
        source = make_build(self.base, 'zapret-incoming')
        (source / 'lists' / 'list-general-user.txt').write_text('zip-user.example\n', encoding='utf-8')
        previous = make_build(self.base, 'zapret-prev')
        (previous / 'lists' / 'list-general-user.txt').write_text('local-user.example\n', encoding='utf-8')
        destination = self.base / 'dest'
        destination.mkdir(parents=True, exist_ok=True)
        install = destination / 'zapret-incoming'
        install.mkdir()
        (install / 'bin').mkdir()
        (install / 'bin' / 'winws.exe').write_bytes(b'old')
        result = zi.import_folder(source, destination, previous=previous, log=lambda *_: None)
        self.assertEqual(result, install)
        self.assertTrue((install / 'bin' / 'winws.exe').is_file())
        self.assertEqual((install / 'lists' / 'list-general-user.txt').read_text(encoding='utf-8'),
                         'local-user.example\n', 'локальные пользовательские файлы перевешивают папку-источник')
        backups = list(destination.glob('zapret-incoming.prev-*'))
        self.assertEqual(len(backups), 1, 'существующая сборка сохранена как prev-')

    def test_import_folder_from_build_dir_rolls_back_on_failure(self):
        source = make_build(self.base, 'zapret-incoming')
        destination = self.base / 'dest'

        def boom(src, dst):
            raise OSError('simulated move failure')
        with patch('zapret_import.shutil.move', side_effect=boom):
            with self.assertRaises(zi.ImportError_):
                zi.import_folder(source, destination, log=lambda *_: None)
        self.assertFalse((destination / 'zapret-incoming').exists(), 'при ошибке установка откатывается')

    def test_import_folder_from_zip_dispatches_to_import_zip(self):
        zip_path = write_build_zip(self.base / 'good.zip')
        install = zi.import_folder(zip_path, self.base / 'dest', log=lambda *_: None)
        self.assertEqual(install, self.base / 'dest' / 'build')
        self.assertTrue((install / 'bin' / 'winws.exe').is_file())

    def test_import_folder_rejects_user_folder(self):
        user = make_user_dir(self.base, 'userlists')
        with self.assertRaises(zi.ImportError_):
            zi.import_folder(user, self.base / 'dest')

    def test_source_inside_destination_rejected_without_mutation(self):
        source = make_build(self.base / 'outer', 'build')
        destination = self.base / 'outer'
        with self.assertRaises(zi.ImportError_):
            zi.import_folder(source, destination, log=lambda *_: None)
        self.assertTrue((source / 'bin' / 'winws.exe').is_file(), 'источник не тронут')
        self.assertFalse(list(destination.glob('.import-*')), 'после отказа нет временных папок')

    def test_symlinked_child_in_build_rejected(self):
        source = make_build(self.base, 'zapret-sym')
        os.symlink(str(source / 'bin'), str(source / 'bin-link'), target_is_directory=True)
        destination = self.base / 'dest-sym'
        with self.assertRaises(zi.ImportError_):
            zi.import_folder(source, destination, log=lambda *_: None)
        self.assertFalse((destination / source.name).exists(),
                         'дочерний symlink в сборке — отказ без установки')


@unittest.skipUnless(REAL_BUILD.is_dir(), 'реальная локальная сборка zapret не найдена')
class RealLocalBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_build_folder_inspected_as_build(self):
        report = zi.inspect_folder(REAL_BUILD)
        self.assertEqual(report['type'], 'build')
        self.assertTrue((REAL_BUILD / 'bin' / 'winws.exe').is_file())
        self.assertGreaterEqual(len(report['strategies']), 1)

    def test_real_user_lists_validated_and_imported_into_temp_root(self):
        user = self.base / 'real-user'
        (user / 'lists').mkdir(parents=True, exist_ok=True)
        copied = []
        for kind, filename in sorted(zf.USER_LIST_FILES.items()):
            src = zf.user_list_path(REAL_BUILD, kind)
            if src.is_file():
                shutil.copy2(src, user / 'lists' / filename)
                copied.append(kind)
        root = self.base / 'real-target'
        (root / 'lists').mkdir(parents=True, exist_ok=True)
        for kind in copied:
            zf.validate_user_text(kind, zf.read_user_list(REAL_BUILD, kind))
        result = zi.import_user_folder(user, root, log=lambda *_: None)
        self.assertEqual(result['moved'], len(copied))

    def test_real_build_folder_copied_and_imported_into_temp(self):
        source = self.base / REAL_BUILD.name
        shutil.copytree(REAL_BUILD, source)
        dest = self.base / 'real-dest'
        install = zi.import_folder(source, dest, log=lambda *_: None)
        self.assertEqual(install, dest / REAL_BUILD.name)
        self.assertTrue((install / 'bin' / 'winws.exe').is_file())
        self.assertTrue((install / 'lists' / 'list-general-user.txt').is_file())
        report = zi.inspect_folder(install)
        self.assertEqual(report['type'], 'build')
        self.assertGreaterEqual(len(report['strategies']), 1)


if __name__ == '__main__':
    unittest.main()

