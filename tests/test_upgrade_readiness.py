import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_integrity import updater
from test_portable_remediation import portable

def test_upgrade_waits_for_the_expected_running_version(tmp_path,monkeypatch):
    replies=iter([{}, {'status':'running','version':'old'}, {'status':'running','version':'new'}])
    monkeypatch.setattr(updater,'controller',lambda *a,**kw:next(replies))
    monkeypatch.setattr(updater.time,'sleep',lambda *a:None)
    assert updater.wait_health('python',tmp_path,'new')['version']=='new'

def test_upgrade_readiness_has_a_bounded_deadline(tmp_path,monkeypatch):
    with pytest.raises(RuntimeError,match='startup deadline'): updater.wait_health('python',tmp_path,'new',timeout=0)


def test_failed_new_readiness_uses_new_controller_for_rollback(portable,monkeypatch):
    from upgrade_fixture import prepare
    h=prepare(portable,monkeypatch);h.fail_health=True
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.1.6')
    events=[row for row in h.events if row['kind']=='controller']
    actions=[row['action'] for row in events]
    assert actions==['inspect','hold','stop','start','stop','install','start','finish']
    assert all(row['checkout']==str(h.release) for row in events)
    assert h.health==['0.1.6','0.1.0']


@pytest.mark.parametrize('refusal',['inspect','hold','disabled','absent'])
def test_pre_hold_task_or_hold_refusal_never_claims_nonexistent_recovery(portable,monkeypatch,refusal):
    from upgrade_fixture import prepare
    h=prepare(portable,monkeypatch);actual=h.updater.controller
    h.running=refusal!='disabled';initial_running=h.running
    before=(h.root/'device.json').read_bytes()
    app={path.name:path.read_bytes() for path in (h.root/'app').glob('*.py')}
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='inspect' and refusal in ('disabled','absent'):
            return {'status':'inspected','task_present':refusal!='absent','task_enabled':refusal!='disabled'}
        if action==refusal:raise RuntimeError('synthetic task ownership/control refusal')
        return actual(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='before a hold was created'):h.updater.perform(h.root,'v0.1.6')
    result=json.loads((h.root/'state/upgrade-status.json').read_text())
    assert result['status']=='update deferred; no hold created'
    assert not (h.root/'state/service-transaction.json').exists() and h.running==initial_running
    assert (h.root/'device.json').read_bytes()==before
    assert {path.name:path.read_bytes() for path in (h.root/'app').glob('*.py')}==app
    assert not any(row.get('action') in ('stop','cancel') for row in h.events)


@pytest.mark.parametrize('foreign',['different-uuid','unreadable'])
def test_unexpected_pre_stop_transaction_is_preserved_without_cancel_or_wrong_recovery_uuid(portable,monkeypatch,foreign):
    from upgrade_fixture import prepare
    h=prepare(portable,monkeypatch);actual=h.updater.controller;path=h.root/'state/service-transaction.json'
    preserved=b'not-json' if foreign=='unreadable' else json.dumps({'hold_id':'f'*32,'root':str(h.root),'snapshot':'foreign-snapshot'}).encode()
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='hold':
            path.write_bytes(preserved);raise RuntimeError('synthetic concurrent transaction')
        return actual(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='different or unreadable transaction'):h.updater.perform(h.root,'v0.1.6')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_text())
    assert receipt['status']=='transaction inspection required' and 'hold_id' not in receipt
    assert path.read_bytes()==preserved and h.running
    assert not any(row.get('action') in ('stop','cancel') for row in h.events)
