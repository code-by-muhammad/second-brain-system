"""The launch seam the index runner offers the service lifecycle: admission scope, start gate, deferral and drain.

The lifecycle side is a stand-in here; real processes are this test's own children and all data is synthetic."""
from contextlib import closing,contextmanager
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_index_bootstrap as bootstrap
from test_index_coverage import config,database,add_note

posix=pytest.mark.skipif(os.name=='nt',reason='POSIX start pipe and signals; the Windows runner is exercised by its native job.')
# Stands for the worker: the same gate, then whatever the test asked for. It never imports anything upstream.
GATED='''import os,sys,time
from pathlib import Path
folder=Path(__file__).parent
line=sys.stdin.buffer.readline(7)
if line!=b"start\\n":
    (folder/"refused").write_text(repr(line));os._exit(75)
(folder/"released").write_text(" ".join(sys.argv[1:]))
plan=folder/"plan"
time.sleep(float(plan.read_text()) if plan.exists() else 0)
'''


def ready():
    config()
    with closing(database()) as connection:add_note(connection,1,'01-Projects/Source.md')


@pytest.fixture
def worker(brain):
    """A stand-in at the path the bootstrap starts, so main() runs its real runner."""
    folder=cfg.ROOT/'app';folder.mkdir()
    (folder/'brain_index_worker.py').write_text(GATED,encoding='utf-8')
    return folder


def _command(folder):return [sys.executable,'-I','-B',str(folder/'brain_index_worker.py'),'reindex','-p','brain']


def _run(folder,tmp_path,seconds=30,**launch):
    with (tmp_path/'runner.log').open('ab') as log:
        return bootstrap._run_reindex(_command(folder),log,time.monotonic()+seconds,lambda:None,lambda:None,**launch)


class Launch:
    """service-control.lock, tried without waiting, is all this stand-in knows about the lifecycle."""
    def __init__(self,*,generation='generation-A',drained=False,publish=None,closed=None):
        self.generation=generation;self.events=[];self._drained=drained;self._publish=publish;self._closed=closed
        self.process=None;self.reaped_before_left=None
    @contextmanager
    def admission(self):
        if self._closed:raise bootstrap.LaunchDeferred(self._closed)
        try:lock=FileLock(str(cfg.STATE/'service-control.lock'),timeout=0);lock.acquire()
        except Timeout:raise bootstrap.LaunchDeferred('launch_admission_busy') from None
        self.events.append('entered')
        try:yield
        finally:
            self.reaped_before_left=self.process is not None and self.process.returncode is not None
            lock.release();self.events.append('left')
    def publish(self,process):
        self.process=process;self.events.append('published')
        if self._publish is not None:self._publish(process)
    def drained(self):return self._drained


def _contender():
    try:
        with FileLock(str(cfg.STATE/'service-control.lock'),timeout=0):return True
    except Timeout:return False


@posix
def test_scope_is_entered_before_the_spawn_and_left_only_after_the_grant(worker,tmp_path):
    seen={}
    def publish(process):
        time.sleep(.3)  # Publication takes a while; the worker must still be waiting and the lock still held.
        seen.update(alive=process.poll() is None,released=(worker/'released').exists(),contended=_contender(),pipe_open=not process.stdin.closed)
    launch=Launch(publish=publish)
    result=_run(worker,tmp_path,on_spawn=launch.publish,admission=launch.admission)
    assert result.returncode==0 and launch.events==['entered','published','left']
    assert seen=={'alive':True,'released':False,'contended':False,'pipe_open':True}
    assert launch.process.stdin.closed and (worker/'released').read_text()=='reindex -p brain' and _contender()
    assert launch.reaped_before_left is False  # Left at the grant, not held for the whole pass.


@posix
def test_without_a_launch_object_the_gate_still_applies(worker,tmp_path):
    assert _run(worker,tmp_path).returncode==0 and (worker/'released').exists()


