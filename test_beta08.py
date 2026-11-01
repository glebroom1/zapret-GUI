import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from app_updates import parse_release, version_key, PREFIX
from single_instance import SingleInstance


class Updates(unittest.TestCase):
    def test_beta_to_stable_and_no_downgrade(self):
        self.assertGreater(version_key('v0.8'), version_key('0.8.0-beta.1'))
        self.assertIsNone(parse_release({'tag_name': 'v0.7'}, '0.8.0-beta.1'))
        result = parse_release({'tag_name':'v0.8', 'html_url': PREFIX+'v0.8'}, '0.8.0-beta.1')
        self.assertEqual(result['tag'], 'v0.8')

    def test_untrusted_urls_and_invalid_versions(self):
        with self.assertRaises(ValueError):
            parse_release({'tag_name':'v0.9', 'html_url':'https://example.com'}, '0.8')
        with self.assertRaises(ValueError):
            version_key('latest')
        self.assertIsNone(parse_release({'draft':True}, '0.8'))
        self.assertIsNone(parse_release({'prerelease':True}, '0.8'))


class Lifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_second_instance_restores_first(self):
        with tempfile.TemporaryDirectory() as folder, patch('single_instance.QStandardPaths.writableLocation', return_value=folder):
            first = SingleInstance()
            self.assertTrue(first.acquire())
            restored = Mock()
            first.listen(restored)
            second = SingleInstance()
            self.assertFalse(second.acquire())
            for _ in range(10):
                QCoreApplication.processEvents()
            restored.assert_called_once()
            first.server.close()
            first.lock.unlock()
            self.assertTrue(second.acquire())
            second.lock.unlock()

    def test_close_hides_without_stopping_connected_engine(self):
        from app_qt import App
        window = Mock()
        window.tray = object()
        window.exit_requested = False
        event = Mock()
        App.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.hide.assert_called_once()
        window.stop_engine.assert_not_called()

    def test_explicit_exit_detaches_engine(self):
        from app_qt import App
        window = Mock()
        window.exit_requested = True
        window.feature_busy = False
        window.engine.running.return_value = True
        window.operation_active = False
        window.state = 'connected'
        event = Mock()
        App.closeEvent(window, event)
        window.stop_engine.assert_not_called()
        window.engine.detach.assert_called_once()
        event.accept.assert_called_once()

    def test_no_tray_falls_back_to_clean_exit(self):
        from app_qt import App
        window = Mock()
        window.tray = None
        window.exit_requested = False
        window.feature_busy = False
        window.engine.running.return_value = False
        window.operation_active = False
        window.state = 'idle'
        event = Mock()
        App.closeEvent(window, event)
        event.accept.assert_called_once()
