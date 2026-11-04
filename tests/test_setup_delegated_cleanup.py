"""Trusted handoff must retain admission while the inner runner drains/reaps."""
import ast,importlib.util,os,subprocess,sys,time,types
from pathlib import Path
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'setup_runtime.py'

@pytest.fixture
def rig(monkeypatch):
    spec=importlib.util.spec_from_file_location('delegated_setup',SOURCE)
    setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
    events=[];state={'trigger':'cancel','cleanup_calls':0,'error_once':False}
    handlers={2:0,15:0,1:0,17:0}
    def signal_handler(signum,value):previous=handlers[signum];handlers[signum]=value;return previous
    signal=types.SimpleNamespace(SIGINT=2,SIGTERM=15,SIGHUP=1,SIGKILL=9,SIGCHLD=17,SIG_DFL=0,SIG_IGN=1,
                                 signal=signal_handler,getsignal=lambda s:handlers[s])
    class Child:
        pid=321;returncode=None;stdin=None;stdout=None;stderr=None
        def communicate(self,timeout):
            events.append(('communicate',self.returncode))
            if state.get('already_reaped'):
                self.returncode=0;handlers[15](15,None);return 'out','err'
            if ('signal',15) not in events:
                if state['trigger']=='cancel':handlers[15](15,None)
                raise subprocess.TimeoutExpired(['synthetic'],timeout)
            state['cleanup_calls']+=1
            if state.get('persistent_error'):raise OSError('synthetic persistent pipe error')
            if state['cleanup_calls']<=2:
                # Pending signals cannot throw through or abort delegated cleanup.
                handlers[2](2,None)
                if state['error_once'] and state['cleanup_calls']==1:raise OSError('synthetic interrupted pipe')
                raise subprocess.TimeoutExpired(['synthetic'],timeout)
            self.returncode=0;events.append('inner-cleanup-complete');return 'out','err'
        def wait(self,timeout):events.append('wait');assert self.returncode is not None;return self.returncode
        def poll(self):
            events.append('poll')
            if state.get('persistent_error') and state['cleanup_calls']>=3:
                self.returncode=0;events.append('inner-cleanup-complete')
            return self.returncode
    child=Child()
    def launch(*args,**kwargs):
        assert kwargs['start_new_session'] is True
        assert 'delegated_cleanup' not in kwargs
        events.append('spawn');return child
    def killpg(pid,signum):
        assert pid==child.pid;events.append(('signal',signum))
        if signum:assert child.returncode is None
        else:raise ProcessLookupError
    monkeypatch.setitem(sys.modules,'signal',signal)
    monkeypatch.setattr(setup,'os',types.SimpleNamespace(name='posix',killpg=killpg))
    monkeypatch.setattr(setup,'subprocess',types.SimpleNamespace(Popen=launch,PIPE=subprocess.PIPE,
        CompletedProcess=subprocess.CompletedProcess,TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(time,'sleep',lambda value:events.append(('sleep',value)))
    return setup,state,events,handlers,child

@pytest.mark.parametrize('trigger,error',[('cancel',InterruptedError),('timeout',subprocess.TimeoutExpired)])
def test_trusted_cancel_or_timeout_waits_for_inner_cleanup_without_outer_kill(rig,trigger,error):
    setup,state,events,handlers,child=rig;state['trigger']=trigger
    with pytest.raises(error):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True,
                                       capture_output=True,text=True,timeout=.001)
    assert [e for e in events if isinstance(e,tuple) and e[0]=='signal']==[('signal',15),('signal',0)]
    assert state['cleanup_calls']==3
    assert events.index('inner-cleanup-complete')<events.index('wait')<events.index(('signal',0))
    assert child.returncode==0 and all(value==0 for value in handlers.values())

def test_delegated_pipe_error_retains_admission_until_child_reaped(rig):
    setup,state,events,handlers,_=rig;state['error_once']=True
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True)
    assert state['cleanup_calls']==3 and ('sleep',.1) in events
    assert ('signal',9) not in events and events[-1]==('signal',0)

def test_invalid_delegation_policy_refuses_before_launch(rig):
    setup,_,events,_,_=rig
    with pytest.raises(ValueError,match='trusted cleanup'):setup.run(['synthetic'],source_app='unused',delegated_cleanup='yes')
    assert not events

