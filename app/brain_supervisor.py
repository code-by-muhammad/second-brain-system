"""One OS-owned local service; indexing everywhere, shared maintenance on one host."""
import json, socket, subprocess, time, traceback
from filelock import FileLock, Timeout
import brain_settings as cfg
import brain_process as processes
import brain_service as service


def _bootstrap_admitted():
    """Read/update interruption state under the same nonblocking writer lock."""
    try:
        with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):
            path=cfg.STATE/'index-bootstrap-status.json'
            try: state=json.loads(path.read_text()) if path.exists() else {}
            except (OSError,ValueError,RecursionError): state={}
            if not isinstance(state,dict): state={}
            if state.get('status')=='indexing':
                from datetime import datetime,timezone
                from brain_index_bootstrap import _save
                _save({**state,'status':'needs attention','reason':'interrupted_bootstrap',
                       'stage':'explicit retry required','finished_at':datetime.now(timezone.utc).isoformat(timespec='seconds')},terminal=True)
                return False
            return state.get('status') not in ('verified','needs review','needs attention')
    except Timeout:
        return False

def _publish_owned(owned):
    # Publish only this process's live handles, never adopt cached registry PIDs.
    import os
    registry=cfg.STATE/'service-pids.json'
    temp=registry.with_suffix('.tmp')
    pids={'supervisor':os.getpid()}
    for name in ('server','bootstrap','maintenance'):
        child=owned.get(name)
        pids[name]=child.pid if child is not None and child.poll() is None else None
    temp.write_text(json.dumps(pids),encoding='utf-8')
    os.replace(temp,registry)

def main(owned=None):
    # Keep live handles and completion cadence across unexpected-fault retries.
    owned={} if owned is None else owned
    for folder in ('state','logs'): (cfg.ROOT/folder).mkdir(parents=True,exist_ok=True)
    with FileLock(str(cfg.STATE/'supervisor.lock'),timeout=1):
        child=owned.get('server'); bootstrap=owned.get('bootstrap'); maintenance=owned.get('maintenance')
        while True:
            try:
                # Serialize every spawn against stop/update admission. A held service
                # idles, including under launchd KeepAlive, without relaunching workers.
                with FileLock(str(cfg.STATE/'service-control.lock'),timeout=0):
                    _publish_owned(owned)
                    if not service.spawn_allowed():
                        service.write_heartbeat('held')
                    else:
                        identity=processes.listener_identity()
                        listening=identity is not None
                        if not listening and (child is None or child.poll() is not None):
                            with (cfg.ROOT/'logs/service-server.log').open('ab') as log:
                                child=subprocess.Popen([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_server.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),**cfg.child_options())
                                owned['server']=child
                                _publish_owned(owned)
                        if listening and (bootstrap is None or bootstrap.poll() is not None) and _bootstrap_admitted():
                            with (cfg.ROOT/'logs/service-bootstrap.log').open('ab') as log:
                                bootstrap=subprocess.Popen([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_index_bootstrap.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),**cfg.child_options())
                                owned['bootstrap']=bootstrap
                                _publish_owned(owned)
                        if maintenance is not None and maintenance.poll() is not None:
                            owned['_maintenance_finished']=time.monotonic()
                            owned.pop('maintenance',None)
                            maintenance=None
                            _publish_owned(owned)
                        if listening and maintenance is None and time.monotonic()-owned.get('_maintenance_finished',0)>900:
                            with (cfg.ROOT/'logs/service-maintenance.log').open('ab') as log:
                                maintenance=subprocess.Popen([str(cfg.PYTHON),str(cfg.ROOT/'app/brain_maintenance.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=cfg.environment(),**cfg.child_options())
                                owned['maintenance']=maintenance
                                _publish_owned(owned)
                        service.write_heartbeat('ready' if listening else 'starting')
            except Timeout:
                pass
            time.sleep(20)

if __name__=='__main__':
    processes.validate_environment(entrypoint=True)
    processes.enter_supervisor_job()
    owned={}
    while True:
        try: main(owned)
        except Timeout: break
        except Exception:
            with (cfg.ROOT/'logs/service-supervisor.log').open('a',encoding='utf-8') as log: log.write(traceback.format_exc()+'\n')
            time.sleep(10)
