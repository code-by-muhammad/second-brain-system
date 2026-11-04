"""Real helper inspection over stateful fake D-Bus/filesystem boundaries.

No manager is contacted. Native lifecycle acceptance remains a separate gate.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import time

import pytest
import brain_linux_registration as native
import brain_process as processes
from test_linux_loaded_contract import known,UNIT,PATH,ARGV,PREFIX

BOOT='12345678-1234-1234-1234-123456789abc'
OBJECT='/org/freedesktop/systemd1/unit/known'


def stateful(monkeypatch,**changes):
    unit=object.__new__(native.LinuxRegistration)
    unit.unit=UNIT;unit.path=PATH;unit.directory=PATH.parent;unit.home=Path.cwd();unit.prefix=PREFIX
    unit.group=PREFIX+'/app.slice/'+UNIT;unit.peer=':1.0';unit.manager_version='255.4-1ubuntu8.18'
    unit.known={b'old':ARGV,b'new':(str(Path.cwd()/'fixture/new-python'),ARGV[1])};unit.installable=b'new'
    state={'payload':b'old','loaded_payload':b'old','loaded':True,'enabled':True,
           'loaded_enabled':True,'running':False,'dirty':False,'inode':1,**changes}
    actions=[]
    unit._manager=lambda:('root','peer',1001)
    unit._disk_boundary=lambda:None
    unit._cgroup_empty=lambda group:not state['running']
    unit._links=lambda enabled:None
    unit._read_unit=lambda:(state['payload'],(1,state['inode'],len(state['payload']),1)) if state['payload'] is not None else None
    def getall(peer,obj,interface):
        assert obj==OBJECT
        u,s=known();argv=unit.known[state['loaded_payload']]
        u['UnitFileState']['data']='enabled' if state['loaded_enabled'] else 'disabled'
        u['NeedDaemonReload']['data']=state['dirty']
        u['ActiveState']['data']='active' if state['running'] else 'inactive'
        u['SubState']['data']='running' if state['running'] else 'dead'
        s['ExecStart']['data'][0][:3]=[argv[0],list(argv),False]
        s['ExecStart']['data'][0][7]=1234 if state['running'] else 0
        s['MainPID']['data']=1234 if state['running'] else 0
        s['ControlGroup']['data']=unit.group if state['running'] else ''
        return deepcopy(u if interface=='Unit' else s)
    unit._getall=getall
    def method(name,*args):
        if name=='ListUnits':
            rows=[[UNIT,'known','loaded','active' if state['running'] else 'inactive',
                   'running' if state['running'] else 'dead','',OBJECT,0,'','/']] if state['loaded'] else []
            return {'type':'a(ssssssouso)','data':[rows]}
        if name=='ListUnitFilesByPatterns':
            return {'type':'a(ss)','data':[[[str(PATH),'enabled' if state['enabled'] else 'disabled']]] if state['payload'] is not None else [[]]}
        actions.append(name)
        if name in ('EnableUnitFiles','DisableUnitFiles'):
            state['enabled']=name=='EnableUnitFiles';state['dirty']=True
            return {'type':'ba(sss)' if state['enabled'] else 'a(sss)',
                    'data':[True,[]] if state['enabled'] else [[]]}
        if name=='Reload':
            state.update(dirty=False,loaded_payload=state['payload'],loaded_enabled=state['enabled'])
            return {'type':'','data':[]}
        if name=='StopUnit':state['running']=False
        if name=='LoadUnit':state.update(loaded=True,loaded_payload=state['payload'],loaded_enabled=state['enabled'])
        return {'type':'o','data':[OBJECT if name=='LoadUnit' else '/org/freedesktop/systemd1/job/1']}
    unit._method=method
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:{'pid':pid,'start_ticks':100,'boot_id':BOOT})
    def executor(*args,**kwargs):raise processes.OwnershipError('fixture executor before script metadata')
    monkeypatch.setattr(processes,'inspect',executor)
    return unit,state,actions


def test_same_hold_suspend_retry_clears_stale_cache_without_losing_first_prior(monkeypatch):
    unit,state,actions=stateful(monkeypatch,running=True,enabled=False,loaded_enabled=True,dirty=True)
    prior={'enabled':True,'definition_sha256':hashlib.sha256(b'old').hexdigest()}
    result=unit.suspend_activation(prior)
    assert result==prior and state['enabled'] is False and state['dirty'] is False
    assert actions==['Reload']


def test_verified_unit_can_stop_without_waiting_for_executor_role_metadata(monkeypatch):
    unit,state,actions=stateful(monkeypatch,running=True,enabled=False,loaded_enabled=False)
    result=unit.native_stop()
    assert result['native_stopped'] and result['native_end_sent'] and result['main_pid']==0
    assert result['pre_stop_main_lifetime']=={'pid':1234,'start_ticks':100,'boot_id':BOOT}
    assert actions==['StopUnit']


def test_stop_poll_retries_a_mainpid_exit_race_without_losing_pre_stop_receipt(monkeypatch):
    unit,state,actions=stateful(monkeypatch,running=True,enabled=False,loaded_enabled=False)
    method=unit._method;life=processes._linux_lifetime
    def stop_deactivating(name,*args):
        result=method(name,*args)
        if name=='StopUnit':state['running']=True
        return result
    def exited(pid):
        if 'StopUnit' in actions and state['running']:
            state['running']=False
            raise processes.OwnershipError('fixture PID exited after manager property read')
        return life(pid)
    unit._method=stop_deactivating
    monkeypatch.setattr(processes,'_linux_lifetime',exited)
    result=unit.native_stop()
    assert result['native_stopped'] and result['native_end_sent'] and result['main_pid']==0
    assert result['pre_stop_main_lifetime']=={'pid':1234,'start_ticks':100,'boot_id':BOOT}
    assert actions==['StopUnit']


def test_changed_known_live_command_is_stopped_before_reload(monkeypatch):
    unit,state,actions=stateful(monkeypatch,running=True,payload=b'new',dirty=True)
    prior=unit.inspect(allow_reload=True,prove_main=False)
    unit.suspend_activation(prior)
    assert actions==['DisableUnitFiles'] and state['dirty']
    result=unit.native_stop()
    assert actions==['DisableUnitFiles','StopUnit','Reload']
    assert result['native_stopped'] and result['needs_reload'] is False


def test_post_replace_failure_requires_explicit_original_intent_on_retry(monkeypatch):
    unit,state,actions=stateful(monkeypatch)
    def committed(payload,before):
        state.update(payload=payload,inode=2,dirty=True);unit._replace_committed=True
        raise OSError('fixture directory fsync failure after replace')
    unit._replace=committed
    with pytest.raises(native.RegistrationError) as caught:unit.install(b'new',enabled=True)
    assert caught.value.bytes_replaced and not caught.value.prior_restored and not state['enabled']
    before=list(actions)
    with pytest.raises(native.RegistrationError,match='explicit durable'):unit.install(b'new')
    assert actions==before
    result=unit.install(b'new',enabled=True)
    assert result['enabled'] is True and state['dirty'] is False


def test_expired_install_budget_gets_a_separate_bounded_cleanup_attempt(monkeypatch):
    unit,state,actions=stateful(monkeypatch)
    def refused(payload,before):
        unit._deadline=time.monotonic()-1
        raise native.CommandTimeout('fixture pre-write operation budget expired')
    unit._replace=refused
    method=unit._method
    def checked(name,*args):
        assert unit._deadline>time.monotonic()
        return method(name,*args)
    unit._method=checked
    with pytest.raises(native.CommandTimeout) as caught:unit.install(b'new',enabled=True)
    assert caught.value.prior_restored is True and caught.value.restore_error is None
    assert state['enabled'] is True and not caught.value.bytes_replaced


@pytest.mark.parametrize('pending',['higher-fragment','generic-dropin','prefix-dropin'])
def test_reload_refuses_pending_unowned_disk_configuration(tmp_path,pending):
    unit=object.__new__(native.LinuxRegistration);unit.unit='second-brain.service'
    high=tmp_path/'high';own=tmp_path/'own';high.mkdir();own.mkdir()
    unit.directory=own;unit.unit_paths=(high,own)
    if pending=='higher-fragment':(high/unit.unit).write_text('foreign')
    else:
        directory=high/('service.d' if pending=='generic-dropin' else 'second-.service.d')
        directory.mkdir();(directory/'10-foreign.conf').write_text('foreign')
    with pytest.raises(native.RegistrationError):unit._disk_boundary()
