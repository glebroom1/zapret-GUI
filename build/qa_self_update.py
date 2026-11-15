"""Exercise the actual frozen helper on disposable EXEs, never the user's running app."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import psutil

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'dist/Zapret-GUI-0.9.0.exe'
fixture = Path(tempfile.mkdtemp(prefix='update-fixture-', dir=ROOT/'build'))
folder = fixture / "путь с пробелами и ' апострофом"
folder.mkdir(exist_ok=True)
target = folder / 'старое имя приложения.exe'
source = fixture / 'parent.cs'
source.write_text('class Parent { static void Main() { System.Threading.Thread.Sleep(4000); } }', encoding='utf-8')
compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:' + str(target), str(source)], check=True, capture_output=True)
stage_folder = fixture / 'staged'
stage_folder.mkdir(exist_ok=True)
stage = stage_folder / 'update.exe'
shutil.copyfile(EXE, stage)
old_digest = hashlib.sha256(target.read_bytes()).hexdigest()
new_digest = hashlib.sha256(stage.read_bytes()).hexdigest()
environment = dict(os.environ, LOCALAPPDATA=str(fixture/'appdata'), QT_QPA_PLATFORM='offscreen',
                   PYINSTALLER_RESET_ENVIRONMENT='1')
parent = subprocess.Popen([str(target)], creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
job = stage_folder / 'job.json'
job.write_text(json.dumps({'schema': 1, 'pid': parent.pid, 'created': psutil.Process(parent.pid).create_time(),
                          'target': str(target.resolve()), 'stage': str(stage.resolve()), 'sha256': new_digest,
                          'old_sha256': old_digest, 'tray': False}), encoding='utf-8')
helper = subprocess.Popen([str(stage), '--apply-update', str(job), '--smoke'], env=environment,
                          creationflags=subprocess.CREATE_NO_WINDOW)
assert helper.wait(timeout=120) == 0
assert parent.wait(timeout=10) == 0
result = json.loads((stage_folder/'result.json').read_text(encoding='utf-8'))
assert result['ok'], result
assert hashlib.sha256(target.read_bytes()).hexdigest() == new_digest
assert hashlib.sha256((stage_folder/'previous.exe').read_bytes()).hexdigest() == old_digest
smoke_path = fixture/'appdata/ZapretDesk/smoke_qt.json'
deadline = time.monotonic() + 30
while not smoke_path.is_file() and time.monotonic() < deadline:
    time.sleep(.1)
smoke = json.loads(smoke_path.read_text(encoding='utf-8'))
assert smoke['errors'] == [] and smoke['frozen'], smoke
report = {'ok': True, 'exe_sha256': new_digest, 'actual_frozen_helper_executed': True,
          'same_path_and_old_filename_preserved': True, 'spaces_unicode_apostrophe_path': True,
          'previous_exe_preserved': True, 'new_exe_relaunched_as_smoke': True,
          'smoke_errors': smoke['errors'], 'network_or_autostart_changed': False}
(ROOT/'build/self-update-qa.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
