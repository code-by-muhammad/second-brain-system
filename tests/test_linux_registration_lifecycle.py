"""Systemd boundary controls with fake IPC; not native lifecycle acceptance."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy

import pytest
import brain_linux_registration as native
import brain_service as service
import brain_settings as cfg
import brain_process as processes
from test_linux_loaded_contract import known,UNIT,PATH,ARGV,PREFIX


def helper(**changes):
    result=object.__new__(native.LinuxRegistration)
    result.unit='second-brain-ci-'+'a'*32+'.service'
    result.group='/user.slice/user-1001.slice/user@1001.service/app.slice/'+result.unit
    result.installable=b'current';calls=[]
    state={'file_present':True,'loaded':True,'enabled':False,'running':False,'main_pid':0,
        'control_pid':0,'active_state':'inactive','job':[0,'/'],'timeout_stop_usec':90_000_000,
        'definition_sha256':hashlib.sha256(b'current').hexdigest(),'needs_reload':False,'quiescent':True,**changes}
    result.inspect=lambda **kwargs:dict(state)
    result._inspect=lambda **kwargs:dict(state)
    result._cgroup_empty=lambda group:True
    result._disk_boundary=lambda:None
    def method(name,*args):
        calls.append((name,args))
        if name=='EnableUnitFiles':state['enabled']=True;return {'type':'ba(sss)','data':[True,[]]}
        if name=='DisableUnitFiles':state['enabled']=False;return {'type':'a(sss)','data':[[]]}
        if name=='Reload':return {'type':'','data':[]}
        if name=='StartUnit':state.update(running=True,main_pid=333,active_state='active')
        return {'type':'o','data':['/org/freedesktop/systemd1/job/1']}
    result._method=method
    return result,state,calls


def test_current_renderer_matches_shared_source_and_retained_argv(brain):
    payload=service.definition('linux').encode()
    values=native.definitions('second-brain.service',cfg.ROOT,[cfg.PYTHON])
    assert values[payload]==(str(cfg.PYTHON),str(cfg.ROOT/'app/brain_supervisor.py'))


def test_suspend_and_restore_activation_preserve_prior_enabled_state():
    unit,state,calls=helper(enabled=True)
    prior=unit.suspend_activation()
    assert prior['enabled'] is True and state['enabled'] is False
    assert unit.restore_activation(prior)['enabled'] is True
    assert [name for name,args in calls]==['DisableUnitFiles','Reload','EnableUnitFiles','Reload']


def test_absent_activation_intent_is_not_invented():
    unit,state,calls=helper(file_present=False,loaded=False,enabled=None,definition_sha256=None)
    prior=unit.suspend_activation()
    assert unit.restore_activation(prior)['enabled'] is None and calls==[]


def test_changed_definition_prevents_activation_restore():
    unit,state,calls=helper()
    with pytest.raises(native.RegistrationError,match='definition changed'):
        unit.restore_activation({'definition_sha256':'other','enabled':True})
    assert calls==[]


def test_disabled_loaded_inactive_unit_is_a_valid_stop_receipt():
    unit,state,calls=helper()
    result=unit.native_stop()
    assert result['loaded'] is True and result['native_stopped'] is True
    assert result['native_end_exit']==0 and not result['native_end_timed_out']
    assert [name for name,args in calls]==['StopUnit']


def test_stop_refuses_enabled_activation_before_end():
    unit,state,calls=helper(enabled=True)
    with pytest.raises(native.RegistrationError,match='Disable'):unit.native_stop()
    assert calls==[]


def test_native_timeout_is_reported_only_after_verified_empty_state():
    unit,state,calls=helper()
    def timeout(*args):raise native.CommandTimeout('bounded fixture timeout')
    unit._method=timeout
    result=unit.native_stop()
    assert result['native_stopped'] and result['native_end_timed_out'] and result['native_end_exit'] is None


def test_stop_never_issues_receipt_for_populated_cgroup(monkeypatch):
    unit,state,calls=helper();unit._cgroup_empty=lambda group:False
    values=iter([0,60,120])
    monkeypatch.setattr(native,'time',SimpleNamespace(monotonic=lambda:next(values),sleep=lambda seconds:None))
    with pytest.raises(native.RegistrationError,match='quiescence'):unit.native_stop()


def test_long_manager_timeout_refuses_before_end():
    unit,state,calls=helper(running=True,active_state='active',timeout_stop_usec=130_000_000)
    with pytest.raises(native.RegistrationError,match='shorter'):unit.native_stop()
    assert calls==[]


def test_start_respects_disabled_state_unless_explicit():
    unit,state,calls=helper(loaded=False)
    with pytest.raises(native.RegistrationError,match='disabled'):unit.start(explicit=False)
    assert calls==[]
    def load():state['loaded']=True;return dict(state)
    unit._load=load
    assert unit.start(explicit=True)['main_pid']==333
    assert [name for name,args in calls]==['EnableUnitFiles','Reload','StartUnit']


def test_install_unchanged_bytes_preserves_disabled_state_without_rewrite(monkeypatch):
    unit,state,calls=helper()
    unit._read_unit=lambda:(b'current',(1,2,3,4))
    monkeypatch.setattr(native.os,'replace',lambda *args:pytest.fail('identical definition rewritten'))
    assert unit.install(b'current',enabled=False)['enabled'] is False and calls==[]


def test_identical_enabled_install_does_not_churn_activation_links():
    unit,state,calls=helper(enabled=True)
    unit._read_unit=lambda:(b'current',(1,2,3,4))
    assert unit.install(b'current',enabled=True)['enabled'] is True and calls==[]


def test_foreign_install_payload_refuses_before_manager_queries():
    unit,state,calls=helper()
    unit.inspect=lambda:pytest.fail('foreign payload reached manager')
    with pytest.raises(native.RegistrationError,match='current exact'):unit.install(b'foreign')


def test_native_transport_pins_address_and_denies_automatic_activation(monkeypatch):
    unit,_,_=helper();unit.tool='/usr/bin/busctl';unit.runtime=Path('/run/user/1001')
    unit._preconnect=lambda system:Path('/run/dbus/system_bus_socket') if system else unit.runtime/'bus'
    seen=[]
    def run(command,**kwargs):
        seen.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'s','data':['known']}).encode())
    monkeypatch.setattr(native.subprocess,'run',run)
    assert unit._call(':1.2','/known','known.interface','Known',system=True)['data']==['known']
    command,options=seen[0]
    assert '--address=unix:path='+str(Path('/run/dbus/system_bus_socket')) in command
    assert '--auto-start=no' in command and '--allow-interactive-authorization=no' in command
    assert options['timeout']<=20 and options['stdin']==native.subprocess.DEVNULL


@pytest.mark.parametrize('failure',['timeout','oserror','malformed-json'])
def test_native_transport_errors_do_not_retain_private_payload(monkeypatch,failure):
    unit,_,_=helper();unit.tool='fixture-busctl';unit.runtime=Path('/fixture-runtime')
    unit._preconnect=lambda system:unit.runtime/'bus'
    private='private-fixture-token'
    def run(command,**kwargs):
        if failure=='timeout':raise native.subprocess.TimeoutExpired(command,1,output=private)
        if failure=='oserror':raise OSError(private)
        return SimpleNamespace(returncode=0,stdout=private.encode())
    monkeypatch.setattr(native.subprocess,'run',run)
    with pytest.raises(native.RegistrationError) as caught:unit._call(':1.2','/known','known.interface','Known')
    assert private not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_activation_change_reloads_before_strict_post_inspection():
    unit,state,calls=helper(enabled=True)
    method=unit._method
    def inspect(**kwargs):
        if state['needs_reload'] and not kwargs.get('allow_reload'):raise native.ReloadRequired('fixture dirty manager cache')
        return dict(state)
    def invoke(name,*args):
        response=method(name,*args)
        if name in ('EnableUnitFiles','DisableUnitFiles'):state['needs_reload']=True
        if name=='Reload':state['needs_reload']=False
        return response
    unit.inspect=inspect;unit._method=invoke
    prior=unit.suspend_activation()
    assert prior['enabled'] is True and state['enabled'] is False
    assert [name for name,args in calls]==['DisableUnitFiles','Reload']


def test_failed_reload_exposes_prior_and_restore_reconciles_dirty_cache():
    unit,state,calls=helper(enabled=True)
    method=unit._method;failures=[True]
    def inspect(**kwargs):
        if state['needs_reload'] and not kwargs.get('allow_reload'):raise native.ReloadRequired('fixture dirty manager cache')
        return dict(state)
    def invoke(name,*args):
        if name=='Reload' and failures:
            failures.pop();raise native.CommandTimeout('fixture reload timeout')
        response=method(name,*args)
        if name in ('EnableUnitFiles','DisableUnitFiles'):state['needs_reload']=True
        if name=='Reload':state['needs_reload']=False
        return response
    unit.inspect=inspect;unit._method=invoke
    with pytest.raises(native.CommandTimeout) as caught:unit.suspend_activation()
    assert caught.value.prior_native['enabled'] is True and not state['enabled']
    assert unit.restore_activation(caught.value.prior_native)['enabled'] is True


def test_explicit_load_proves_returned_path_without_gc_inventory():
    unit,state,calls=helper(loaded=False)
    unit._manager=lambda:'stable';unit._read_unit=lambda:(b'known',(1,2,3,4));unit.known={b'known':('python','script')}
    unit.inspect=lambda **kwargs:pytest.fail('GC-prone inventory used after explicit load')
    seen=[]
    unit._loaded_state=lambda obj,content:seen.append((obj,content)) or {'loaded':True,'running':False,'needs_reload':False}
    def load_reply(name,*args):return {'type':'o','data':['/org/freedesktop/systemd1/unit/known']}
    unit._method=load_reply
    assert unit._load()['loaded'] is True
    assert seen==[('/org/freedesktop/systemd1/unit/known',b'known')]


def test_fresh_install_accepts_file_only_without_forcing_unit_load():
    unit,state,calls=helper(file_present=False,loaded=False,enabled=None,definition_sha256=None)
    stored=[None];unit._read_unit=lambda:stored[0]
    def replace(payload,before):
        assert before is None
        stored[0]=(payload,(1,2,3,4));state.update(file_present=True,enabled=False,definition_sha256=hashlib.sha256(payload).hexdigest())
    unit._replace=replace;unit._load=lambda:pytest.fail('fresh install forced load')
    assert unit.install(b'current',enabled=False)['loaded'] is False
    assert calls==[]


def test_install_reconciles_known_dirty_quiescent_state_before_identical_noop():
    unit,state,calls=helper(needs_reload=True)
    unit._read_unit=lambda:(b'current',(1,2,3,4))
    method=unit._method
    def invoke(name,*args):
        response=method(name,*args)
        if name=='Reload':state['needs_reload']=False
        return response
    unit._method=invoke
    assert unit.install(b'current',enabled=False)['enabled'] is False
    assert [name for name,args in calls]==['Reload']


def test_result_requires_declared_reply_arity():
    with pytest.raises(native.RegistrationError):native.LinuxRegistration._result({'type':'o','data':[]},'o')
    with pytest.raises(native.RegistrationError):native.LinuxRegistration._result({'type':'','data':['extra']},'')


def loaded_helper(*,active=False,dirty=False):
    unit,_,_=helper();unit.unit=UNIT;unit.path=PATH;unit.prefix=PREFIX;unit.home=Path.cwd()
    unit.known={b'old':ARGV,b'new':(str(Path.cwd()/'fixture/new-python'),ARGV[1])}
    u,s=known(trusted_home=unit.home);u['NeedDaemonReload']['data']=dirty
    if not active:
        u['ActiveState']['data']='inactive';u['SubState']['data']='dead'
        s['MainPID']['data']=0;s['ControlGroup']['data']=''
    unit._getall=lambda peer,obj,interface:deepcopy(u if interface=='Unit' else s)
    unit.peer=':1.0'
    return unit,u,s


def test_dirty_loaded_recovery_recognizes_only_exact_retained_command():
    unit,u,s=loaded_helper(dirty=True)
    value=unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'new')
    assert value['needs_reload'] and value['arguments']==list(ARGV)
    s['ExecStart']['data'][0][1]=['foreign']
    with pytest.raises(native.RegistrationError,match='retained definition'):
        unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'new')


def test_foreign_dirty_state_never_causes_reload():
    unit,state,calls=helper()
    def foreign(**kwargs):raise native.RegistrationError('foreign definition fixture')
    unit._inspect=foreign
    with pytest.raises(native.RegistrationError,match='foreign'):unit.install(b'current',enabled=False)
    assert calls==[]


def test_main_pid_ownership_transition_becomes_typed_notready(monkeypatch):
    unit,_,_=loaded_helper(active=True)
    def pending(*args,**kwargs):raise processes.OwnershipError('fixture executor before exec')
    monkeypatch.setattr(processes,'inspect',pending)
    with pytest.raises(native.NotReady):unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'old')


def test_running_native_snapshot_returns_fresh_main_lifetime(monkeypatch):
    unit,_,_=loaded_helper(active=True)
    life={'pid':1234,'start_ticks':100,'boot_id':'12345678-1234-1234-1234-123456789abc'};closed=[]
    owned=SimpleNamespace(pid=1234,role='supervisor',exe=processes._path(ARGV[0]),
        lifetime=life,alive=lambda:True,close=lambda:closed.append(True))
    monkeypatch.setattr(processes,'inspect',lambda *args,**kwargs:owned)
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:SimpleNamespace(cmdline=lambda:list(ARGV),cwd=lambda:str(unit.home)))
    unit.verify_processes=lambda owned:None
    value=unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'old')
    assert value['main_lifetime']==life and value['main_lifetime'] is not life and closed==[True]
    assert value['invocation_id']


def test_product_invocation_change_during_inspection_is_not_ready():
    unit,u,s=loaded_helper();calls=[]
    def getall(peer,obj,interface):
        calls.append(interface)
        value=deepcopy(u if interface=='Unit' else s)
        if interface=='Unit' and calls.count('Unit')==2:value['InvocationID']['data']=[2]*16
        return value
    unit._getall=getall
    with pytest.raises(native.NotReady,match='changed during'):
        unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'old')


def test_install_failure_before_write_restores_prior_activation():
    unit,state,calls=helper(enabled=True,definition_sha256=hashlib.sha256(b'old').hexdigest())
    unit._read_unit=lambda:(b'old',(1,2,3,4))
    def refuse(*args):raise native.RegistrationError('fixture CAS refusal')
    unit._replace=refuse
    with pytest.raises(native.RegistrationError,match='CAS') as caught:unit.install(b'current',enabled=True)
    assert state['enabled'] is True and caught.value.prior_native['enabled'] is True
    assert [name for name,args in calls]==['DisableUnitFiles','Reload','EnableUnitFiles','Reload']


def test_loaded_state_passes_its_trusted_home_not_process_cwd(monkeypatch):
    unit,u,s=loaded_helper()
    unit.home=Path.cwd()/'distinct-trusted-home'
    assert unit.home!=Path.cwd()
    s['WorkingDirectory']['data']='!'+str(unit.home)
    original=native.loaded_contract;seen=[]
    def capture(*args,**kwargs):
        seen.append(kwargs['trusted_home'])
        return original(*args,**kwargs)
    monkeypatch.setattr(native,'loaded_contract',capture)
    value=unit._loaded_state('/org/freedesktop/systemd1/unit/known',b'old')
    assert len(seen)==1 and seen[0] is unit.home
    assert value['loaded'] is True and value['running'] is False
