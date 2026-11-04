"""Pure Mac controller controls. Guard stubs do not qualify native service work."""
import json
import hashlib
import os
import struct
from types import SimpleNamespace
from filelock import FileLock
import pytest
import brain_service as service
import brain_settings as cfg
import brain_process as processes


class Registration:
    def __init__(self):
        from brain_mac_registration import definitions
        self.known=definitions(service.LABEL,cfg.ROOT,[str(cfg.PYTHON)])
        self.exact=hashlib.sha256(service.definition('darwin').encode()).hexdigest()
        self.value={'file_present':True,'loaded':True,'enabled':True,'running':True,'definition_sha256':self.exact}
        self.calls=[];self.suspend_error=None;self.stop_error=None
    def inspect(self):self.calls.append('inspect');return dict(self.value)
    def suspend_activation(self):
        record=json.loads((cfg.STATE/'service-control-status.json').read_text())
        assert record['activation_disable_attempted'] is True
        assert type(record['prior_native']['enabled']) is bool
        assert record['prior_native']['definition_sha256']==self.value['definition_sha256']
        self.calls.append('suspend');prior=dict(self.value);self.value['enabled']=False
        if self.suspend_error:raise self.suspend_error
        return prior
    def restore_activation(self,prior):
        self.calls.append('restore');self.value['enabled']=prior['enabled'];return self.inspect()
    def native_stop(self):
        self.calls.append('end')
        if self.stop_error:raise self.stop_error
        self.value.update(loaded=False,running=False)
        return {**self.value,'native_end_exit':0}
    def start(self,*,explicit):self.calls.append(('start',explicit));return self.inspect()


@pytest.fixture
def mac_controller(brain,monkeypatch):
    # This substitutes an OS boundary in pure controls, never a native run.
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'roots',lambda:[])
    monkeypatch.setattr(processes,'descendants',lambda _:[])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(processes,'listener_identity',lambda:None)
    registration=Registration()
    monkeypatch.setattr(service,'_mac_registration',lambda:registration)
    monkeypatch.setattr(service.subprocess,'run',lambda *_a,**_kw:pytest.fail('No bare manager or PID command belongs in the Mac route'))
    return registration


@pytest.mark.parametrize('explicit',[False,True])
def test_mac_start_routes_explicit_activation_to_verified_helper(mac_controller,explicit):
    service._start_native(explicit=explicit)
    assert mac_controller.calls==[('start',explicit),'inspect']


def test_unsupported_mac_guard_stays_first_before_helper_or_state_work(brain,monkeypatch):
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(struct,'calcsize',lambda code:8)
    monkeypatch.setattr(os,'uname',lambda:SimpleNamespace(machine='arm64e'),raising=False)
    monkeypatch.setattr(service,'_mac_registration',lambda:pytest.fail('Unqualified guard was bypassed'))
    monkeypatch.setattr(processes,'validate_environment',lambda:pytest.fail('Unqualified state was inspected'))
    for function in (service.install,service.start,service.stop):
        with pytest.raises(processes.OwnershipError,match='Unsupported candidate architecture'):function()
    assert not (cfg.STATE/'service-control-status.json').exists()


def test_mac_stop_requires_disabled_unloaded_manager_and_empty_roles(mac_controller):
    result=service.stop(wait_seconds=1)
    assert result['status']=='stopped' and result['task_disabled']
    assert result['native_end_exit']==0
    assert mac_controller.calls==['inspect','suspend','inspect','end','inspect']
    assert not mac_controller.value['enabled']


def test_mac_busy_writer_restores_prior_activation_before_end(mac_controller):
    with FileLock(str(cfg.STATE/'writer.lock'),timeout=0):
        with pytest.raises(processes.OwnershipError,match='writer admission'):service.stop(wait_seconds=1)
    receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert not receipt['end_attempted'] and receipt['prior_enabled_restored']
    assert receipt['prior_task_enabled'] and mac_controller.value['enabled']
    assert 'end' not in mac_controller.calls and 'restore' in mac_controller.calls
    assert 'empty_at_filetime' not in receipt


