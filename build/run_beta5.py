"""Repeatable local build, with UTF-8 logs and explicit failure propagation."""
import os, subprocess, sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
os.chdir(R)
os.environ.update(QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8',
                  ZAPRET_SOURCE=str(R/'build/engine-payload'),ZAPRET_NAME='zapret-discord-youtube-1.10.4',
                  EXE_NAME='Zapret-GUI-0.9.0')
for name,args in [('tests',['-m','unittest','discover','-q']),
                  ('payload',['build/prepare_payload.py']),('info',['build/make_zapret_info.py']),
                  ('pyinstaller',['-m','PyInstaller','--clean','--noconfirm','--distpath','dist',
                                  '--workpath','build/work-beta5','build/zapret_desk_qt.spec'])]:
 with (R/'reports'/f'{name}.log').open('wb') as out:
  result=subprocess.run([sys.executable,*args],stdout=out,stderr=subprocess.STDOUT)
 print(name,result.returncode,flush=True)
 if result.returncode:sys.exit(result.returncode)
