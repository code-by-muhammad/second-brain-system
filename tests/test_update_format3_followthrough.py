"""Real format3/snapshot/CAS/order; the OS/controller boundary is explicit fake."""
import hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_portable_remediation import portable
from upgrade_fixture import prepare

@pytest.fixture
def h(portable,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'update.py'
    spec=importlib.util.spec_from_file_location('format3_actual_update',path)
    update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)
    install,_,root,vault,home=portable
    state=prepare((install,update,root,vault,home),monkeypatch,previous_version='0.1.7',target_version='0.2.0')
    # This is a fake registry observation, not a native ownership proof. Native
    # service tests validate the descriptor producer and XML independently.
    registration={'kind':sys.platform,'definition_sha256':'a'*64,'enabled':True,'interpreter':state.device['python']}
    if sys.platform=='win32':registration.update(xml='<Task/>',owner_sid='S-1-5-21-101-102-103-1001')
    state.registration=registration;original=update.controller
    def controller(python,checkout,selected,command,action=None,*flags):
        result=original(python,checkout,selected,command,action,*flags)
        if command=='service' and action=='inspect':result['registration']=dict(state.registration)
        if command=='service' and action=='install':result.update(restored='sealed-restored',definition_sha256=state.registration['definition_sha256'])
        return result
    monkeypatch.setattr(update,'controller',controller)
    return state

def previous(h):return h.root/'state/previous-release.json'
def set_before(h,raw=b'{"owner":"original metadata"}\n'):
    h.install.atomic(previous(h),raw);return raw
def seal(h):
    return h.updater.snapshot(h.root,sys.executable,registration=h.registration,
                             controller_checkout=h.release,controller_identity={'tag':'v0.2.0'})
def images(h,saved):
    value=h.updater._snapshot(h.root,saved)
    return h.updater._binding_images(saved,value)

def test_native_inspect_precedes_seal_and_hold(h,monkeypatch):
    original=h.updater.snapshot
    def snapshot(*args,**options):h.events.append({'kind':'snapshot'});return original(*args,**options)
    monkeypatch.setattr(h.updater,'snapshot',snapshot)
    result=h.updater.perform(h.root,'v0.2.0')
    assert result['status']=='updated'
    inspection=next(i for i,e in enumerate(h.events) if e.get('action')=='inspect')
    sealing=next(i for i,e in enumerate(h.events) if e.get('kind')=='snapshot')
    hold=next(i for i,e in enumerate(h.events) if e.get('action')=='hold')
    assert inspection<sealing<hold
    value=h.updater._snapshot(h.root,result['snapshot'])
    assert value['format']==3 and value['registration']==h.registration

def test_other_selected_interpreter_refused_before_snapshot_or_hold(h):
    h.registration['interpreter']=str(h.root/'unrelated/.venv/python')
    with pytest.raises(RuntimeError,match='before a hold'):h.updater.perform(h.root,'v0.2.0')
    assert not any(e.get('action')=='hold' for e in h.events)
    assert not (h.root/'state/service-transaction.json').exists()

@pytest.mark.parametrize('raw',[None,b'{"older":"owner retained"}\n'])
def test_binding_images_are_sealed_and_restore_is_replay_safe(h,raw):
    if raw is not None:set_before(h,raw)
    saved=seal(h);before,after=images(h,saved)
    assert before==raw
    assert h.updater._restore_previous_binding(h.root,saved)['status']=='original binding retained'
    h.install.atomic(previous(h),after)
    assert h.updater._restore_previous_binding(h.root,saved)['status']=='original binding restored'
    assert (previous(h).read_bytes() if previous(h).exists() else None)==raw
    assert h.updater._restore_previous_binding(h.root,saved)['status']=='original binding retained'

def test_unknown_binding_edit_is_not_overwritten(h):
    set_before(h);saved=seal(h);original=b'{"owner":"concurrent edit"}'
    previous(h).write_bytes(original)
    with pytest.raises(ValueError,match='active hold'):h.updater._restore_previous_binding(h.root,saved)
    assert previous(h).read_bytes()==original

