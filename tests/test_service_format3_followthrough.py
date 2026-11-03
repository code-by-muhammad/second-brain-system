"""Synthetic format3 lifecycle controls: every native operation is fake."""
import hashlib,json,types
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_process as processes

if Path(__file__).parent.name=='tests':
    import brain_service as service
else:
    P=Path(__file__).with_name('brain_service.py')
    module=types.ModuleType('proposed_service');module.__file__=str(P)
    exec(compile(P.read_bytes(),str(P),'exec'),module.__dict__)
    service=module
HOLD='a'*32
FP='b'*64

@pytest.fixture
def synthetic(brain,monkeypatch):
    monkeypatch.setattr(service,'sys',types.SimpleNamespace(platform='win32'))
    monkeypatch.setattr(service,'_qualified_native_platform',lambda **kwargs:None)
    monkeypatch.setattr(processes,'validate_environment',lambda **kwargs:None)
    monkeypatch.setattr(processes,'roots',lambda:[])
    monkeypatch.setattr(processes,'descendants',lambda value:[])
    monkeypatch.setattr(processes,'_listeners',lambda:[])
    monkeypatch.setattr(service,'TASK','SecondBrain-Test-'+'d'*32)
    launcher=str(cfg.PYTHON.with_name('pythonw.exe'))
    monkeypatch.setattr(processes,'_launchers',lambda:{processes._path(launcher):launcher})
    owned=service._windows_definition({'xml':service.definition('win32'),'owner_sid':'S-1-5-21-123','current_sid':'S-1-5-21-123'})
    app=cfg.ROOT/'app';app.mkdir(exist_ok=True);(app/'core.py').write_bytes(b'# synthetic only\n')
    device=cfg.ROOT/'device.json';device.write_text(json.dumps({'python':str(cfg.PYTHON)}))
    target=cfg.ROOT/'backups/synthetic';target.mkdir(parents=True)
    snap={'format':3,'root':str(cfg.ROOT),'previous':{'python':str(cfg.PYTHON)},
        'files':[{'path':str(device),'before_sha256':hashlib.sha256(device.read_bytes()).hexdigest()}],
        'app_sha256':{'core.py':hashlib.sha256((app/'core.py').read_bytes()).hexdigest()},
        'registration':{'kind':'win32','enabled':True,'definition_sha256':owned['definition_sha256'],'interpreter':str(cfg.PYTHON),'xml':owned['xml'],'owner_sid':'S-1-5-21-123'}}
    (target/'0').write_bytes(device.read_bytes())
    (target/'app').mkdir();(target/'app/core.py').write_bytes((app/'core.py').read_bytes())
    forward=b'synthetic-forward-binding';(target/'previous-binding.after').write_bytes(forward)
    snap['previous_binding']={'before_sha256':None,'after_sha256':hashlib.sha256(forward).hexdigest()}
    def save():
        raw=json.dumps(snap).encode();(target/'snapshot.json').write_bytes(raw)
        return {'hold_id':HOLD,'root':str(cfg.ROOT),'stage':'prepared','stop_entered':False,
            'snapshot':str(target),'snapshot_sha256':hashlib.sha256(raw).hexdigest()}
    value=save();(cfg.STATE/'service-transaction.json').write_text(json.dumps(value))
    native={**owned,'state':'Ready','trigger_count':0}
    monkeypatch.setattr(service,'_windows_task',lambda **kwargs:dict(native))
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if '/Disable' in command:native['enabled']=False
        if '/Enable' in command:native['enabled']=True
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess,'run',run)
    monkeypatch.setattr(service,'_tool',lambda name:name)
    return value,snap,native,calls,save


def write_hold(value):
    (cfg.STATE/'service-transaction.json').write_text(json.dumps(value))


