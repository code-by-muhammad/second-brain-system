"""Process ownership is exact and birth-bound; fixture targets only."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
import brain_settings as cfg
import brain_process as processes


@pytest.mark.skipif(os.name!='nt',reason='Retained native Windows handles.')
@pytest.mark.parametrize('filename,args,role',[
    ('brain_server.py',[],'server'),
    ('brain_index_bootstrap.py',['--reconcile'],'index_bootstrap'),
])
def test_real_launcher_pair_and_birth_mismatch_refuse_before_signal(brain,filename,args,role):
    from brain_windows_process import run
    app=cfg.ROOT/'app';app.mkdir()
    script=app/filename;marker=cfg.STATE/'process.json'
    script.write_text("import os,time,json\nfrom pathlib import Path\nPath("+repr(str(marker))+").write_text(json.dumps({'pid':os.getpid()}))\ntime.sleep(120)\n",encoding='utf-8')
    seen=[]
    def tick():
        if seen or not marker.exists():return
        pid=json.loads(marker.read_text())['pid']
        owned=processes.inspect(pid,required=True)
        try:
            assert owned.role==role and owned.kind=='interpreter'
            assert owned.parents[0].role==role and owned.parents[0].birth<owned.birth
            birth=owned.birth;owned.birth-=1
            with pytest.raises(processes.OwnershipError,match='identity changed'):owned.terminate()
            owned.birth=birth
            assert owned.alive()
            seen.append(owned.record());owned.terminate()
        finally:owned.close()
    result=run([sys.executable,'-I','-B',str(script),*args],capture_output=True,timeout=15,on_tick=tick,env=cfg.environment())
    assert seen and result.returncode!=0



@pytest.mark.parametrize('error,exit_state,exits',[(5,0,True),(5,258,False),(5,0xffffffff,False),(6,0,False)])
def test_windows_termination_requires_signaled_retained_handle(monkeypatch,error,exit_state,exits):
    handle=99
    def wait(candidate,timeout):
        assert candidate==handle
        assert 0<=timeout<=1000
        return exit_state if timeout else 258
    api=SimpleNamespace(WaitForSingleObject=wait,TerminateProcess=lambda *_:False)
    monkeypatch.setattr(processes,'_birth',lambda *_:100)
    monkeypatch.setattr(processes.ctypes,'get_last_error',lambda:error,raising=False)
    owned=processes.Process(77,api,handle,100,'server','python.exe',None,42,'interpreter')
    if exits:
        owned.terminate()
    else:
        with pytest.raises(processes.OwnershipError,match=f'Win32 error {error}'):
            owned.terminate()


@pytest.mark.skipif(os.name!='nt',reason='Windows identity API fake.')
@pytest.mark.parametrize('name,args',[
    ('brain_client.py',[]),('brain_hooks.py',[]),('brain_server.py',['--pilot']),('prefix-brain_server.py',[]),
    ('brain_server.py',['--reconcile']),('brain_index_bootstrap.py',['--reconcile','--extra']),
])
def test_adapters_pilot_and_substrings_never_owned(brain,monkeypatch,name,args):
    closed=[]
    api=SimpleNamespace(OpenProcess=lambda *a:99,WaitForSingleObject=lambda *a:258,CloseHandle=lambda value:closed.append(value))
    monkeypatch.setattr(processes,'_api',lambda:api)
    monkeypatch.setattr(processes,'_birth',lambda *a:100)
    process=SimpleNamespace(cmdline=lambda:[str(cfg.PYTHON),str(cfg.ROOT/'app'/name),*args],exe=lambda:str(cfg.PYTHON),ppid=lambda:42)
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:process)
    assert processes.inspect(77) is None
    with pytest.raises(processes.OwnershipError):processes.inspect(77,required=True)
    assert closed==[99,99]


def test_environment_drift_refused_before_service_work(brain,monkeypatch):
    device=dict(vault=str(cfg.VAULT),python=str(cfg.PYTHON),port=cfg.PORT)
    monkeypatch.setattr(cfg,'DEVICE',device)
    for key,value in [('BRAIN_SYSTEM_ROOT',cfg.ROOT),('BRAIN_STATE',cfg.STATE),('BRAIN_VAULT',cfg.VAULT),('BRAIN_PORT',cfg.PORT)]:monkeypatch.setenv(key,str(value))
    processes.validate_environment()
    monkeypatch.setenv('BRAIN_STATE',str(cfg.ROOT/'other-state'))
    with pytest.raises(processes.OwnershipError,match='Ambient BRAIN'):processes.validate_environment()


def test_foreign_and_multiple_listeners_refused_without_process_adoption(brain,monkeypatch):
    row=lambda pid,ip:SimpleNamespace(pid=pid,laddr=SimpleNamespace(ip=ip,port=cfg.PORT))
    for rows in ([row(1,'0.0.0.0')],[row(1,'127.0.0.1'),row(2,'127.0.0.1')],[row(None,'127.0.0.1')]):
        monkeypatch.setattr(processes,'_listeners',lambda:rows)
        with pytest.raises(processes.OwnershipError,match='unverified'):processes.listener_identity()


@pytest.mark.parametrize('orphan,changed',[(False,False),(True,False),(False,True)])
def test_listener_requires_live_recorded_supervisor_and_same_socket(brain,monkeypatch,orphan,changed):
    # This fake models Windows' separate launcher/base-interpreter processes.
    monkeypatch.setattr(processes.sys,'platform','win32')
    registry=cfg.ROOT/'state/service-pids.json';registry.write_text(json.dumps({'supervisor':70}))
    closed=[]
    launcher=SimpleNamespace(parent_pid=69 if orphan else 70,birth=200)
    server=SimpleNamespace(pid=80,role='server',birth=300,kind='interpreter',parents=[launcher],record=lambda:{'pid':80,'birth':300},close=lambda:closed.append('server'))
    supervisor=SimpleNamespace(role='supervisor',pid=70,birth=100,close=lambda:closed.append('supervisor'))
    monkeypatch.setattr(processes,'inspect',lambda pid,**kwargs:server if pid==80 else supervisor)
    count=[0]
    def listeners():
        count[0]+=1
        return [SimpleNamespace(pid=81 if changed and count[0]>1 else 80,laddr=SimpleNamespace(ip='127.0.0.1',port=cfg.PORT))]
    monkeypatch.setattr(processes,'_listeners',listeners)
    if orphan or changed:
        with pytest.raises(processes.OwnershipError):processes.listener_identity()
    else:
        assert processes.listener_identity()['supervisor_pid']==70
    assert 'server' in closed


# ---- the index worker's command line ----------------------------------------------------------------------------

def _worker_line(*arguments,python=None):
    return [str(python or cfg.PYTHON),'-I','-B',str(cfg.ROOT/'app'/processes.INDEX_WORKER),'reindex','-p','brain',*arguments]


def test_index_worker_form_accepts_exactly_three_forms_and_is_total(brain):
    expected=cfg.ROOT/'app'/processes.INDEX_WORKER
    forms=[_worker_line(),_worker_line('--full'),_worker_line('--full','--search')]
    for line in forms:
        assert processes.index_worker_form(line,expected) is True
        # Any spelling of the interpreter: the process image is checked elsewhere, not this text.
        assert processes.index_worker_form(['python3',*line[1:]],expected) is True
        # Every proper prefix is refused, unless it is one of the two shorter forms itself.
        for cut in range(len(line)):assert processes.index_worker_form(line[:cut],expected) is (line[:cut] in forms)
    for line in ([],['-I'],['-I','-B'],[str(cfg.PYTHON)],[str(cfg.PYTHON),'-I','-B'],['-I','-B',str(expected),'reindex','-p','brain']):
        assert processes.index_worker_form(line,expected) is False
    script=str(expected)
    near=[
        _worker_line('--search'),_worker_line('--search','--full'),_worker_line('--full','--search','--embeddings'),_worker_line('--full','--full'),
        [str(cfg.PYTHON),'-B','-I',script,'reindex','-p','brain'],[str(cfg.PYTHON),'-I',script,'reindex','-p','brain'],
        [str(cfg.PYTHON),script,'reindex','-p','brain'],[str(cfg.PYTHON),'-I','-B','-B',script,'reindex','-p','brain'],
        [str(cfg.PYTHON),'-I','-B',script,'reindex','-p','main'],[str(cfg.PYTHON),'-I','-B',script,'sync','-p','brain'],
        [str(cfg.PYTHON),'-I','-B',os.path.join('app',processes.INDEX_WORKER),'reindex','-p','brain'],
        [str(cfg.PYTHON),'-I','-B',str(cfg.ROOT/'other'/processes.INDEX_WORKER),'reindex','-p','brain'],
        [str(cfg.PYTHON),'-I','-B',str(cfg.ROOT/'app'/'..'/'app'/processes.INDEX_WORKER),'reindex','-p','brain'],
        [str(cfg.PYTHON),'-I','-B',script+'x','reindex','-p','brain'],
    ]
    for line in near:assert processes.index_worker_form(line,expected) is False,line
    assert processes.INDEX_WORKER not in processes.SCRIPTS.values()


GATED_WORKER='import sys,time\nsys.stdin.buffer.readline()\ntime.sleep(60)\n'


@pytest.fixture
def waiting_worker(brain):
    """A real child with the worker's exact command line, held at its start gate; it never runs anything."""
    import subprocess
    app=cfg.ROOT/'app';app.mkdir()
    (app/processes.INDEX_WORKER).write_text(GATED_WORKER,encoding='utf-8')
    children=[]
    def start(line):
        child=subprocess.Popen(line,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);children.append(child);return child
    yield start
    for child in children:
        child.kill();child.wait(timeout=10)


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Real process discovery; Windows and macOS have native jobs.')
@pytest.mark.parametrize('extra',[[],['--full'],['--full','--search']])
def test_real_worker_command_line_is_discovered_as_an_index_worker(waiting_worker,extra):
    child=waiting_worker(_worker_line(*extra))
    owned=processes.inspect(child.pid,required=True)
    try:
        assert owned.role=='index_worker' and owned.kind=='direct'
        assert owned.script==os.path.normcase(os.path.abspath(str(cfg.ROOT/'app'/processes.INDEX_WORKER)))
    finally:owned.close()
    found=processes.roots()
    try:assert [(process.pid,process.role) for process in found if process.pid==child.pid]==[(child.pid,'index_worker')]
    finally:
        for process in found:process.close()


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Real process discovery; Windows and macOS have native jobs.')
def test_real_near_miss_command_lines_get_no_role(waiting_worker,tmp_path):
    script=cfg.ROOT/'app'/processes.INDEX_WORKER
    alias=tmp_path/'alias.py';alias.symlink_to(script)
    elsewhere=tmp_path/processes.INDEX_WORKER;elsewhere.write_text(GATED_WORKER,encoding='utf-8')
    lines=[[str(cfg.PYTHON),'-I','-B',str(alias),'reindex','-p','brain'],
           [str(cfg.PYTHON),'-I','-B',str(elsewhere),'reindex','-p','brain'],
           [str(cfg.PYTHON),'-B','-I',str(script),'reindex','-p','brain'],
           [str(cfg.PYTHON),'-I','-B',str(script),'reindex','-p','brain','--search'],
           [str(cfg.PYTHON),'-I','-B',str(script)]]
    for line in lines:
        child=waiting_worker(line)
        assert processes.inspect(child.pid) is None,line
        with pytest.raises(processes.OwnershipError):processes.inspect(child.pid,required=True)
    found=processes.roots()
    try:assert not [process for process in found if process.role=='index_worker']
    finally:
        for process in found:process.close()


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Real process discovery; Windows and macOS have native jobs.')
def test_worker_under_an_interpreter_outside_this_runtime_is_refused(waiting_worker,monkeypatch,tmp_path):
    child=waiting_worker(_worker_line())
    monkeypatch.setattr(processes,'_launchers',lambda:{str(tmp_path/'other-python'):str(tmp_path/'other-python')})
    with pytest.raises(processes.OwnershipError,match='unrecognized interpreter'):processes.inspect(child.pid)


