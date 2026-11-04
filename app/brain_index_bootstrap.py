"""Explicit, coverage-bound local indexing; no source rewrites or Git calls."""
from contextlib import closing
from datetime import datetime,timezone
import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from filelock import FileLock
import brain_settings as cfg
import brain_index as index

OVERALL_SECONDS=28800
STALL_SECONDS=1800
LOG_LIMIT=64*1024*1024


class IndexStalled(RuntimeError): pass


def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _save(value,*,terminal=False):
    path=cfg.STATE/'index-bootstrap-status.json'
    temp=path.with_suffix('.tmp')
    for attempt in range(4):
        try:
            temp.write_text(json.dumps(value,ensure_ascii=True),encoding='utf-8')
            os.replace(temp,path)
            return value
        except PermissionError:
            if attempt==3:
                if terminal: raise
                return value
            time.sleep(.05)


def _counts(deadline=None):
    with closing(sqlite3.connect((cfg.STATE/'memory.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as connection:
        if deadline is not None:connection.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        present={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {table:connection.execute('SELECT count(*) FROM '+table).fetchone()[0] if table in present else None
                for table in ('entity','search_vector_chunks','search_vector_embeddings_rowids')}


def _run_reindex(command,log,overall,on_tick,on_cleanup_wait):
    if os.name=='nt':
        from brain_windows_process import run
        return run(command,env=cfg.environment(),stdout=log,stderr=log,timeout=overall,
                   on_tick=on_tick,on_cleanup_wait=on_cleanup_wait,direct_output=True)
    # This local Popen has one waiter. Ignored/custom SIGCHLD reaping would
    # invalidate the unreaped-child PID reservation used during cancellation.
    if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
        raise index.IndexAttention('child_reaping_disposition_refused')
    stopped=0
    def check_interrupt():
        if stopped==signal.SIGINT:raise KeyboardInterrupt
        if stopped:raise InterruptedError('Local index operation interrupted.')
    def interrupted(signum,frame):
        nonlocal stopped
        if not stopped or signum==signal.SIGINT:stopped=signum
        # Signal delivery only records cancellation. Raising is synchronous at
        # owned-child checkpoints, never across Popen or finally admission.
        # Signals already dispatched into cleanup do not re-raise afterward.
    handlers={signal.SIGINT:signal.getsignal(signal.SIGINT)}
    process=None
    deadline=time.monotonic()+overall
    try:
        for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            if signum==signal.SIGINT and handlers[signum]==signal.SIG_IGN:continue
            handlers[signum]=signal.signal(signum,interrupted)
        check_interrupt()
        process=subprocess.Popen(command,env=cfg.environment(),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        while True:
            check_interrupt()
            if process.poll() is not None:break
            if time.monotonic()>=deadline: raise subprocess.TimeoutExpired(command,overall)
            try:on_tick()
            except Exception:
                check_interrupt()
                if process.poll() is None:raise
            check_interrupt()
            time.sleep(1)
        check_interrupt()
        return subprocess.CompletedProcess(command,process.returncode)
    finally:
        try:
            if process is not None:
                while True:
                    try:
                        for signum in handlers:signal.signal(signum,signal.SIG_IGN)
                        break
                    except (InterruptedError,KeyboardInterrupt):pass
                try:process.poll()
                except OSError:pass
                if process.returncode is None:
                    # Do not poll/wait during grace: even an exited, unreaped
                    # leader reserves this PID/PGID through the final signal.
                    for signum in (signal.SIGTERM,signal.SIGKILL):
                        try:os.killpg(process.pid,signum)
                        except OSError:pass
                        if signum==signal.SIGTERM:time.sleep(2)
                try:process.wait(timeout=5)
                except (subprocess.TimeoutExpired,OSError):pass
                notified=False
                while True:
                    # A reaped leader no longer grants group signal authority.
                    # Existence/permission errors can only retain admission.
                    try:reaped=process.poll() is not None
                    except OSError:reaped=False
                    if reaped:
                        try:os.killpg(process.pid,0)
                        except ProcessLookupError:break
                        except OSError:pass
                    if not notified:
                        notified=True
                        try:on_cleanup_wait()
                        except Exception:pass
                    time.sleep(.1)
        finally:
            for signum in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
                if signum in handlers:signal.signal(signum,handlers[signum])


def _archive_previous():
    path=cfg.STATE/'index-bootstrap-status.json'
    if not path.exists(): return None
    with path.open('rb') as stream: raw=stream.read(65537)
    if len(raw)>65536: raise index.IndexAttention('status_history_size_limit')
    folder=cfg.STATE/'index-bootstrap-history'
    if folder.is_symlink() or os.path.isjunction(folder):raise index.IndexAttention('linked_history_refused')
    folder.mkdir(exist_ok=True)
    with os.scandir(folder) as entries:
        count=0
        for entry in entries:
            if entry.name.endswith('.json'):count+=1
            if count>=256:raise index.IndexAttention('status_history_count_limit')
    name=uuid.uuid4().hex+'.json'
    with (folder/name).open('xb') as output: output.write(raw)
    return name


def main(*,overall_seconds=OVERALL_SECONDS,stall_seconds=STALL_SECONDS,full=False,retry=False):
    if (type(overall_seconds) is not int or type(stall_seconds) is not int
            or not 1<=stall_seconds<overall_seconds<=86400):
        raise ValueError('Use integer budgets: 1 <= stall-seconds < overall-seconds <= 86400.')
    cfg.STATE.mkdir(parents=True,exist_ok=True)
    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):
        cfg.write_admission()
        try:history=_archive_previous() if retry else None
        except index.IndexAttention as error:
            attention={'status':'needs attention','stage':'retry admission','reason':str(error),
                       'prior_status_preserved':True,'history_folder':str(cfg.STATE/'index-bootstrap-history'),
                       'guidance':'Preserve the prior status/history; in a verified quiet window archive history privately, then retry.'}
            target=cfg.STATE/'index-bootstrap-attention.json'
            if target.is_symlink() or os.path.isjunction(target):raise index.IndexAttention('linked_attention_refused')
            temporary=target.with_name('.index-bootstrap-attention-'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('x',encoding='utf-8') as output:json.dump(attention,output);output.flush();os.fsync(output.fileno())
                os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
            return attention
        started=time.monotonic(); changed=started; observed=None;last_saved=started
        deadline=started+overall_seconds
        def check():
            if time.monotonic()>=deadline:raise index.IndexAttention('overall_time_limit')
        progress={'status':'indexing','stage':'checking source-safe configuration','started_at':_now(),
                  'progress_signal':'whole-DB counts from any writer; not attributed to this process',
                  'overall_seconds':overall_seconds,'stall_seconds':stall_seconds,'full':bool(full),'history_record':history}
        _save(progress)
        try:
            check();index.configuration();check()
            progress['index_scope_migration']=index.ensure_history_scope()
            check();notes,manifest=index.inventory(check);check()
            if not notes:
                return _save({**progress,'status':'needs review','stage':'complete','reason':'no_eligible_markdown',
                              'finished_at':_now(),'elapsed_seconds':time.monotonic()-started},terminal=True)
            binary=cfg.PYTHON.parent/('bm.exe' if os.name=='nt' else 'bm')
            command=[str(binary),'reindex','-p','brain']+(['--full'] if full else [])
            log_path=cfg.ROOT/'logs/index-bootstrap.log'; log_path.parent.mkdir(parents=True,exist_ok=True)
            previous=log_path.with_name('index-bootstrap.previous.log')
            if any(path.is_symlink() or os.path.isjunction(path) for path in (log_path,previous)):
                raise index.IndexAttention('linked_log_refused')
            if log_path.exists():os.replace(log_path,previous)
            progress.update(stage='indexing local notes',source_manifest_sha256=manifest,last_progress_at=_now())
            _save(progress)
            def tick():
                nonlocal changed,observed,last_saved
                check()
                try: current=_counts(deadline)
                except (sqlite3.Error,OSError): current=None
                now=time.monotonic()
                if current is not None and any(value is not None for value in current.values()) and current!=observed:
                    observed=current; changed=now
                    progress.update(counts=current,last_progress_at=_now())
                progress['elapsed_seconds']=now-started
                if now-last_saved>=15:
                    _save(progress);last_saved=now
                if log_path.stat().st_size>LOG_LIMIT: raise index.IndexAttention('private_log_budget_exceeded')
                if now-changed>=stall_seconds: raise IndexStalled('Local indexing made no observable DB progress within its stall budget.')
            def cleanup_wait():
                progress.update(stage='containment cleanup pending',cleanup='bootstrap lock retained until owned processes exit')
                _save(progress,terminal=True)
            with log_path.open('ab') as log:
                check()
                result=_run_reindex(command,log,deadline-time.monotonic(),tick,cleanup_wait)
            check()
            if log_path.stat().st_size>LOG_LIMIT:raise index.IndexAttention('private_log_budget_exceeded')
            progress.pop('cleanup',None)
            progress['stage']='verifying current coverage'
            _save(progress)
            counts=_counts(deadline);check();coverage=index.status(deadline=deadline);check()
            stable=coverage.get('coverage',{}).get('manifest_sha256')==manifest and coverage.get('inventory_stable') is True
            good=result.returncode==0 and coverage.get('status')=='verified' and stable
            outcome='verified' if good else ('needs attention' if result.returncode or coverage.get('status')=='needs attention' else 'needs review')
            return _save({**progress,'status':outcome,'stage':'complete',
                          'counts':counts,'process_exit':result.returncode,'current_index':coverage,
                          'inventory_unchanged_during_run':stable,'verification':index.SCOPE,
                          'finished_at':_now(),'elapsed_seconds':time.monotonic()-started},terminal=True)
        except Exception as error:
            if 'cleanup' in progress:progress['cleanup']='runner returned after cleanup; inspect native receipt for platform scope'
            return _save({**progress,'status':'needs attention','error_type':type(error).__name__,
                          'reason':str(error) if isinstance(error,(index.IndexAttention,index.WalkLimit)) else 'local_indexing_failed',
                          'error':'Indexing did not verify. Preserve the database and private log; use index status, then an explicit index retry after repair.',
                          'finished_at':_now(),'elapsed_seconds':time.monotonic()-started},terminal=True)


if __name__=='__main__': main()
