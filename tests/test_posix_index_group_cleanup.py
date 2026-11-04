"""POSIX group lifetime controls; pure fakes never address a host PID."""
from collections import deque
import errno
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from filelock import FileLock, Timeout
import pytest
import brain_index_bootstrap as bootstrap
import brain_settings as cfg


class Child:
    pid=43123

    def __init__(self,polls,waits=()):
        self.polls=deque(polls)
        self.waits=deque(waits)
        self.returncode=None
        self.events=[]

    def poll(self):
        self.events.append(('poll',self.returncode))
        if self.returncode is None and self.polls:
            value=self.polls.popleft()
            if isinstance(value,Exception):raise value
            if value is not None:self.returncode=value;self.events.append(('reaped',value))
        return self.returncode

    def wait(self,timeout):
        self.events.append(('wait',timeout))
        if self.returncode is not None:return self.returncode
        value=self.waits.popleft() if self.waits else subprocess.TimeoutExpired('synthetic',timeout)
        if isinstance(value,Exception):raise value
        self.returncode=value
        self.events.append(('reaped',value))
        return value


@pytest.fixture
def posix_runner(monkeypatch):
    def install(child,groups=(),delivery_error=None,sleep=None,disposition=None,delivery_hook=None):
        # Use private namespaces, rather than changing host os.name or signals.
        handlers={1:object(),2:object(),15:object(),17:0 if disposition is None else disposition}
        original=dict(handlers)
        fake_signal=SimpleNamespace(SIGTERM=15,SIGHUP=1,SIGINT=2,SIGKILL=9,SIGCHLD=17,SIG_DFL=0,SIG_IGN=1)
        fake_signal.getsignal=lambda signum:handlers[signum]
        def set_handler(signum,value):
            before=handlers[signum];handlers[signum]=value;return before
        fake_signal.signal=set_handler
        remaining=deque(groups)
        def killpg(pid,signum):
            assert pid==child.pid
            assert all(handlers[s]==fake_signal.SIG_IGN for s in (1,2,15))
            child.events.append(('signal',signum,child.returncode))
            if signum:
                assert child.returncode is None,'Never signal a group after reaping its leader.'
                if delivery_hook:delivery_hook(signum)
                if delivery_error:raise delivery_error
            else:
                value=remaining.popleft() if remaining else ProcessLookupError()
                if isinstance(value,Exception):raise value
        launches=[]
        def popen(command,**kwargs):launches.append((command,kwargs));return child
        monkeypatch.setattr(bootstrap,'os',SimpleNamespace(**{**vars(os),'name':'posix','killpg':killpg}))
        monkeypatch.setattr(bootstrap,'signal',fake_signal)
        monkeypatch.setattr(bootstrap,'subprocess',SimpleNamespace(Popen=popen,DEVNULL=subprocess.DEVNULL,TimeoutExpired=subprocess.TimeoutExpired,CompletedProcess=subprocess.CompletedProcess))
        def sleeping(seconds):
            child.events.append(('sleep',seconds))
            if sleep:sleep(seconds)
        monkeypatch.setattr(bootstrap,'time',SimpleNamespace(monotonic=lambda:0,sleep=sleeping))
        monkeypatch.setattr(cfg,'environment',lambda:{'SYNTHETIC':'1'})
        return handlers,original,launches
    return install


def run(child,overall=10,tick=lambda:None,cleanup=lambda:None):
    return bootstrap._run_reindex(['synthetic','literal argument'],object(),overall,tick,cleanup)


@pytest.mark.parametrize('polls,overall,waits,expected',[
    ([0],10,[],[]),
    ([None,None],0,[-9],[15,9]),
])
def test_group_signal_authority_stops_at_first_reap(posix_runner,polls,overall,waits,expected):
    child=Child(polls,waits)
    handlers,original,launches=posix_runner(child,groups=[True,ProcessLookupError()])
    notified=[]
    if overall:
        assert run(child,cleanup=lambda:notified.append(True)).returncode==0
    else:
        with pytest.raises(subprocess.TimeoutExpired):run(child,overall,cleanup=lambda:notified.append(True))
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==expected
    reaped=next(i for i,e in enumerate(child.events) if e[0]=='reaped')
    assert not any(e[0]=='signal' and e[1] for e in child.events[reaped+1:])
    assert notified==[True] and handlers==original
    assert launches[0][1]['start_new_session'] is True