def test_mac_disable_partial_failure_restores_on_any_exception(mac_controller):
    mac_controller.suspend_error=OSError('synthetic observation failure after disable')
    with pytest.raises(processes.OwnershipError):service.stop(wait_seconds=1)
    receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert not receipt['end_attempted'] and receipt['prior_enabled_restored']
    assert mac_controller.value['enabled'] and 'end' not in mac_controller.calls


def test_mac_end_failure_never_restores_activation_or_claims_empty(mac_controller):
    mac_controller.stop_error=OSError('synthetic native End failure')
    with pytest.raises(processes.OwnershipError):service.stop(wait_seconds=1)
    receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert receipt['end_attempted'] and not receipt['prior_enabled_restored']
    assert not mac_controller.value['enabled'] and 'restore' not in mac_controller.calls
    assert 'empty_at_filetime' not in receipt


def test_mac_remaining_owned_role_is_preserved_without_pid_signal(mac_controller,monkeypatch):
    live=SimpleNamespace(role='server',alive=lambda:True,close=lambda:None,
        terminate=lambda:pytest.fail('A Mac PID signal cannot clean up an unverified manager child'))
    rows=iter([[],[live]])
    monkeypatch.setattr(processes,'roots',lambda:next(rows,[live]))
    clock=[0]
    def monotonic():clock[0]+=.1;return clock[0]
    monkeypatch.setattr(service,'time',SimpleNamespace(monotonic=monotonic,sleep=lambda _:None))
    with pytest.raises(processes.OwnershipError,match='no empty-runtime receipt'):service.stop(wait_seconds=1)
    receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert receipt['end_attempted'] and 'empty_at_filetime' not in receipt
    assert not mac_controller.value['enabled'] and 'restore' not in mac_controller.calls


def test_mac_update_preflight_requires_loaded_owned_registration(mac_controller):
    assert service.inspect()['task_present']
    mac_controller.value['loaded']=False
    assert not service.inspect()['task_present']


@pytest.mark.parametrize('identity,native',[
    (None,{'loaded':True,'running':True,'main_pid':123}),
    ({'supervisor_pid':123},{'loaded':False,'running':True,'main_pid':123}),
    ({'supervisor_pid':123},{'loaded':True,'running':False,'main_pid':123}),
    ({'supervisor_pid':123},{'loaded':True,'running':True,'main_pid':124}),
    ({'supervisor_pid':True},{'loaded':True,'running':True,'main_pid':True}),
    ({'supervisor_pid':123},{'loaded':True,'running':True,'main_pid':'123'}),
])
def test_native_listener_binding_requires_same_typed_manager_supervisor(identity,native):
    with pytest.raises(processes.OwnershipError,match='native manager job'):service._native_listener_binding(identity,native)


def test_native_listener_binding_accepts_same_loaded_running_supervisor():
    assert service._native_listener_binding({'supervisor_pid':123},{'loaded':True,'running':True,'main_pid':123}) is None


@pytest.mark.parametrize('change',['none','missing','pid','ticks','boot','bool-ticks'])
def test_linux_native_binding_requires_matching_boot_relative_lifetime(monkeypatch,change):
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform='linux'))
    identity={'supervisor_pid':123,'supervisor_start_ticks':456,'supervisor_boot_id':'same-boot'}
    lifetime={'pid':123,'start_ticks':456,'boot_id':'same-boot'}
    if change=='missing':lifetime=None
    elif change=='pid':lifetime['pid']=124
    elif change=='ticks':lifetime['start_ticks']=457
    elif change=='boot':lifetime['boot_id']='other-boot'
    elif change=='bool-ticks':lifetime['start_ticks']=True
    native={'loaded':True,'running':True,'main_pid':123,'main_lifetime':lifetime}
    if change=='none':assert service._native_listener_binding(identity,native) is None
    else:
        with pytest.raises(processes.OwnershipError,match='Linux listener lifetime'):
            service._native_listener_binding(identity,native)


def test_mac_manual_pair_refuses_before_start_status_or_native_mutation(mac_controller,monkeypatch):
    monkeypatch.setattr(processes,'listener_identity',lambda:{'supervisor_pid':124})
    mac_controller.value['main_pid']=123
    with pytest.raises(processes.OwnershipError,match='native manager job'):service.start(explicit=True)
    assert not (cfg.STATE/'service-control-status.json').exists()
    assert mac_controller.calls==['inspect']


