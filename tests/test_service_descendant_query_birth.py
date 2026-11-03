"""Windows lifetime exclusion must work without terminate rights on stale PPIDs."""
import os
import json
import sys
from collections import Counter
from types import SimpleNamespace

import psutil
import pytest
import brain_process as processes
import brain_service as service
import brain_settings as cfg

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows handle access contract')


@pytest.fixture
def scan(monkeypatch):
    parent=SimpleNamespace(pid=10,birth=200,alive=lambda:True)
    row=SimpleNamespace(pid=11,info={'ppid':10})
    state=SimpleNamespace(parent=parent,rows=[row],calls=[],opened=[],closed=[],error=5,
                          query=101,broad=201,query_birth=300,broad_birth=300,wait=258,
                          ppid=lambda:10,exe=lambda:__file__,births={},metadata={})
    def open_process(mask,inherit,pid):
        assert inherit is False and pid in (11,12)
        state.calls.append((mask,pid))
        if mask==0x1000:handle=state.query+(pid-11) if state.query else None
        else:
            assert mask==0x101001
            assert state.query+(pid-11) not in state.closed
            handle=state.broad+(pid-11) if state.broad else None
        if handle:state.opened.append(handle)
        return handle
    api=SimpleNamespace(OpenProcess=open_process,
        WaitForSingleObject=lambda *_:state.wait,CloseHandle=lambda h:state.closed.append(h))
    state.api=api
    def birth(_api,handle):
        value=state.births.get(handle,state.query_birth if handle in (101,102) else state.broad_birth)
        if isinstance(value,Exception):raise value
        return value
    monkeypatch.setattr(processes,'_api',lambda:api)
    monkeypatch.setattr(processes,'_birth',birth)
    monkeypatch.setattr(processes.psutil,'process_iter',lambda *_:state.rows)
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:state.metadata.get(pid) or SimpleNamespace(ppid=state.ppid,exe=state.exe))
    monkeypatch.setattr(processes.ctypes,'get_last_error',lambda:state.error)
    yield state
    assert Counter(state.opened)==Counter(state.closed)


@pytest.mark.parametrize('birth',[100,199])
def test_older_protected_candidate_excluded_without_termination_access(scan,birth):
    scan.query_birth=birth;scan.broad=None
    assert processes.descendants([scan.parent])==[]
    assert scan.calls==[(0x1000,11)] and scan.closed==[101]


@pytest.mark.parametrize('birth',[200,300])
def test_equal_or_younger_candidate_retains_broad_handle(scan,birth):
    scan.query_birth=scan.broad_birth=birth
    found=processes.descendants([scan.parent])
    assert len(found)==1 and found[0].pid==11 and found[0].birth==birth
    assert scan.calls==[(0x1000,11),(0x101001,11)] and scan.closed==[101]
    found[0].close()
    assert scan.closed==[101,201]


def test_denied_birth_query_refuses_without_broad_open(scan):
    scan.query=None
    with pytest.raises(processes.OwnershipError,match='birth cannot be inspected'):
        processes.descendants([scan.parent])
    assert scan.calls==[(0x1000,11)] and not scan.closed


def test_disappeared_before_query_is_absent(scan):
    scan.query=None;scan.error=87
    assert processes.descendants([scan.parent])==[] and not scan.closed


@pytest.mark.parametrize('error',[5,87])
@pytest.mark.parametrize('birth',[200,300])
def test_broad_open_denial_while_query_pins_object_refuses(scan,error,birth):
    scan.query_birth=scan.broad_birth=birth
    scan.broad=None;scan.error=error
    with pytest.raises(processes.OwnershipError,match='owned descendant cannot be inspected'):
        processes.descendants([scan.parent])
    assert scan.closed==[101]


def test_different_birth_between_retained_handles_refuses(scan):
    scan.broad_birth=400
    with pytest.raises(processes.OwnershipError,match='identity changed'):
        processes.descendants([scan.parent])
    assert scan.closed==[201,101]


@pytest.mark.parametrize('field,closed',[('query_birth',[101]),('broad_birth',[201,101])])
def test_birth_read_error_closes_every_open_handle(scan,field,closed):
    setattr(scan,field,processes.OwnershipError('birth unavailable'))
    with pytest.raises(processes.OwnershipError,match='birth unavailable'):
        processes.descendants([scan.parent])
    assert scan.closed==closed


@pytest.mark.parametrize('wait',[0,0xffffffff])
def test_exit_or_unknown_wait_does_not_retain_candidate(scan,wait):
    scan.wait=wait
    if wait==0:assert processes.descendants([scan.parent])==[]
    else:
        with pytest.raises(processes.OwnershipError,match='exit state'):
            processes.descendants([scan.parent])
    assert scan.closed==[201,101]


@pytest.mark.parametrize('change_at',[1,2])
def test_parent_id_change_during_metadata_checks_refuses(scan,change_at):
    calls=[]
    def ppid():
        calls.append(True)
        return 99 if len(calls)==change_at else 10
    scan.ppid=ppid
    with pytest.raises(processes.OwnershipError,match='ancestry changed'):
        processes.descendants([scan.parent])
    assert scan.closed==[201,101]


@pytest.mark.parametrize('dies_at',[2,3])
def test_parent_dies_during_metadata_checks_refuses(scan,dies_at):
    calls=[]
    def alive():
        calls.append(True)
        return len(calls)!=dies_at
    scan.parent.alive=alive
    with pytest.raises(processes.OwnershipError,match='ancestry changed'):
        processes.descendants([scan.parent])
    assert scan.closed==[201,101]


