"""Explicit, coverage-bound local indexing; no source rewrites or Git calls."""
from contextlib import closing,ExitStack
from datetime import datetime,timezone
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
# As an entry point this imports its sibling modules before it reads the start line. However it was launched, one that
# is never released leaves the application folder as it found it: no compiled files.
if __name__=='__main__':sys.dont_write_bytecode=True
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
EMBEDDING_MARKER=b'Building vector embeddings'
MAX_PASSES=160  # More than the largest verifiable note needs: 32,768 chunks at 256 per pass.
# The worker is app/brain_index_worker.py. It ends with 78 when this Python environment is not the reviewed build
# and with 75 when it never received the start line; neither has indexed anything.
GUARD_EXIT=78
UNRELEASED_EXIT=75
GUARD_REASON='index_worker_guard_refused'
REVIEWED_BASIC_MEMORY='0.23.2'
GUARD_REASONS=('linked_worker_script','basic_memory_missing','basic_memory_version_not_reviewed','basic_memory_import_failed',
               'vector_cleanup_missing','vector_cleanup_unreadable','vector_cleanup_not_reviewed')
GUARD_MARKER=b'index worker: guard_refused:'
# Every basic_memory module app/brain_index.py imports when it is used; a test keeps this equal to that source.
UPSTREAM=('basic_memory.ignore_utils','basic_memory.index.filesystem','basic_memory.file_utils',
          'basic_memory.repository.semantic_chunking','basic_memory.repository.embedding_provider_factory')
GUARD_GUIDANCE=('The Python environment of this runtime is not the reviewed build, or is incomplete, so nothing was indexed. '
                'A retry and a same-version setup will not change it. Search keeps using the existing index. See '
                '"Index worker refused" in docs/DEVICE-SETUP.md.')
# Setup for the version a device already runs never replaces app/, and refuses a runtime that holds a link there. So
# this receipt names the one supported repair: put back the one file, from the verified release, and check its hash.
LINKED_SECTION='Index worker is a link'
LINKED_GUIDANCE=('app/brain_index_worker.py in this runtime is a link, not the installed file, so nothing was indexed. '
                 'A retry will not change it, and setup for this same version does not replace application files. '
                 'Follow "'+LINKED_SECTION+'" in docs/DEVICE-SETUP.md: in the release checkout named by release_root in '
                 'device.json, confirm app/brain_index_worker.py is a regular file whose SHA-256 is the one '
                 'release-manifest.json lists; remove the link itself, never its target; copy that file to '
                 'app/brain_index_worker.py; confirm the same SHA-256 there; then run index retry. '
                 'Search keeps using the existing index.')
DEFER_GUIDANCE=('Nothing was indexed because service control was busy or closed. The service tries again by itself; '
                'a manual index retry can be repeated.')
# A deferral names the pass that was not started. What earlier passes of the same run committed stays committed.
LATER_DEFER_GUIDANCE=('Pass {next} was not started because service control was busy or closed. The {done} earlier '
                      'pass(es) of this run finished and what they indexed is kept; the index is not yet verified. '
                      'The service tries again by itself; a manual index retry can be repeated.')
DRAIN_GUIDANCE='The service was stopped while indexing. Nothing needs repair; the service indexes again when it next starts.'
RELEASE=b'start\n'
RELEASE_SECONDS=30  # How long an entry point waits for its launcher; the worker waits as long.
GRANT_SECONDS=25    # The launcher's own window, started before the worker exists, so it always ends first.
READ_SECONDS=10
EXPIRY_READS=5
UNKNOWN_FOLLOW_UPS=2
MAX_DEFERRALS=10


class IndexStalled(RuntimeError): pass


class LaunchDeferred(RuntimeError):
    """Raised by launch.admission(): service control is busy or closed, so no worker may be started now."""


