"""Admission and credential access boundaries; no native scheduler mutations."""
import asyncio
import json
from types import SimpleNamespace
import pytest
import brain_settings as cfg
import brain_process as processes
import brain_service as service
import brain_client as client
import brain_cli as cli


@pytest.fixture
def device(brain,monkeypatch):
    monkeypatch.setattr(cfg,'DEVICE',dict(vault=str(cfg.VAULT),python=str(cfg.PYTHON),port=cfg.PORT,version=cfg.VERSION,device_id='fixture'))
    for name,value in [('BRAIN_SYSTEM_ROOT',cfg.ROOT),('BRAIN_STATE',cfg.STATE),('BRAIN_VAULT',cfg.VAULT),('BRAIN_PORT',cfg.PORT)]:
        monkeypatch.setenv(name,str(value))


def test_foreign_listener_prevents_token_read_in_adapter_and_cli(device,monkeypatch):
    def foreign():raise processes.OwnershipError('foreign listener')
    monkeypatch.setattr(processes,'listener_identity',foreign)
    # No client-access.json exists: a credential access would instead fail FileNotFoundError.
    with pytest.raises(processes.OwnershipError,match='foreign listener'):client.ensure_service()
    with pytest.raises(processes.OwnershipError,match='foreign listener'):asyncio.run(cli.mcp_call('brain_health',{}))


def test_automatic_start_never_enables_disabled_task(device,monkeypatch):
    monkeypatch.setattr(service.sys,'platform','win32')
    monkeypatch.setattr(service,'_windows_task',lambda:{'enabled':False})
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:pytest.fail('native task mutation'))
    with pytest.raises(processes.OwnershipError,match='explicitly stopped'):service.start()


def test_transaction_requires_uuid_and_blocks_automatic_start(device,monkeypatch):
    hold='a'*32
    (cfg.STATE/'service-transaction.json').write_text(json.dumps({'hold_id':hold,'root':str(cfg.ROOT),'stage':'stopped'}))
    monkeypatch.setattr(service,'_start_native',lambda:pytest.fail('native start'))
    with pytest.raises(processes.OwnershipError,match='owns this runtime'):service.start(explicit=True)
    with pytest.raises(processes.OwnershipError,match='Automatic'):service.start(hold_id=hold)
    assert not service.spawn_allowed()


def test_finish_refuses_listener_older_than_quiescence(device,monkeypatch):
    hold='a'*32
    path=cfg.STATE/'service-transaction.json'
    path.write_text(json.dumps({'hold_id':hold,'root':str(cfg.ROOT),'stage':'started','started_at_filetime':510,'empty_at_filetime':500}))
    monkeypatch.setattr(processes,'listener_identity',lambda:dict(birth=600,supervisor_birth=400))
    with pytest.raises(processes.OwnershipError,match='predates'):service.finish_transaction(hold,cfg.DEVICE.get('version'))
    assert path.exists()


def test_legacy_reindex_refused_before_native_end(device,monkeypatch):
    monkeypatch.setattr(service.sys,'platform','win32')
    monkeypatch.setattr(service,'_windows_task',lambda:None)
    worker=SimpleNamespace(role='index_worker',alive=lambda:True,close=lambda:None)
    monkeypatch.setattr(processes,'roots',lambda:[worker])
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:pytest.fail('native End'))
    with pytest.raises(processes.OwnershipError,match='legacy reindex'):service.stop()


def test_finish_prepared_transaction_cannot_release_old_listener(device,monkeypatch):
    path=cfg.STATE/'service-transaction.json'
    path.write_text(json.dumps({'hold_id':'b'*32,'root':str(cfg.ROOT),'stage':'prepared'}))
    monkeypatch.setattr(processes,'listener_identity',lambda:pytest.fail('prepared transaction inspected listener'))
    with pytest.raises(processes.OwnershipError,match='verified stop and start'):service.finish_transaction('b'*32)
    assert path.exists()


def test_stale_reused_parent_pid_does_not_authorize_descendant(device,monkeypatch):
    parent=SimpleNamespace(pid=10,birth=200,alive=lambda:True)
    row=SimpleNamespace(pid=11,info={'ppid':10})
    closed=[]
    api=SimpleNamespace(OpenProcess=lambda *args:99,WaitForSingleObject=lambda *args:258,CloseHandle=lambda value:closed.append(value))
    monkeypatch.setattr(processes,'_api',lambda:api)
    monkeypatch.setattr(processes,'_birth',lambda *args:100)
    monkeypatch.setattr(processes.psutil,'process_iter',lambda *args:[row])
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:SimpleNamespace(ppid=lambda:10))
    assert processes.descendants([parent])==[] and closed==[99]