@posix
@pytest.mark.parametrize('slow',['admission','publication'])
def test_nothing_is_granted_after_the_deadline_and_the_scope_outlasts_the_worker(worker,tmp_path,slow):
    launch=Launch(publish=(lambda process:time.sleep(.6)) if slow=='publication' else None)
    @contextmanager
    def admission():
        with launch.admission():
            if slow=='admission':time.sleep(.6)
            yield
    with pytest.raises(subprocess.TimeoutExpired):
        _run(worker,tmp_path,seconds=.3,on_spawn=launch.publish,admission=admission)
    assert not (worker/'released').exists() and _contender()
    if slow=='admission':assert launch.process is None and not (worker/'refused').exists()  # Nothing was started at all.
    else:assert launch.process.returncode is not None and launch.reaped_before_left is True


@posix
def test_grant_window_ends_before_the_worker_stops_waiting(worker,tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'GRANT_SECONDS',.2)
    launch=Launch(publish=lambda process:time.sleep(.5))
    with pytest.raises(bootstrap.index.IndexAttention,match='index_worker_release_timeout'):
        _run(worker,tmp_path,on_spawn=launch.publish,admission=launch.admission)
    assert not (worker/'released').exists() and launch.reaped_before_left is True


@posix
@pytest.mark.parametrize('lifetimes',[[None,None],[('boot',1),('boot',2)],[('boot',1),None]],ids=['unreadable-twice','changed','lost'])
def test_grant_needs_the_same_positive_identity_at_spawn_and_at_the_grant(worker,tmp_path,monkeypatch,lifetimes):
    (worker/'plan').write_text('30')
    values=iter(lifetimes);monkeypatch.setattr(bootstrap,'_lifetime',lambda pid:next(values))
    launch=Launch()
    with pytest.raises(bootstrap.index.IndexAttention,match='index_worker_identity_unverified'):
        _run(worker,tmp_path,on_spawn=launch.publish,admission=launch.admission)
    assert not (worker/'released').exists() and launch.process.returncode is not None and launch.reaped_before_left is True


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux start ticks and boot id.')
def test_linux_identity_is_boot_and_start_ticks_and_absent_for_no_process(tmp_path):
    mine=bootstrap._lifetime(os.getpid())
    assert mine==bootstrap._lifetime(os.getpid()) and len(mine)==2 and mine[1]>0
    child=subprocess.Popen([sys.executable,'-I','-B','-c','pass']);child.wait()
    assert bootstrap._lifetime(child.pid) is None


@posix
def test_worker_that_ended_before_the_grant_is_read_not_refused(worker,tmp_path):
    (worker/'brain_index_worker.py').write_text('raise SystemExit(7)\n',encoding='utf-8')
    launch=Launch(publish=lambda process:process.wait(timeout=20))
    assert _run(worker,tmp_path,on_spawn=launch.publish,admission=launch.admission).returncode==7


# ---- through main(): receipts ----------------------------------------------------------------------------------

def _receipt():return json.loads((cfg.STATE/'index-bootstrap-status.json').read_text())


def _admitted():
    import brain_supervisor as supervisor
    return supervisor._bootstrap_admitted()


@posix
def test_publish_refusal_is_terminal_and_the_worker_is_never_released(worker):
    ready()
    def refuse(process):raise RuntimeError('synthetic lineage write failure')
    launch=Launch(publish=refuse)
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
    assert (result['status'],result['reason'],result['error_type'])==('needs attention','local_indexing_failed','RuntimeError')
    assert 'synthetic' not in json.dumps(result) and result['launch_generation']=='generation-A'
    assert not (worker/'released').exists() and launch.process.returncode is not None and _admitted() is False


@posix
def test_managed_run_verifies_through_the_seam(worker):
    ready()
    launch=Launch()
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
    assert (result['status'],result['process_exit'])==('verified',0) and launch.events==['entered','published','left']


