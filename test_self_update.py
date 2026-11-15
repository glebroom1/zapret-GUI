import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import self_update as u


class SelfUpdate(unittest.TestCase):
    def release(self):
        tag = 'v0.8.1-beta.5'
        name = 'Zapret-GUI-0.8.1-beta5.exe'
        body = b'MZ' + b'x' * 2046
        digest = hashlib.sha256(body).hexdigest()
        release = {'tag_name': tag, 'html_url': u.PREFIX + tag, 'prerelease': True, 'assets': [
            {'name': name, 'browser_download_url': u.DOWNLOAD + tag + '/' + name, 'size': len(body), 'digest': 'sha256:' + digest},
            {'name': 'update-windows.json', 'browser_download_url': u.DOWNLOAD + tag + '/update-windows.json'}]}
        manifest = {'schema': 1, 'platform': 'windows-x64', 'version': '0.8.1-beta.5', 'asset': name,
                    'sha256': digest, 'size': len(body)}
        return release, manifest, body

    def test_beta_lookup_ignores_android_and_older_stable(self):
        release, manifest, _ = self.release()
        android = {'tag_name': 'android-v9.0.0', 'html_url': 'https://bad.test'}
        with patch.object(u, 'json_url', side_effect=[[android, release], manifest]):
            self.assertEqual(u.latest_release('0.8.1-beta.4')['tag'], release['tag_name'])
        with patch.object(u, 'json_url', return_value=[release]):
            self.assertIsNone(u.latest_release('0.8.1'))

    def test_manifest_rejects_cross_repository_urls_and_corruption(self):
        release, manifest, _ = self.release()
        for key, value in [('platform', 'android'), ('version', '0.8.1-beta.1'), ('asset', '../x.exe'),
                           ('size', True), ('size', u.MAX_EXE + 1), ('sha256', 'bad')]:
            bad = dict(manifest, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                u.parse_manifest(release, bad, '0.8.1-beta.4')
        bad = copy.deepcopy(release)
        bad['assets'][0]['browser_download_url'] = 'https://github.com/attacker/repo/releases/download/a/x.exe'
        with self.assertRaises(ValueError):
            u.parse_manifest(bad, manifest, '0.8.1-beta.4')
        bad = copy.deepcopy(release); bad['assets'].append(bad['assets'][0])
        with self.assertRaises(ValueError):
            u.parse_manifest(bad, manifest, '0.8.1-beta.4')

    def test_redirects_are_https_and_exact_hosts(self):
        for url in ['http://github.com/a', 'https://github.com.evil.test/a', 'https://user@github.com/a',
                    'https://github.com:444/a', 'file:///a', 'https://evil.test/a']:
            self.assertFalse(u.allowed_download(url), url)
        self.assertTrue(u.allowed_download('https://release-assets.githubusercontent.com/file?sig=example'))

    def test_download_rejects_size_digest_and_non_exe(self):
        release, manifest, body = self.release()
        info = u.parse_manifest(release, manifest, '0.8.1-beta.4')
        with tempfile.TemporaryDirectory() as directory:
            for bad in [body[:-1], body + b'overflow', b'MZ' + b'y' * 2046]:
                with patch.object(u, 'open_url', return_value=io.BytesIO(bad)), self.assertRaises(ValueError):
                    u.download_release(info, directory)
            with patch.object(u, 'open_url', return_value=io.BytesIO(body)):
                path = u.download_release(info, directory)
            self.assertEqual(path.read_bytes(), body)
            self.assertFalse(list(Path(directory).rglob('*.part')))

    def test_same_path_replacement_preserves_other_files_and_backup(self):
        with tempfile.TemporaryDirectory(prefix='zapret update space ') as directory:
            root = Path(directory)
            target = root / "старое имя ' программа.exe"
            stage = root / 'stage.exe'
            settings = root / 'settings.json'
            settings.write_text('keep')
            target.write_bytes(b'old'); stage.write_bytes(b'new')
            backup = u.replace_transaction(stage, target, u.sha256(stage), u.sha256(target))
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual(backup.read_bytes(), b'old')
            self.assertEqual(settings.read_text(), 'keep')
            self.assertEqual(stage.read_bytes(), b'new')

    def test_write_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root/'target.exe'; stage = root/'stage.exe'
            target.write_bytes(b'old'); stage.write_bytes(b'new')
            replace = u.os.replace
            calls = 0
            def fail_once(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('simulated write failure')
                return replace(src, dst)
            with patch.object(u.os, 'replace', side_effect=fail_once), self.assertRaises(OSError):
                u.replace_transaction(stage, target, u.sha256(stage), u.sha256(target))
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(list(root.glob('.*')), [])

    def test_changed_old_exe_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'target.exe'; stage = Path(directory)/'stage.exe'
            target.write_bytes(b'old'); stage.write_bytes(b'new')
            old = u.sha256(target); target.write_bytes(b'user changed this')
            with self.assertRaises(ValueError):
                u.replace_transaction(stage, target, u.sha256(stage), old)
            self.assertEqual(target.read_bytes(), b'user changed this')

    def test_locked_target_does_not_remove_original(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'target.exe'; stage = Path(directory)/'stage.exe'
            target.write_bytes(b'old'); stage.write_bytes(b'new')
            with patch.object(u.os, 'replace', side_effect=PermissionError('locked')), self.assertRaises(TimeoutError):
                u.replace_transaction(stage, target, u.sha256(stage), u.sha256(target), timeout=0)
            self.assertEqual(target.read_bytes(), b'old')

    def test_failed_restart_rolls_back_and_relaunches_previous_exe(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root/'target.exe'; stage = root/'stage.exe'
            target.write_bytes(b'old'); stage.write_bytes(b'new')
            job = root/'job.json'
            job.write_text(json.dumps({'schema': 1, 'pid': 123, 'created': 0,
                'target': str(target), 'stage': str(stage), 'sha256': u.sha256(stage),
                'old_sha256': u.sha256(target), 'tray': False}), encoding='utf-8')
            with patch.object(u.sys, 'executable', str(stage)), \
                    patch('psutil.Process', side_effect=psutil.NoSuchProcess(123)), \
                    patch.object(u.subprocess, 'Popen', side_effect=[OSError('simulated launch failure'), object()]) as spawn, \
                    patch('ctypes.windll.user32.MessageBoxW'):
                self.assertEqual(u.apply_update(job), 1)
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(spawn.call_count, 2)
            self.assertFalse(json.loads((root/'result.json').read_text(encoding='utf-8'))['ok'])


if __name__ == '__main__':
    unittest.main()
