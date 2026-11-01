"""Reattach only to a verified engine launched by this GUI, never by PID alone."""
import json
import os
from pathlib import Path
import subprocess
import psutil


class AttachedProcess:
    def __init__(self, process):
        self.process = process
        self.pid = process.pid

    def poll(self):
        return None if self.process.is_running() else 0

    def terminate(self):
        self.process.terminate()

    kill = terminate

    def wait(self, timeout):
        try:
            return self.process.wait(timeout)
        except psutil.TimeoutExpired as exc:
            raise subprocess.TimeoutExpired('winws', timeout) from exc


def record(path, process, strategy):
    live = psutil.Process(process.pid)
    state = dict(pid=live.pid, created=live.create_time(), exe=live.exe(),
                 args=live.cmdline(), strategy=str(Path(strategy).resolve()))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


def recover(path):
    try:
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        live = psutil.Process(int(state['pid']))
        try:
            args = live.cmdline()
        except psutil.AccessDenied:
            # Elevated engines remain identifiable by image and creation time.
            args = state['args']
        if (live.create_time() != state['created'] or
                os.path.normcase(live.exe()) != os.path.normcase(state['exe']) or
                args != state['args'] or
                Path(live.exe()).name.lower() != 'winws.exe' or
                os.path.normcase(str(Path(state['strategy']).parent / 'bin' / 'winws.exe')) !=
                os.path.normcase(live.exe())):
            return None
        return AttachedProcess(live), Path(state['strategy'])
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return None
