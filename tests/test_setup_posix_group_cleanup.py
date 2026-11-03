"""Single-waiter lifetime controls; native process proof is separate."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess,sys,time,types
import pytest

@pytest.fixture
def rig(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'setup_runtime.py'
    spec=importlib.util.spec_from_file_location('setup_group_proposal',path)
    setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
    events=[];handlers={2:0,15:0,1:0,17:0};mode={'value':'normal'};group={'left':False}
    def handler(signum,value):previous=handlers[signum];handlers[signum]=value;return previous
    signal=types.SimpleNamespace(SIGINT=2,SIGTERM=15,SIGHUP=1,SIGCHLD=17,SIG_DFL=0,SIG_IGN=1,
                                 signal=handler,getsignal=lambda s:handlers[s])
    class Child:
        pid=123;returncode=None;stdin=None;stdout=None;stderr=None
        def communicate(self,timeout):
            events.append('communicate')
            if mode['value']=='timeout':raise subprocess.TimeoutExpired(['synthetic'],timeout)
            if mode['value']=='cancel-reap':handlers[2](2,None)
            if mode['value']=='cancel':
                handlers[2](2,None);raise subprocess.TimeoutExpired(['synthetic'],timeout)
            if mode['value']=='reaped-error':self.returncode=1;raise ValueError('synthetic decode failure after reap')
            self.returncode=1 if mode['value']=='bad-exit' else 0
            return 'out','err'
        def wait(self,timeout):events.append('wait');self.returncode=0;return 0
        def poll(self):events.append('poll');return self.returncode
    child=Child()
    def popen(*args,**kwargs):
        events.append('spawn');assert kwargs['start_new_session'] is True
        if mode['value']=='spawn-cancel':handlers[15](15,None)
        return child
    def killpg(pid,signum):
        assert pid==child.pid;events.append(('signal',signum))
        if signum==0:
            if group['left']:group['left']=False;return
            raise ProcessLookupError
    monkeypatch.setitem(sys.modules,'signal',signal)
    monkeypatch.setattr(setup,'os',types.SimpleNamespace(name='posix',killpg=killpg))
    monkeypatch.setattr(setup,'subprocess',types.SimpleNamespace(Popen=popen,PIPE=subprocess.PIPE,CompletedProcess=subprocess.CompletedProcess,TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(time,'sleep',lambda seconds:events.append(('sleep',seconds)))
    return setup,mode,group,handlers,events,child

def test_normal_result_and_group_absence_before_return(rig):
    setup,mode,group,handlers,events,_=rig
    result=setup.run(['synthetic'],source_app='unused',capture_output=True,text=True,timeout=3,check=True)
    assert (result.returncode,result.stdout,result.stderr)==(0,'out','err')
    assert ('signal',0) in events and not any(x in events for x in [('signal',15),('signal',9)])
    assert all(value==0 for value in handlers.values())

@pytest.mark.parametrize('mode_name,expected',[('timeout',subprocess.TimeoutExpired),('cancel',InterruptedError),('spawn-cancel',InterruptedError)])
def test_cancellation_keeps_leader_unreaped_through_last_signal(rig,mode_name,expected):
    setup,mode,_,handlers,events,_=rig;mode['value']=mode_name
    # SIGKILL constant must be present on the fake POSIX signal module.
    sys.modules['signal'].SIGKILL=9
    with pytest.raises(expected):setup.run(['synthetic'],source_app='unused',timeout=.01)
    term=events.index(('signal',15));kill=events.index(('signal',9));wait=events.index('wait')
    assert term<kill<wait and not any(x in ('wait','poll') for x in events[term:kill])
    assert all(value==0 for value in handlers.values())

def test_exception_after_reap_never_uses_numeric_signal_authority(rig):
    setup,mode,_,_,events,_=rig;mode['value']='reaped-error'
    with pytest.raises(ValueError,match='decode failure'):setup.run(['synthetic'],source_app='unused',timeout=.01)
    assert [event for event in events if isinstance(event,tuple) and event[0]=='signal']==[('signal',0)]

def test_nonzero_check_occurs_without_postreap_signals(rig):
    setup,mode,_,_,events,_=rig;mode['value']='bad-exit'
    with pytest.raises(subprocess.CalledProcessError):setup.run(['synthetic'],source_app='unused',timeout=3,check=True)
    assert [event for event in events if isinstance(event,tuple) and event[0]=='signal']==[('signal',0)]

def test_remaining_group_retains_admission_until_absence(rig):
    setup,_,group,_,events,_=rig;group['left']=True
    setup.run(['synthetic'],source_app='unused',timeout=.01)
    assert events.count(('signal',0))==2 and ('sleep',.1) in events

def test_ignored_sigchld_refuses_before_spawn(rig):
    setup,_,_,handlers,events,_=rig;handlers[17]=1
    with pytest.raises(ValueError,match='child-reaping'):setup.run(['synthetic'],source_app='unused',timeout=.01)
    assert not events


def test_flag_handler_returns_through_reap_before_cancellation(rig):
    setup,mode,_,_,events,_=rig;mode['value']='cancel-reap'
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',timeout=3)
    assert [e for e in events if isinstance(e,tuple) and e[0]=='signal']==[('signal',0)]

def test_ignored_hangup_is_preserved(rig):
    setup,_,_,handlers,_,_=rig;handlers[1]=1
    assert setup.run(['synthetic'],source_app='unused',timeout=3).returncode==0
    assert handlers[1]==1

def test_unrestoreable_handler_refuses_before_spawn(rig):
    setup,_,_,handlers,events,_=rig;handlers[15]=None
    with pytest.raises(ValueError,match='unrestoreable'):setup.run(['synthetic'],source_app='unused',timeout=3)
    assert 'spawn' not in events

def test_closed_stderr_never_releases_group_retention(rig,monkeypatch):
    setup,_,group,_,events,_=rig;group['left']=True
    class Closed:
        def write(self,*args):raise ValueError('synthetic closed log')
        def flush(self):raise ValueError('synthetic closed log')
    monkeypatch.setattr(setup.sys,'stderr',Closed())
    assert setup.run(['synthetic'],source_app='unused',timeout=3).returncode==0
    assert events.count(('signal',0))==2


@pytest.mark.parametrize('error',[PermissionError('synthetic denied group query'),OSError('synthetic uncertain group query')])
def test_group_probe_errors_retain_cleanup_until_verified_absence(rig,monkeypatch,error):
    setup,_,_,_,events,_=rig
    original=setup.os.killpg;attempts=[]
    def probe(pid,signum):
        if signum==0 and not attempts:
            attempts.append(True);events.append(('uncertain-probe',signum));raise error
        return original(pid,signum)
    monkeypatch.setattr(setup.os,'killpg',probe)
    assert setup.run(['synthetic'],source_app='unused',timeout=3).returncode==0
    assert attempts==[True] and ('sleep',.1) in events
    assert events[-1]==('signal',0)


def test_pending_cancellation_during_cleanup_cannot_skip_final_signal_or_reap(rig,monkeypatch):
    setup,mode,_,handlers,events,_=rig;mode['value']='timeout'
    sys.modules['signal'].SIGKILL=9
    original=setup.os.killpg;delivered=[]
    def signal_with_pending(pid,signum):
        if signum:
            for pending in (1,2,15):handlers[pending](pending,None);delivered.append(pending)
        return original(pid,signum)
    monkeypatch.setattr(setup.os,'killpg',signal_with_pending)
    with pytest.raises(subprocess.TimeoutExpired):setup.run(['synthetic'],source_app='unused',timeout=.01)
    assert delivered==[1,2,15,1,2,15]
    assert events.index(('signal',9))<events.index('wait') and all(value==0 for value in handlers.values())


@pytest.mark.skipif(os.name=='nt',reason='Actual POSIX process/session proof; pure controls above run on Windows.')
@pytest.mark.parametrize('mode',['normal-descendant','timeout','cancel','repeated-signals'])
def test_native_setup_group_lifetime_is_bounded_and_never_signals_after_reap(tmp_path,mode):
    """A separate bounded driver contains signals and observes the actual runner."""
    setup_path=Path(__file__).resolve().parents[1]/'setup_runtime.py'
    leaf=tmp_path/'leaf.py';leader=tmp_path/'leader.py';driver=tmp_path/'driver.py'
    marker=tmp_path/'ready';signal_marker=tmp_path/'leaf-signal'
    leaf.write_text('''import signal,sys,time
from pathlib import Path
def seen(signum,frame):Path(sys.argv[2]).write_text(str(signum))
signal.signal(signal.SIGTERM,seen if sys.argv[3]=='normal-descendant' else signal.SIG_IGN)
Path(sys.argv[1]).write_text('ready')
time.sleep(3 if sys.argv[3]=='normal-descendant' else 8)
''',encoding='utf-8')
    leader.write_text('''import signal,subprocess,sys,time
from pathlib import Path
if sys.argv[4]!='normal-descendant':signal.signal(signal.SIGTERM,signal.SIG_IGN)
subprocess.Popen([sys.executable,'-I','-B',sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
deadline=time.monotonic()+4
while not Path(sys.argv[2]).exists():
    if time.monotonic()>deadline:raise RuntimeError('bounded leaf readiness failed')
    time.sleep(.01)
if sys.argv[4]!='normal-descendant':
    time.sleep(8)
''',encoding='utf-8')
    driver.write_text('''import importlib.util,json,os,signal,subprocess,sys,threading,time,types
from pathlib import Path
setup_path,leader,leaf,marker,signal_marker,mode=sys.argv[1:]
spec=importlib.util.spec_from_file_location('native_setup_group',setup_path)
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
children=[];signals=[];sender_errors=[];native_killpg=os.killpg
def launch(*args,**kwargs):
    child=subprocess.Popen(*args,**kwargs);children.append(child);return child
def group_signal(pid,signum):
    assert children and pid==children[0].pid
    signals.append([int(signum),children[0].returncode])
    if signum:assert children[0].returncode is None,'numeric group signal after reap'
    return native_killpg(pid,signum)
setup.os=types.SimpleNamespace(name='posix',killpg=group_signal)
setup.subprocess=types.SimpleNamespace(Popen=launch,PIPE=subprocess.PIPE,
    CompletedProcess=subprocess.CompletedProcess,TimeoutExpired=subprocess.TimeoutExpired)
def send():
    deadline=time.monotonic()+4
    while not Path(marker).exists():
        if time.monotonic()>deadline:sender_errors.append('leaf readiness timeout');return
        time.sleep(.01)
    # All sends finish within the runner's two-second anchored cleanup grace.
    sequence=[signal.SIGTERM] if mode=='cancel' else [signal.SIGHUP,signal.SIGINT,signal.SIGTERM]*4
    for signum in sequence:
        os.kill(os.getpid(),signum)
        time.sleep(.06)
sender=threading.Thread(target=send) if mode in ('cancel','repeated-signals') else None
if sender:sender.start()
started=time.monotonic();result=None;error=None
try:
    result=setup.run([sys.executable,'-I','-B',leader,leaf,marker,signal_marker,mode],
        source_app='unused',capture_output=True,text=True,timeout=1 if mode=='timeout' else 12)
except (InterruptedError,subprocess.TimeoutExpired) as caught:error=type(caught).__name__
finally:
    if sender:sender.join(timeout=5)
elapsed=time.monotonic()-started
assert not sender_errors and (sender is None or not sender.is_alive())
assert len(children)==1 and children[0].returncode is not None
assert signals and signals[-1][0]==0
try:native_killpg(children[0].pid,0)
except ProcessLookupError:pass
else:raise AssertionError('independent native group absence check failed')
print(json.dumps(dict(mode=mode,error=error,returncode=result.returncode if result else None,
    leader_returncode=children[0].returncode,elapsed=elapsed,signals=signals,leaf_ready=Path(marker).is_file(),
    leaf_signalled=Path(signal_marker).exists())))
''',encoding='utf-8')
    # This outer owned child is an execution bound, not a product cleanup signal.
    observed=subprocess.run([sys.executable,'-I','-B',str(driver),str(setup_path),str(leader),str(leaf),
                             str(marker),str(signal_marker),mode],capture_output=True,text=True,timeout=30)
    assert observed.returncode==0,(observed.stdout,observed.stderr)
    value=json.loads(observed.stdout)
    assert value['mode']==mode and value['leaf_ready'] and value['elapsed']<10
    if mode=='normal-descendant':
        assert value['error'] is None and value['returncode']==0 and value['elapsed']>=2.5
        assert all(signum==0 for signum,_ in value['signals']) and not value['leaf_signalled']
    else:
        assert 2<=value['elapsed']<6 and value['leader_returncode']==-9
        assert value['error']==('TimeoutExpired' if mode=='timeout' else 'InterruptedError')
        assert [signum for signum,_ in value['signals'] if signum]==[15,9]
        assert all(returncode is None for signum,returncode in value['signals'] if signum)