def test_never_entered_stop_can_cancel_original_runtime(synthetic):
    value,snap,native,calls,save=synthetic
    assert service.cancel_transaction(HOLD)['status']=='cancelled; runtime not switched'
    assert not (cfg.STATE/'service-transaction.json').exists() and calls==[]

@pytest.mark.parametrize('marker',[None,0,'false'])
def test_untyped_stop_marker_preserves_hold(synthetic,marker):
    value,snap,native,calls,save=synthetic;value['stop_entered']=marker;write_hold(value)
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists() and calls==[]

@pytest.mark.parametrize('change',['definition','enabled','app','device'])
def test_unentered_cancel_requires_original_all_boundaries(synthetic,change):
    value,snap,native,calls,save=synthetic
    if change=='definition':native['definition_sha256']='c'*64
    if change=='enabled':native['enabled']=False
    if change=='app':(cfg.ROOT/'app/core.py').write_bytes(b'owner change')
    if change=='device':(cfg.ROOT/'device.json').write_bytes(b'{}')
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists() and calls==[]


def test_matching_receipt_contradicts_unentered_marker(synthetic):
    value,snap,native,calls,save=synthetic
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'hold_id':HOLD,'status':'stop failed','end_attempted':False,'activation_disable_attempted':False}))
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists() and calls==[]


def test_preflight_query_failure_cancellation_is_rechecked(synthetic,monkeypatch):
    value,snap,native,calls,save=synthetic
    def failure():raise processes.OwnershipError('synthetic query failure')
    monkeypatch.setattr(service,'_windows_task',failure)
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    recorded=json.loads((cfg.STATE/'service-transaction.json').read_text())
    assert recorded['stop_entered'] is True and calls==[]
    monkeypatch.setattr(service,'_windows_task',lambda **kwargs:dict(native))
    service.cancel_transaction(HOLD)
    assert not (cfg.STATE/'service-transaction.json').exists()


def test_first_stop_sealed_registration_change_precedes_mutation(synthetic):
    value,snap,native,calls,save=synthetic;native['definition_sha256']='c'*64
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert calls==[] and json.loads((cfg.STATE/'service-transaction.json').read_text())['stop_entered'] is True

@pytest.mark.parametrize('kind',['prepared','starting','started'])
def test_windows_install_requires_positive_stopped_receipt(synthetic,kind):
    value,snap,native,calls,save=synthetic;value['stage']=kind;write_hold(value)
    with pytest.raises(processes.OwnershipError):service.install()
    assert calls==[]


def test_format2_hold_is_not_silently_inferred(synthetic):
    value,snap,native,calls,save=synthetic;snap['format']=2;write_hold(save())
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert calls==[] and (cfg.STATE/'service-transaction.json').exists()


def test_disabled_diagnostic_descriptor_retained(synthetic):
    value,snap,native,calls,save=synthetic;native['enabled']=False
    result=service.inspect()
    assert result['task_present'] is True and result['task_enabled'] is False
    assert result['registration']=={'kind':'win32','definition_sha256':snap['registration']['definition_sha256'],'enabled':False,'interpreter':str(cfg.PYTHON),'xml':snap['registration']['xml'],'owner_sid':'S-1-5-21-123'}


def test_absent_diagnostic_descriptor_is_none(synthetic,monkeypatch):
    monkeypatch.setattr(service,'_windows_task',lambda **kwargs:None)
    result=service.inspect();assert result['task_present'] is False and result['registration'] is None


@pytest.mark.parametrize('wrong',['hash','interpreter','unknown'])
def test_mac_sealed_restore_authority_refuses_before_native_work(synthetic,monkeypatch,wrong):
    if Path(__file__).parent.name=='tests':
        import brain_mac_registration as mod
    else:
        path=Path(__file__).with_name('brain_mac_registration.py')
        mod=types.ModuleType('proposed_mac');mod.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),mod.__dict__)
    obj=mod.MacRegistration.__new__(mod.MacRegistration)
    legacy=b'synthetic-known-original';obj.installable=b'synthetic-new';obj.known={legacy:[str(cfg.PYTHON),'fixture']};obj.current_python=str(cfg.PYTHON)
    digest=hashlib.sha256(legacy).hexdigest()
    if wrong=='hash':digest='c'*64
    if wrong=='interpreter':obj.current_python='/different/python'
    if wrong=='unknown':obj.known={}
    obj.inspect=lambda:pytest.fail('invalid restore reached native inspection')
    with pytest.raises(mod.RegistrationError):obj.install(legacy,enabled=False,restore_sha256=digest)


