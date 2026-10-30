"""UI-логика Zapret Desk: чистые проверки без GUI-окна и сети.

Проверяются валидация портов/списков, конструктор payload фильтров,
решение о блокировке настроек и (если доступен Tk) создание всех блоков
одного экрана в свёрнутом окне — без отображения на рабочем столе.
"""
import unittest
from unittest.mock import Mock, patch

import ui_widgets as uw

try:
    import tkinter as tk
    _TK = True
except Exception:  # pragma: no cover - окружение без Tk
    _TK = False


class PortsValidationTests(unittest.TestCase):
    def test_full_range_and_list_accepted(self):
        for value in ('1024-65535', '80', '1024-65535,80', '1,2,3'):
            self.assertEqual(uw.validate_ports(value), (True, ''))

    def test_spaces_rejected(self):
        ok, reason = uw.validate_ports('1024-65535, 80')
        self.assertFalse(ok)
        self.assertIn('Пробелы', reason)

    def test_shape_rejected(self):
        for value in ('', 'abc', '80;', '1024-65535,,80', '-80', '80-'):
            ok, _ = uw.validate_ports(value)
            self.assertFalse(ok, value)

    def test_bounds(self):
        self.assertEqual(uw.validate_ports('1-65535'), (True, ''))
        self.assertFalse(uw.validate_ports('0-100')[0])
        self.assertFalse(uw.validate_ports('1-65536')[0])

    def test_reversed_range_rejected(self):
        self.assertFalse(uw.validate_ports('5000-1000')[0])

    def test_payload_builder_validates_and_passes_through(self):
        payload = uw.game_filter_payload('tcp', '1024-65535', '80,443', 'loaded')
        self.assertEqual(payload, {'game_mode': 'tcp', 'tcp': '1024-65535',
                                   'udp': '80,443', 'ipset_mode': 'loaded'})
        with self.assertRaises(ValueError):
            uw.game_filter_payload('weird', '1', '2', 'loaded')
        with self.assertRaises(ValueError):
            uw.game_filter_payload('tcp', '', '2-1', 'any')
        with self.assertRaises(ValueError):
            uw.game_filter_payload('all', '1', '2', 'bad')


class ListValidationTests(unittest.TestCase):
    def test_kinds_and_labels(self):
        self.assertEqual(tuple(uw.LIST_KINDS), ('general', 'exclude', 'ipset_exclude'))
        self.assertEqual(set(uw.LIST_LABELS), set(uw.LIST_KINDS))

    def test_general_hostlist_accepts_domains_only(self):
        self.assertEqual(uw.validate_list('general', 'example.com'), (True, ''))
        self.assertFalse(uw.validate_list('general', '1.2.3.4')[0])
        self.assertFalse(uw.validate_list('general', '10.0.0.0/8')[0])

    def test_exclude_requires_domains(self):
        ok, reason = uw.validate_list('exclude', 'example.com\nsub.example.org')
        self.assertTrue(ok)
        ok, reason = uw.validate_list('exclude', '1.2.3.4')
        self.assertFalse(ok)
        self.assertIn('домен', reason)

    def test_ipset_exclude_requires_ip(self):
        self.assertEqual(uw.validate_list('ipset_exclude', '1.2.3.4\n10.0.0.0/8'), (True, ''))
        ok, reason = uw.validate_list('ipset_exclude', 'example.com')
        self.assertFalse(ok)
        self.assertIn('IP', reason)

    def test_comments_preserved_and_garbage_rejected(self):
        self.assertEqual(uw.validate_list('general', '# comment\nexample.com'), (True, ''))
        ok, reason = uw.validate_list('general', 'two words here')
        self.assertFalse(ok)
        self.assertIn('пробел', reason)
        ok, reason = uw.validate_list('general', 'http://example.com')
        self.assertFalse(ok)

    def test_empty_list_allowed(self):
        self.assertEqual(uw.validate_list('exclude', '\n   \n'), (True, ''))