@pytest.mark.parametrize('name',['previous-binding.before','previous-binding.after'])
def test_corrupt_binding_image_is_refused_before_root_change(h,name):
    before=set_before(h);saved=seal(h);(saved/name).write_bytes(b'corrupt saved metadata')
    with pytest.raises(ValueError,match='binding bytes'):h.updater._snapshot(h.root,saved)
    assert previous(h).read_bytes()==before

def test_legacy_unsealed_format2_is_not_adopted(h):
    saved=seal(h);path=saved/'snapshot.json';value=json.loads(path.read_bytes());value['format']=2
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='Unsupported snapshot'):h.updater._snapshot(h.root,saved)

def test_rollback_restores_native_before_binding_and_binding_before_start(h,monkeypatch):
    before=set_before(h);h.fail_health=True;original=h.updater.controller;seen=[]
    def controller(python,checkout,root,command,action=None,*flags):
        old=json.loads((root/'device.json').read_bytes())['version']=='0.1.7'
        if old and action=='install':
            assert previous(h).read_bytes()!=before;seen.append('native')
        if old and action=='start':
            assert previous(h).read_bytes()==before;seen.append('start')
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.2.0')
    assert seen==['native','start'] and previous(h).read_bytes()==before
    assert not (h.root/'state/service-transaction.json').exists()

def test_binding_is_restored_before_pre_stop_cancel_releases_hold(h,monkeypatch):
    before=set_before(h);original=h.updater.controller
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='stop':raise RuntimeError('synthetic controller refused before stop entry')
        if action=='cancel':assert previous(h).read_bytes()==before
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='runtime switch'):h.updater.perform(h.root,'v0.2.0')
    assert previous(h).read_bytes()==before and not (h.root/'state/service-transaction.json').exists()

def test_not_switched_recovery_never_reinstalls_native_registration(h,monkeypatch):
    before=set_before(h);original=h.updater._work_locks;once=[True]
    def locks(*args,**options):
        if once[0]:once[0]=False;raise RuntimeError('synthetic work-lock admission refusal')
        return original(*args,**options)
    monkeypatch.setattr(h.updater,'_work_locks',locks)
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.2.0')
    assert not any(e.get('action')=='install' for e in h.events)
    assert previous(h).read_bytes()==before

def test_explicit_recovery_restores_sealed_binding(h,monkeypatch):
    before=set_before(h);h.fail_health=True;original=h.updater.controller;stops=[]
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='stop':
            stops.append(True)
            if len(stops)==2:raise RuntimeError('synthetic auto-rollback stop refusal')
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='recovery is incomplete'):h.updater.perform(h.root,'v0.2.0')
    tx=json.loads((h.root/'state/service-transaction.json').read_bytes())
    monkeypatch.setattr(h.updater,'controller',original)
    with h.updater.update_lock(h.root):result=h.updater.recover(h.root,tx['hold_id'])
    assert result['status']=='recovered' and previous(h).read_bytes()==before
    assert not (h.root/'state/service-transaction.json').exists()

def test_binding_restore_failure_retains_findable_recovery_hold(h,monkeypatch):
    set_before(h);h.fail_health=True;original=h.updater.controller
    def controller(python,checkout,root,command,action=None,*flags):
        result=original(python,checkout,root,command,action,*flags)
        if action=='install' and json.loads((root/'device.json').read_bytes())['version']=='0.1.7':
            previous(h).write_bytes(b'{"owner":"changed during rollback"}')
        return result
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='recovery is incomplete'):h.updater.perform(h.root,'v0.2.0')
    tx=json.loads((h.root/'state/service-transaction.json').read_bytes())
    status=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    assert status['hold_id']==tx['hold_id'] and status['status']=='recovery required'
    assert not any(e.get('action')=='finish' for e in h.events)
    assert sum(e.get('action')=='start' for e in h.events)==1