def test_busy_admission_defers_without_starting_anything_and_is_admitted_again(worker,monkeypatch):
    ready()
    monkeypatch.setattr(bootstrap.subprocess,'Popen',lambda *a,**k:pytest.fail('No worker while service control is busy.'))
    launch=Launch()
    with FileLock(str(cfg.STATE/'service-control.lock'),timeout=0):
        result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
    assert (result['status'],result['stage'],result['reason'],result['deferrals'])==('deferred','launch deferred','launch_admission_busy',1)
    assert result['guidance']==bootstrap.DEFER_GUIDANCE and 'process_exit' not in result and _receipt()==result
    assert result['deferred_pass']==1 and 'passes' not in result and result['guidance'].startswith('Nothing was indexed')
    assert _admitted() is True and _receipt()==result  # Admission reads it and leaves it alone.


@posix
def test_a_deferral_on_a_later_pass_does_not_say_that_nothing_was_indexed(worker,monkeypatch):
    """Pass one runs and commits; admission is refused for pass two. The receipt is about pass two only."""
    ready()
    class SecondDenied(Launch):
        asked=0
        @contextmanager
        def admission(self):
            self.asked+=1
            if self.asked==2:raise bootstrap.LaunchDeferred('launch_admission_busy')
            with Launch.admission(self):yield
    review={'status':'needs review','inventory_stable':True,'coverage':{'missing':0,'stale':0},'vectors':{'invalid':1}}
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:review)
    marks=iter(['before','after pass one'])
    monkeypatch.setattr(bootstrap,'_ready_settled',lambda deadline=None,attempts=3:next(marks))  # Pass one committed a shard.
    launch=SecondDenied()
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
    assert launch.asked==2 and launch.events==['entered','published','left'] and (worker/'released').exists()
    assert (result['status'],result['stage'],result['reason'],result['deferrals'])==('deferred','launch deferred','launch_admission_busy',1)
    assert (result['passes'],result['deferred_pass'])==(1,2) and 'process_exit' not in result
    assert result['guidance']==bootstrap.LATER_DEFER_GUIDANCE.format(next=2,done=1)
    assert 'Nothing was indexed' not in result['guidance'] and 'Pass 2 was not started' in result['guidance']
    assert _receipt()==result and _admitted() is True


def test_ten_deferrals_in_a_row_end_managed_retries_but_never_a_manual_one(worker,monkeypatch):
    ready()
    launch=Launch(closed='launch_admission_closed')
    for number in range(1,11):
        result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
        assert (result['status'],result['reason'],result['deferrals'])==('deferred','launch_admission_closed',number)
        assert _admitted() is True
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=launch)
    assert (result['status'],result['reason'],result['deferrals'])==('needs attention','launch_admission_unavailable',10)
    assert _admitted() is False
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:SimpleNamespace(returncode=0))
    assert bootstrap.main(retry=True,overall_seconds=30,stall_seconds=5)['status']=='verified'


def test_any_other_receipt_resets_the_deferral_count(worker,monkeypatch):
    ready()
    path=cfg.STATE/'index-bootstrap-status.json'
    path.write_text(json.dumps({'status':'deferred','reason':'launch_admission_busy','deferrals':9}))
    assert bootstrap._prior_deferrals()==9
    for other in ({'status':'needs review','deferrals':9},{'status':'deferred','deferrals':'9'},{'status':'deferred','deferrals':True},['deferred']):
        path.write_text(json.dumps(other));assert bootstrap._prior_deferrals()==0
    path.write_text('{"status":"deferred","deferrals":10,"pad":"'+'x'*70000+'"}');assert bootstrap._prior_deferrals()==0


def test_admission_closed_by_a_drain_is_the_drain_and_does_not_count(worker):
    ready()
    (cfg.STATE/'index-bootstrap-status.json').write_text(json.dumps({'status':'deferred','reason':'launch_admission_busy','deferrals':4}))
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=Launch(closed='launch_admission_closed',drained=True))
    assert (result['status'],result['reason'],result['deferrals'],result['guidance'])==('deferred','managed_drain',0,bootstrap.DRAIN_GUIDANCE)
    assert _admitted() is True


def _terminate_self():
    os.kill(os.getpid(),signal.SIGTERM)
    for _ in range(100):time.sleep(.01)  # The handler runs between two instructions of this thread.