def test_mac_known_original_restore_proceeds_to_existing_native_checks(synthetic):
    if Path(__file__).parent.name=='tests':
        import brain_mac_registration as mod
    else:
        path=Path(__file__).with_name('brain_mac_registration.py')
        mod=types.ModuleType('proposed_mac');mod.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),mod.__dict__)
    obj=mod.MacRegistration.__new__(mod.MacRegistration)
    legacy=b'synthetic-known-original';obj.installable=b'synthetic-new';obj.known={legacy:[str(cfg.PYTHON),'fixture']};obj.current_python=str(cfg.PYTHON)
    class InspectionReached(Exception):pass
    def observe():raise InspectionReached()
    obj.inspect=observe
    with pytest.raises(InspectionReached):obj.install(legacy,enabled=False,restore_sha256=hashlib.sha256(legacy).hexdigest())


@pytest.mark.parametrize('change',['missing_xml','nonstring_hash','missing_interpreter','extra','sid_mismatch','xml_hash_mismatch'])
def test_malformed_registration_refused_before_new_hold(synthetic,change):
    value,snap,native,calls,save=synthetic
    if change=='missing_xml':snap['registration'].pop('xml')
    if change=='nonstring_hash':snap['registration']['definition_sha256']=123
    if change=='missing_interpreter':snap['registration']['interpreter']=None;snap['previous']['python']=None
    if change=='extra':snap['registration']['unexpected']='no'
    if change=='sid_mismatch':snap['registration']['owner_sid']='S-1-5-21-999'
    if change=='xml_hash_mismatch':snap['registration']['xml']=snap['registration']['xml'].replace('PT0S','PT7M')
    value=save();(cfg.STATE/'service-transaction.json').unlink()
    with pytest.raises(processes.OwnershipError):service.begin_transaction(HOLD,Path(value['snapshot']))
    assert not (cfg.STATE/'service-transaction.json').exists() and calls==[]

@pytest.mark.parametrize('image',['device','app','binding'])
def test_snapshot_images_refused_before_stop_action(synthetic,image):
    value,snap,native,calls,save=synthetic;target=Path(value['snapshot'])
    path=target/'0' if image=='device' else target/'app/core.py' if image=='app' else target/'previous-binding.after'
    path.write_bytes(b'changed synthetic backup')
    with pytest.raises(processes.OwnershipError):service.stop(hold_id=HOLD,wait_seconds=1)
    assert calls==[] and (cfg.STATE/'service-transaction.json').exists()


def test_stop_marker_is_durable_inside_first_native_query(synthetic,monkeypatch):
    value,snap,native,calls,save=synthetic;seen=[]
    def observe():
        seen.append(json.loads((cfg.STATE/'service-transaction.json').read_text())['stop_entered'])
        return dict(native)
    monkeypatch.setattr(service,'_windows_task',observe)
    service.stop(hold_id=HOLD,wait_seconds=1)
    assert seen and all(x is True for x in seen)

@pytest.mark.parametrize('status',[None,{'status':'stop failed','end_attempted':True,'activation_disable_attempted':True},
    {'status':'stop preflight failed','end_attempted':False,'activation_disable_attempted':True}])
