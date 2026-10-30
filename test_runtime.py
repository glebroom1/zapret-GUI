import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import runtime_paths as rp

class RuntimeTests(unittest.TestCase):
    def test_frozen_elevation_relaunches_exe(self):
        with patch.object(rp, 'is_frozen', return_value=True), patch.object(rp.sys, 'executable', 'C:\\App\\ZapretDesk.exe'):
            target, args, workdir = rp.elevate_target()
            self.assertTrue(target.endswith('ZapretDesk.exe'))
            self.assertEqual(args, ['--connect'])
            self.assertNotIn('python', target)

    def test_runtime_repair_preserves_user_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); src = base/'bundle'; root = base/'runtime'; dest = root/'version'
            (src/'bin').mkdir(parents=True); (src/'bin/winws.exe').write_bytes(b'engine')
            (src/'lists').mkdir(); (src/'lists/user.txt').write_text('bundled')
            (dest/'lists').mkdir(parents=True); (dest/'lists/user.txt').write_text('personal')
            with patch.object(rp, 'bundled_zapret', return_value=src), patch.object(rp, 'runtime_root', return_value=root), patch.object(rp, 'bundle_name', return_value='version'):
                self.assertEqual(rp.bootstrap_bundled(), dest)
            self.assertEqual((dest/'lists/user.txt').read_text(), 'personal')
            self.assertTrue((dest/'bin/winws.exe').exists())

if __name__ == '__main__': unittest.main()
