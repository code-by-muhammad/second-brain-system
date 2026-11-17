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
FOLLOW_UP_STALL=300
# Upstream rebuilds the text index first and, with --full, rewrites every note's rows without changing any of the
# counted tables. Measured on a 4,400-note vault: 6 to 11 minutes on one machine, over 30 on another. Ending the
# program there leaves the rebuild half done, so that phase has its own budget and the count rule starts after it.
TEXT_PHASE_SECONDS=7200
# Kept back at the end of a run for ending the program and reading coverage, so that coverage and not the clock
# decides the result: five minutes, or a quarter of a shorter run. Reading coverage of 4,400 notes takes seconds.
COVERAGE_RESERVE=300
EMBEDDING_MARKER=b'Building vector embeddings'
MAX_PASSES=160  # More than the largest verifiable note needs: 32,768 chunks at 256 per pass.


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
            command=[str(binary),'reindex','-p','brain'];arguments=['--full'] if full else []
            log_path=cfg.ROOT/'logs/index-bootstrap.log'; log_path.parent.mkdir(parents=True,exist_ok=True)
            previous=log_path.with_name('index-bootstrap.previous.log')
            if any(path.is_symlink() or os.path.isjunction(path) for path in (log_path,previous)):
                raise index.IndexAttention('linked_log_refused')
            if log_path.exists():os.replace(log_path,previous)
            progress.update(stage='indexing local notes',source_manifest_sha256=manifest,last_progress_at=_now())
            _save(progress)
            limit=stall_seconds;moved=False;offset=0;embedding=False;began=started;tail=b''
            def tick():
                nonlocal changed,observed,last_saved,moved,embedding,offset,tail
                check();started_embedding=False
                if not embedding:
                    # Only what this pass has written counts; the marker of an earlier pass is before the offset.
                    # New output is read as it comes, with enough of the previous piece kept to join a split marker.
                    with log_path.open('rb') as written:
                        written.seek(offset);piece=written.read(1048576)
                    offset+=len(piece);embedding=started_embedding=EMBEDDING_MARKER in tail+piece
                    tail=(tail+piece)[-len(EMBEDDING_MARKER):]
                try: current=_counts(deadline)
                except (sqlite3.Error,OSError): current=None
                now=time.monotonic()
                if current is not None and any(value is not None for value in current.values()) and current!=observed:
                    moved=moved or observed is not None
                    observed=current; changed=now
                    progress.update(counts=current,last_progress_at=_now())
                progress['elapsed_seconds']=now-started
                if now-last_saved>=15:
                    _save(progress);last_saved=now
                if log_path.stat().st_size>LOG_LIMIT: raise index.IndexAttention('private_log_budget_exceeded')
                if deadline-now<=min(COVERAGE_RESERVE,overall_seconds/4):
                    raise IndexStalled('Local indexing is ended before the overall budget so that coverage can still be read.')
                if not embedding:
                    # The text phase is judged by its own budget: two hours, but no more than a quarter of what this
                    # run had left when the pass began, so that coverage can still be read and decide after the program
                    # is ended. It is never shorter than the stall budget, which is what applied here before.
                    if now-began>=max(limit,min(TEXT_PHASE_SECONDS,(deadline-began)/4)):
                        raise IndexStalled('Local indexing did not finish its text index phase within its budget.')
                    changed=now
                elif started_embedding:changed=now  # The count clock starts when embedding does.
                elif now-changed>=limit: raise IndexStalled('Local indexing made no observable DB progress within its stall budget.')
            def cleanup_wait():
                progress.update(stage='containment cleanup pending',cleanup='bootstrap lock retained until owned processes exit')
                _save(progress,terminal=True)
            # One upstream run is not always a finished index. It embeds a very large note 256
            # chunks at a time, drops search rows that lose a write-lock race, and ends with an
            # orphan cleanup that is quadratic in chunk count and changes no row. So: repeat while
            # coverage improves, rebuild lost search rows once, and let coverage decide the result.
            best=None;misses=0;repaired=False;passes=0;had=None
            def embedded(value):return int((value or {}).get('search_vector_embeddings_rowids') or 0)
            with log_path.open('ab') as log:
                while True:
                    passes+=1;check()
                    changed=began=time.monotonic();observed=None;moved=False;embedding=False;tail=b''
                    offset=log_path.stat().st_size  # The program writes through its own handle; ours does not move.
                    limit=stall_seconds if passes==1 else min(stall_seconds,FOLLOW_UP_STALL)
                    try:result=_run_reindex(command+arguments,log,deadline-time.monotonic(),tick,cleanup_wait)
                    except IndexStalled:result=subprocess.CompletedProcess(command+arguments,None)
                    check()
                    if log_path.stat().st_size>LOG_LIMIT:raise index.IndexAttention('private_log_budget_exceeded')
                    progress.pop('cleanup',None)
                    progress.update(stage='verifying current coverage',passes=passes)
                    _save(progress)
                    counts=_counts(deadline);check();coverage=index.status(deadline=deadline);check()
                    if result.returncode or coverage.get('status')=='verified' or passes>=MAX_PASSES:break
                    vectors=coverage.get('vectors') or {};scope=coverage.get('coverage') or {}
                    defects=sum(int(value or 0) for value in (vectors.get('invalid'),scope.get('missing'),scope.get('stale')))
                    # Measured progress: more embedded vectors than after the pass before (a very large note
                    # stays one defect while each pass embeds 256 more of its chunks), or fewer defects than ever.
                    gained=(had is not None and embedded(counts)>had) or (best is not None and defects<best)
                    best=defects if best is None else min(best,defects);had=embedded(counts)
                    misses=0 if gained else misses+1
                    lost=vectors.get('zero_chunk_entities') or vectors.get('stale_search_sources')
                    if arguments==['--full','--search'] or ((moved or gained) and misses<2):arguments=[]
                    elif lost and not repaired:repaired=True;misses=0;arguments=['--full','--search']
                    else:break
                    progress['stage']='indexing local notes'
            if result.returncode is None and coverage.get('status')!='verified':
                progress['stage']='indexing local notes'
                raise IndexStalled('Local indexing made no observable DB progress within its stall budget.')
            stable=coverage.get('coverage',{}).get('manifest_sha256')==manifest and coverage.get('inventory_stable') is True
            good=result.returncode in (0,None) and coverage.get('status')=='verified' and stable
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