class _Stop:
    """TERM and HUP for as long as main() owns the receipt. The first one raises once, and only while armed.

    A raise that some helper absorbs is not lost: pending() stays true and main()'s check() raises again."""
    def __init__(self):self.signal=0;self.armed=False;self.closed=False;self.previous={}
    def __call__(self,signum,frame):
        first=not self.signal
        if first:self.signal=signum
        if first and self.armed:
            self.armed=False;raise InterruptedError('Local index operation interrupted.')
    def pending(self):return bool(self.signal) and not self.closed
    def install(self):
        import threading
        if os.name=='nt' or threading.current_thread() is not threading.main_thread():return
        for signum in (signal.SIGTERM,signal.SIGHUP):self.previous[signum]=signal.signal(signum,self)
        self.armed=True
    def restore(self):
        self.armed=False
        for signum,old in self.previous.items():
            if old is not None:signal.signal(signum,old)


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


def _receipt():
    """The saved receipt as a mapping, from a bounded read; None when it is absent, too large or not a mapping."""
    try:
        with (cfg.STATE/'index-bootstrap-status.json').open('rb') as stream:raw=stream.read(65537)
        value=json.loads(raw) if len(raw)<=65536 else None
    except (OSError,ValueError,RecursionError):return None
    return value if isinstance(value,dict) else None


def _prior_deferrals():
    state=_receipt() or {}
    count=state.get('deferrals')
    return count if state.get('status')=='deferred' and type(count) is int and count>0 else 0


def settle_interrupted(drain=None,state=None):
    """For a caller that holds index-bootstrap.lock, so no bootstrap owns the receipt. Returns the status now saved.

    state is the receipt that caller has already read under that lock. It is settled as read and the file is not
    read a second time: a second read that failed would hide the interrupted run this call exists to settle.

    A bootstrap that was killed leaves 'indexing'. drain(generation) is the lifecycle owner's answer for the generation
    that receipt names: True when a recorded drain intent names it and its bootstrap and worker are verified gone,
    False when no drain intent names it. Any other answer, or an error, proves neither: nothing is decided and
    'indexing' is returned. With no predicate, or a receipt with no generation (a manual retry), the receipt becomes
    'needs attention' / 'interrupted_bootstrap', as it always has."""
    if state is None:state=_receipt()
    if not isinstance(state,dict):return None
    if state.get('status')!='indexing':return state.get('status')
    generation=state.get('launch_generation');drained=False
    if drain is not None and isinstance(generation,str) and generation:
        try:drained=drain(generation)
        except Exception:drained=None
        if drained is not True and drained is not False:return 'indexing'
    if drained:
        _save({**state,'status':'deferred','stage':'launch deferred','reason':'managed_drain','deferrals':0,
               'guidance':DRAIN_GUIDANCE,'finished_at':_now()},terminal=True)
        return 'deferred'
    _save({**state,'status':'needs attention','reason':'interrupted_bootstrap','stage':'explicit retry required',
           'finished_at':_now()},terminal=True)
    return 'needs attention'


def _upstream():
    """Why Basic Memory cannot be used here, or None. Called where this process first needs it."""
    from importlib import import_module,metadata
    try:found=metadata.version('basic-memory')
    except metadata.PackageNotFoundError:return 'basic_memory_missing'
    if found!=REVIEWED_BASIC_MEMORY:return 'basic_memory_version_not_reviewed'
    try:
        for name in UPSTREAM:import_module(name)
    except ImportError:return 'basic_memory_import_failed'  # Whatever its name: a missing dependency of one counts.
    return None


