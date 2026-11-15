"""Real worker/process/control; injected upstream outage and clock jump, no GUI."""
import json, os, secrets, socket, subprocess, sys, tempfile, time
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
from telegram_proxy import TelegramProxyManager,atomic_json,protect
if '--child' in sys.argv:
 import telegram_worker as worker
 backend=worker.import_backend()
 from proxy.raw_websocket import RawWebSocket
 async def unavailable(*a,**kw):raise OSError('injected upstream outage')
 RawWebSocket.connect=unavailable
 clock=worker.time.time; start=time.monotonic()
 worker.time.time=lambda:clock()+(120 if time.monotonic()-start>=6 else 0)
 sys.exit(worker.main(sys.argv[-1]))
with tempfile.TemporaryDirectory(prefix='worker-beta5-') as temp:
 root=Path(temp)
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 secret,token=secrets.token_hex(16),secrets.token_hex(32)
 atomic_json(root/'config.json',dict(port=port,direct=False,cfproxy=False,domain='',
                 secret=protect(secret),control_key=protect(token)))
 manager=TelegramProxyManager(root)
 child=subprocess.Popen([sys.executable,__file__,'--child',str(root)],cwd=R,
                          creationflags=subprocess.CREATE_NO_WINDOW)
 rows=[]
 try:
  deadline=time.monotonic()+22
  while time.monotonic()<deadline:
   status=manager.status()
   if status.get('running'):
    rows.append({k:status[k] for k in ('pid','instance','phase','restarts','loop_age','upstream','port')})
   time.sleep(.5)
  assert rows and len({r['pid'] for r in rows})==1
  assert len({r['instance'] for r in rows})==1
  assert max(r['restarts'] for r in rows)==1,rows
  assert rows[-1]['phase']=='listening' and max(r['loop_age'] for r in rows)<5
  manager.stop();assert child.wait(timeout=15)==0
  assert not manager.status()['running']
  log=(root/'proxy.log').read_text(encoding='utf-8')
  assert secret not in log and token not in log
  report=dict(ok=True,injected_outage=True,simulated_resume=True,real_sleep=False,
              same_pid_instance_port=True,restarts=1,stop_no_revival=True,rows=rows)
  (R/'reports/worker-recovery.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
  print('worker recovery PASS; injected clock jump and upstream outage, not real suspend')
 finally:
  if child.poll() is None:
   manager.stop();child.wait(timeout=15)