def test_mac_manual_supervisor_refuses_before_end_and_restores_activation(mac_controller,monkeypatch):
    mac_controller.value['main_pid']=123
    manual=SimpleNamespace(role='supervisor',pid=124,alive=lambda:True,close=lambda:None,
        terminate=lambda:pytest.fail('Manual supervisor must be preserved'))
    monkeypatch.setattr(processes,'roots',lambda:[manual])
    with pytest.raises(processes.OwnershipError,match='manual same-root supervisor'):service.stop(wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert not record['end_attempted'] and record['prior_enabled_restored']
    assert 'end' not in mac_controller.calls and mac_controller.value['enabled']


def test_mac_stop_can_end_verified_job_with_contained_orphan_without_pid_signals(mac_controller,monkeypatch):
    orphan=SimpleNamespace(role='server',alive=lambda:mac_controller.value['loaded'],close=lambda:None,
        terminate=lambda:pytest.fail('Only the native manager may end an orphan'))
    monkeypatch.setattr(processes,'roots',lambda:[orphan])
    def listener():raise processes.OwnershipError('Owned orphan found; run service stop then explicit service start')
    monkeypatch.setattr(processes,'listener_identity',listener)
    assert service.stop(wait_seconds=1)['status']=='stopped'
    assert 'end' in mac_controller.calls and not mac_controller.value['enabled']


@pytest.mark.parametrize('mismatch_at',['before-health','after-health'])
def test_mac_finish_binds_native_supervisor_on_both_sides_of_authenticated_health(mac_controller,monkeypatch,mismatch_at):
    import brain_cli
    identity={'supervisor_pid':123,'birth':3,'supervisor_birth':3}
    monkeypatch.setattr(processes,'listener_identity',lambda:dict(identity))
    monkeypatch.setattr(processes,'same_runtime',lambda left,right:left==right)
    monkeypatch.setattr(cfg,'DEVICE',{'version':cfg.VERSION})
    monkeypatch.setattr(service,'_match_transaction',lambda _: {'stage':'started','empty_at_filetime':1,'started_at_filetime':2})
    monkeypatch.setattr(service,'_heartbeat',lambda *_:'verified')
    calls=[]
    async def health(*_):calls.append('authenticated-health');return {'status':'running','version':cfg.VERSION}
    monkeypatch.setattr(brain_cli,'mcp_call',health)
    observations=iter([dict(mac_controller.value,main_pid=124)] if mismatch_at=='before-health' else
        [dict(mac_controller.value,main_pid=123),dict(mac_controller.value,main_pid=124)])
    mac_controller.inspect=lambda:next(observations)
    with pytest.raises(processes.OwnershipError,match='native manager job'):
        service.finish_transaction('a'*32,cfg.VERSION)
    assert calls==([] if mismatch_at=='before-health' else ['authenticated-health'])
    assert not (cfg.STATE/'service-transactions'/('a'*32+'.json')).exists()


@pytest.mark.parametrize('prior_end',[False,True])
def test_same_hold_retry_preserves_original_activation_intent_and_end_uncertainty(mac_controller,monkeypatch,prior_end):
    hold='b'*32;prior=dict(mac_controller.value)
    mac_controller.value['enabled']=False
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stopping','hold_id':hold,
        'activation_disable_attempted':True,'prior_native':prior,'end_attempted':prior_end}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    with FileLock(str(cfg.STATE/'writer.lock'),timeout=0):
        with pytest.raises(processes.OwnershipError):service.stop(hold_id=hold,wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert record['phase']=='writer admission'
    assert record['end_attempted'] is prior_end
    assert record['prior_native']['enabled'] is True
    assert record['prior_enabled_restored'] is (not prior_end)
    assert mac_controller.value['enabled'] is (not prior_end)
    assert 'end' not in mac_controller.calls and 'empty_at_filetime' not in record


def test_cancel_cannot_release_hold_after_native_definition_changed(mac_controller):
    from service_format3_fixture import seed_hold
    hold='c'*32;prior=dict(mac_controller.value)
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold,stop_entered=True)
    mac_controller.value['definition_sha256']='different-recognized-definition'
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stop failed','hold_id':hold,
        'activation_disable_attempted':True,'end_attempted':False,'prior_task_enabled':True,'prior_native':prior}))
    with pytest.raises(processes.OwnershipError,match='definition'):service.cancel_transaction(hold)
    assert (cfg.STATE/'service-transaction.json').exists()