@pytest.mark.skipif(os.name=='nt',reason='Symbolic links need a privilege on Windows.')
@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Real process discovery; Windows and macOS have native jobs.')
def test_linked_worker_script_cannot_establish_ownership(waiting_worker,tmp_path):
    script=cfg.ROOT/'app'/processes.INDEX_WORKER
    child=waiting_worker(_worker_line())
    real=tmp_path/'real.py';real.write_text(GATED_WORKER,encoding='utf-8');script.unlink();script.symlink_to(real)
    with pytest.raises(processes.OwnershipError,match='Linked runtime script'):processes.inspect(child.pid)


@pytest.mark.skipif(os.name!='nt',reason='Windows launcher/base pair.')
def test_windows_launcher_pair_is_accepted_for_the_worker_script(brain):
    """The venv launcher and its base interpreter both carry the worker's command line; the pair is one worker."""
    from brain_windows_process import run
    app=cfg.ROOT/'app';app.mkdir()
    script=app/processes.INDEX_WORKER;marker=cfg.STATE/'process.json'
    script.write_text("import os,sys,time,json\nfrom pathlib import Path\nline=sys.stdin.buffer.readline()\n"
                      "Path("+repr(str(marker))+").write_text(json.dumps({'pid':os.getpid(),'line':line.decode()}))\ntime.sleep(120)\n",encoding='utf-8')
    seen=[]
    def tick():
        if seen or not marker.exists():return
        value=json.loads(marker.read_text())
        owned=processes.inspect(value['pid'],required=True)
        try:
            assert owned.role=='index_worker' and owned.kind=='interpreter'
            assert owned.parents[0].role=='index_worker' and owned.parents[0].kind=='launcher'
            seen.append(value['line']);owned.terminate()
        finally:owned.close()
    result=run([sys.executable,'-I','-B',str(script),'reindex','-p','brain'],input=b'start\n',capture_output=True,timeout=30,on_tick=tick,env=cfg.environment())
    # The start line reached the base interpreter through the launcher.
    assert seen==['start\n'] and result.returncode!=0
