import json,subprocess
from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'build/engine-payload/bin/winws.exe'
try:
 r=subprocess.run([str(p),'--wf-tcp=9','--filter-tcp=9','--dpi-desync=fake'],cwd=p.parent,
                  capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
 result=dict(exit=r.returncode,stdout=r.stdout.decode('utf-8',errors='replace'),stderr=r.stderr.decode('utf-8',errors='replace'))
except OSError as exc:
 result=dict(started=False,winerror=exc.winerror,reason=str(exc),elevation_attempted=False)
(R/'reports/native-launch.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
