import unittest
from unittest.mock import Mock, patch
import startup_admin


class StartupAdminTests(unittest.TestCase):
    def test_already_admin_does_not_relaunch(self):
        with patch.object(startup_admin, 'is_admin', return_value=True), \
                patch.object(startup_admin.ctypes, 'windll') as windows:
            self.assertTrue(startup_admin.ensure_admin())
            windows.shell32.ShellExecuteW.assert_not_called()

    def test_elevation_preserves_tray_without_connecting(self):
        with patch.object(startup_admin, 'is_admin', return_value=False), \
                patch.object(startup_admin.sys, 'argv', ['gui.exe', '--tray']), \
                patch.object(startup_admin.ctypes, 'windll') as windows:
            windows.shell32.ShellExecuteW.return_value = 42
            self.assertFalse(startup_admin.ensure_admin())
            args = windows.shell32.ShellExecuteW.call_args.args
            self.assertEqual(args[1], 'runas')
            self.assertIn('--tray', args[3])
            self.assertNotIn('--connect', args[3])
            windows.user32.MessageBoxW.assert_not_called()

    def test_cancelled_uac_stops_startup(self):
        with patch.object(startup_admin, 'is_admin', return_value=False), \
                patch.object(startup_admin.ctypes, 'windll') as windows:
            windows.shell32.ShellExecuteW.return_value = 5
            self.assertFalse(startup_admin.ensure_admin())
            windows.user32.MessageBoxW.assert_called_once()