@posix
@pytest.mark.parametrize('drained',[True,False])
@pytest.mark.parametrize('phase',['preparation','pass','verification','absorbed'])
def test_term_while_the_receipt_is_owned_is_a_drain_only_when_the_lifecycle_says_so(worker,monkeypatch,phase,drained):
    ready()
    before={signum:signal.getsignal(signum) for signum in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT)}
    launch=Launch(drained=drained)
    inventory=bootstrap.index.inventory;status=bootstrap.index.status
    if phase=='preparation':
        def interrupted(check):_terminate_self();return inventory(check)
        monkeypatch.setattr(bootstrap.index,'inventory',interrupted)
    elif phase=='pass':
        (worker/'plan').write_text('60')
        counts=bootstrap._counts
        def interrupted(deadline=None):
            if (worker/'released').exists():os.kill(os.getpid(),signal.SIGTERM)
            return counts(deadline)
        monkeypatch.setattr(bootstrap,'_counts',interrupted)
    elif phase=='verification':
        def interrupted(deadline=None):_terminate_self();return status(deadline=deadline)
        monkeypatch.setattr(bootstrap.index,'status',interrupted)
    else:
        # A helper turns the interruption into an ordinary failure, as the real status reader does.
        def absorbed(deadline=None):
            try:_terminate_self()
            except InterruptedError:return {'status':'needs attention','reason':'index_status_failed','error_type':'InterruptedError'}
            pytest.fail('The interruption was not delivered.')
        monkeypatch.setattr(bootstrap.index,'status',absorbed)
    started=time.monotonic()
    result=bootstrap.main(overall_seconds=120,stall_seconds=30,launch=launch)
    assert time.monotonic()-started<20
    assert {signum:signal.getsignal(signum) for signum in before}==before
    if drained:
        assert (result['status'],result['stage'],result['reason'],result['deferrals'])==('deferred','launch deferred','managed_drain',0)
        assert result['guidance']==bootstrap.DRAIN_GUIDANCE and _admitted() is True
    else:
        assert (result['status'],result['reason'],result['error_type'])==('needs attention','local_indexing_failed','InterruptedError')
        assert _admitted() is False
    assert _receipt()==result
    if phase=='pass':assert launch.process.returncode is not None  # No worker is left behind.


@posix
def test_term_after_a_verified_receipt_is_decided_leaves_it_verified(worker,monkeypatch):
    ready()
    save=bootstrap._save
    def interrupted(value,*,terminal=False):
        if value.get('status')=='verified':os.kill(os.getpid(),signal.SIGTERM);time.sleep(.05)
        return save(value,terminal=terminal)
    monkeypatch.setattr(bootstrap,'_save',interrupted)
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=Launch(drained=True))
    assert result['status']=='verified' and _receipt()['status']=='verified'


@posix
def test_the_run_after_a_drain_receipt_verifies(worker,monkeypatch):
    ready()
    inventory=bootstrap.index.inventory
    def interrupted(check):_terminate_self();return inventory(check)
    with monkeypatch.context() as scope:
        scope.setattr(bootstrap.index,'inventory',interrupted)
        assert bootstrap.main(overall_seconds=30,stall_seconds=5,launch=Launch(drained=True))['reason']=='managed_drain'
    assert _admitted() is True
    result=bootstrap.main(overall_seconds=30,stall_seconds=5,launch=Launch(generation='generation-B'))
    assert (result['status'],result['process_exit'],result['launch_generation'])==('verified',0,'generation-B')


# ---- a bootstrap that was killed: settling its receipt ----------------------------------------------------------

def _killed(generation='generation-A'):
    (cfg.STATE/'index-bootstrap-status.json').write_text(json.dumps({'status':'indexing','stage':'indexing local notes','launch_generation':generation}))


def _raises(generation):raise RuntimeError('synthetic predicate failure')


