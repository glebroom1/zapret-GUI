"""Package verified Windows diagnostic prerelease; never publish or commit."""
import hashlib,json,zipfile,re
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=R/'dist';NAME='Zapret-GUI-0.9.0'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
exe=D/(NAME+'.exe');digest=sha(exe)
for name in ('tg-build-verification.json','self-update-qa.json'):
 data=json.loads((R/'reports'/name).read_text(encoding='utf-8'))
 assert data['ok'] and data['exe_sha256']==digest
assert json.loads((R/'reports/worker-recovery.json').read_text())['ok']
worker = json.loads((R/'reports/frozen-beta3-qa.json').read_text())
assert worker['frozen_worker_start'] and worker['exe_sha256'] == digest
tests=(R/'reports/tests.log').read_text(encoding='utf-8')
count = re.search(r'Ran (\d+) tests', tests)
assert count and int(count[1]) >= 266 and tests.rstrip().endswith('OK')
manifest=dict(schema=1,platform='windows-x64',version='0.9.0',asset=exe.name,size=exe.stat().st_size,sha256=digest)
(D/'update-windows.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
build_names={'run_beta5.py','final_checks5.py','package_beta5.py','prepare_payload.py','make_zapret_info.py',
             'zapret_desk_qt.spec','verify_telegram_build.py','qa_self_update.py','qa_frozen_beta3.py',
             'qa_worker_recovery.py','qa_engine5.py','qa_native_launch5.py'}
sources=[]
for p in sorted(R.rglob('*')):
 if not p.is_file():continue
 if p.suffix.lower() in ('.apk','.aab') or 'android' in p.name.lower():continue
 rel=p.relative_to(R); parts=rel.parts
 if '__pycache__' in parts or p.suffix=='.pyc':continue
 if len(parts)==1 and (p.suffix in ('.py','.md','.txt') or p.name in ('LICENSE','.gitignore')) and not p.name.startswith('edit_'):
  sources.append(p)
 elif parts[0] in ('assets','licenses','vendor'):sources.append(p)
 elif len(parts)==2 and parts[0]=='build' and p.name in build_names:sources.append(p)
source_manifest={p.relative_to(R).as_posix():sha(p) for p in sources}
(R/'reports/source-files.json').write_text(json.dumps(source_manifest,indent=2),encoding='utf-8')
baseline=R.parent/'zapret-GUI-beta4'
changes={name:dict(sha256=d,baseline_sha256=sha(baseline/name) if (baseline/name).is_file() else None)
         for name,d in source_manifest.items() if not (baseline/name).is_file() or sha(baseline/name)!=d}
(R/'reports/changed-files.json').write_text(json.dumps(changes,indent=2),encoding='utf-8')
report=R/'RELEASE-NOTES-0.9.0.md'
(D/report.name).write_bytes(report.read_bytes())
(D/'PUBLISH-FILES.json').write_text(json.dumps(dict(tag='v0.9.0',prerelease=True,
    source_files=source_manifest,changed_files=list(changes),commit_performed=False,published=False),indent=2),encoding='utf-8')
with zipfile.ZipFile(D/(NAME+'-source.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in sources:z.write(p,NAME+'-source/'+p.relative_to(R).as_posix())
with zipfile.ZipFile(D/(NAME+'-checks.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in sorted((R/'reports').iterdir()):
  if p.is_file():z.write(p,'reports/'+p.name)
 z.write(report,report.name)
with zipfile.ZipFile(D/(NAME+'-Windows-x64.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 z.write(exe,NAME+'/'+exe.name)
 z.write(report,NAME+'/docs/'+report.name)
 z.write(D/'update-windows.json',NAME+'/docs/update-windows.json')
 z.writestr(NAME+'/QUICKSTART.txt','Windows 0.9.0\nЗапустите EXE; для winws нужны права администратора.\nАвтоподбор может выбрать стратегию по HTTPS без проверки видео. См. docs/RELEASE-NOTES-0.9.0.md.\nПользовательские настройки/списки сохраняются в прежнем каталоге ZapretDesk.\n')
 for p in sources:
  rel=p.relative_to(R)
  if rel.parts[0]=='licenses' and rel.as_posix() not in ('licenses/SOURCES.md','licenses/THIRD_PARTY.md'):
   z.write(p,NAME+'/'+rel.as_posix())
 for name in ('LICENSE','SOURCES.md','THIRD_PARTY.md'):
  z.write(R/name,NAME+'/licenses/'+('Zapret-GUI-LICENSE.txt' if name=='LICENSE' else name))
for p in D.glob('*.zip'):
 with zipfile.ZipFile(p) as z:
  assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
  assert not any(Path(n).name in ('config.json','worker.json') or '__pycache__' in n for n in z.namelist())
with zipfile.ZipFile(D/(NAME+'-source.zip')) as z:
 for name,d in source_manifest.items():assert hashlib.sha256(z.read(NAME+'-source/'+name)).hexdigest()==d
with zipfile.ZipFile(D/(NAME+'-Windows-x64.zip')) as z:
 assert hashlib.sha256(z.read(NAME+'/'+exe.name)).hexdigest()==digest
assert not any('beta' in p.name.lower() or 'android' in p.name.lower() or p.suffix.lower() in ('.apk','.aab') for p in D.iterdir())
files=sorted(p for p in D.iterdir() if p.is_file() and not p.name.startswith('SHA256SUMS'))
(D/'SHA256SUMS-Windows-0.9.0.txt').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in files),encoding='utf-8')
print(json.dumps({p.name:p.stat().st_size for p in files},indent=2))
