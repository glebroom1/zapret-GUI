import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import zapret_features as zf


class ReadWriteFiltersTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_read_filters_defaults_without_files(self):
        result = zf.read_filters(self.root)
        self.assertEqual(result['game_mode'], 'disabled')
        self.assertEqual(result['tcp'], '1024-65535')
        self.assertEqual(result['udp'], '1024-65535')
        self.assertEqual(result['ipset_mode'], 'any')

    def test_write_filters_disabled_writes_inactive_ranges(self):
        zf.write_filters(self.root, 'disabled', '1024-65535', '1024-65535', 'none')
        flag = self.root / 'utils' / 'game_filter.enabled'
        self.assertEqual(flag.read_text(encoding='utf-8'), 'mode=disabled\ntcp=1024-65535\nudp=1024-65535\n')
        ipset = self.root / 'lists' / 'ipset-all.txt'
        self.assertEqual(ipset.read_text(encoding='utf-8'), '203.0.113.113/32\n')

    def test_write_filters_all_mode(self):
        zf.write_filters(self.root, 'all', '1000-2000', '3000-4000', 'any')
        flag = self.root / 'utils' / 'game_filter.enabled'
        self.assertEqual(flag.read_text(encoding='utf-8'), 'mode=all\ntcp=1000-2000\nudp=3000-4000\n')
        self.assertEqual((self.root / 'lists' / 'ipset-all.txt').read_text(encoding='utf-8'), '')

    def test_write_filters_tcp_mode_marks_udp_inactive(self):
        ipset = self.root / 'lists' / 'ipset-all.txt'
        ipset.parent.mkdir(parents=True, exist_ok=True)
        ipset.write_text('5.6.7.8/32\n', encoding='utf-8')
        backup = ipset.with_name('ipset-all.txt.bak-11112222')
        backup.write_text('9.9.9.9/32\n', encoding='utf-8')
        zf.write_filters(self.root, 'tcp', '5000-6000', '1024-65535', 'loaded')
        flag = self.root / 'utils' / 'game_filter.enabled'
        self.assertEqual(flag.read_text(encoding='utf-8'), 'mode=tcp\ntcp=5000-6000\nudp=1024-65535\n')
        self.assertEqual(ipset.read_text(encoding='utf-8'), '5.6.7.8/32\n')

    def test_write_filters_creates_unique_backups_and_keeps_original(self):
        ipset = self.root / 'lists' / 'ipset-all.txt'
        ipset.parent.mkdir(parents=True, exist_ok=True)
        ipset.write_text('9.9.9.9/32\n', encoding='utf-8')
        zf.write_filters(self.root, 'disabled', '1024-65535', '1024-65535', 'none')
        self.assertEqual(ipset.read_text(encoding='utf-8'), '203.0.113.113/32\n')
        backups = list(ipset.parent.glob('ipset-all.txt.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), '9.9.9.9/32\n')

    def test_write_filters_any_empties_list_and_backs_up(self):
        ipset = self.root / 'lists' / 'ipset-all.txt'
        ipset.parent.mkdir(parents=True, exist_ok=True)
        ipset.write_text('9.9.9.9/32\n', encoding='utf-8')
        zf.write_filters(self.root, 'disabled', '1024-65535', '1024-65535', 'any')
        self.assertEqual(ipset.read_text(encoding='utf-8'), '')
        backups = list(ipset.parent.glob('ipset-all.txt.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), '9.9.9.9/32\n')

    def test_write_filters_loaded_without_backup_raises(self):
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'disabled', '1024-65535', '1024-65535', 'loaded')

    def test_write_filters_invalid_mode_or_range_raises(self):
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'bogus', '1024-65535', '1024-65535', 'none')
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'all', '70000-70000', '1024-65535', 'none')
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'all', 'abc', '1024-65535', 'none')
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'all', '2000-1000', '1024-65535', 'none')
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'all', '1024-65535', '1024-65535', 'bogus')

    def test_ipset_loaded_none_any_loaded_roundtrip_preserves_real_list(self):
        path = zf.ipset_path(self.root); path.parent.mkdir(parents=True)
        original = '9.9.9.9/32\n8.8.8.0/24\n'; path.write_text(original)
        for mode in ('none', 'any', 'loaded'):
            zf.write_filters(self.root, 'all', '80,443', '50000-50100', mode)
            self.assertEqual(zf.read_filters(self.root)['ipset_mode'], mode)
        self.assertEqual(path.read_text(), original)

    def test_game_setting_failure_does_not_partially_write_flag(self):
        with self.assertRaises(zf.SettingsError):
            zf.write_filters(self.root, 'all', '80', '443', 'loaded')
        self.assertFalse(zf.game_filter_path(self.root).exists())

    def test_second_commit_failure_rolls_back_first_file(self):
        flag = zf.game_filter_path(self.root); flag.parent.mkdir(parents=True)
        flag.write_text('mode=disabled\ntcp=80\nudp=443\n')
        path = zf.ipset_path(self.root); path.parent.mkdir(parents=True)
        path.write_text('9.9.9.9/32\n')
        before = (flag.read_bytes(), path.read_bytes())
        real_replace = zf.os.replace
        calls = 0
        def replace(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('simulated disk failure')
            return real_replace(src, dst)
        with patch('zapret_features.os.replace', side_effect=replace):
            with self.assertRaises(OSError):
                zf.write_filters(self.root, 'all', '8080', '8443', 'none')
        self.assertEqual((flag.read_bytes(), path.read_bytes()), before)


class ReadFiltersValidationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_read_filters_invalid_mode_raises(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=bogus\ntcp=1024-65535\nudp=1024-65535\n', encoding='utf-8')
        with self.assertRaises(zf.SettingsError):
            zf.read_filters(self.root)

    def test_read_filters_invalid_range_raises(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=all\ntcp=70000-70000\nudp=1024-65535\n', encoding='utf-8')
        with self.assertRaises(zf.SettingsError):
            zf.read_filters(self.root)

    def test_read_filters_tcp_mode_inactive_udp(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=tcp\ntcp=5000-6000\nudp=1024-65535\n', encoding='utf-8')
        result = zf.read_filters(self.root)
        self.assertEqual(result['game_mode'], 'tcp')
        self.assertEqual(result['tcp'], '5000-6000')
        self.assertEqual(result['udp'], '1024-65535')

    def test_read_filters_disabled_returns_inactive_ranges(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=disabled\ntcp=1024-65535\nudp=1024-65535\n', encoding='utf-8')
        result = zf.read_filters(self.root)
        self.assertEqual(result['game_mode'], 'disabled')
        self.assertEqual(result['tcp'], '1024-65535')
        self.assertEqual(result['udp'], '1024-65535')


class UserListTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.path = self.root / 'lists' / 'list-exclude-user.txt'

    def tearDown(self):
        self.folder.cleanup()

    def test_read_user_list_defaults(self):
        self.assertEqual(zf.read_user_list(self.root, 'general'), '# Never leave this file empty\ndomain.example.abc\n')
        self.assertEqual(zf.read_user_list(self.root, 'exclude'), 'domain.example.abc\n')
        self.assertEqual(zf.read_user_list(self.root, 'ipset_exclude'), '203.0.113.113/32\n')

    def test_read_user_list_unknown_kind_raises(self):
        with self.assertRaises(zf.SettingsError):
            zf.read_user_list(self.root, 'bogus')

    def test_write_user_list_creates_unique_backup(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('domain.example.abc\n', encoding='utf-8')
        zf.write_user_list(self.root, 'exclude', 'discord.com\n')
        backups = list(self.path.parent.glob('list-exclude-user.txt.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), 'domain.example.abc\n')
        self.assertEqual(zf.read_user_list(self.root, 'exclude'), 'discord.com\n')

    def test_write_user_list_rejects_invalid_domain(self):
        with self.assertRaises(zf.SettingsError):
            zf.write_user_list(self.root, 'general', 'not valid domain!!\n')
        with self.assertRaises(zf.SettingsError):
            zf.write_user_list(self.root, 'general', 'domain..abc\n')
        self.assertFalse((self.root / 'lists' / 'list-general-user.txt').exists())

    def test_write_user_list_accepts_comment_and_valid_domain(self):
        zf.write_user_list(self.root, 'general', '# comment\ndiscord.com\n')
        self.assertEqual(zf.read_user_list(self.root, 'general'), '# comment\ndiscord.com\n')

    def test_write_user_list_ipset_exclude_rejects_invalid_ip(self):
        with self.assertRaises(zf.SettingsError):
            zf.write_user_list(self.root, 'ipset_exclude', '999.1.2.3/32\n')


class UpdateIpSetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.path = self.root / 'lists' / 'ipset-all.txt'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.folder.cleanup()

    def _response(self, payload, status=200):
        response = MagicMock()
        response.status = status
        response.getcode.return_value = status
        response.read.return_value = payload
        return response

    def test_update_ipset_valid_content(self):
        response = self._response(b'203.0.113.113/32\n5.6.7.8/32\n')
        with patch('zapret_features.urllib.request.urlopen', return_value=response) as mock_open:
            count = zf.update_ipset(self.root)
        self.assertEqual(count, 2)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '203.0.113.113/32\n5.6.7.8/32\n')
        mock_open.assert_called_once()

    def test_update_ipset_network_error_raises_settings_error(self):
        import urllib.error
        with patch('zapret_features.urllib.request.urlopen', side_effect=urllib.error.URLError('boom')):
            with self.assertRaises(zf.SettingsError):
                zf.update_ipset(self.root)

    def test_update_ipset_invalid_content_does_not_replace(self):
        response = self._response(b'not-an-ip/99\n')
        with patch('zapret_features.urllib.request.urlopen', return_value=response):
            with self.assertRaises(zf.SettingsError):
                zf.update_ipset(self.root)
        self.assertFalse(self.path.exists())

    def test_update_ipset_size_limit(self):
        response = self._response(b'x' * (zf.IPSET_MAX_BYTES + 1))
        with patch('zapret_features.urllib.request.urlopen', return_value=response):
            with self.assertRaises(zf.SettingsError):
                zf.update_ipset(self.root)

    def test_empty_update_does_not_enable_any(self):
        self.path.write_text('9.9.9.9/32\n')
        with patch('zapret_features.urllib.request.urlopen', return_value=self._response(b'')):
            with self.assertRaises(zf.SettingsError): zf.update_ipset(self.root)
        self.assertEqual(self.path.read_text(), '9.9.9.9/32\n')

    def test_update_ipset_creates_backup(self):
        self.path.write_text('1.2.3.4/32\n', encoding='utf-8')
        response = self._response(b'203.0.113.113/32\n')
        with patch('zapret_features.urllib.request.urlopen', return_value=response):
            count = zf.update_ipset(self.root)
        self.assertEqual(count, 1)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '203.0.113.113/32\n')
        backups = list(self.path.parent.glob('ipset-all.txt.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), '1.2.3.4/32\n')


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_diagnostics_is_local_and_read_only(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=all\ntcp=1024-65535\nudp=1024-65535\n', encoding='utf-8')
        ipset = self.root / 'lists' / 'ipset-all.txt'
        ipset.parent.mkdir(parents=True, exist_ok=True)
        ipset.write_text('203.0.113.113/32\n', encoding='utf-8')
        before = {p: p.read_text(encoding='utf-8') for p in (flag, ipset) if p.exists()}

        result = zf.diagnostics(self.root)

        self.assertEqual(set(result), {'game_filter', 'ipset', 'user_lists'})
        self.assertEqual(result['game_filter']['game_mode'], 'all')
        self.assertEqual(result['ipset']['mode'], 'none')
        self.assertEqual(set(result['user_lists']), {'general', 'exclude', 'ipset_exclude'})
        after = {p: p.read_text(encoding='utf-8') for p in (flag, ipset) if p.exists()}
        self.assertEqual(before, after)

    def test_diagnostics_reports_invalid_game_filter(self):
        flag = self.root / 'utils' / 'game_filter.enabled'
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('mode=whatever\ntcp=1\nudp=1\n', encoding='utf-8')
        result = zf.diagnostics(self.root)
        self.assertIn('error', result['game_filter'])


if __name__ == '__main__':
    unittest.main()