@pytest.mark.parametrize('answer,settled,admitted',[
    (lambda generation:True,'deferred',True),
    (lambda generation:False,'needs attention',False),
    (lambda generation:None,'indexing',None),
    (lambda generation:1,'indexing',None),
    (_raises,'indexing',None),
    (None,'needs attention',False),
])
def test_killed_bootstrap_is_settled_by_the_drain_predicate_or_not_at_all(brain,monkeypatch,answer,settled,admitted):
    import brain_supervisor as supervisor
    _killed();asked=[]
    predicate=None if answer is None else (lambda generation:(asked.append(generation),answer(generation))[1])
    monkeypatch.setattr(supervisor.service,'managed_drain_recorded',predicate,raising=False)
    assert supervisor._bootstrap_admitted() is admitted
    receipt=_receipt()
    assert receipt['status']==settled and asked==([] if answer is None else ['generation-A'])
    if settled=='deferred':assert (receipt['reason'],receipt['deferrals'],receipt['guidance'])==('managed_drain',0,bootstrap.DRAIN_GUIDANCE)
    if settled=='needs attention':assert (receipt['reason'],receipt['stage'])==('interrupted_bootstrap','explicit retry required')
    if settled=='indexing':assert 'finished_at' not in receipt
    assert receipt['launch_generation']=='generation-A'


@pytest.mark.parametrize('generation',[None,'',7])
def test_a_manual_run_has_no_generation_and_is_never_settled_as_drained(brain,generation):
    _killed(generation)
    assert bootstrap.settle_interrupted(lambda value:pytest.fail('No generation to ask about.'))=='needs attention'
    assert _receipt()['reason']=='interrupted_bootstrap'


def test_settling_leaves_every_other_receipt_alone(brain):
    path=cfg.STATE/'index-bootstrap-status.json'
    assert bootstrap.settle_interrupted(lambda value:True) is None
    for status in ('verified','needs review','needs attention','deferred'):
        raw=json.dumps({'status':status,'launch_generation':'generation-A'});path.write_text(raw)
        assert bootstrap.settle_interrupted(lambda value:True)==status and path.read_text()==raw


def test_supervisor_starts_nothing_not_even_the_repair_while_an_earlier_exit_is_unproved(brain,monkeypatch):
    import brain_supervisor as supervisor
    from supervisor_fixture import install
    install(monkeypatch,supervisor)
    class Done(BaseException):pass
    _killed()
    monkeypatch.setattr(supervisor.service,'managed_drain_recorded',lambda generation:None,raising=False)
    monkeypatch.setattr(supervisor.service,'spawn_allowed',lambda:True)
    monkeypatch.setattr(supervisor.processes,'listener_identity',lambda:{'synthetic':True})
    monkeypatch.setattr(supervisor,'_maintenance_due',lambda owned:False)
    monkeypatch.setattr(supervisor.subprocess,'Popen',lambda *a,**k:pytest.fail('Nothing may be started.'))
    monkeypatch.setattr(supervisor.time,'sleep',lambda seconds:(_ for _ in ()).throw(Done()))
    with pytest.raises(Done):supervisor.main({})
    assert _receipt()['status']=='indexing'


def _cycles(monkeypatch,supervisor,count,started,after_admission=None):
    """Run supervisor.main() for whole cycles with a listener up, no maintenance due and the repair long overdue."""
    from supervisor_fixture import install
    install(monkeypatch,supervisor)
    class Done(BaseException):pass
    slept=[];answers=[];admitted=supervisor._bootstrap_admitted
    def admission():
        answers.append(admitted())
        if after_admission is not None:after_admission(len(answers))
        return answers[-1]
    def sleep(seconds):
        slept.append(seconds)
        if len(slept)==count:raise Done()
    def start(arguments,log):
        started.append(list(arguments));return SimpleNamespace(pid=4242,poll=lambda:None)
    monkeypatch.setattr(supervisor,'_bootstrap_admitted',admission)
    monkeypatch.setattr(supervisor,'_start_bootstrap',start)
    monkeypatch.setattr(supervisor.service,'spawn_allowed',lambda:True)
    monkeypatch.setattr(supervisor.processes,'listener_identity',lambda:{'synthetic':True})
    monkeypatch.setattr(supervisor,'_maintenance_due',lambda owned:False)
    monkeypatch.setattr(supervisor.subprocess,'Popen',lambda *a,**k:pytest.fail('Nothing else may be started.'))
    monkeypatch.setattr(supervisor.time,'sleep',sleep)
    with pytest.raises(Done):supervisor.main({})
    return answers