@pytest.mark.parametrize('error',[psutil.NoSuchProcess(11),psutil.AccessDenied(11)])
def test_metadata_failure_closes_handles(scan,error):
    def ppid():raise error
    scan.ppid=ppid
    if isinstance(error,psutil.NoSuchProcess):assert processes.descendants([scan.parent])==[]
    else:
        with pytest.raises(processes.OwnershipError,match='metadata could not be verified'):
            processes.descendants([scan.parent])
    assert scan.closed==[201,101]


def test_constructor_failure_closes_handles(scan,monkeypatch):
    def fail(*_):raise RuntimeError('constructor failure')
    monkeypatch.setattr(processes,'Process',fail)
    with pytest.raises(RuntimeError,match='constructor failure'):
        processes.descendants([scan.parent])
    assert scan.closed==[201,101]


def test_previously_retained_children_closed_when_later_candidate_refuses(scan,monkeypatch):
    scan.rows.append(SimpleNamespace(pid=12,info={'ppid':10}))
    scan.exe=lambda:__file__
    initial=processes.psutil.Process
    def metadata(pid):
        if pid==12:raise psutil.AccessDenied(pid)
        return initial(pid)
    monkeypatch.setattr(processes.psutil,'Process',metadata)
    with pytest.raises(processes.OwnershipError,match='metadata could not be verified'):
        processes.descendants([scan.parent])
    assert scan.closed==[101,202,102,201]


def test_preexisting_exited_parent_skip_behavior_is_unchanged(scan):
    scan.parent.alive=lambda:False
    assert processes.descendants([scan.parent])==[] and not scan.calls


@pytest.mark.parametrize('grandchild_birth',[250,350])
def test_descendant_birth_comparison_uses_immediate_parent(scan,grandchild_birth):
    scan.rows.append(SimpleNamespace(pid=12,info={'ppid':11}))
    scan.metadata[12]=SimpleNamespace(ppid=lambda:11,exe=lambda:__file__)
    scan.births.update({102:grandchild_birth,202:grandchild_birth})
    found=processes.descendants([scan.parent])
    try:
        assert [p.pid for p in found]==([11] if grandchild_birth==250 else [11,12])
        assert ((0x101001,12) in scan.calls)==(grandchild_birth==350)
    finally:
        for process in found:process.close()


def test_posix_branch_does_not_use_windows_handles(scan,monkeypatch):
    monkeypatch.setattr(processes,'os',SimpleNamespace(name='posix',path=os.path))
    monkeypatch.setattr(processes,'_api',lambda:pytest.fail('Windows API on POSIX branch'))
    scan.parent.birth=116444736000000000+2000000000
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,ppid=lambda:10))
    assert processes.descendants([scan.parent])==[] and not scan.calls


@pytest.mark.parametrize('query_denied',[False,True])
def test_stop_protects_unrelated_process_and_restores_task_on_uncertainty(brain,scan,monkeypatch,query_denied):
    monkeypatch.setattr(cfg,'DEVICE',dict(vault=str(cfg.VAULT),python=sys.executable,
                                        port=cfg.PORT,version=cfg.VERSION,device_id='fixture'))
    for name,value in [('BRAIN_SYSTEM_ROOT',cfg.ROOT),('BRAIN_STATE',cfg.STATE),
                       ('BRAIN_VAULT',cfg.VAULT),('BRAIN_PORT',cfg.PORT)]:
        monkeypatch.setenv(name,str(value))
    dead=set();terminated=[];commands=[]
    root_handle=301
    scan.api.WaitForSingleObject=lambda handle,_:0 if handle in dead else 258
    def terminate(handle,_):
        terminated.append(handle);dead.add(handle);return True
    scan.api.TerminateProcess=terminate
    scan.births[root_handle]=200
    scan.opened.append(root_handle)
    scan.parent=processes.Process(10,scan.api,root_handle,200,'server',__file__,
                                  str(cfg.ROOT/'app/brain_server.py'),1,'direct')
    scan.query_birth=100
    scan.rows.append(SimpleNamespace(pid=12,info={'ppid':10}))
    scan.births.update({102:300,202:300})
    if query_denied:scan.query=None
    monkeypatch.setattr(processes,'roots',lambda:[scan.parent] if scan.parent.alive() else [])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(processes,'filetime_now',lambda:400)
    monkeypatch.setattr(service,'_windows_task',lambda:{'enabled':True})
    monkeypatch.setattr(service,'_tool',lambda _:str(cfg.ROOT/'synthetic-schtasks.exe'))
    monkeypatch.setattr(service.subprocess,'run',lambda command,**_:(commands.append(command) or SimpleNamespace(returncode=0)))
    if query_denied:
        with pytest.raises(processes.OwnershipError,match='process identity'):
            service.stop(wait_seconds=1)
        receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
        assert receipt['prior_enabled_restored'] and not receipt['end_attempted']
        assert [command[-1] for command in commands]==['/Disable','/Enable']
        assert not terminated and root_handle not in dead
    else:
        receipt=service.stop(wait_seconds=1)
        assert receipt['status']=='stopped' and receipt['task_disabled']
        assert [next(arg for arg in command if arg in ('/Disable','/End','/Enable'))
                for command in commands]==['/Disable','/End']
        assert terminated==[root_handle,202] and dead=={root_handle,202}
        assert (0x101001,11) not in scan.calls
        assert set(scan.calls)=={(0x1000,11),(0x1000,12),(0x101001,12)}
        assert scan.calls.count((0x101001,12))==1
    assert root_handle in scan.closed
