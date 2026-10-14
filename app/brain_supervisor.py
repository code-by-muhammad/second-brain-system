"""One OS-owned local service; indexing everywhere, shared maintenance on one host."""
import json, socket, subprocess, time, traceback
from filelock import FileLock, Timeout
import brain_settings as cfg

def main():
    for folder in ('state','logs'): (cfg.ROOT/folder).mkdir(parents=True,exist_ok=True)
    with FileLock(str(cfg.STATE/'supervisor.lock'),timeout=1):
        child=None; bootstrap=None; last=0
        while True:
            with socket.socket() as sock: listening=sock.connect_ex(('127.0.0.1',cfg.PORT))==0
            if not listening and (child is None or child.poll() is not None):
                with (cfg.ROOT/'logs/service-server.log').open('ab') as log:
                    child=subprocess.Popen([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_server.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),**cfg.child_options())
            progress=cfg.STATE/'index-bootstrap-status.json'
            try: state=json.loads(progress.read_text()) if progress.exists() else {}
            except (OSError,ValueError): state={}
            if listening and state.get('status') not in ('verified','needs review','needs attention') and (bootstrap is None or bootstrap.poll() is not None):
                with (cfg.ROOT/'logs/service-bootstrap.log').open('ab') as log:
                    bootstrap=subprocess.Popen([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_index_bootstrap.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),**cfg.child_options())
            if time.monotonic()-last>900:
                with (cfg.ROOT/'logs/service-maintenance.log').open('ab') as log:
                    try: subprocess.run([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_maintenance.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),timeout=240,**cfg.child_options())
                    except subprocess.TimeoutExpired: log.write(b'Maintenance timed out; retry next cycle.\n')
                last=time.monotonic()
            time.sleep(20)

if __name__=='__main__':
    while True:
        try: main()
        except Timeout: break
        except Exception:
            with (cfg.ROOT/'logs/service-supervisor.log').open('a',encoding='utf-8') as log: log.write(traceback.format_exc()+'\n')
            time.sleep(10)
