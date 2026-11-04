"""Pure finish/cancel reply-loss classification, never a native runtime proof."""
import json
from pathlib import Path
import pytest
from filelock import FileLock
from test_portable_remediation import portable


def prepare_case(portable,monkeypatch):
    from upgrade_fixture import prepare
    return prepare(portable,monkeypatch,previous_version='0.1.7',target_version='0.2.0')


def retain_known_failure(h,monkeypatch):
    original=h.updater.controller;stops=[]
    def failed_stop(python,checkout,root,command,action=None,*flags):
        if action=='stop':
            stops.append(True)
            if len(stops)==2:raise RuntimeError('synthetic rollback stop refusal with known hold')
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',failed_stop);h.fail_health=True
    with pytest.raises(RuntimeError,match='recovery is incomplete'):h.updater.perform(h.root,'v0.2.0')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    assert receipt['status']=='recovery required' and 'attempt_hold_id' not in receipt
    assert json.loads((h.root/'state/service-transaction.json').read_bytes())['hold_id']==receipt['hold_id']
    monkeypatch.setattr(h.updater,'controller',original)
    return receipt['hold_id']


@pytest.mark.parametrize('mode',['forward','automatic-rollback','manual-recover'])
def test_lost_finish_reply_never_advertises_nonexistent_recovery(portable,monkeypatch,tmp_path,mode):
    h=prepare_case(portable,monkeypatch);hold=None
    if mode=='manual-recover':hold=retain_known_failure(h,monkeypatch)
    elif mode=='automatic-rollback':h.fail_health=True
    original=h.updater.controller;path=h.root/'state/service-transaction.json';attempts=[];finished={}
    def controller(python,checkout,root,command,action=None,*flags):
        attempts.append(action)
        if action=='finish' and not finished:
            result=original(python,checkout,root,command,action,*flags)
            assert not path.exists()
            finished['device']=(h.root/'device.json').read_bytes()
            finished['app']={p.relative_to(h.root/'app').as_posix():p.read_bytes() for p in (h.root/'app').rglob('*.py')}
            raise RuntimeError('synthetic finish reply loss after unlink')
        if action in ('stop','install','start','finish','cancel') and not path.exists():
            raise RuntimeError('named update transaction does not exist')
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError):
        if mode=='manual-recover':h.updater.recover(h.root,hold)
        else:h.updater.perform(h.root,'v0.2.0')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    expected=(['stop','install','start','finish'] if mode=='manual-recover' else
        ['inspect','hold','stop','install','start','finish'] if mode=='forward' else
        ['inspect','hold','stop','install','start','stop','install','start','finish'])
    observed={'mode':mode,'status':receipt['status'],'hold_id':receipt.get('hold_id'),'attempt_hold_id':receipt.get('attempt_hold_id'),
        'active_hold_exists':path.exists(),'attempts':attempts,'expected_attempts':expected,'running':h.running,
        'device_version':json.loads((h.root/'device.json').read_bytes())['version'],
        'device_after_finish_preserved':(h.root/'device.json').read_bytes()==finished['device'],
        'app_after_finish_preserved':{p.relative_to(h.root/'app').as_posix():p.read_bytes() for p in (h.root/'app').rglob('*.py')}==finished['app']}
    (tmp_path/'finish-loss-observation.json').write_text(json.dumps(observed,indent=2))
    assert receipt['status']=='transaction outcome inspection required' and 'hold_id' not in receipt
    assert receipt['observed_transaction']=='absent'
    assert receipt['transaction_evidence_path']==str(h.root/'state/service-transactions'/(receipt['attempt_hold_id']+'.json'))
    assert len(receipt['attempt_hold_id'])==32 and not path.exists()
    assert attempts==expected and h.running and observed['device_after_finish_preserved'] and observed['app_after_finish_preserved']
    with pytest.raises((ValueError,OSError)):h.updater._recovery_binding(h.root,receipt['attempt_hold_id'])


def test_health_failure_automatic_rollback_remains_verified(portable,monkeypatch):
    h=prepare_case(portable,monkeypatch);h.fail_health=True
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.2.0')
    assert [r['action'] for r in h.events if r['kind']=='controller']==['inspect','hold','stop','install','start','stop','install','start','finish']
    assert h.health==['0.2.0','0.1.7'] and h.running
    assert json.loads((h.root/'state/upgrade-status.json').read_bytes())['status']=='update failed; previous runtime verified'


def test_failure_with_matching_active_hold_keeps_findable_recovery_uuid(portable,monkeypatch):
    h=prepare_case(portable,monkeypatch);hold=retain_known_failure(h,monkeypatch)
    saved,record,selected=h.updater._recovery_binding(h.root,hold)
    assert saved.exists() and selected==h.release and record['previous']['version']=='0.1.7'