def test_uncertain_or_contradictory_entry_cannot_cancel(synthetic,status):
    value,snap,native,calls,save=synthetic;value['stop_entered']=True;write_hold(value)
    if status is not None:(cfg.STATE/'service-control-status.json').write_text(json.dumps(dict(status,hold_id=HOLD)))
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert calls==[] and (cfg.STATE/'service-transaction.json').exists()


def test_original_binding_required_before_cancel(synthetic):
    value,snap,native,calls,save=synthetic
    (cfg.STATE/'previous-release.json').write_bytes(b'synthetic different binding')
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert calls==[] and (cfg.STATE/'service-transaction.json').exists()


def stopped(synthetic):
    value,snap,native,calls,save=synthetic;value.update(stage='stopped',empty_at_filetime=123)
    write_hold(value);native.update(enabled=False,state='Disabled')
    return value,snap,native,calls,save


def test_held_windows_running_disabled_refuses_replacement(synthetic):
    value,snap,native,calls,save=stopped(synthetic);native['state']='Running'
    with pytest.raises(processes.OwnershipError):service.install()
    assert calls==[]


def test_original_native_retained_without_registration_write(synthetic):
    value,snap,native,calls,save=stopped(synthetic)
    result=service.install()
    assert result['status']=='installed' and result['restored']=='sealed-retained'
    assert result['definition_sha256']==snap['registration']['definition_sha256']
    assert calls==[] and not (cfg.STATE/'service-task.xml').exists()


def test_windows_original_xml_import_disables_preserves_metadata(synthetic,monkeypatch):
    import xml.etree.ElementTree as ET
    value,snap,native,calls,save=stopped(synthetic);native['definition_sha256']='c'*64
    parsed=ET.fromstring(snap['registration']['xml']);ns='{http://schemas.microsoft.com/windows/2004/02/mit/task}'
    info=ET.Element(ns+'RegistrationInfo');ET.SubElement(info,ns+'Description').text='synthetic-preserved-owner-metadata';parsed.insert(0,info)
    parsed.find('{*}Settings/{*}ExecutionTimeLimit').text='PT7M'
    sealed=service._windows_definition({'xml':ET.tostring(parsed,encoding='unicode'),'owner_sid':'S-1-5-21-123','current_sid':'S-1-5-21-123'})
    snap['registration'].update(xml=sealed['xml'],definition_sha256=sealed['definition_sha256']);new=save();value['snapshot_sha256']=new['snapshot_sha256'];write_hold(value)
    observed={}
    def replace(command,**kwargs):
        calls.append(command);assert '/Create' in command and '/F' in command
        payload=Path(command[command.index('/XML')+1]).read_text(encoding='utf-16')
        result=service._windows_definition({'xml':payload,'owner_sid':'S-1-5-21-123','current_sid':'S-1-5-21-123'})
        observed.update(payload=payload,parsed=result);native.update(result,state='Disabled')
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess,'run',replace)
    receipt=service.install();payload=ET.fromstring(observed['payload'])
    assert receipt['restored']=='sealed-restored' and receipt['definition_sha256']==sealed['definition_sha256']
    assert observed['parsed']['enabled'] is False and observed['parsed']['definition_sha256']==sealed['definition_sha256']
    assert payload.findtext('{*}RegistrationInfo/{*}Description')=='synthetic-preserved-owner-metadata'
    assert payload.findtext('{*}Settings/{*}ExecutionTimeLimit')=='PT7M' and len(calls)==1


def test_windows_post_import_fingerprint_mismatch_preserves_hold(synthetic):
    value,snap,native,calls,save=stopped(synthetic);native['definition_sha256']='c'*64
    with pytest.raises(processes.OwnershipError):service.install()
    assert len(calls)==1 and '/Create' in calls[0]
    assert (cfg.STATE/'service-transaction.json').exists()


