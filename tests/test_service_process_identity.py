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
def test_real_launcher_pair_and_birth_mismatch_refuse_before_signal(brain):
    from brain_windows_process import run
    app=cfg.ROOT/'app';app.mkdir()
    script=app/'brain_server.py';marker=cfg.STATE/'process.json'
    script.write_text("import os,time,json\nfrom pathlib import Path\nPath("+repr(str(marker))+").write_text(json.dumps({'pid':os.getpid()}))\ntime.sleep(120)\n",encoding='utf-8')
    seen=[]
    def tick():
        if seen or not marker.exists():return
        pid=json.loads(marker.read_text())['pid']
        owned=processes.inspect(pid,required=True)
        try:
            assert owned.role=='server' and owned.kind=='interpreter'
            assert owned.parents[0].role=='server' and owned.parents[0].birth<owned.birth
            birth=owned.birth;owned.birth-=1
            with pytest.raises(processes.OwnershipError,match='identity changed'):owned.terminate()
            owned.birth=birth
            assert owned.alive()
            seen.append(owned.record());owned.terminate()
        finally:owned.close()
    result=run([sys.executable,'-I','-B',str(script)],capture_output=True,timeout=15,on_tick=tick,env=cfg.environment())
    assert seen and result.returncode!=0


@pytest.mark.skipif(os.name!='nt',reason='Windows identity API fake.')
@pytest.mark.parametrize('name,args',[('brain_client.py',[]),('brain_hooks.py',[]),('brain_server.py',['--pilot']),('prefix-brain_server.py',[])])
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