@pytest.mark.parametrize('proof,second,launched,status',[
    (None,None,[],'indexing'),                          # Still unproved once the lock is free: still nothing.
    (False,False,[['--reconcile']],'needs attention'),  # Proved an unexplained interruption: the repair may run.
    (True,True,[[]],'deferred'),                        # Proved a managed drain: the first index runs again.
])
def test_a_held_writer_lock_starts_nothing_even_if_it_is_let_go_before_the_spawn(brain,monkeypatch,proof,second,launched,status):
    """The interleaving that mattered: the holder exits between the admission answer and the launch.

    Contention used to answer False, which is also what lets the repair start; the repair then ran beside a
    generation whose bootstrap and worker had never been proved gone, and the drain predicate was never asked."""
    import brain_supervisor as supervisor
    _killed();asked=[];started=[];seen=[]
    holder=FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0);holder.acquire()
    def let_go(cycle):
        if cycle==1:
            holder.release();seen.append((list(started),list(asked)))
    monkeypatch.setattr(supervisor.service,'managed_drain_recorded',lambda generation:(asked.append(generation),proof)[1],raising=False)
    answers=_cycles(monkeypatch,supervisor,2,started,let_go)
    # Cycle one: contended, so not proved; the lock was free again before any launch could happen, and nothing did.
    assert answers[0] is None and seen==[([],[])]
    # Cycle two: the lock is free, so the lifecycle owner is asked about that generation, and only its proof decides.
    assert answers[1] is second and asked==['generation-A'] and started==launched
    assert _receipt()['status']==status


@pytest.mark.parametrize('proof,answer,launched,status',[
    (None,None,[],'indexing'),
    (False,False,[['--reconcile']],'needs attention'),
    (True,True,[[]],'deferred'),
])
def test_the_receipt_read_under_the_lock_is_the_one_settled_and_is_not_read_again(brain,monkeypatch,proof,answer,launched,status):
    """A second read of the receipt that failed once answered False, which lets the repair start: it then ran while
    the receipt still said 'indexing' and the drain predicate had never been asked about that generation."""
    import brain_supervisor as supervisor
    from pathlib import Path
    _killed();asked=[];started=[];reads=[]
    path=cfg.STATE/'index-bootstrap-status.json';opened=Path.open
    def one_read(self,*arguments,**options):
        if self==path and arguments[:1]==('rb',):
            reads.append(self)
            if len(reads)>1:raise PermissionError('synthetic transient failure of a second receipt read')
        return opened(self,*arguments,**options)
    monkeypatch.setattr(Path,'open',one_read)
    monkeypatch.setattr(supervisor.service,'managed_drain_recorded',lambda generation:(asked.append(generation),proof)[1],raising=False)
    answers=_cycles(monkeypatch,supervisor,1,started)
    monkeypatch.setattr(Path,'open',opened)
    assert len(reads)==1 and asked==['generation-A']
    assert answers==[answer] and started==launched and _receipt()['status']==status


def test_settling_a_receipt_it_was_given_reads_nothing(brain,monkeypatch):
    _killed();state=_receipt()
    monkeypatch.setattr(bootstrap,'_receipt',lambda:pytest.fail('The receipt was already read under the lock.'))
    assert bootstrap.settle_interrupted(lambda generation:None,state)=='indexing'
    assert bootstrap.settle_interrupted(lambda generation:False,state)=='needs attention'
    assert bootstrap.settle_interrupted(lambda generation:True,{'status':'verified'})=='verified'