def test_term_grace_does_not_reap_before_final_group_signal(posix_runner):
    child=Child([None,None],[-9])
    posix_runner(child)
    with pytest.raises(subprocess.TimeoutExpired):run(child,overall=0)
    term=child.events.index(('signal',15,None));kill=child.events.index(('signal',9,None))
    assert child.events[term+1:kill]==[('sleep',2)]
    assert ('wait',5) in child.events[kill+1:]


@pytest.mark.parametrize('error',[PermissionError(errno.EPERM,'synthetic'),OSError(errno.EIO,'synthetic')])
def test_signal_and_probe_errors_keep_admission_and_original_failure(posix_runner,error):
    child=Child([None,None],[-9]);notified=[]
    handlers,original,_=posix_runner(child,groups=[error,True,ProcessLookupError()],delivery_error=error)
    with pytest.raises(subprocess.TimeoutExpired):run(child,overall=0,cleanup=lambda:notified.append(True))
    assert len([e for e in child.events if e[0]=='signal' and e[1]==0])==3
    assert notified==[True] and handlers==original


def test_notification_failure_does_not_skip_absence_wait(posix_runner):
    child=Child([0]);calls=[]
    posix_runner(child,groups=[True,True,ProcessLookupError()])
    def cleanup():calls.append(True);raise OSError('synthetic status write denied')
    assert run(child,cleanup=cleanup).returncode==0
    assert calls==[True]
    assert len([e for e in child.events if e[0]=='sleep'])==2


def test_uncertain_child_wait_never_releases_before_reap_and_group_absence(posix_runner):
    child=Child([None,None,None,OSError('synthetic poll'),None,0],[subprocess.TimeoutExpired('synthetic',5)])
    posix_runner(child,groups=[True,ProcessLookupError()])
    with pytest.raises(subprocess.TimeoutExpired):run(child,overall=0)
    assert child.events[-1][0]=='signal' and child.events[-1][1]==0
    assert len([e for e in child.events if e[0]=='sleep' and e[1]==.1])>=3


@pytest.mark.parametrize('disposition',[1,lambda *a:None,object()])
def test_nondefault_reaper_refused_before_spawn(posix_runner,disposition):
    child=Child([0]);handlers,original,launches=posix_runner(child,disposition=disposition)
    with pytest.raises(bootstrap.index.IndexAttention,match='child_reaping_disposition_refused'):run(child)
    assert launches==[] and handlers==original and child.events==[]


def test_spawn_failure_restores_handlers(posix_runner,monkeypatch):
    child=Child([0]);handlers,original,_=posix_runner(child)
    def failed(*args,**kwargs):raise OSError('synthetic spawn failure')
    monkeypatch.setattr(bootstrap.subprocess,'Popen',failed)
    with pytest.raises(OSError,match='synthetic spawn failure'):run(child)
    assert handlers==original and child.events==[]


def test_partial_handler_install_failure_restores_prior_handler(posix_runner,monkeypatch):
    child=Child([0]);handlers,original,launches=posix_runner(child)
    native=bootstrap.signal.signal
    def fail_hup(signum,value):
        if signum==bootstrap.signal.SIGHUP:raise OSError('synthetic handler install')
        return native(signum,value)
    monkeypatch.setattr(bootstrap.signal,'signal',fail_hup)
    with pytest.raises(OSError,match='synthetic handler install'):run(child)
    assert handlers==original and launches==[]


def test_second_pending_interrupt_cannot_skip_cleanup(posix_runner,monkeypatch):
    child=Child([None,None],[-9]);handlers,original,_=posix_runner(child)
    native=bootstrap.signal.signal;pending=[];restored=[]
    def pending_during_ignore(signum,value):
        if value==bootstrap.signal.SIG_IGN and not pending:
            pending.append(True)
            handlers[bootstrap.signal.SIGTERM](bootstrap.signal.SIGTERM,None)
        if value is original[signum]:restored.append(signum)
        return native(signum,value)
    monkeypatch.setattr(bootstrap.signal,'signal',pending_during_ignore)
    with pytest.raises(subprocess.TimeoutExpired):run(child,overall=0)
    assert pending==[True] and handlers==original
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==[15,9]
    assert restored[-1]==bootstrap.signal.SIGINT