def test_windows_job_runner_does_not_receive_posix_policy(rig,monkeypatch,tmp_path):
    setup,_,_,_,_=rig;job=types.ModuleType('brain_windows_process');job.__file__=str(tmp_path/'brain_windows_process.py')
    calls=[]
    def run(command,**options):calls.append((command,options));return subprocess.CompletedProcess(command,0)
    job.run=run;monkeypatch.setitem(sys.modules,'brain_windows_process',job)
    monkeypatch.setattr(setup,'os',types.SimpleNamespace(name='nt'))
    assert setup.run(['synthetic'],source_app=tmp_path,delegated_cleanup=True,check=True,timeout=3).returncode==0
    assert calls==[(['synthetic'],{'timeout':3})]

def test_only_trusted_self_installer_handoff_enables_delegated_cleanup():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'install.py').read_bytes())
    selected=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            for item in node.keywords:
                if item.arg=='delegated_cleanup':selected.append((node,item))
    assert len(selected)==1
    node,item=selected[0]
    assert isinstance(item.value,ast.Constant) and item.value.value is True
    assert ast.unparse(node.func)=='helpers.run' and "REPO / 'install.py'" in ast.unparse(node.args[0])


@pytest.mark.skipif(os.name=='nt',reason='Actual POSIX nested-session cleanup proof runs in disposable native CI.')
@pytest.mark.parametrize('mode',['cancel','timeout'])
def test_native_trusted_handoff_reaps_inner_session_before_admission_release(tmp_path,mode):
    import hashlib,json
    probe=Path(__file__).with_name('native_nested_setup_delegation.py')
    receipt=tmp_path/('native-'+mode+'.json')
    command=[sys.executable,'-I','-B',str(probe),'--execute-reviewed-native','--mode',mode,
             '--setup-source',str(SOURCE),'--setup-sha256',hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
             '--receipt',str(receipt)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    value=json.loads(receipt.read_bytes())
    assert value['status']=='delegated nested cleanup verified' and value['mode']==mode and value['source_unchanged']
    observed=value['observation']
    assert observed['synthetic_admission_released'] and observed['middle_reaped'] and observed['middle_returncode']==0
    assert observed['leaf_alive_after_outer_return'] is False and observed['leaf_not_executing_at_end']
    assert observed['mechanism_verified'] and not observed['leaf_normal_exit_at_outer_return']
    assert [e['signal'] for e in value['outer_signals'] if e['signal']]==[15]
    assert [e['signal'] for e in value['inner_signals'] if e['signal']]==[15,9]
    assert 2<=value['elapsed']<10


def test_persistent_pipe_error_observes_child_reap_without_further_signals(rig):
    setup,state,events,_,child=rig;state['persistent_error']=True
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True)
    assert child.returncode==0 and state['cleanup_calls']==3
    assert [e for e in events if isinstance(e,tuple) and e[0]=='signal']==[('signal',15),('signal',0)]
    assert events.index('inner-cleanup-complete')<events.index('wait')


def test_delegated_exception_after_reap_has_no_destructive_signal(rig):
    setup,state,events,_,_=rig;state['already_reaped']=True
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True)
    assert [e for e in events if isinstance(e,tuple) and e[0]=='signal']==[('signal',0)]


def test_explicit_false_keeps_default_term_kill_and_reap_order(rig):
    setup,_,events,_,child=rig
    def wait(timeout):child.returncode=-9;events.append('wait');return -9
    child.wait=wait
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=False)
    assert events.index(('signal',15))<events.index(('signal',9))<events.index('wait')


def test_delegated_wait_emits_one_safe_retention_notice(rig,capsys):
    setup,_,_,_,_=rig
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True)
    streams=capsys.readouterr()
    assert not streams.out and streams.err.count('Setup delegated cleanup pending:')==1
    assert 'admission remains held' in streams.err


def test_closed_notice_stream_does_not_abort_delegated_cleanup(rig,monkeypatch):
    setup,state,events,_,child=rig
    class Closed:
        def write(self,*args):raise ValueError('synthetic closed stderr')
        def flush(self):raise ValueError('synthetic closed stderr')
    monkeypatch.setattr(setup.sys,'stderr',Closed())
    with pytest.raises(InterruptedError):setup.run(['synthetic'],source_app='unused',delegated_cleanup=True)
    assert child.returncode==0 and state['cleanup_calls']==3 and ('signal',9) not in events