def test_a_writer_lock_held_throughout_never_starts_the_repair(brain,monkeypatch):
    import brain_supervisor as supervisor
    _killed();started=[]
    monkeypatch.setattr(supervisor.service,'managed_drain_recorded',lambda generation:pytest.fail('Not asked while it is held.'),raising=False)
    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):
        assert _cycles(monkeypatch,supervisor,3,started)==[None,None,None]
    assert started==[] and _receipt()['status']=='indexing'


# ---- both entry forms wait for their launcher -------------------------------------------------------------------

ENTRY=os.path.join(os.path.dirname(bootstrap.__file__),'brain_index_bootstrap.py')


def _entry(tmp_path,arguments,data):
    root=tmp_path/'runtime';state=root/'state';vault=tmp_path/'vault'
    for folder in (state,vault,tmp_path/'home'):folder.mkdir(parents=True,exist_ok=True)
    environment={**os.environ,'BRAIN_SYSTEM_ROOT':str(root),'BRAIN_STATE':str(state),'BRAIN_VAULT':str(vault),
                 'BASIC_MEMORY_CONFIG_DIR':str(state),'HOME':str(tmp_path/'home'),'USERPROFILE':str(tmp_path/'home')}
    result=subprocess.run([sys.executable,'-B',ENTRY,*arguments],input=data,capture_output=True,timeout=120,env=environment)
    return result,sorted(path.name for path in state.rglob('*'))+sorted(path.name for path in root.glob('logs/*'))


@pytest.mark.parametrize('arguments',[[],['--reconcile']])
@pytest.mark.parametrize('data',[b'',b'go\n',b'start',b'start \n'])
def test_entry_forms_do_nothing_without_the_start_line(tmp_path,arguments,data):
    result,written=_entry(tmp_path,arguments,data)
    assert result.returncode==75 and result.stderr.replace(b'\r\n',b'\n')==b'index bootstrap: not_released\n'
    assert written==[]


@pytest.mark.parametrize('arguments',[['--full'],['--reconcile','--now'],['retry']])
def test_entry_refuses_unknown_arguments_before_the_gate(tmp_path,arguments):
    result,written=_entry(tmp_path,arguments,b'start\n')
    assert result.returncode==2 and written==[]


def test_released_entry_runs_and_saves_its_own_receipt(tmp_path):
    """The pipe release is enough only on backends without Linux user-space enrolment."""
    result,written=_entry(tmp_path,[],b'start\n')
    if sys.platform=='linux':
        from brain_linux_user_service import systemd_manager_present
        if not systemd_manager_present():
            assert result.returncode==75 and b'bootstrap_unenrolled' in result.stderr
            assert written==[]
            return
    assert result.returncode==0 and b'not_released' not in result.stderr
    assert 'index-bootstrap-status.json' in written


# ---- the supervisor releases what it starts ---------------------------------------------------------------------

def _stand_in_bootstrap():
    folder=cfg.ROOT/'app';folder.mkdir(exist_ok=True)
    (folder/'brain_index_bootstrap.py').write_text(
        'import sys\nfrom pathlib import Path\nline=sys.stdin.buffer.readline(7)\n'
        'Path(__file__).with_name("seen").write_text(repr(line)+" "+" ".join(sys.argv[1:]))\n'
        'sys.exit(0 if line==b"start\\n" else 75)\n',encoding='utf-8')
    return folder


@pytest.mark.parametrize('arguments',[[],['--reconcile']])
def test_supervisor_releases_either_entry_form_after_enrolment(brain,monkeypatch,tmp_path,arguments):
    import brain_supervisor as supervisor
    folder=_stand_in_bootstrap();order=[]
    def enrol(child,given):
        time.sleep(.3);order.append(((folder/'seen').exists(),child.poll(),given))
    monkeypatch.setattr(supervisor.service,'enrol_index_bootstrap',enrol,raising=False)
    monkeypatch.setattr(cfg,'environment',lambda:dict(os.environ))
    with (tmp_path/'bootstrap.log').open('ab') as log:child=supervisor._start_bootstrap(arguments,log)
    assert child.wait(timeout=30)==0 and child.stdin.closed
    assert order==[(False,None,arguments)]  # Enrolled while it was still waiting and had done nothing.
    assert (folder/'seen').read_text()==repr(b'start\n')+' '+' '.join(arguments)