@pytest.mark.parametrize('change',[
    {'prior_native':None},
    {'prior_native':{'enabled':'true','definition_sha256':'exact'}},
    {'prior_native':{'enabled':True}},
    {'end_attempted':1},
    {'activation_disable_attempted':None},
])
def test_malformed_matching_stop_history_refuses_without_overwrite(mac_controller,monkeypatch,change):
    hold='d'*32
    history={'status':'stopping','hold_id':hold,'prior_native':dict(mac_controller.value),
        'end_attempted':False,'activation_disable_attempted':True,**change}
    raw=json.dumps(history).encode();path=cfg.STATE/'service-control-status.json';path.write_bytes(raw)
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    with pytest.raises(processes.OwnershipError,match='history'):service.stop(hold_id=hold,wait_seconds=1)
    assert path.read_bytes()==raw and mac_controller.calls==[]


@pytest.mark.parametrize('raw',[b'{',b'[]',b'x'*65537],ids=['bad-json','not-map','oversized'])
def test_unreadable_history_during_hold_is_preserved(mac_controller,monkeypatch,raw):
    hold='e'*32
    path=cfg.STATE/'service-control-status.json';path.write_bytes(raw)
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    with pytest.raises(processes.OwnershipError,match='receipt'):service.stop(hold_id='e'*32,wait_seconds=1)
    assert path.read_bytes()==raw and mac_controller.calls==[]


@pytest.mark.parametrize('status,prior_hold',[('stopping','f'*32),('start failed','a'*32)])
def test_other_hold_or_start_status_is_not_reused_as_stop_history(mac_controller,monkeypatch,status,prior_hold):
    hold='a'*32;mac_controller.value['enabled']=False
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':status,'hold_id':prior_hold,
        'prior_native':{'enabled':True,'definition_sha256':'exact'},'activation_disable_attempted':True,'end_attempted':True}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold,stage='stopped')
    with FileLock(str(cfg.STATE/'writer.lock'),timeout=0):
        with pytest.raises(processes.OwnershipError):service.stop(hold_id=hold,wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert record['phase']=='writer admission' and not record['end_attempted']
    assert not record['prior_task_enabled'] and not mac_controller.value['enabled']


@pytest.mark.parametrize('prior_end',[False,True])
def test_changed_history_definition_refuses_before_suspend_and_retains_original(mac_controller,monkeypatch,prior_end):
    hold='b'*32;prior=dict(mac_controller.value,definition_sha256='original')
    mac_controller.value['enabled']=False
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stopping','hold_id':hold,
        'prior_native':prior,'activation_disable_attempted':True,'end_attempted':prior_end}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    with pytest.raises(processes.OwnershipError,match='definition changed'):service.stop(hold_id=hold,wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert record['prior_native']==prior and record['end_attempted'] is prior_end
    assert record['task_disabled'] and not record['prior_enabled_restored']
    assert mac_controller.calls==['inspect']


def test_sticky_end_never_reenables_even_if_current_activation_was_enabled(mac_controller,monkeypatch):
    hold='c'*32;prior=dict(mac_controller.value)
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stop failed','hold_id':hold,
        'prior_native':prior,'activation_disable_attempted':True,'end_attempted':True}))
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    with FileLock(str(cfg.STATE/'writer.lock'),timeout=0):
        with pytest.raises(processes.OwnershipError):service.stop(hold_id=hold,wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert record['phase']=='writer admission' and record['end_attempted']
    assert not record['prior_enabled_restored'] and not mac_controller.value['enabled']


@pytest.mark.parametrize('fail',[False,True])
@pytest.mark.parametrize('stage',['prepared','stopped'])
def test_install_during_hold_preserves_stop_evidence_and_never_restores_activation(mac_controller,monkeypatch,fail,stage):
    mac_controller.value['loaded']=False
    mac_controller.value['enabled']=False
    mac_controller.path=cfg.ROOT/'synthetic.plist'
    raw=json.dumps({'status':'stop failed','hold_id':'d'*32,'end_attempted':True,
        'prior_native':dict(mac_controller.value),'activation_disable_attempted':True}).encode()
    (cfg.STATE/'service-control-status.json').write_bytes(raw)
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold='d'*32,stage=stage,forward=True)
    def install(payload,*,enabled):
        assert enabled is False
        mac_controller.calls.append('install')
        if fail:raise OSError('synthetic install failure')
        return dict(mac_controller.value,enabled=False)
    mac_controller.install=install
    if stage=='prepared':
        with pytest.raises(processes.OwnershipError,match='verified stop'):service.install()
        assert 'install' not in mac_controller.calls
    elif fail:
        with pytest.raises(OSError):service.install()
    else:assert service.install()['status']=='installed'
    assert (cfg.STATE/'service-control-status.json').read_bytes()==raw
    assert 'restore' not in mac_controller.calls