def test_stop_waits_retained_exit_then_retries_unverified_rescan(device,monkeypatch):
    monkeypatch.setattr(service.sys,'platform','win32')
    monkeypatch.setattr(service,'_windows_task',lambda:None)
    class Owned:
        role='server';kind='direct';ticks=1
        def alive(self):
            result=self.ticks>0;self.ticks-=1;return result
        def terminate(self):pass
        def close(self):pass
    owned=Owned();calls=[]
    def roots():
        calls.append(1)
        if len(calls)==1:return [owned]
        if len(calls)==2:raise processes.OwnershipError('base still exiting')
        return []
    monkeypatch.setattr(processes,'roots',roots)
    monkeypatch.setattr(processes,'descendants',lambda value:[])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1))
    monkeypatch.setattr(service.time,'sleep',lambda value:None)
    receipt=service.stop(wait_seconds=1)
    assert receipt['status']=='stopped' and receipt['task_disabled'] is False and len(calls)==3


def test_disable_then_busy_writer_restores_enabled_state_and_failure_receipt(device,monkeypatch):
    from filelock import Timeout
    monkeypatch.setattr(service.sys,'platform','win32')
    monkeypatch.setattr(service,'_windows_task',lambda:{'enabled':True})
    calls=[]
    monkeypatch.setattr(service.subprocess,'run',lambda command,**kwargs:(calls.append(command) or SimpleNamespace(returncode=0)))
    class Lock:
        def __init__(self,path,**kwargs):self.path=path
        def acquire(self):pass
        def release(self):pass
        def __enter__(self):raise Timeout(self.path)
        def __exit__(self,*args):pass
    monkeypatch.setattr(service,'FileLock',Lock)
    with pytest.raises(processes.OwnershipError,match='writer admission'):service.stop(wait_seconds=1)
    receipt=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert receipt['status']=='stop failed' and receipt['prior_enabled_restored'] and not receipt['task_disabled'] and not receipt['end_attempted']
    assert [command[-1] for command in calls]==['/Disable','/Enable']


@pytest.mark.parametrize('change',['two-actions','HighestAvailable','unknown','',' ','duplicate','missing-context'])
def test_task_xml_foreign_security_shape_refused(device,monkeypatch,change):
    import xml.etree.ElementTree as ET
    definition=ET.fromstring(service.definition('win32'))
    if change=='two-actions':definition.find('{*}Actions').append(ET.Element('Exec'))
    elif change=='missing-context':
        definition.find('{*}Actions').attrib.pop('Context')
        definition.find('{*}Principals/{*}Principal').attrib.pop('id')
    elif change=='duplicate':
        ET.SubElement(definition.find('{*}Principals/{*}Principal'),'{http://schemas.microsoft.com/windows/2004/02/mit/task}RunLevel').text='HighestAvailable'
    else:definition.find('{*}Principals/{*}Principal/{*}RunLevel').text=change
    value={'exists':True,'xml':ET.tostring(definition,encoding='unicode'),'owner_sid':'S-1-5-1','current_sid':'S-1-5-1'}
    monkeypatch.setattr(service,'_scheduled_module',lambda:cfg.ROOT/'mock-ScheduledTasks.psd1')
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(value)))
    with pytest.raises(processes.OwnershipError,match='actions|security context'):service._windows_task()


@pytest.mark.parametrize('enabled',[True,False])
def test_task_export_omits_default_run_level_and_enabled(device,monkeypatch,enabled):
    import xml.etree.ElementTree as ET
    definition=ET.fromstring(service.definition('win32'))
    principal=definition.find('{*}Principals/{*}Principal')
    principal.find('{*}UserId').text='S-1-5-21-1001'
    principal.remove(principal.find('{*}RunLevel'))
    settings=definition.find('{*}Settings')
    if enabled:settings.remove(settings.find('{*}Enabled'))
    else:settings.find('{*}Enabled').text='false'
    ET.SubElement(settings,'{http://schemas.microsoft.com/windows/2004/02/mit/task}IdleSettings')
    definition.remove(definition.find('{*}Triggers'))
    ET.SubElement(definition,'{http://schemas.microsoft.com/windows/2004/02/mit/task}Triggers')
    value={'exists':True,'state':'Ready' if enabled else 'Disabled',
           'xml':'<?xml version="1.0" encoding="UTF-16"?>'+ET.tostring(definition,encoding='unicode'),
           'owner_sid':'S-1-5-21-1001','current_sid':'S-1-5-21-1001'}
    monkeypatch.setattr(service,'_scheduled_module',lambda:cfg.ROOT/'mock-ScheduledTasks.psd1')
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(value)))
    result=service._windows_task()
    assert result['enabled'] is enabled and result['trigger_count']==0 and 'xml' not in result
    value['state']='Disabled' if enabled else 'Ready'
    with pytest.raises(processes.OwnershipError,match='disagrees'):service._windows_task()


def test_http_request_hook_rechecks_foreign_reconnect_before_transport(device,monkeypatch):
    import httpx2
    calls=[]
    def identity():
        calls.append(1)
        if len(calls)>1:raise processes.OwnershipError('foreign reconnect')
        return {'pid':1}
    monkeypatch.setattr(processes,'listener_identity',identity)
    request=httpx2.Request('POST',f'http://127.0.0.1:{cfg.PORT}/mcp')
    asyncio.run(client.verify_request(request))
    with pytest.raises(processes.OwnershipError,match='foreign reconnect'):asyncio.run(client.verify_request(request))
    with pytest.raises(processes.OwnershipError,match='unexpected MCP destination'):asyncio.run(client.verify_request(httpx2.Request('POST','https://example.invalid/mcp')))


