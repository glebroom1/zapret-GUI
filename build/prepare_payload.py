"""Stage the engine without personal lists/backups; never edit the source build."""
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
source = ROOT.parent / 'zapret-discord-youtube-1.10.4'
target = ROOT / 'build/engine-payload'
personal = {'list-general-user.txt', 'list-exclude-user.txt', 'ipset-exclude-user.txt'}
assert (source/'bin/winws.exe').is_file()
target.mkdir(parents=True, exist_ok=True)
for path in source.rglob('*'):
    if not path.is_file() or path.suffix.lower() in ('.backup', '.bak', '.log'):
        continue
    relative = path.relative_to(source)
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.name in personal:
        destination.write_bytes(b'')
    else:
        shutil.copyfile(path, destination)
for name in personal:
    assert (target/'lists'/name).read_bytes() == b''
print('Engine staged with empty personal lists; source build unchanged')
