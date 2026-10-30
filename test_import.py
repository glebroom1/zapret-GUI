"""Запрет обхода: проверка и импорт ZIP со сборкой zapret; ДНС-резервные проверки."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import zapret_import as zi
from core import check_network

GOOD_FILES = ['bin/winws.exe', 'bin/WinDivert.dll', 'bin/WinDivert64.sys', 'bin/cygwin1.dll',
              'general.bat', 'general (ALT).bat', 'lists/list-general.txt']


def write_zip(path, root='build/', extra=(), symlink=False):
    with zipfile.ZipFile(path, 'w') as z:
        for name in GOOD_FILES:
            z.writestr(root+name, b'mock')
        z.writestr(root+'lists/list-general-user.txt', 'zip-user.example\n')
        for name in extra:
            z.writestr(name, b'extra')
        if symlink:
            info = zipfile.ZipInfo('chain/link')
            info.external_attr = 0o120777 << 16
            z.writestr(info, 'bin')


def prev_build(path: Path) -> Path:
    (path/'lists').mkdir(parents=True, exist_ok=True)
    (path/'lists/list-general-user.txt').write_text('local-user.example\n', encoding='utf-8')
    (path/'lists/list-general-user.txt.bak-999').write_text('old.example\n', encoding='utf-8')
    (path/'utils').mkdir(parents=True, exist_ok=True)
    (path/'utils/game_filter.enabled').write_text('mode=all\n', encoding='utf-8')
    return path


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_nested_zip(self):
        good = self.base/'good.zip'
        write_zip(good, 'zapret-discord-youtube-1.11.0/')
        report = zi.inspect_zip(good)
        self.assertEqual(report['build_name'], 'zapret-discord-youtube-1.11.0')
        self.assertIn('general (ALT).bat', report['strategies'])
        self.assertGreater(report['entry_count'], 6)

    def test_files_in_archive_root(self):
        good = self.base/'good.zip'
        write_zip(good, '')
        report = zi.inspect_zip(good)
        self.assertEqual(report['root'], '')
        self.assertEqual(report['build_name'], good.stem)

    def test_missing_winws_rejected(self):
        broke = self.base/'broke.zip'
        with zipfile.ZipFile(broke, 'w') as z:
            z.writestr('build/general.bat', 'start "z" /min "%BIN%winws.exe" --x')
        with self.assertRaisesRegex(zi.ImportError_, 'winws'):
            zi.inspect_zip(broke)

    def test_missing_windivert_rejected(self):
        sick = self.base/'sick.zip'
        with zipfile.ZipFile(sick, 'w') as z:
            z.writestr('build/bin/winws.exe', b'mock')
            z.writestr('build/general.bat', 'x')
        with self.assertRaisesRegex(zi.ImportError_, 'отсутствуют'):
            zi.inspect_zip(sick)

    def test_no_strategies_rejected(self):
        sick = self.base/'sick.zip'
        with zipfile.ZipFile(sick, 'w') as z:
            for name in ('bin/winws.exe', 'bin/WinDivert.dll', 'bin/WinDivert64.sys',
                         'bin/cygwin1.dll'):
                z.writestr('build/'+name, b'mock')
        with self.assertRaisesRegex(zi.ImportError_, 'general'):
            zi.inspect_zip(sick)

    def test_path_traversal_rejected(self):
        evil = self.base/'evil2.zip'
        with zipfile.ZipFile(evil, 'w') as z:
            z.writestr('../evil.txt', b'x')
        with self.assertRaisesRegex(zi.ImportError_, 'корня'):
            zi.inspect_zip(evil)

    def test_absolute_path_rejected(self):
        archive = self.base/'a2.zip'
        write_zip(archive)
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr(zipfile.ZipInfo('/abs.txt'), b'x')
        with self.assertRaisesRegex(zi.ImportError_, 'абсолютный'):
            zi.inspect_zip(archive)

    def test_raw_backslash_name_rejected(self):
        archive = self.base/'a3.zip'
        write_zip(archive)
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr(zipfile.ZipInfo('dir\\x.txt'), b'x')
        # Python zipfile обычно нормализует '\\'; проверяем оба исхода честно.
        raw_names = zipfile.ZipFile(archive).namelist()
        if any('\\' in n for n in raw_names):
            with self.assertRaisesRegex(zi.ImportError_, 'слэши'):
                zi.inspect_zip(archive)
        else:
            report = zi.inspect_zip(archive)
            self.assertTrue(report['strategies'])

    def test_symlink_rejected(self):
        bad = self.base/'bad.zip'
        write_zip(bad, symlink=True)
        with self.assertRaisesRegex(zi.ImportError_, 'Символические'):
            zi.inspect_zip(bad)

    def test_not_zip_or_missing_file(self):
        plain = self.base/'plain.txt'
        plain.write_text('not a zip', encoding='utf-8')
        with self.assertRaisesRegex(zi.ImportError_, 'ZIP'):
            zi.inspect_zip(plain)
        with self.assertRaisesRegex(zi.ImportError_, 'не найден'):
            zi.inspect_zip(self.base/'ghost.zip')


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.good = self.base/'good.zip'
        write_zip(self.good, 'build/')

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_and_preserve_local_user_files(self):
        dest = self.base/'dest'
        previous = prev_build(self.base/'previous')
        install = zi.import_zip(self.good, dest, previous=previous, log=lambda *_: None)
        self.assertEqual(install, dest/'build')
        self.assertTrue((install/'bin/winws.exe').is_file())
        self.assertEqual((install/'lists/list-general-user.txt').read_text(encoding='utf-8'),
                         'local-user.example\n')
        self.assertTrue((install/'lists/list-general-user.txt.bak-999').exists())
        self.assertEqual((install/'utils/game_filter.enabled').read_text(encoding='utf-8'),
                         'mode=all\n')
        self.assertFalse((install/'lists/list-general-user.txt')
                         .read_text(encoding='utf-8').startswith('zip-user'),
                         'локальный список перевешивает содержимое архива')

    def test_reimport_keeps_prev_copy(self):
        dest = self.base/'dest'
        zi.import_zip(self.good, dest, log=lambda *_: None)
        zi.import_zip(self.good, dest, log=lambda *_: None)
        names = [p.name for p in dest.iterdir()]
        self.assertIn('build', names)
        self.assertTrue(any(n.startswith('build.prev-') for n in names), names)

    def test_failure_keeps_dest_untouched(self):
        dest = self.base/'dest'
        zi.import_zip(self.good, dest, log=lambda *_: None)
        marker = (dest/'build'/'general.bat').read_bytes()
        evil = self.base/'evil.zip'
        with zipfile.ZipFile(evil, 'w') as z:
            for name in GOOD_FILES:
                z.writestr('build/'+name, b'mock2')
            z.writestr('../escape.txt', b'x')
        with self.assertRaises(zi.ImportError_):
            zi.import_zip(evil, dest, log=lambda *_: None)
        self.assertTrue((dest/'build'/'bin/winws.exe').is_file())
        self.assertEqual((dest/'build'/'general.bat').read_bytes(), marker)
        self.assertEqual([p.name for p in dest.iterdir()], ['build'])

    def test_engine_rejected_during_import_path_is_separate(self):
        # Сам импорт не касается winws-процессов: никаких subprocess в модуле.
        import inspect as _inspect
        source = _inspect.getsource(zi)
        self.assertNotIn('Popen', source)
        self.assertNotIn('tasklist', source)


class CheckNetworkDnsTests(unittest.TestCase):
    """Резерв: getaddrinfo-отказ у цели при живых контрольных → DoH/IP-проверка канала."""

    def test_dns_failure_recovers_via_doh_and_adds_note(self):
        with patch('core.probe', side_effect=[
                (False, None, '<urlopen error [Errno 11001] getaddrinfo failed>'),   # YouTube
                (True, 10, ''),                                                      # Discord
                (True, 5, ''), (True, 6, '')]), \
             patch('core.probe_via_doh', return_value=(True, 300, '')):
            results = check_network({'YouTube': 'https://www.youtube.com/generate_204',
                                     'Discord': 'https://discord.com/api/v10/gateway'})
        self.assertTrue(results['YouTube'][0])
        self.assertNotIn('Internet TCP', results)
        note_key = 'YouTube DNS'
        self.assertIn(note_key, results)
        self.assertIn('getaddrinfo', results[note_key][2])
        self.assertIn('DoH', results[note_key][2])

    def test_dns_failure_with_dead_controls_is_not_masked(self):
        with patch('core.probe', return_value=(False, None, 'timeout')):
            results = check_network({'YouTube': 'https://www.youtube.com/generate_204'})
        for name in ('YouTube', 'Internet 1', 'Internet 2'):
            self.assertFalse(results[name][0])

    def test_success_gets_fake_ip_note_when_route_is_router_fake_ip(self):
        fake = (('IPv4', 1, 2, '', ('198.18.0.7', 443)),)
        with patch('core.probe', side_effect=[(True, 10, ''), (True, 5, ''), (True, 6, '')]), \
             patch('core.socket.getaddrinfo', return_value=fake):
            results = check_network({'YouTube': 'https://www.youtube.com/generate_204'})
        self.assertTrue(results['YouTube'][0])
        note_key = 'YouTube DNS'
        self.assertIn(note_key, results)
        self.assertIn('fake-IP', results[note_key][2])
        self.assertIn('роутер', results[note_key][2])

    def test_healthy_result_without_fake_ip_keeps_no_note(self):
        with patch('core.probe', side_effect=[(True, 10, ''), (True, 10, ''),
                                              (True, 5, ''), (True, 6, '')]), \
             patch('core.socket.getaddrinfo',
                   return_value=(('IPv4', 1, 2, '', ('142.250.0.1', 443)),)):
            results = check_network()
        self.assertFalse(any(k.endswith(' DNS') for k in results), list(results))


if __name__ == '__main__':
    unittest.main()