@pytest.mark.parametrize('failure',['missing-definition','inspection-error'])
def test_read_only_preflight_refusal_never_poison_same_hold_retry(mac_controller,monkeypatch,failure):
    hold='e'*32
    from service_format3_fixture import seed_hold
    seed_hold(service,cfg,{'kind':'darwin','definition_sha256':mac_controller.exact,'enabled':True,
        'interpreter':str(cfg.PYTHON)},hold=hold)
    if failure=='missing-definition':mac_controller.value.update(file_present=False,definition_sha256=None)
    else:
        actual=mac_controller.inspect
        def unavailable():mac_controller.calls.append('inspect');raise OSError('synthetic read-only preflight unavailable')
        mac_controller.inspect=unavailable
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=hold,wait_seconds=1)
    record=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert record['status']=='stop preflight failed' and record['prior_native'] is None
    assert record['activation_disable_attempted'] is False and record['end_attempted'] is False
    assert mac_controller.calls==['inspect']
    with pytest.raises(processes.OwnershipError,match='hold preserved'):service.cancel_transaction(hold)
    if failure=='inspection-error':mac_controller.inspect=actual
    mac_controller.value.update(file_present=True,definition_sha256=mac_controller.exact)
    with FileLock(str(cfg.STATE/'writer.lock'),timeout=0):
        with pytest.raises(processes.OwnershipError):service.stop(hold_id=hold,wait_seconds=1)
    retried=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert retried['phase']=='writer admission' and retried['prior_enabled_restored']


def test_mac_nonheld_install_retry_keeps_requested_activation_then_completes(mac_controller):
    mac_controller.path=cfg.ROOT/'synthetic.plist'
    mac_controller.value.update(file_present=False,loaded=False,running=False,definition_sha256=None)
    attempts=[]
    def install(payload,*,enabled):
        attempts.append(enabled)
        mac_controller.value.update(file_present=True,definition_sha256=hashlib.sha256(payload).hexdigest(),enabled=False)
        if len(attempts)==1:raise OSError('synthetic post-write verification failure')
        mac_controller.value['enabled']=enabled;return dict(mac_controller.value)
    def restore(prior):
        if prior['definition_sha256']!=mac_controller.value['definition_sha256']:raise processes.OwnershipError('Definition changed')
        mac_controller.value['enabled']=prior['enabled']
    mac_controller.install=install;mac_controller.restore_activation=restore
    with pytest.raises(OSError):service.install()
    failed=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert failed['requested_enabled'] and failed['prior_native']['definition_sha256'] is None
    assert not mac_controller.value['enabled']
    assert service.install()['status']=='installed'
    done=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert done['status']=='installed' and mac_controller.value['enabled']
    # A later explicit stopped state does not replay a completed install intent.
    mac_controller.value['enabled']=False
    assert service.install()['status']=='installed'
    assert attempts==[True,True,False] and not mac_controller.value['enabled']