@pytest.mark.parametrize('transient',[PermissionError('registry temporarily sharing-locked'),ValueError('partial registry read')])
def test_readiness_retries_transient_registry_reads_but_not_foreign_identity(device,monkeypatch,transient):
    identity={'birth':20,'supervisor_birth':20};calls=[]
    def lookup():
        calls.append(1)
        if len(calls)==1:raise transient
        return identity
    monkeypatch.setattr(processes,'listener_identity',lookup)
    monkeypatch.setattr(service,'_heartbeat',lambda *args:'verified')
    monkeypatch.setattr(service.time,'sleep',lambda *args:None)
    assert service._wait_started(1,10,False,service.time.monotonic()+2)==(identity,'verified')
    assert len(calls)==2
    def foreign():raise processes.OwnershipError('foreign listener')
    monkeypatch.setattr(processes,'listener_identity',foreign)
    with pytest.raises(processes.OwnershipError,match='foreign listener'):
        service._wait_started(1,10,False,service.time.monotonic()+2)


def test_task_inspection_refusal_creates_no_transaction(device,monkeypatch):
    def foreign():raise processes.OwnershipError('task belongs to another root')
    monkeypatch.setattr(service,'_windows_task',foreign)
    with pytest.raises(processes.OwnershipError,match='another root'):service.inspect()
    assert not (cfg.STATE/'service-transaction.json').exists()


@pytest.mark.parametrize('failure',[processes.OwnershipError,KeyboardInterrupt])
def test_readiness_failure_records_failed_stage_and_blocks_spawns(device,monkeypatch,failure):
    hold='c'*32;path=cfg.STATE/'service-transaction.json'
    path.write_text(json.dumps({'hold_id':hold,'root':str(cfg.ROOT),'stage':'stopped','empty_at_filetime':1}))
    monkeypatch.setattr(processes,'listener_identity',lambda:None)
    monkeypatch.setattr(service,'_windows_task',lambda:{'enabled':True})
    monkeypatch.setattr(service,'_start_native',lambda:None)
    def reject(*args):raise failure('synthetic readiness interruption')
    monkeypatch.setattr(service,'_wait_started',reject)
    with pytest.raises(processes.OwnershipError,match='Startup readiness failed'):service.start(explicit=True,hold_id=hold)
    assert json.loads(path.read_text())['stage']=='start failed' and not service.spawn_allowed()
    status=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert status['status']=='start failed' and status['error_type']==failure.__name__


def test_pre_end_refusal_cancels_only_unchanged_transaction(device,monkeypatch):
    import hashlib
    hold='d'*32;saved=cfg.ROOT/'backups/fixture';saved.mkdir(parents=True)
    (cfg.ROOT/'device.json').write_bytes(b'{"fixture":true}')
    (cfg.ROOT/'app').mkdir()
    snapshot={'files':[{'before_sha256':hashlib.sha256(b'{"fixture":true}').hexdigest()}],'app_sha256':{}}
    raw=json.dumps(snapshot).encode();(saved/'snapshot.json').write_bytes(raw)
    (cfg.STATE/'service-transaction.json').write_text(json.dumps({'hold_id':hold,'root':str(cfg.ROOT),'stage':'prepared','snapshot':str(saved),'snapshot_sha256':hashlib.sha256(raw).hexdigest()}))
    status={'status':'stop failed','hold_id':hold,'end_attempted':False,'prior_task_enabled':True}
    (cfg.STATE/'service-control-status.json').write_text(json.dumps(status))
    monkeypatch.setattr(service,'_windows_task',lambda:{'enabled':True})
    result=service.cancel_transaction(hold)
    assert result['status']=='cancelled; runtime not switched'
    assert not (cfg.STATE/'service-transaction.json').exists()
    assert (cfg.STATE/'service-transactions'/(hold+'.json')).exists()
    assert json.loads((cfg.STATE/'service-control-status.json').read_text())['transaction_cancelled']


def test_supervisor_held_does_not_query_listener_or_spawn(device,monkeypatch):
    import brain_supervisor as supervisor
    class Done(BaseException):pass
    (cfg.STATE/'service-transaction.json').write_text(json.dumps({'hold_id':'a'*32,'root':str(cfg.ROOT),'stage':'stopped'}))
    monkeypatch.setattr(processes,'listener_identity',lambda:pytest.fail('listener queried during hold'))
    monkeypatch.setattr(supervisor.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('worker spawn'))
    monkeypatch.setattr(supervisor.time,'sleep',lambda seconds:(_ for _ in ()).throw(Done()))
    with pytest.raises(Done):supervisor.main()
    assert json.loads((cfg.STATE/'service-heartbeat.json').read_text())['status']=='held'
