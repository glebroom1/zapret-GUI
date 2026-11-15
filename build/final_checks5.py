import json, os, subprocess, sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];os.chdir(R)
os.environ.update(QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8')
for name,script in [('frozen-code','verify_telegram_build.py'),('frozen-worker','qa_frozen_beta3.py'),('self-update','qa_self_update.py')]:
 with (R/'reports'/f'{name}.log').open('wb') as out:
  result=subprocess.run([sys.executable,'build/'+script],stdout=out,stderr=subprocess.STDOUT)
 print(name,result.returncode,flush=True)
 if result.returncode:sys.exit(result.returncode)
for name in ('tg-build-verification.json','frozen-beta3-qa.json','self-update-qa.json'):
 (R/'reports'/name).write_bytes((R/'build'/name).read_bytes())