def test_supervisor_ends_a_bootstrap_it_could_not_enrol(brain,monkeypatch,tmp_path):
    import brain_supervisor as supervisor
    folder=_stand_in_bootstrap();children=[]
    def refuse(child,given):children.append(child);raise RuntimeError('synthetic enrolment failure')
    monkeypatch.setattr(supervisor.service,'enrol_index_bootstrap',refuse,raising=False)
    monkeypatch.setattr(cfg,'environment',lambda:dict(os.environ))
    with (tmp_path/'bootstrap.log').open('ab') as log:
        with pytest.raises(RuntimeError,match='synthetic enrolment failure'):supervisor._start_bootstrap([],log)
    assert children[0].returncode==75 and (folder/'seen').read_text().startswith(repr(b''))


# ---- a bootstrap that is never released leaves the application folder as it found it ---------------------------

def _clean_application():
    """The real application files at the path the supervisor starts, with no compiled files among them."""
    import shutil
    folder=cfg.ROOT/'app'
    shutil.copytree(os.path.dirname(bootstrap.__file__),folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    assert not _compiled(folder)
    return folder


def _compiled(folder):return sorted(str(path.relative_to(folder)) for path in folder.rglob('*') if path.name=='__pycache__' or path.suffix=='.pyc')


def _writing_environment():
    # The service environment does not turn compiled files off; this suite's own does, and would hide the fault.
    return {name:value for name,value in os.environ.items() if name!='PYTHONDONTWRITEBYTECODE'}


@pytest.mark.parametrize('arguments',[[],['--reconcile']])
def test_supervisor_launch_of_an_unreleased_bootstrap_writes_no_compiled_files(brain,monkeypatch,tmp_path,arguments):
    import brain_supervisor as supervisor
    folder=_clean_application();commands=[];spawn=supervisor.subprocess.Popen
    def record(command,**options):
        commands.append([str(part) for part in command]);return spawn(command,**options)
    def refuse(child,given):
        time.sleep(1.5)  # Long enough for the child to have imported every sibling module it imports before the gate.
        raise RuntimeError('synthetic enrolment failure')
    monkeypatch.setattr(supervisor.subprocess,'Popen',record)
    monkeypatch.setattr(supervisor.service,'enrol_index_bootstrap',refuse,raising=False)
    monkeypatch.setattr(cfg,'environment',_writing_environment)
    with (tmp_path/'bootstrap.log').open('ab') as log:
        with pytest.raises(RuntimeError,match='synthetic enrolment failure'):supervisor._start_bootstrap(arguments,log)
    assert commands==[[str(cfg.PYTHON),'-B',str(folder/'brain_index_bootstrap.py'),*arguments]]
    assert (tmp_path/'bootstrap.log').read_bytes().replace(b'\r\n',b'\n')==b'index bootstrap: not_released\n'
    assert _compiled(folder)==[] and sorted(path.name for path in cfg.STATE.iterdir())==[]


@pytest.mark.parametrize('arguments',[[],['--reconcile']])
def test_an_unreleased_entry_form_writes_no_compiled_files_whoever_launched_it(brain,arguments):
    """Without -B and without the environment switch: the entry turns compiled files off before its first import."""
    folder=_clean_application()
    environment={**_writing_environment(),'BRAIN_SYSTEM_ROOT':str(cfg.ROOT),'BRAIN_STATE':str(cfg.STATE),'BRAIN_VAULT':str(cfg.VAULT),
                 'BASIC_MEMORY_CONFIG_DIR':str(cfg.STATE)}
    result=subprocess.run([sys.executable,str(folder/'brain_index_bootstrap.py'),*arguments],input=b'',capture_output=True,timeout=120,env=environment)
    assert result.returncode==75 and _compiled(folder)==[] and sorted(path.name for path in cfg.STATE.iterdir())==[]
