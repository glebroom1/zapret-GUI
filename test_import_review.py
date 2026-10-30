import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zapret_import as zi

class ImportReviewTests(unittest.TestCase):
    def test_windows_reparse_root_and_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'source'
            root.mkdir()
            original = Path.lstat
            for blocked in (root, root.parent):
                def lstat(path, *args, **kwargs):
                    if path == blocked:
                        return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                    return original(path, *args, **kwargs)
                with self.subTest(blocked=str(blocked)), patch.object(Path, 'lstat', lstat):
                    with self.assertRaisesRegex(zi.ImportError_, 'reparse'):
                        zi._reject_symlinks(root)