def test_term_during_popen_is_deferred_until_child_is_owned(posix_runner,monkeypatch):
    child=Child([None],[-9]);handlers,original,_=posix_runner(child)
    def launch(*a,**kw):
        handlers[bootstrap.signal.SIGTERM](bootstrap.signal.SIGTERM,None)
        return child
    monkeypatch.setattr(bootstrap.subprocess,'Popen',launch)
    with pytest.raises(InterruptedError):run(child)
    assert handlers==original
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==[15,9]


def test_already_dispatched_term_hup_handlers_cannot_raise_during_cleanup(posix_runner):
    child=Child([None,None],[-9]);saved=[];delivered=[]
    def delivery(signum):
        for pending in (1,15):
            saved[0](pending,None)
            delivered.append(pending)
    handlers,original,_=posix_runner(child,delivery_hook=delivery)
    def tick():
        saved.append(handlers[15])
        saved[0](15,None)
    with pytest.raises(InterruptedError):run(child,tick=tick)
    assert delivered==[1,15,1,15] and handlers==original
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==[15,9]


@pytest.mark.parametrize('sqlite_error',[False,True])
def test_signal_during_counts_is_checked_after_callback(brain,posix_runner,monkeypatch,sqlite_error):
    from test_index_retry import ready
    ready()
    child=Child([None,None],[-9]);handlers,original,_=posix_runner(child)
    monkeypatch.setattr(bootstrap.time,'monotonic',time.monotonic)
    completed=[]
    def counts(deadline):
        handlers[bootstrap.signal.SIGTERM](bootstrap.signal.SIGTERM,None)
        completed.append('handler returned')
        if sqlite_error:raise bootstrap.sqlite3.OperationalError('synthetic callback failure after signal')
        completed.append('counts returned')
        return None
    monkeypatch.setattr(bootstrap,'_counts',counts)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert completed==(['handler returned'] if sqlite_error else ['handler returned','counts returned'])
    assert result['status']=='needs attention' and result['error_type']=='InterruptedError'
    assert handlers==original
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==[15,9]


def test_passive_group_wait_retains_real_bootstrap_lock(brain,posix_runner,monkeypatch):
    from test_index_retry import ready
    ready()
    child=Child([0]);attempts=[]
    def sleep(seconds):
        with pytest.raises(Timeout):
            with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):pass
        attempts.append(seconds)
    posix_runner(child,groups=[True,ProcessLookupError()],sleep=sleep)
    monkeypatch.setattr(bootstrap.time,'monotonic',time.monotonic)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert attempts==[.1] and result['status']=='verified' and 'cleanup' not in result


def test_windows_job_delegation_has_no_posix_side_effects(monkeypatch):
    calls=[];result=object();environment={'SYNTHETIC':'1'};log=object();tick=object();cleanup=object()
    monkeypatch.setattr(bootstrap,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(bootstrap,'signal',SimpleNamespace())
    monkeypatch.setattr(cfg,'environment',lambda:environment)
    monkeypatch.setitem(sys.modules,'brain_windows_process',SimpleNamespace(run=lambda *a,**k:(calls.append((a,k)),result)[1]))
    assert bootstrap._run_reindex(['synthetic'],log,9,tick,cleanup) is result
    assert calls==[((['synthetic'],),dict(env=environment,stdout=log,stderr=log,timeout=9,on_tick=tick,on_cleanup_wait=cleanup,direct_output=True))]


@pytest.mark.skipif(os.name=='nt',reason='Native POSIX session/group proof; pure controls above also run on Windows.')
def test_native_normal_exit_waits_for_bounded_descendant_without_signalling(brain,tmp_path,monkeypatch):
    child_script=tmp_path/'group_child.py'
    child_script.write_text('import signal,time\nfrom pathlib import Path\ndef seen(s,f): Path(__file__).with_suffix(".signal").write_text(str(s))\nsignal.signal(signal.SIGTERM,seen)\ntime.sleep(3)\n',encoding='utf-8')
    leader_script=tmp_path/'group_leader.py'
    leader_script.write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"-I","-B",sys.argv[1]])\n',encoding='utf-8')
    calls=[];waits=[];native_killpg=os.killpg
    def probe(pid,signum):calls.append(signum);return native_killpg(pid,signum)
    monkeypatch.setattr(bootstrap.os,'killpg',probe)
    with (tmp_path/'native.log').open('ab') as log:
        result=bootstrap._run_reindex([sys.executable,'-I','-B',str(leader_script),str(child_script)],log,10,lambda:None,lambda:waits.append(True))
    assert result.returncode==0 and waits==[True]
    assert calls and all(s==0 for s in calls)
    assert not child_script.with_suffix('.signal').exists()