def test_cancel_reply_loss_classifies_no_hold_without_runtime_switch(portable,monkeypatch,tmp_path):
    h=prepare_case(portable,monkeypatch);before=(h.root/'device.json').read_bytes();original=h.updater.controller
    path=h.root/'state/service-transaction.json';attempts=[]
    def controller(python,checkout,root,command,action=None,*flags):
        attempts.append(action);result=original(python,checkout,root,command,action,*flags)
        if action=='cancel':assert not path.exists();raise RuntimeError('synthetic cancel reply loss after unlink')
        return result
    monkeypatch.setattr(h.updater,'controller',controller)
    with FileLock(str(h.root/'state/writer.lock'),timeout=0):
        with pytest.raises(RuntimeError):h.updater.perform(h.root,'v0.2.0')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    (tmp_path/'cancel-loss-observation.json').write_text(json.dumps({'status':receipt['status'],'hold_id':receipt.get('hold_id'),'attempt_hold_id':receipt.get('attempt_hold_id'),'active_hold_exists':path.exists(),'attempts':attempts}))
    assert receipt['status']=='transaction outcome inspection required' and 'hold_id' not in receipt
    assert receipt['observed_transaction']=='absent'
    assert receipt['transaction_evidence_path']==str(h.root/'state/service-transactions'/(receipt['attempt_hold_id']+'.json'))
    assert not path.exists() and h.running and (h.root/'device.json').read_bytes()==before
    assert attempts==['inspect','hold','stop','cancel']


@pytest.mark.parametrize('damage,observed',[
    ('replaced-hold','mismatch'),('non-dict','mismatch'),('malformed','unreadable'),('absent','absent'),('unreadable','unreadable')])
def test_post_stop_untrusted_transaction_preserves_binding_without_rollback(portable,monkeypatch,damage,observed):
    from contextlib import contextmanager
    h=prepare_case(portable,monkeypatch);controller=h.updater.controller
    transaction=h.root/'state/service-transaction.json';binding=h.root/'state/previous-release.json'
    stopped=[];read=h.updater._read
    def control(python,checkout,root,command,action=None,*flags):
        result=controller(python,checkout,root,command,action,*flags)
        if action=='stop':
            stopped.append(binding.read_bytes())
            if damage=='replaced-hold':
                value=json.loads(transaction.read_bytes());value['hold_id']='f'*32;transaction.write_text(json.dumps(value))
            elif damage=='non-dict':transaction.write_text('[]')
            elif damage=='malformed':transaction.write_bytes(b'invalid synthetic JSON')
            elif damage=='absent':transaction.unlink()
        return result
    def read_transaction(path):
        if damage=='unreadable' and stopped and path==transaction:raise PermissionError('synthetic transaction read denied')
        return read(path)
    @contextmanager
    def no_work(*args,**kwargs):
        raise RuntimeError('synthetic post-stop work-lock failure before runtime switch')
        yield
    monkeypatch.setattr(h.updater,'controller',control)
    monkeypatch.setattr(h.updater,'_read',read_transaction)
    monkeypatch.setattr(h.updater,'_work_locks',no_work)
    device=(h.root/'device.json').read_bytes()
    with pytest.raises(RuntimeError,match='outcome is uncertain') as error:h.updater.perform(h.root,'v0.2.0')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    assert receipt['status']=='transaction outcome inspection required' and 'hold_id' not in receipt
    assert receipt['observed_transaction']==observed and receipt['pending_work']=='preserved'
    evidence=str(h.root/'state/service-transactions'/(receipt['attempt_hold_id']+'.json'))
    assert receipt['transaction_evidence_path']==evidence and evidence in str(error.value) and 'service recover' in str(error.value)
    assert binding.read_bytes()==stopped[0] and (h.root/'device.json').read_bytes()==device
    assert [r['action'] for r in h.events if r['kind']=='controller']==['inspect','hold','stop']


def test_equivalent_snapshot_spelling_does_not_suppress_health_rollback(portable,monkeypatch):
    h=prepare_case(portable,monkeypatch);controller=h.updater.controller;h.fail_health=True;aliases=[]
    def control(python,checkout,root,command,action=None,*flags):
        result=controller(python,checkout,root,command,action,*flags)
        if action=='hold':
            transaction=root/'state/service-transaction.json';value=json.loads(transaction.read_bytes())
            saved=Path(value['snapshot']);alias_parent=saved.parent/'plain-alias-parent';alias_parent.mkdir()
            alias=alias_parent/'..'/saved.name
            assert str(alias)!=str(saved) and alias.resolve()==saved.resolve()
            value['snapshot']=str(alias);transaction.write_text(json.dumps(value));aliases.append(True)
        return result
    monkeypatch.setattr(h.updater,'controller',control)
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.2.0')
    assert aliases==[True] and h.health==['0.2.0','0.1.7'] and h.running
    assert [r['action'] for r in h.events if r['kind']=='controller']==['inspect','hold','stop','install','start','stop','install','start','finish']
    assert json.loads((h.root/'state/upgrade-status.json').read_bytes())['status']=='update failed; previous runtime verified'


def test_snapshot_resolve_runtime_error_is_unreadable_without_outcome_claim(portable,monkeypatch):
    _,updater,root,_,_=portable;hold='a'*32;saved=root/'backups/synthetic-snapshot'
    value={'hold_id':hold,'root':str(root),'snapshot':str(saved)}
    monkeypatch.setattr(updater,'_read',lambda path:json.dumps(value).encode())
    class LoopPath:
        def resolve(self):raise RuntimeError('Synthetic Python3.12 symlink loop resolution failure')
    monkeypatch.setattr(updater,'Path',lambda value:LoopPath())
    result=updater._recovery_receipt_fields(root,hold,saved)
    assert result['status']=='transaction outcome inspection required' and result['observed_transaction']=='unreadable'
    assert result['attempt_hold_id']==hold and 'hold_id' not in result
    assert result['transaction_evidence_path']==str(root/'state/service-transactions'/(hold+'.json'))
    assert not any(key in result for key in ('authenticated_version','finished','recovered','outcome'))