def forward(synthetic):
    value,snap,native,calls,save=stopped(synthetic)
    target=cfg.ROOT/'releases/v9.9.9';target.mkdir(parents=True)
    candidate=b'# synthetic forward app\n'
    manifest={'repository':'code-by-muhammad/second-brain-system','version':'9.9.9','sha256':{'app/core.py':hashlib.sha256(candidate).hexdigest()}}
    raw=json.dumps(manifest).encode();(target/'release-manifest.json').write_bytes(raw)
    snap['controller_checkout']=str(target);snap['controller_identity']={'repository':manifest['repository'],'tag':'v9.9.9','manifest_sha256':hashlib.sha256(raw).hexdigest()}
    device=json.dumps({'release_root':str(target),'python':str(cfg.PYTHON)}).encode()
    snap['files'][0]['after_sha256']=hashlib.sha256(device).hexdigest()
    new=save();value['snapshot_sha256']=new['snapshot_sha256'];write_hold(value)
    return value,snap,native,calls,device,candidate

@pytest.mark.parametrize('partial',['original_device','original_app','owner_app','manifest'])
def test_mixed_or_owner_modified_runtime_cannot_install(synthetic,partial):
    value,snap,native,calls,device,candidate=forward(synthetic)
    if partial!='original_device':(cfg.ROOT/'device.json').write_bytes(device)
    if partial!='original_app':(cfg.ROOT/'app/core.py').write_bytes(candidate)
    if partial=='owner_app':(cfg.ROOT/'app/core.py').write_bytes(b'owner app edit')
    if partial=='manifest':(Path(snap['controller_checkout'])/'release-manifest.json').write_bytes(b'{}')
    with pytest.raises(processes.OwnershipError):service.install()
    assert calls==[] and (cfg.STATE/'service-transaction.json').exists()


def test_exact_forward_app_device_selects_standard_definition(synthetic):
    value,snap,native,calls,device,candidate=forward(synthetic)
    (cfg.ROOT/'device.json').write_bytes(device);(cfg.ROOT/'app/core.py').write_bytes(candidate)
    service.install()
    assert len(calls)==1 and '/Create' in calls[0]


@pytest.mark.parametrize('prior_present',[None,False])
def test_contradictory_windows_stop_receipt_never_releases_hold(synthetic,prior_present):
    value,snap,native,calls,save=synthetic;value['stop_entered']=True;write_hold(value)
    status={'status':'stop failed','hold_id':HOLD,'root':str(cfg.ROOT),'task_address':service._task_address(),
        'activation_disable_attempted':False,'end_attempted':False,'prior_task_enabled':None,
        'prior_task_definition_sha256':None}
    if prior_present is not None:status['prior_task_present']=prior_present
    (cfg.STATE/'service-control-status.json').write_text(json.dumps(status))
    with pytest.raises(processes.OwnershipError):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists() and calls==[]


def test_fresh_cancel_query_failure_is_typed_and_preserves_hold(synthetic,monkeypatch):
    def unknown():raise OSError('synthetic native observation failure')
    monkeypatch.setattr(service,'_windows_task',unknown)
    with pytest.raises(processes.OwnershipError,match='cannot be verified'):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