def _guard_reason(log_path,start):
    """The reason the worker named in what this pass wrote. The start is fixed; the tick's own offset moves."""
    longest=len(GUARD_MARKER)+max(map(len,GUARD_REASONS))+2;tail=b''
    try:
        with log_path.open('rb') as written:
            written.seek(start)
            # Other startup output may come first. A pass cannot have written more than the log budget.
            for _ in range(LOG_LIMIT//1048576+2):
                piece=written.read(1048576)
                if not piece:break
                text=(tail+piece).replace(b'\r\n',b'\n')
                for name in GUARD_REASONS:
                    if GUARD_MARKER+name.encode()+b'\n' in text:return name
                tail=text[-longest:]
    except OSError:pass
    return 'guard_reason_unavailable'


def _lifetime(pid):
    """A positive identity for the child just started, or None. Two failed reads never compare equal."""
    if not sys.platform.startswith('linux'):return ('unreaped child',pid)  # Its PID is reserved until this process reaps it.
    import brain_linux_identity as identity
    try:found=identity.process(pid)
    except (identity.IdentityError,OSError,ValueError):return None
    return None if found is None else (found['boot_id'],found['start_ticks'])


def _ready(deadline=None):
    """A digest of the committed chunk rows whose stored vector matches them. It changes when a shard is embedded
    in place, which leaves every row count as it was."""
    digest=hashlib.sha256()
    with closing(sqlite3.connect((cfg.STATE/'memory.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as connection:
        if deadline is not None:connection.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        for row in connection.execute(
                "SELECT c.id,c.source_hash FROM search_vector_chunks AS c JOIN search_vector_embeddings_auxiliary AS a "
                "ON a.rowid=c.id WHERE c.embedding_status='ready' AND a.value00=c.source_hash ORDER BY c.id"):
            digest.update(b'%d:%s;'%(row[0],str(row[1]).encode()))
    return digest.hexdigest()


def _ready_settled(deadline=None,attempts=3):
    # A failed read is not evidence of anything. Read again, briefly, before calling the result unknown.
    for attempt in range(attempts):
        try:return _ready(deadline)
        except InterruptedError:raise
        except (sqlite3.Error,OSError):
            if attempt+1<attempts:time.sleep(.5)
    return None


def _run_reindex(command,log,deadline,on_tick,on_cleanup_wait,on_spawn=None,admission=None):
    """Run one worker pass to the absolute monotonic deadline.

    admission() returns a context manager whose entry never waits and raises LaunchDeferred when service control is
    busy or closed. This runner owns that scope: it is entered before anything is started and left only after the
    worker has been given its start line, or after a worker that was never given it has been ended and reaped.
    on_spawn(process) runs inside the scope, before the start line; if it raises, the worker is never released."""
    overall=max(0.0,deadline-time.monotonic())  # Only for the text of a timeout; never added to a clock again.
    with ExitStack() as scope:
        if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(command,overall)
        if admission is not None:
            scope.enter_context(admission())
            # Admission that took too long starts nothing.
            if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(command,overall)
        return _run_admitted(command,log,deadline,overall,on_tick,on_cleanup_wait,on_spawn,scope)


def _run_admitted(command,log,deadline,overall,on_tick,on_cleanup_wait,on_spawn,scope):
    if os.name=='nt':
        from brain_windows_process import run,StartWindowExpired
        # The start line is in the runner's standard-input file before the program exists, so creating the program
        # is the grant. The runner checks both clocks immediately before creating it inside its Job, and the scope
        # is closed right after. A launcher that dies takes the Job with it.
        try:
            return run(command,input=RELEASE,env=cfg.environment(),stdout=log,stderr=log,timeout=overall,
                       deadline=deadline,start_by=min(deadline,time.monotonic()+GRANT_SECONDS),
                       on_tick=on_tick,on_cleanup_wait=on_cleanup_wait,direct_output=True,on_started=scope.close)
        except StartWindowExpired:raise index.IndexAttention('index_worker_release_timeout') from None
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
    try:
        for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            if signum==signal.SIGINT and handlers[signum]==signal.SIG_IGN:continue
            handlers[signum]=signal.signal(signum,interrupted)
        check_interrupt()
        grant=min(deadline,time.monotonic()+GRANT_SECONDS)
        # The worker is in its own session and would outlive this process. It does nothing until it reads the start
        # line, and reads end of input instead if this process dies first.
        process=subprocess.Popen(command,env=cfg.environment(),stdin=subprocess.PIPE,stdout=log,stderr=log,start_new_session=True)
        born=_lifetime(process.pid)
        check_interrupt()
        if on_spawn is not None:on_spawn(process)
        check_interrupt()
        if process.poll() is None:
            if born is None or _lifetime(process.pid)!=born:
                # No positive match. A worker that ended meanwhile is read by the loop; anything else is refused.
                if process.poll() is None:raise index.IndexAttention('index_worker_identity_unverified')
            else:
                now=time.monotonic()  # The last statement before the write: nothing slow may follow it.
                if now>=deadline:raise subprocess.TimeoutExpired(command,overall)
                if now>=grant:raise index.IndexAttention('index_worker_release_timeout')
                try:process.stdin.write(RELEASE);process.stdin.close()
                except OSError:pass  # It ended first; its exit status is read below.
        scope.close()
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
                # A worker still waiting for its start line reads end of input here.
                try:process.stdin.close()
                except (AttributeError,OSError,ValueError):pass
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


def main(*,overall_seconds=OVERALL_SECONDS,stall_seconds=STALL_SECONDS,full=False,retry=False,launch=None):
    """Index, verify coverage, and save one receipt.

    launch is the service lifecycle's seam and may be None (no lifecycle integration; the start gate still applies):
      launch.admission()      a context manager for _run_reindex; raises LaunchDeferred when busy or closed
      launch.publish(process) called with the spawned worker before it is released; raises to refuse it
      launch.drained()        True when a recorded drain intent names this run; must not take service-control.lock,
                              because a stop holds that lock while it waits for this process
      launch.generation       an opaque string saved in the receipt as launch_generation; None for a manual retry
    """
    cfg.clamp_search_memory()
    if (type(overall_seconds) is not int or type(stall_seconds) is not int
            or not 1<=stall_seconds<overall_seconds<=86400):
        raise ValueError('Use integer budgets: 1 <= stall-seconds < overall-seconds <= 86400.')
    cfg.STATE.mkdir(parents=True,exist_ok=True)
    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):
        cfg.write_admission()
        deferrals=_prior_deferrals()
        if deferrals>=MAX_DEFERRALS and not retry:
            # Ten managed runs in a row found service control busy or closed. A manual retry is never held to this.
            return _save({'status':'needs attention','stage':'launch deferred','reason':'launch_admission_unavailable',
                          'deferrals':deferrals,'finished_at':_now(),
                          'guidance':'Service control stayed busy or closed. Check the service, then use an explicit index retry.'},terminal=True)
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
        stop=_Stop()
        def check():
            # Also where an interruption that a helper absorbed is raised again.
            if stop.pending():raise InterruptedError('Local index operation interrupted.')
            if time.monotonic()>=deadline:raise index.IndexAttention('overall_time_limit')
        progress={'status':'indexing','stage':'checking source-safe configuration','started_at':_now(),
                  'progress_signal':'whole-DB counts and the committed vector manifest, from any writer; not attributed to this process',
                  'overall_seconds':overall_seconds,'stall_seconds':stall_seconds,'full':bool(full),'history_record':history}
        generation=getattr(launch,'generation',None)
        progress['launch_generation']=generation if isinstance(generation,str) and generation else None
        def drained():
            try:return launch is not None and launch.drained() is True
            except Exception:return False
        def finish(value):
            # Every terminal save. From here a signal is only recorded: the receipt being written is the true one.
            stop.armed=False;stop.closed=True
            # A stop that is draining this run may have interrupted it where nothing could raise: during the runner's
            # cleanup, or inside a helper that turned the interruption into an ordinary failure. Whatever that left,
            # short of a verified index, is the drain's doing and is saved as such, so the service indexes again.
            if value.get('status')!='verified' and drained():
                value={**progress,'status':'deferred','stage':'launch deferred','reason':'managed_drain','deferrals':0,
                       'guidance':DRAIN_GUIDANCE,'finished_at':_now(),'elapsed_seconds':time.monotonic()-started}
            return _save(value,terminal=True)
        def interrupted():
            if 'cleanup' in progress:progress['cleanup']='runner returned after cleanup; inspect native receipt for platform scope'
            return finish({**progress,'status':'needs attention','error_type':'InterruptedError','reason':'local_indexing_failed',
                           'error':'Indexing did not verify. Preserve the database and private log; use index status, then an explicit index retry after repair.',
                           'finished_at':_now(),'elapsed_seconds':time.monotonic()-started})
        try:
            stop.install();_save(progress)
            try:
                check();index.configuration();check()
                # The first place this process needs Basic Memory. What cannot be imported here is the environment's
                # fault and is named as such; no worker is started.
                refused=_upstream()
                if refused:
                    progress['guard_reason']=refused;raise index.IndexAttention(GUARD_REASON)
                progress['index_scope_migration']=index.ensure_history_scope()
                check();notes,manifest=index.inventory(check);check()
                if not notes:
                    return finish({**progress,'status':'needs review','stage':'complete','reason':'no_eligible_markdown',
                                  'finished_at':_now(),'elapsed_seconds':time.monotonic()-started})
                worker=cfg.ROOT/'app'/'brain_index_worker.py'
                if worker.is_symlink() or os.path.isjunction(worker):raise index.IndexAttention('linked_worker_refused')
                command=[str(cfg.PYTHON),'-I','-B',str(worker),'reindex','-p','brain'];arguments=['--full'] if full else []
                log_path=cfg.ROOT/'logs/index-bootstrap.log'; log_path.parent.mkdir(parents=True,exist_ok=True)
                previous=log_path.with_name('index-bootstrap.previous.log')
                if any(path.is_symlink() or os.path.isjunction(path) for path in (log_path,previous)):
                    raise index.IndexAttention('linked_log_refused')
                if log_path.exists():os.replace(log_path,previous)
                progress.update(stage='indexing local notes',source_manifest_sha256=manifest,last_progress_at=_now())
                _save(progress)
                limit=stall_seconds;moved=False;offset=0;embedding=False;began=started;tail=b''
                sample=known=None;due=float('-inf');unread=0
                def tick():
                    nonlocal changed,observed,last_saved,moved,embedding,offset,tail,sample,due,unread
                    check();started_embedding=False
                    if not embedding:
                        # Only what this pass has written counts; the marker of an earlier pass is before the offset.
                        # New output is read as it comes, with enough of the previous piece kept to join a split marker.
                        # Everything written so far is read before the phase is judged; the log has its own size limit.
                        with log_path.open('rb') as written:
                            written.seek(offset)
                            while not embedding:
                                check();piece=written.read(1048576)
                                if not piece:break
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
                    if not embedding:
                        # The text phase is judged by its own budget: two hours, but no more than a quarter of what this
                        # run had left when the pass began, so that coverage can still be read and decide after the program
                        # is ended. It is never shorter than the stall budget, which is what applied here before.
                        if now-began>=max(limit,min(TEXT_PHASE_SECONDS,(deadline-began)/4)):
                            raise IndexStalled('Local indexing did not finish its text index phase within its budget.')
                        changed=now;return
                    if started_embedding:changed=now  # The count clock starts when embedding does.
                    # Counts stay flat while a shard is embedded in place, so what is committed is read as well: four
                    # times per stall budget, and always on a tick that would declare a stall. The schedule moves on
                    # whether or not a read succeeds. A stall needs two successful reads that agree, the later one made
                    # in this tick, with a whole budget since the earlier; or EXPIRY_READS failed reads on consecutive
                    # expired ticks.
                    expired=now-changed>=limit
                    if now>=due or expired:
                        due=now+limit/4;fresh=False
                        try:mark=_ready(min(deadline,time.monotonic()+READ_SECONDS))
                        except InterruptedError:raise
                        except (sqlite3.Error,OSError):pass
                        else:
                            fresh=True
                            # The first successful read of a pass is a baseline, not progress: the budget is counted
                            # from what was seen. It happens at most once per pass.
                            if sample is None:changed=now;expired=False
                            elif mark!=sample:changed=now;expired=False;progress.update(last_progress_at=_now())
                            sample=mark
                        if expired:
                            unread+=1
                            if fresh or unread>=EXPIRY_READS:
                                raise IndexStalled('Local indexing made no observable DB progress within its stall budget.')
                    if not expired:unread=0
                def cleanup_wait():
                    progress.update(stage='containment cleanup pending',cleanup='bootstrap lock retained until owned processes exit')
                    _save(progress,terminal=True)
                # One upstream run is not always a finished index. It embeds a very large note 256
                # chunks at a time and drops search rows that lose a write-lock race. So: repeat while
                # the committed index changes, rebuild lost search rows once, and let coverage decide the
                # result. Basic Memory 0.23.2 also ended every run with an orphan cleanup that was quadratic
                # in chunk count and changed no row; the worker replaces that statement, and the stall
                # watchdog above stays as a second defence.
                best=None;misses=0;repaired=False;passes=0;had=None
                def embedded(value):return int(value.get('search_vector_embeddings_rowids') or 0)
                # A fast first pass can finish before the runner samples any writes.
                try:had=embedded(_counts(deadline))
                except (sqlite3.Error,OSError):
                    if not (cfg.STATE/'memory.db').exists():had=0
                # Every committed state seen in this run. One seen before is not progress, so an index that only
                # thrashes cannot keep the loop going.
                seen=set();blind=0;known=_ready_settled(deadline,1)
                if known is not None:seen.add(known)
                with log_path.open('ab') as log:
                    while True:
                        passes+=1;check()
                        changed=began=time.monotonic();observed=None;moved=False;embedding=False;tail=b''
                        pass_start=offset=log_path.stat().st_size  # The program writes through its own handle; ours does not move.
                        progress.pop('process_exit',None);sample=known;due=float('-inf');unread=0
                        limit=stall_seconds if passes==1 else min(stall_seconds,FOLLOW_UP_STALL)
                        # The runner records signals itself and raises at its own checkpoints, so the handler of
                        # this function does not raise across it; tick() and the check() after it still do.
                        stop.armed=False
                        try:result=_run_reindex(command+arguments,log,deadline,tick,cleanup_wait,
                                                on_spawn=getattr(launch,'publish',None),admission=getattr(launch,'admission',None))
                        except IndexStalled:result=subprocess.CompletedProcess(command+arguments,None)
                        finally:stop.armed=not stop.signal and bool(stop.previous)
                        # The worker's own verdict is recorded before anything reads the database, which may not exist.
                        progress.update(process_exit=result.returncode,passes=passes)
                        if result.returncode==GUARD_EXIT:
                            progress['guard_reason']=_guard_reason(log_path,pass_start);raise index.IndexAttention(GUARD_REASON)
                        if result.returncode==UNRELEASED_EXIT:raise index.IndexAttention('index_worker_not_released')
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
                        # stays one defect while each pass embeds 256 more of its chunks), fewer defects than ever,
                        # or a committed state not seen before in this run (a shard embedded in place changes
                        # neither count). A state that could not be read after a clean pass is unknown: neither a
                        # gain nor a miss, and it earns a follow-up at most UNKNOWN_FOLLOW_UPS times per run.
                        mark=_ready_settled(deadline);check()
                        shard=result.returncode==0 and mark is not None and mark not in seen
                        unknown=result.returncode==0 and mark is None and blind<UNKNOWN_FOLLOW_UPS
                        if unknown:blind+=1
                        if mark is not None:seen.add(mark);known=mark
                        gained=(had is not None and embedded(counts)>had) or (best is not None and defects<best) or shard
                        best=defects if best is None else min(best,defects);had=embedded(counts)
                        if not unknown:misses=0 if gained else misses+1
                        lost=vectors.get('zero_chunk_entities') or vectors.get('stale_search_sources')
                        # A full pass resets the vector count. Give its first shard one incremental follow-up.
                        if passes==1 and full and coverage.get('status')=='needs review':
                            arguments=[]
                        elif arguments==['--full','--search'] or ((moved or gained or unknown) and misses<2):arguments=[]
                        elif lost and not repaired:repaired=True;misses=0;arguments=['--full','--search']
                        else:break
                        progress['stage']='indexing local notes'
                if result.returncode is None and coverage.get('status')!='verified':
                    progress['stage']='indexing local notes'
                    raise IndexStalled('Local indexing made no observable DB progress within its stall budget.')
                stable=coverage.get('coverage',{}).get('manifest_sha256')==manifest and coverage.get('inventory_stable') is True
                good=result.returncode in (0,None) and coverage.get('status')=='verified' and stable
                outcome='verified' if good else ('needs attention' if result.returncode or coverage.get('status')=='needs attention' else 'needs review')
                return finish({**progress,'status':outcome,'stage':'complete',
                              'counts':counts,'process_exit':result.returncode,'current_index':coverage,
                              'inventory_unchanged_during_run':stable,'verification':index.SCOPE,
                              'finished_at':_now(),'elapsed_seconds':time.monotonic()-started})
            except LaunchDeferred as error:
                # The pass that asked was not started. The supervisor admits this status again by itself. Passes this
                # run had already finished are counted in 'passes', and the receipt does not say they indexed nothing.
                done=progress.get('passes') or 0
                return finish({**progress,'status':'deferred','stage':'launch deferred','reason':str(error),
                               'deferrals':deferrals+1,'deferred_pass':done+1,
                               'guidance':LATER_DEFER_GUIDANCE.format(next=done+1,done=done) if done else DEFER_GUIDANCE,
                               'finished_at':_now(),'elapsed_seconds':time.monotonic()-started})
            except Exception as error:
                if isinstance(error,InterruptedError):return interrupted()
                if isinstance(error,ImportError):
                    # Past the preflight, a module that cannot be imported is still the environment's fault.
                    progress.setdefault('guard_reason','runtime_import_failed');error=index.IndexAttention(GUARD_REASON)
                if 'cleanup' in progress:progress['cleanup']='runner returned after cleanup; inspect native receipt for platform scope'
                reason=str(error) if isinstance(error,(index.IndexAttention,index.WalkLimit)) else 'local_indexing_failed'
                advice={'error':'Indexing did not verify. Preserve the database and private log; use index status, then an explicit index retry after repair.'}
                if reason=='linked_worker_refused' or (reason==GUARD_REASON and progress.get('guard_reason')=='linked_worker_script'):
                    advice={'error':'Indexing did not start: the index worker script is not the installed file.','guidance':LINKED_GUIDANCE}
                elif reason==GUARD_REASON:
                    advice={'error':'Indexing did not start: this Python environment is not the reviewed build.','guidance':GUARD_GUIDANCE}
                return finish({**progress,'status':'needs attention','error_type':type(error).__name__,'reason':reason,**advice,
                               'finished_at':_now(),'elapsed_seconds':time.monotonic()-started})
        except InterruptedError:return interrupted()  # Raised inside a handler above, or before the inner block.
        finally:stop.restore()


def _released(seconds=RELEASE_SECONDS):
    """True once the launcher sends the start line; end of input means the launcher ended first."""
    import threading
    got=[]
    def read():
        try:got.append(sys.stdin.buffer.readline(len(RELEASE)+1))
        except (AttributeError,OSError,ValueError):pass
    reader=threading.Thread(target=read,daemon=True);reader.start();reader.join(seconds)
    return got==[RELEASE]


if __name__=='__main__':
    # Either entry form does nothing, and writes nothing, until its launcher has sent the start line: the supervisor
    # sends it only after it has durably enrolled this process. A manual index retry calls main() and passes no gate.
    if sys.argv[1:] not in ([],['--reconcile']):raise SystemExit(2)
    if not _released():
        print('index bootstrap: not_released',file=sys.stderr,flush=True)
        os._exit(UNRELEASED_EXIT)  # The reader thread may still hold stdin.
    if sys.argv[1:]==['--reconcile']:
        from brain_index_reconcile import main as reconcile
        reconcile()
    else:
        main()
