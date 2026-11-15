"""Terminate a verified Windows worker through the same pinned process handle."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import psutil


def terminate_worker(state, directory):
    if os.name != 'nt':
        raise RuntimeError('Принудительная остановка доступна только в Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x1, False, int(state['pid']))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 0) == 0:
            return
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(system), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        timestamp = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 1e7 - 11644473600
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (abs(timestamp - float(state['created'])) > .00001 or
                os.path.normcase(image.value) != os.path.normcase(state['exe'])):
            raise RuntimeError('Идентичность процесса изменилась; остановка отменена')
        # The handle pins this process while the PID-based command line is checked.
        args = psutil.Process(int(state['pid'])).cmdline()
        try:
            flag = args.index('--telegram-data')
            target = Path(args[flag + 1]).resolve()
        except (ValueError, IndexError):
            raise RuntimeError('Процесс не является Telegram worker') from None
        if '--telegram-worker' not in args or target != Path(directory).resolve():
            raise RuntimeError('Процесс не принадлежит этому Telegram-прокси')
        if not kernel.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle, 5000) != 0:
            raise RuntimeError('Процесс ещё не завершился')
    finally:
        kernel.CloseHandle(handle)