def test_unknown_binding_edit_refuses_recovery_before_stop(h):
    saved=seal(h);previous(h).write_bytes(b'{"owner":"concurrent edit"}')
    with pytest.raises(ValueError,match='before stopping'):
        h.updater._restart_previous(h.root,saved,sys.executable,h.release,'a'*32)
    assert not h.events

@pytest.mark.parametrize('field,value',[
    ('enabled',1),('definition_sha256','A'*64),('kind','unsupported'),
    ('extra','unsupported'),('interpreter','different'),
])
def test_registration_malformed_refused_before_sealing(h,field,value):
    existing=set((h.root/'backups').iterdir())
    h.registration[field]=value
    with pytest.raises(ValueError):seal(h)
    assert set((h.root/'backups').iterdir())==existing

@pytest.mark.parametrize('receipt',[{}, {'restored':'sealed-restored','definition_sha256':'b'*64}])
def test_unverified_native_restore_never_changes_binding_or_starts(h,monkeypatch,receipt):
    saved=seal(h);_,after=images(h,saved);h.install.atomic(previous(h),after)
    original=h.updater.controller
    def controller(python,checkout,root,command,action=None,*flags):
        result=original(python,checkout,root,command,action,*flags)
        return dict(receipt) if action=='install' else result
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(ValueError,match='registration restoration'):
        h.updater._restart_previous(h.root,saved,sys.executable,h.release,'a'*32)
    assert previous(h).read_bytes()==after
    assert not any(e.get('action') in ('start','finish') for e in h.events)

@pytest.mark.skipif(sys.platform!='win32',reason='Windows path equality differs from exact strings')
def test_case_alias_of_interpreter_refused_before_backup(h):
    existing=set((h.root/'backups').iterdir());original=h.registration['interpreter']
    h.registration['interpreter']=original.swapcase()
    assert Path(h.registration['interpreter'])==Path(original)
    with pytest.raises(ValueError):seal(h)
    assert set((h.root/'backups').iterdir())==existing

def test_equivalent_path_spelling_refused_before_backup(h):
    existing=set((h.root/'backups').iterdir());original=h.registration['interpreter']
    h.registration['interpreter']=original+'/'
    assert Path(h.registration['interpreter'])==Path(original)
    with pytest.raises(ValueError):seal(h)
    assert set((h.root/'backups').iterdir())==existing

@pytest.mark.skipif(sys.platform!='win32',reason='Windows-only SID metadata')
@pytest.mark.parametrize('sid',['S-1--','S-1-5','S-1-5-٢١'])
def test_malformed_sid_is_refused_before_backup(h,sid):
    existing=set((h.root/'backups').iterdir());h.registration['owner_sid']=sid
    with pytest.raises(ValueError):seal(h)
    assert set((h.root/'backups').iterdir())==existing

def test_pre_stop_binding_refusal_records_real_cause_and_keeps_hold(h,monkeypatch):
    set_before(h);original=h.updater.controller;edited=b'{"owner":"new binding"}'
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='stop':
            previous(h).write_bytes(edited)
            raise RuntimeError('synthetic pre-stop refusal')
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='pre-stop refusal'):h.updater.perform(h.root,'v0.2.0')
    tx=json.loads((h.root/'state/service-transaction.json').read_bytes())
    status=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    assert status['status']=='recovery required' and status['hold_id']==tx['hold_id']
    assert status['recovery_error_type']=='ValueError' and previous(h).read_bytes()==edited
    assert not any(e.get('action')=='cancel' for e in h.events)
    monkeypatch.setattr(h.updater,'controller',original);h.events.clear()
    with h.updater.update_lock(h.root),pytest.raises(ValueError,match='before stopping'):
        h.updater.recover(h.root,tx['hold_id'])
    assert not any(e.get('action')=='stop' for e in h.events)
    assert json.loads((h.root/'state/service-transaction.json').read_bytes())['hold_id']==tx['hold_id']
    assert json.loads((h.root/'state/upgrade-status.json').read_bytes())['status']=='recovery required'
