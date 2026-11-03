"""Pure Windows crash-boundary controls; every scheduler/process operation is fake."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock,Timeout
import brain_service as service
import brain_settings as cfg
import brain_process as processes

HOLD='a'*32
DEFINITION='b'*64


def prepared(*,stage='prepared'):
    from service_format3_fixture import seed_hold
    return seed_hold(service,cfg,REGISTRATION,hold=HOLD,stage=stage)


@pytest.fixture
def windows(brain,monkeypatch):
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(service,'_candidate_platform',lambda:None,raising=False)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'roots',lambda:[])
    monkeypatch.setattr(processes,'descendants',lambda owned:[])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(service,'_tool',lambda name:name)
    monkeypatch.setattr(processes,'_launchers',lambda:{processes._path(cfg.PYTHON.with_name('pythonw.exe')):None})
    task=service._windows_definition({'xml':service.definition('win32'),'owner_sid':'S-1-5-21-123','current_sid':'S-1-5-21-123'})
    task['state']='Ready'
    monkeypatch.setitem(globals(),'DEFINITION',task['definition_sha256'])
    monkeypatch.setitem(globals(),'REGISTRATION',{'kind':'win32','enabled':True,'definition_sha256':task['definition_sha256'],
        'interpreter':task['interpreter'],'xml':task['xml'],'owner_sid':task['owner_sid']})
    observed={};calls=[]
    monkeypatch.setattr(service,'_windows_task',lambda **kwargs:dict(task))
    def run(command,**kwargs):
        calls.append(command)
        path=cfg.STATE/'service-control-status.json'
        if '/Disable' in command:
            observed['before_disable']=json.loads(path.read_text()) if path.exists() else None
            task['enabled']=False
        elif '/Enable' in command:task['enabled']=True
        elif '/End' in command:observed['before_end']=json.loads(path.read_text()) if path.exists() else None
        else:pytest.fail('Unexpected fake scheduler action')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess,'run',run)
    return task,observed,calls


def busy_writer(monkeypatch):
    class Busy:
        def __init__(self,path):self.path=path
        def __enter__(self):raise Timeout(self.path)
        def __exit__(self,*args):return False
    monkeypatch.setattr(service,'FileLock',lambda path,**kwargs:FileLock(path,**kwargs) if path.endswith('service-control.lock') else Busy(path))


def test_original_activation_is_durable_before_disable_boundary(windows,monkeypatch):
    task,seen,calls=windows;prepared();busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    before=seen.get('before_disable')
    assert before is not None,'No durable prior activation/definition record existed before fake OS Disable.'
    assert before['hold_id']==HOLD and before['prior_task_enabled'] is True
    assert before['prior_task_definition_sha256']==DEFINITION and before['end_attempted'] is False


def test_end_attempt_is_durable_before_native_end_boundary(windows):
    task,seen,calls=windows;prepared()
    service.stop(hold_id=HOLD,wait_seconds=1)
    before=seen.get('before_end')
    assert before is not None,'No durable End-attempt record existed before fake OS End.'
    assert before['hold_id']==HOLD and before['end_attempted'] is True
    assert before['prior_task_definition_sha256']==DEFINITION


def test_uncertain_end_retry_cannot_rewrite_it_as_pre_end_refusal(windows,monkeypatch):
    task,seen,calls=windows;prepared();task['enabled']=False
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stopping','phase':'native end',
        'root':str(cfg.ROOT),'task_address':service._task_address(),'hold_id':HOLD,'end_attempted':True,'prior_task_present':True,'prior_task_enabled':True,'prior_task_definition_sha256':DEFINITION,
        'activation_disable_attempted':True}))
    busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    after=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert after['end_attempted'] is True,'Retry forgot a previous uncertain native End attempt.'
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


def test_pre_end_cancel_refuses_different_owned_task_definition(windows):
    task,seen,calls=windows;prepared();task['definition_sha256']='c'*64
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stop failed','root':str(cfg.ROOT),'task_address':service._task_address(),'hold_id':HOLD,
        'end_attempted':False,'prior_task_present':True,'prior_task_enabled':True,'prior_task_definition_sha256':DEFINITION,
        'activation_disable_attempted':True}))
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


def stop_history(**changes):
    value={'status':'stopping','phase':'task activation suspension','root':str(cfg.ROOT),
        'task_address':service._task_address(),'hold_id':HOLD,'prior_task_present':True,'prior_task_enabled':True,
        'prior_task_definition_sha256':DEFINITION,'end_attempted':False,'activation_disable_attempted':True}
    value.update(changes)
    (cfg.STATE/'service-control-status.json').write_text(json.dumps(value))
    return value


def test_retry_after_disable_retains_original_enabled_intent(windows,monkeypatch):
    task,seen,calls=windows;prepared();task['enabled']=False;stop_history();busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    status=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert task['enabled'] and status['prior_task_enabled'] is True and status['prior_enabled_restored']
    assert status['prior_task_definition_sha256']==DEFINITION
    assert not status['end_attempted'] and [c[-1] for c in calls]==['/Disable','/Enable']
    assert service.cancel_transaction(HOLD)['status']=='cancelled; runtime not switched'


def test_originally_disabled_task_is_not_enabled_on_busy_writer(windows,monkeypatch):
    task,seen,calls=windows;task['enabled']=False;busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(wait_seconds=1)
    assert task['enabled'] is False and not any('/Enable' in c for c in calls)
    assert json.loads((cfg.STATE/'service-control-status.json').read_text())['prior_task_enabled'] is False


def test_partial_disable_error_restores_only_same_definition(windows,monkeypatch):
    import subprocess
    task,seen,calls=windows;prepared();original=service.subprocess.run
    def fail_after_effect(command,**kwargs):
        result=original(command,**kwargs)
        if '/Disable' in command:raise subprocess.CalledProcessError(1,command)
        return result
    monkeypatch.setattr(service.subprocess,'run',fail_after_effect)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert task['enabled'] and json.loads((cfg.STATE/'service-control-status.json').read_text())['prior_enabled_restored']
    assert not any('/End' in c for c in calls)


def test_unverified_enable_cannot_cancel_hold(windows,monkeypatch):
    task,seen,calls=windows;prepared();original=service.subprocess.run
    def ignore_enable(command,**kwargs):
        if '/Enable' in command:calls.append(command);return SimpleNamespace(returncode=0)
        return original(command,**kwargs)
    monkeypatch.setattr(service.subprocess,'run',ignore_enable);busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not task['enabled'] and not json.loads((cfg.STATE/'service-control-status.json').read_text())['prior_enabled_restored']
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


@pytest.mark.parametrize('boundary',['task activation suspension','native end'])
def test_durable_write_failure_never_crosses_its_os_boundary(windows,monkeypatch,boundary):
    task,seen,calls=windows;prepared();original=service._atomic
    def reject(path,value):
        if value.get('status')=='stopping' and value.get('phase')==boundary:raise OSError('synthetic durable publication refusal')
        return original(path,value)
    monkeypatch.setattr(service,'_atomic',reject)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    forbidden='/Disable' if boundary=='task activation suspension' else '/End'
    assert not any(forbidden in c for c in calls)
    if boundary=='native end':
        assert json.loads((cfg.STATE/'service-control-status.json').read_text())['end_attempted'] is True
        with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)


@pytest.mark.parametrize('patch',[{'root':'wrong-root'},{'task_address':'wrong-task'},
    {'prior_task_enabled':1},{'prior_task_definition_sha256':None},{'end_attempted':0},
    {'activation_disable_attempted':None}])
def test_malformed_same_hold_history_refuses_without_rewriting_it(windows,patch):
    task,seen,calls=windows;prepared();stop_history(**patch)
    before=(cfg.STATE/'service-control-status.json').read_bytes()
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not calls and (cfg.STATE/'service-control-status.json').read_bytes()==before
    assert (cfg.STATE/'service-transaction.json').exists()


def test_changed_definition_on_retry_is_not_disabled_or_reenabled(windows,monkeypatch):
    task,seen,calls=windows;prepared();task.update(enabled=False,definition_sha256='c'*64);stop_history()
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not calls and task['enabled'] is False
    assert json.loads((cfg.STATE/'service-control-status.json').read_text())['prior_task_definition_sha256']==DEFINITION
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)


def task_export(monkeypatch):
    import xml.etree.ElementTree as ET
    tree=ET.fromstring(service.definition('win32'))
    value={'exists':True,'state':'Ready','last_result':0,'owner_sid':'S-1-5-21-1001','current_sid':'S-1-5-21-1001'}
    monkeypatch.setattr(service,'_scheduled_module',lambda:cfg.ROOT/'mock-ScheduledTasks.psd1')
    monkeypatch.setattr(processes,'_launchers',lambda:{processes._path(str(cfg.PYTHON.with_name('pythonw.exe')))})
    def run(*args,**kwargs):
        value['xml']=ET.tostring(tree,encoding='unicode')
        return SimpleNamespace(returncode=0,stdout=json.dumps(value))
    monkeypatch.setattr(service.subprocess,'run',run)
    return tree,value


def test_task_fingerprint_ignores_activation_and_runtime_counters(brain,monkeypatch):
    tree,value=task_export(monkeypatch)
    first=service._windows_task()['definition_sha256']
    tree.find('{*}Settings/{*}Enabled').text='false';value.update(state='Disabled',last_result=1234)
    assert service._windows_task()['definition_sha256']==first
    tree.find('{*}Settings').remove(tree.find('{*}Settings/{*}Enabled'));value['state']='Ready'
    assert service._windows_task()['definition_sha256']==first


@pytest.mark.parametrize('change',['trigger','execution-limit','principal-policy','security-descriptor'])
def test_task_fingerprint_retains_other_definition_semantics(brain,monkeypatch,change):
    import xml.etree.ElementTree as ET
    tree,value=task_export(monkeypatch);first=service._windows_task()['definition_sha256']
    ns='{http://schemas.microsoft.com/windows/2004/02/mit/task}'
    if change=='trigger':
        trigger=ET.SubElement(tree.find('{*}Triggers'),ns+'LogonTrigger');ET.SubElement(trigger,ns+'Enabled').text='false'
    elif change=='execution-limit':tree.find('{*}Settings/{*}ExecutionTimeLimit').text='PT1H'
    elif change=='principal-policy':ET.SubElement(tree.find('{*}Principals/{*}Principal'),ns+'ProcessTokenSidType').text='Unrestricted'
    else:
        info=ET.SubElement(tree,ns+'RegistrationInfo');ET.SubElement(info,ns+'SecurityDescriptor').text='D:P(A;;GA;;;SY)'
    assert service._windows_task()['definition_sha256']!=first



def test_positive_absent_task_remains_supported_for_held_recovery(windows,monkeypatch):
    task,seen,calls=windows;prepared(stage='stopped');monkeypatch.setattr(service,'_windows_task',lambda:None)
    result=service.stop(hold_id=HOLD,wait_seconds=1)
    assert result['status']=='stopped' and result['prior_task_present'] is False
    assert result['prior_task_enabled'] is None and result['prior_task_definition_sha256'] is None
    assert seen['before_end']['prior_task_present'] is False and seen['before_end']['end_attempted'] is True
    assert not any('/Disable' in c or '/Enable' in c for c in calls)


def test_original_task_absence_before_first_stop_preserves_hold(windows,monkeypatch):
    task,seen,calls=windows;prepared();monkeypatch.setattr(service,'_windows_task',lambda:None);busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not calls
    status=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert status['status']=='stop preflight failed' and status['prior_task_present'] is None
    assert status['prior_task_definition_sha256'] is None and status['end_attempted'] is False
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


def test_absent_task_uncertain_end_remains_sticky_on_retry(windows,monkeypatch):
    task,seen,calls=windows;prepared(stage='stopped');monkeypatch.setattr(service,'_windows_task',lambda:None)
    stop_history(prior_task_present=False,prior_task_enabled=None,prior_task_definition_sha256=None,activation_disable_attempted=False,end_attempted=True)
    busy_writer(monkeypatch)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    status=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert status['end_attempted'] is True and status['prior_task_present'] is False and not calls
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)


def test_task_appearing_after_positive_absence_is_not_adopted(windows):
    task,seen,calls=windows;prepared()
    stop_history(prior_task_present=False,prior_task_enabled=None,prior_task_definition_sha256=None,activation_disable_attempted=False)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not calls and json.loads((cfg.STATE/'service-control-status.json').read_text())['prior_task_present'] is False
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)


def test_missing_presence_key_is_not_inferred_as_absence(windows):
    task,seen,calls=windows;prepared();value=stop_history();value.pop('prior_task_present')
    path=cfg.STATE/'service-control-status.json';path.write_text(json.dumps(value));before=path.read_bytes()
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert not calls and path.read_bytes()==before



def test_unverified_first_query_receipt_is_unknown_not_positive_absence(windows,monkeypatch):
    task,seen,calls=windows;prepared()
    def unavailable():raise processes.OwnershipError('synthetic task query unavailable')
    monkeypatch.setattr(service,'_windows_task',unavailable)
    for _ in range(2):
        with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
        status=json.loads((cfg.STATE/'service-control-status.json').read_text())
        assert status['status']=='stop preflight failed' and status['prior_task_present'] is None
        assert status['prior_task_enabled'] is None and status['prior_task_definition_sha256'] is None
        assert service._windows_stop_history(HOLD) is None and not calls
        with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)


@pytest.mark.parametrize('enabled,state,accepted',[
    (True,'Ready',True),(True,'Queued',True),(True,'Running',True),
    (False,'Disabled',True),(False,'Running',True),
    (True,'Disabled',False),(True,'Unknown',False),(True,None,False),
    (False,'Ready',False),(False,'Queued',False),(False,'Unknown',False),(False,None,False),
])
def test_documented_task_activation_and_instance_state_pairs(brain,monkeypatch,enabled,state,accepted):
    tree,value=task_export(monkeypatch)
    tree.find('{*}Settings/{*}Enabled').text='true' if enabled else 'false'
    value['state']=state
    if accepted:
        result=service._windows_task()
        assert result['enabled'] is enabled and result['state']==state
    else:
        with pytest.raises(processes.OwnershipError,match='supported native state'):service._windows_task()
