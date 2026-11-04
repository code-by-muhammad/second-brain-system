"""Linux caller routing with explicit fake native adapter; no native acceptance."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import brain_process as processes
import brain_service as service
import brain_settings as cfg

BOOT='12345678-1234-1234-1234-123456789abc'
HOLD='a'*32


@pytest.fixture
def linux(brain,monkeypatch):
    monkeypatch.setattr(service.sys,'platform','linux')
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'linux_marker',lambda:{'boot_id':BOOT,'boottime_ticks':900})
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:pytest.fail('native process in fake route control'))
    sha=hashlib.sha256(service.definition('linux').encode()).hexdigest()
    state={'file_present':True,'loaded':True,'enabled':True,'running':True,'main_pid':1234,
        'main_lifetime':{'pid':1234,'start_ticks':100,'boot_id':BOOT},'definition_sha256':sha,
        'quiescent':False,'active_state':'active','job':[0,'/'],'control_pid':0}
    alive=[True];calls=[]
    owned=SimpleNamespace(pid=1234,role='supervisor',kind='direct',lifetime=deepcopy(state['main_lifetime']),
        alive=lambda:alive[0],close=lambda:None,terminate=lambda:pytest.fail('cached PID signal'))
    class Adapter:
        known={service.definition('linux').encode():[str(cfg.PYTHON),str(cfg.ROOT/'app/brain_supervisor.py')]}
        path=cfg.ROOT/'native.service'
        def inspect(self,**kwargs):return deepcopy(state)
        def suspend_activation(self,prior=None):calls.append('disable');state['enabled']=False;return prior
        def restore_activation(self,prior):calls.append('restore');state['enabled']=prior['enabled'];return self.inspect()
        def verify_processes(self,values):calls.append('cgroups')
        def native_stop(self):
            calls.append('end');alive[0]=False
            state.update(running=False,main_pid=0,main_lifetime=None,quiescent=True,active_state='inactive')
            return {**self.inspect(),'native_stopped':True,'native_end_exit':0,'native_end_timed_out':False}
        def start(self,*,explicit):
            calls.append(('start',explicit));state.update(loaded=True,running=True,enabled=True)
            return self.inspect()
        def install(self,payload,*,enabled):
            calls.append(('install',enabled));state.update(enabled=enabled,definition_sha256=hashlib.sha256(payload).hexdigest())
            return self.inspect()
    adapter=Adapter()
    monkeypatch.setattr(service,'_linux_registration',lambda:adapter)
    monkeypatch.setattr(processes,'roots',lambda:[owned] if alive[0] else [])
    monkeypatch.setattr(processes,'descendants',lambda values:[])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(processes,'listener_identity',lambda:None)
    return adapter,state,calls,owned,alive


def failed_admission(monkeypatch):
    class Lock:
        def __init__(self,*args,**kwargs):pass
        def acquire(self,*args,**kwargs):return self
        def release(self):pass
        def __enter__(self):raise service.Timeout('fixture busy writer')
        def __exit__(self,*args):return False
    monkeypatch.setattr(service,'FileLock',Lock)


def test_linux_stop_uses_manager_and_allows_loaded_inactive_receipt(linux):
    adapter,state,calls,owned,alive=linux
    result=service.stop()
    assert result['status']=='stopped' and result['native']['loaded'] is True
    assert result['task_disabled'] is True and result['empty_boot_id']==BOOT
    assert calls==['disable','cgroups','end'] and not alive[0]


def test_linux_stop_does_not_need_live_orphan_listener_credentials(linux,monkeypatch):
    adapter,state,calls,owned,alive=linux
    owned.role='server';state.update(running=False,main_pid=0,main_lifetime=None,active_state='failed')
    def orphan():raise processes.OwnershipError('owned orphan; run service stop')
    monkeypatch.setattr(processes,'listener_identity',orphan)
    assert service.stop()['status']=='stopped' and calls==['disable','cgroups','end']


def test_linux_writer_refusal_restores_activation_before_end(linux,monkeypatch):
    adapter,state,calls,owned,alive=linux
    failed_admission(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop()
    receipt=json.loads((cfg.ROOT/'state/service-control-status.json').read_text())
    assert state['enabled'] is True and calls==['disable','restore']
    assert receipt['end_attempted'] is False and receipt['prior_enabled_restored'] is True


@pytest.mark.parametrize('earlier_end',[False,True])
def test_linux_retry_keeps_first_prior_and_sticky_end(linux,monkeypatch,earlier_end):
    adapter,state,calls,owned,alive=linux
    prior=deepcopy(state);state['enabled']=False
    (cfg.ROOT/'state/service-control-status.json').write_text(json.dumps({'status':'stopping','hold_id':HOLD,
        'prior_native':prior,'activation_disable_attempted':True,'end_attempted':earlier_end}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'linux','definition_sha256':prior['definition_sha256'],'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=HOLD)
    failed_admission(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD)
    result=json.loads((cfg.ROOT/'state/service-control-status.json').read_text())
    assert result['prior_native']['enabled'] is True and result['end_attempted'] is earlier_end
    assert state['enabled'] is (not earlier_end)
    assert ('restore' in calls) is (not earlier_end)


def test_linux_native_main_must_be_in_verified_inventory(linux):
    adapter,state,calls,owned,alive=linux
    owned.role='server'
    with pytest.raises(processes.OwnershipError,match='missing from'):service.stop()
    assert 'end' not in calls and state['enabled'] is True


def test_linux_native_cgroup_refusal_never_sends_end(linux):
    adapter,state,calls,owned,alive=linux
    def wrong(values):raise processes.OwnershipError('outside unit cgroup')
    adapter.verify_processes=wrong
    with pytest.raises(processes.OwnershipError,match='outside unit'):service.stop()
    assert 'end' not in calls and state['enabled'] is True


def test_linux_inspect_emits_exact_current_target_even_file_only(linux):
    adapter,state,calls,owned,alive=linux
    state.update(loaded=False,running=False,main_pid=0,main_lifetime=None)
    result=service.inspect()
    assert result['task_present'] is False and result['native']['file_present'] is True
    assert result['expected_native_definition_sha256']==hashlib.sha256(service.definition('linux').encode()).hexdigest()
    assert calls==[]


def test_linux_nonheld_install_marks_completed_intent(linux):
    adapter,state,calls,owned,alive=linux
    state.update(running=False,main_pid=0,main_lifetime=None,quiescent=True,enabled=False)
    assert service.install()['status']=='installed'
    result=json.loads((cfg.ROOT/'state/service-control-status.json').read_text())
    assert calls==[('install',False)] and result['status']=='installed'
    assert result['requested_enabled'] is False and result['expected_native_definition_sha256']==state['definition_sha256']


def test_linux_held_install_preserves_stop_record_and_stays_disabled(linux):
    adapter,state,calls,owned,alive=linux
    state.update(running=False,main_pid=0,main_lifetime=None,quiescent=True,enabled=False)
    record={'status':'stopped','hold_id':HOLD,'native':'existing receipt'}
    path=cfg.ROOT/'state/service-control-status.json';path.write_text(json.dumps(record))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'linux','definition_sha256':state['definition_sha256'],'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=HOLD,stage='stopped',forward=True)
    service.install()
    assert calls==[('install',False)] and json.loads(path.read_text())==record


def test_linux_held_install_refuses_before_stop_or_if_reenabled(linux):
    adapter,state,calls,owned,alive=linux
    (cfg.ROOT/'state/service-transaction.json').write_text(json.dumps({'hold_id':HOLD,'root':str(cfg.ROOT),'stage':'prepared'}))
    with pytest.raises(processes.OwnershipError,match='stopped phase'):service.install()
    assert calls==[]


def test_linux_cancel_requires_original_native_fingerprint(linux,monkeypatch):
    adapter,state,calls,owned,alive=linux
    prior=deepcopy(state);state['definition_sha256']='different'
    path=cfg.ROOT/'state/service-control-status.json'
    path.write_text(json.dumps({'status':'stop failed','hold_id':HOLD,'end_attempted':False,
        'prior_task_enabled':True,'prior_native':prior,'activation_disable_attempted':True}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'linux','definition_sha256':prior['definition_sha256'],'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=HOLD,stop_entered=True)
    with pytest.raises(processes.OwnershipError,match='definition/activation differs'):service.cancel_transaction(HOLD)
    assert calls==[]