@pytest.mark.parametrize('kind',['darwin','linux'])
@pytest.mark.parametrize('retained',[False,True])
def test_posix_original_restore_receipts_and_exact_descriptors(synthetic,monkeypatch,kind,retained):
    from service_format3_fixture import seed_hold
    monkeypatch.setattr(service,'sys',types.SimpleNamespace(platform=kind))
    if kind=='darwin':
        from brain_mac_registration import definitions
        known=definitions(service.LABEL,cfg.ROOT,[str(cfg.PYTHON)])
        original=next(raw for raw,argv in known.items() if len(argv)==2)
        newer=next(raw for raw,argv in known.items() if len(argv)>2)
    else:
        original=service.definition('linux').encode();selected=cfg.ROOT/'synthetic-selected-new/python.exe'
        with monkeypatch.context() as temporary:
            temporary.setattr(cfg,'PYTHON',selected);newer=service.definition('linux').encode()
        known={original:[str(cfg.PYTHON),str(cfg.ROOT/'app/brain_supervisor.py')],newer:[str(selected),str(cfg.ROOT/'app/brain_supervisor.py')]}
    sealed=hashlib.sha256(original).hexdigest();calls=[]
    state={'file_present':True,'loaded':False,'running':False,'enabled':False,'quiescent':True,
        'definition_sha256':sealed if retained else hashlib.sha256(newer).hexdigest()}
    class Registration:
        path=cfg.ROOT/'synthetic.native'
        def inspect(self,**kwargs):return dict(state)
        def install(self,payload,*,enabled,restore_sha256=None):
            assert payload==original and enabled is False
            if kind=='darwin':assert restore_sha256==sealed
            calls.append('install');state['definition_sha256']=hashlib.sha256(payload).hexdigest()
    registration=Registration();registration.known=known
    monkeypatch.setattr(service,'_mac_registration' if kind=='darwin' else '_linux_registration',lambda:registration)
    monkeypatch.setattr(processes,'listener_identity',lambda **kwargs:None)
    seed_hold(service,cfg,{'kind':kind,'definition_sha256':sealed,'enabled':True,'interpreter':str(cfg.PYTHON)},stage='stopped')
    result=service.install()
    assert result['restored']==('sealed-retained' if retained else 'sealed-restored') and result['definition_sha256']==sealed
    assert calls==([] if retained else ['install'])
    descriptor=service.inspect()['registration']
    assert descriptor=={'kind':kind,'definition_sha256':sealed,'enabled':False,'interpreter':str(cfg.PYTHON)}


@pytest.mark.parametrize('kind',['darwin','linux'])
def test_posix_prior_receipt_contradiction_preserves_original_hold(synthetic,monkeypatch,kind):
    from service_format3_fixture import seed_hold
    monkeypatch.setattr(service,'sys',types.SimpleNamespace(platform=kind))
    native={'enabled':True,'definition_sha256':'b'*64}
    registry=types.SimpleNamespace(inspect=lambda **kwargs:dict(native))
    monkeypatch.setattr(service,'_mac_registration' if kind=='darwin' else '_linux_registration',lambda:registry)
    seed_hold(service,cfg,{'kind':kind,'enabled':True,'definition_sha256':'b'*64,'interpreter':str(cfg.PYTHON)},stop_entered=True)
    (cfg.STATE/'service-control-status.json').write_text(json.dumps({'status':'stop failed','hold_id':HOLD,
        'activation_disable_attempted':True,'end_attempted':False,'prior_native':{'enabled':True,'definition_sha256':'c'*64}}))
    with pytest.raises(processes.OwnershipError,match='contradicts sealed original'):service.cancel_transaction(HOLD)
    assert (cfg.STATE/'service-transaction.json').exists()


def test_linux_sealed_retained_cannot_bypass_fresh_quiescence(synthetic,monkeypatch):
    from service_format3_fixture import seed_hold
    monkeypatch.setattr(service,'sys',types.SimpleNamespace(platform='linux'))
    payload=service.definition('linux').encode();fp=hashlib.sha256(payload).hexdigest()
    registry=types.SimpleNamespace(path=cfg.ROOT/'synthetic.native',
        inspect=lambda **kwargs:{'enabled':False,'file_present':True,'loaded':True,'running':True,
            'definition_sha256':fp,'quiescent':False},
        install=lambda *args,**kwargs:pytest.fail('active cgroup must not authorize installation'))
    monkeypatch.setattr(service,'_linux_registration',lambda:registry)
    seed_hold(service,cfg,{'kind':'linux','enabled':True,'definition_sha256':fp,'interpreter':str(cfg.PYTHON)},stage='stopped')
    with pytest.raises(processes.OwnershipError,match='verified disabled stopped'):service.install()
    assert (cfg.STATE/'service-transaction.json').exists()