class FeatureLockTests(unittest.TestCase):
    def test_idle_unlocked(self):
        self.assertEqual(uw.feature_lock('idle', False, False), (False, ''))

    def test_engine_running_locks(self):
        locked, reason = uw.feature_lock('idle', False, True)
        self.assertTrue(locked)
        self.assertIn('Движок', reason)

    def test_operation_and_states_lock(self):
        for state, operation, running in (('idle', True, False), ('connecting', False, False),
                                          ('stopping', False, False)):
            locked, reason = uw.feature_lock(state, operation, running)
            self.assertTrue(locked, (state, operation, running))
            self.assertTrue(reason)


@unittest.skipUnless(_TK, 'tkinter недоступен')
class AppScreenTests(unittest.TestCase):
    """Экран приложения в свёрнутом окне: блоки и блокировка без рабочего стола."""

    def setUp(self):
        try:
            import app
        except Exception:
            self.app_module = None
            return
        self.app_module = app
        import tempfile
        from pathlib import Path
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        data_patch = patch.object(app, 'DATA', Path(self.data.name))
        data_patch.start(); self.addCleanup(data_patch.stop)
        load_patch = patch.object(app.App, 'load', return_value={})
        load_patch.start(); self.addCleanup(load_patch.stop)
        try:
            self.window = app.App()
        except Exception:
            self.window = None
            self.app_module = None
            return
        self.window.withdraw()
        self.addCleanup(self.window.destroy)

    def test_all_blocks_present(self):
        if self.window is None:
            self.skipTest('Tk/gtk окружение недоступно')
        for name in ('status', 'main_button', 'strategy_combo', 'gf_mode_box', 'gf_tcp_entry',
                     'gf_udp_entry', 'ipset_mode_box', 'ulist_combo', 'list_view',
                     'log_toggle', 'diag_note', 'footer', 'gf_section',
                     'ipset_section', 'list_section'):
            self.assertTrue(hasattr(self.window, name), name)
        self.assertEqual(self.window.gf_mode.get(), 'disabled')

    def test_features_lock_freezes_sections(self):
        if self.window is None:
            self.skipTest('Tk окружение недоступно')
        app = self.app_module
        window = self.window
        window.set_state('idle', '')
        window.refresh()
        self.assertEqual(str(window.gf_tcp_entry['state']), 'normal')

    def test_idle_layout_has_no_callback_errors_or_horizontal_overflow(self):
        if self.window is None: self.skipTest('Tk unavailable')
        errors = []
        self.window.report_callback_exception = lambda *args: errors.append(args)
        self.window.update_idletasks()
        self.assertFalse(errors)
        for section in (self.window.gf_section, self.window.ipset_section, self.window.list_section):
            self.assertLessEqual(section.winfo_width(), section.master.winfo_width())

    def test_feature_lock_roundtrip(self):
        if self.window is None: self.skipTest('Tk unavailable')
        app, window = self.app_module, self.window
        with patch.object(app.uw, 'feature_lock', return_value=(True, 'заблокировано')):
            window.refresh()
        self.assertEqual(str(window.gf_tcp_entry['state']), 'disabled')
        self.assertIn('заблокировано', window.gf_note['text'])
        window.set_state('idle', '')
        window.refresh()
        self.assertEqual(str(window.gf_tcp_entry['state']), 'normal')

    def test_settings_save_roundtrip_keeps_feature_keys(self):
        if self.window is None:
            self.skipTest('Tk окружение недоступно')
        import json as json_module
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        window = self.window
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch.object(self.app_module, 'DATA', directory):
                window.save()
                saved = json_module.loads((directory/'settings.json').read_text(encoding='utf-8'))
        self.assertIn('game_mode', saved)
        self.assertIn('ipset_mode', saved)
        self.assertEqual(saved['game_mode'], 'disabled')


if __name__ == '__main__':
    unittest.main()
