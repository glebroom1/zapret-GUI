"""Fault injection uses temporary files only."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zapret_import as zi


class RecoveryTests(unittest.TestCase):
    def test_double_failure_keeps_backup_and_continues_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in 'abc':
                (root/name).write_bytes(b'old')
            replace = os.replace
            def fail(src, dst):
                if Path(dst).name == 'c' and '.tmp-' in str(src):
                    raise OSError('commit c failed')
                if Path(dst).name == 'b' and '.bak-' in str(src):
                    raise OSError('rollback b failed')
                return replace(src, dst)
            messages = []
            with patch.object(zi.os, 'replace', side_effect=fail):
                with self.assertRaises(zi.ImportError_) as caught:
                    zi._commit_user_lists(root, dict.fromkeys('abc', b'new'), messages.append)
            self.assertEqual((root/'a').read_bytes(), b'old')
            self.assertEqual((root/'b').read_bytes(), b'new')
            self.assertEqual((root/'c').read_bytes(), b'old')
            backups = list(root.glob('*.bak-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'old')
            self.assertIn(str(backups[0]), str(caught.exception))
            self.assertIn(str(root/'b'), str(caught.exception))
            self.assertIn('commit c failed', str(caught.exception))
            self.assertIn('rollback b failed', str(caught.exception))
            self.assertIn('commit c failed', str(caught.exception.__cause__))
            self.assertEqual(messages, [str(caught.exception)])
            self.assertEqual(list(root.glob('*.tmp-*')), [])

    def test_new_file_removed_even_after_other_rollback_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'b').write_bytes(b'old')
            replace = os.replace
            def fail(src, dst):
                if Path(dst).name == 'c' or (Path(dst).name == 'b' and '.bak-' in str(src)):
                    raise OSError('injected')
                return replace(src, dst)
            with patch.object(zi.os, 'replace', side_effect=fail):
                with self.assertRaises(zi.ImportError_):
                    zi._commit_user_lists(root, dict.fromkeys('abc', b'new'), lambda _: None)
            self.assertFalse((root/'a').exists())
            self.assertFalse((root/'c').exists())
            self.assertEqual(next(root.glob('b.bak-*')).read_bytes(), b'old')

    def test_cleanup_and_logger_failures_do_not_hide_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'a').write_bytes(b'old')
            replace, unlink = os.replace, Path.unlink
            def fail_replace(src, dst):
                if Path(dst).name == 'b':
                    raise OSError('original commit error')
                return replace(src, dst)
            def fail_unlink(path, *args, **kwargs):
                if path.name.startswith('b.tmp-'):
                    raise OSError('cleanup error')
                return unlink(path, *args, **kwargs)
            def fail_log(message):
                raise RuntimeError('logger error')
            with patch.object(zi.os, 'replace', side_effect=fail_replace), patch.object(Path, 'unlink', fail_unlink):
                with self.assertRaises(zi.ImportError_) as caught:
                    zi._commit_user_lists(root, dict.fromkeys('ab', b'new'), fail_log)
            self.assertEqual((root/'a').read_bytes(), b'old')
            self.assertIn('original commit error', str(caught.exception))
            self.assertIn('cleanup error', str(caught.exception))
            self.assertIn('original commit error', str(caught.exception.__cause__))

    def test_success_preserves_old_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'a').write_bytes(b'old')
            self.assertEqual(zi._commit_user_lists(root, dict.fromkeys('ab', b'new'), lambda _: None), 2)
            self.assertEqual((root/'a').read_bytes(), b'new')
            self.assertEqual((root/'b').read_bytes(), b'new')
            self.assertEqual(next(root.glob('a.bak-*')).read_bytes(), b'old')
            self.assertEqual(list(root.glob('b.bak-*')), [])
