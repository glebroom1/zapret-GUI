"""Isolated real engine instance; default Windows traffic still traverses Happ."""
import json, shutil, sys, time
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
from core import Engine, foreign_engine, is_admin, bounded_check
report={'admin':is_admin(),'foreign_engine_before':foreign_engine(),'route':'Happ TUN, unchanged',
        'circumvention_proven':False}
if report['foreign_engine_before']:
 report['skipped']='foreign engine present'
else:
 root=R/'build/engine-runtime-test'
 shutil.copytree(R/'build/engine-payload',root,dirs_exist_ok=True)
 engine=Engine(lambda line:None)
 try:
  report['before']=bounded_check()
  report['ready']=engine.start(root/'general.bat',R/'reports/engine-runtime.log',timeout=20)
  report['pid']=engine.process.pid if engine.process else None
  report['during']=bounded_check()
  report['still_running']=engine.running()
 except Exception as exc:report['error']=str(exc)
 finally:
  engine.stop()
  report['owned_stopped']=not engine.running()
 report['foreign_engine_after']=foreign_engine()
(R/'reports/engine-runtime.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
