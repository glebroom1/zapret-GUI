import ctypes
import unittest
from unittest.mock import Mock, patch
import core

class ProcessInspectionTests(unittest.TestCase):
    def api(self, names=(), failure=None):
        api = Mock()
        api.CreateToolhelp32Snapshot.return_value = 123
        remaining = iter(names)
        def advance(handle, ptr):
            try:
                ptr._obj.szExeFile = next(remaining)
                return True
            except StopIteration:
                ctypes.set_last_error(failure or 18)
                return False
        api.Process32FirstW.side_effect = advance
        api.Process32NextW.side_effect = advance
        return api

    def test_empty_and_unrelated_processes(self):
        for names in ([], ['explorer.exe', 'winws.exe.other', 'python.exe']):
            api = self.api(names)
            with patch('core._process_api', return_value=api), patch('core.subprocess.run', side_effect=AssertionError('No tasklist')):
                self.assertFalse(core.foreign_engine())
            api.CloseHandle.assert_called_once_with(123)

    def test_engine_exact_case_insensitive_match(self):
        api = self.api(['explorer.exe', 'WINWS.EXE'])
        with patch('core._process_api', return_value=api):
            self.assertTrue(core.foreign_engine())
        api.CloseHandle.assert_called_once_with(123)

    def test_snapshot_failure_is_fatal(self):
        api = self.api()
        api.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
        ctypes.set_last_error(5)
        with patch('core._process_api', return_value=api):
            with self.assertRaisesRegex(core.ProcessInspectionError, 'Windows 5'):
                core.foreign_engine()
        api.CloseHandle.assert_not_called()

    def test_enumeration_failure_never_means_no_engine(self):
        for names in ([], ['explorer.exe']):
            api = self.api(names, failure=5)
            with patch('core._process_api', return_value=api):
                with self.assertRaisesRegex(core.ProcessInspectionError, 'Windows 5'):
                    core.foreign_engine()
            api.CloseHandle.assert_called_once_with(123)

    def test_inspection_failure_prevents_spawn(self):
        with patch('core.is_admin', return_value=True), patch('core.foreign_engine', side_effect=core.ProcessInspectionError('failed')), patch('core.subprocess.Popen') as spawn:
            with self.assertRaises(core.ProcessInspectionError):
                core.Engine(lambda m: None).start(None, None)
            spawn.assert_not_called()

if __name__ == '__main__':
    unittest.main()