@pytest.mark.skipif(os.name=='nt',reason='Native POSIX session/group proof; pure controls above also run on Windows.')
@pytest.mark.parametrize('interruption',['tick','pending-signals','triple-signals'])
def test_native_abnormal_exit_kills_term_ignoring_descendant_before_reap(brain,tmp_path,monkeypatch,interruption):
    marker=tmp_path/'ready'
    leaf=tmp_path/'ignoring_child.py'
    leaf.write_text('import signal,sys,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath(sys.argv[1]).write_text("ready")\ntime.sleep(8)\n',encoding='utf-8')
    leader=tmp_path/'waiting_leader.py'
    leader.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-I","-B",sys.argv[1],sys.argv[2]])\ntime.sleep(8)\n',encoding='utf-8')
    calls=[];children=[];native_killpg=os.killpg;native_popen=subprocess.Popen
    def launch(*a,**kw):child=native_popen(*a,**kw);children.append(child);return child
    monkeypatch.setattr(bootstrap,'subprocess',SimpleNamespace(**{**vars(subprocess),'Popen':launch}))
    def signal_group(pid,signum):
        if signum:assert children[0].returncode is None
        calls.append(signum);return native_killpg(pid,signum)
    monkeypatch.setattr(bootstrap.os,'killpg',signal_group)
    def cancel():
        if not marker.exists():return
        if interruption=='tick':raise bootstrap.IndexStalled('Synthetic cancellation after descendant readiness.')
        pending={signal.SIGTERM,signal.SIGHUP}
        if interruption=='triple-signals':pending.add(signal.SIGINT)
        previous=signal.pthread_sigmask(signal.SIG_BLOCK,pending)
        try:
            os.kill(os.getpid(),signal.SIGHUP)
            if interruption=='triple-signals':os.kill(os.getpid(),signal.SIGINT)
            os.kill(os.getpid(),signal.SIGTERM)
        finally:signal.pthread_sigmask(signal.SIG_SETMASK,previous)
    started=time.monotonic()
    with (tmp_path/'native-abnormal.log').open('ab') as log:
        with pytest.raises(bootstrap.IndexStalled if interruption=='tick' else (InterruptedError,KeyboardInterrupt)):
            bootstrap._run_reindex([sys.executable,'-I','-B',str(leader),str(leaf),str(marker)],log,12,cancel,lambda:None)
    assert [s for s in calls if s]==[signal.SIGTERM,signal.SIGKILL]
    assert children[0].returncode is not None and time.monotonic()-started<8


def test_inherited_sigint_ignore_is_preserved(posix_runner,monkeypatch):
    child=Child([0]);handlers,original,_=posix_runner(child)
    handlers[2]=original[2]=bootstrap.signal.SIG_IGN
    native=bootstrap.signal.signal;changed=[]
    def capture(signum,value):
        if signum==bootstrap.signal.SIGINT:changed.append(value)
        return native(signum,value)
    monkeypatch.setattr(bootstrap.signal,'signal',capture)
    assert run(child).returncode==0
    assert handlers==original and changed and all(value==bootstrap.signal.SIG_IGN for value in changed)


def test_sigint_raises_keyboardinterrupt_at_safe_checkpoint(posix_runner):
    child=Child([None,None],[-9]);handlers,original,_=posix_runner(child);returned=[]
    def tick():
        handlers[bootstrap.signal.SIGINT](bootstrap.signal.SIGINT,None)
        returned.append(True)
    with pytest.raises(KeyboardInterrupt):run(child,tick=tick)
    assert returned==[True] and handlers==original
    assert [e[1] for e in child.events if e[0]=='signal' and e[1]]==[15,9]
