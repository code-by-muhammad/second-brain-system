"""Malformed recovery states cannot turn genuine unfinished publication unused."""
import json
import pytest
import brain_wiki as w
import brain_settings as cfg
import brain_store as s
from test_wiki import fixture
from test_wiki_model_context_preflight import brain,fake_skip_model,snapshot
from test_wiki_remediation import explicit,fake_model

def interrupted(brain,monkeypatch,legacy):
    source,packet,result=fixture(brain);calls=fake_model(monkeypatch,explicit(packet,result))
    atomic=w._atomic_json;write=s._write
    class Crash(BaseException):pass
    def checkpoint(path,value):
        if legacy and path.name=='journal.json' and value.get('state')=='applying':
            value={key:val for key,val in value.items() if key not in ('semantic_intent_version','in_flight','prior_semantic','legacy_semantic_ambiguity')}
        return atomic(path,value)
    def replace(path,text,kind,*args):
        receipt=write(path,text,kind,*args)
        if kind=='wiki_compile':raise Crash()
        return receipt
    with monkeypatch.context() as scope:
        scope.setattr(w,'_atomic_json',checkpoint);scope.setattr(s,'_write',replace)
        with pytest.raises(Crash):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];journal=folder/'journal.json'
    assert len(calls)==1 and json.loads(journal.read_text(encoding='utf-8'))['completed']==[]
    assert w._saved_publication_started(packet,folder/'publication.json')
    return source,packet,folder

@pytest.mark.parametrize('legacy',[True,False])
@pytest.mark.parametrize('state',['missing','unknown','null','list','dict','bool','number','applying-control'])
def test_invalid_journal_state_preserves_real_partial_across_retries_and_owner_repair(brain,monkeypatch,legacy,state):
    source,packet,folder=interrupted(brain,monkeypatch,legacy)
    journal=folder/'journal.json';valid=journal.read_bytes();saved=json.loads(valid)
    if state=='missing':saved.pop('state')
    elif state!='applying-control':saved['state']={'unknown':'not-a-journal-state','null':None,'list':[],'dict':{},'bool':True,'number':42}[state]
    journal.write_text(json.dumps(saved),encoding='utf-8')
    topic=s.VAULT/w.BASE/'Projects/example.md';published=topic.read_bytes();topic.write_bytes(published+b'\nOwner retained topic context.\n')
    index=s.VAULT/w.BASE/'Index.md';index.write_bytes(b'# Owner retained Index context\n')
    notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch)
    for _ in range(2):
        w.reset_retries()
        with pytest.raises(w.WikiIntegrityError):w.run()
        assert not calls and snapshot(s.VAULT)==notes and snapshot(folder)==private
        assert w._read_worker_state()['batch_id']==packet['batch_id']
        assert w._read_worker_state()['attention_required']
    # Only explicit fixture owner repair restores validated prior evidence.
    journal.write_bytes(valid);topic.write_bytes(published);index.unlink();w.reset_retries()
    assert w.run()['status']=='complete' and not calls

@pytest.mark.parametrize('checkpoint',['applying','published','complete'])
def test_legitimate_current_journal_states_recover_without_another_request(brain,monkeypatch,checkpoint):
    source,packet,result=fixture(brain);calls=fake_model(monkeypatch,explicit(packet,result));atomic=w._atomic_json;write=s._write
    class Crash(BaseException):pass
    def atomic_crash(path,value):
        result=atomic(path,value)
        if path.name=='journal.json' and value.get('state')==checkpoint and checkpoint!='applying':raise Crash()
        return result
    def replace(path,text,kind,*args):
        receipt=write(path,text,kind,*args)
        if checkpoint=='applying' and kind=='wiki_compile':raise Crash()
        return receipt
    with monkeypatch.context() as scope:
        scope.setattr(w,'_atomic_json',atomic_crash);scope.setattr(s,'_write',replace)
        with pytest.raises(Crash):w.run()
    raw=source.read_bytes();w.reset_retries();report=w.run()
    assert report['status']==('idle' if checkpoint=='complete' else 'complete') and len(calls)==1
    assert source.read_bytes()==raw and not w.lint()['issues']

def test_unchanged_legacy_applying_afterimage_remains_independent_replay_evidence(brain,monkeypatch):
    source,packet,folder=interrupted(brain,monkeypatch,True);raw=source.read_bytes();calls=fake_skip_model(monkeypatch)
    assert w._saved_publication_started(packet,folder/'publication.json')
    assert w.run()['status']=='complete' and not calls and source.read_bytes()==raw

def test_invalid_legacy_state_without_current_afterimage_does_not_gain_policy_exemption(brain,monkeypatch):
    source,packet,folder=interrupted(brain,monkeypatch,True)
    topic=s.VAULT/w.BASE/'Projects/example.md';topic.write_bytes(topic.read_bytes()+b'\nOwner retained change.\n')
    saved=json.loads((folder/'journal.json').read_text(encoding='utf-8'));saved.pop('state');w._atomic_json(folder/'journal.json',saved)
    policy={'version':1,'exclude_paths':[packet['sources'][0]['path']],'exclude_prefixes':[]}
    path=s.VAULT/w.SOURCE_POLICY;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('# Owner wiki source policy\n\n```json\n'+json.dumps(policy)+'\n```\n',encoding='utf-8')
    notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch)
    assert not w._saved_publication_started(packet,folder/'publication.json')
    for _ in range(2):
        w.reset_retries()
        with pytest.raises(w.WikiIntegrityError):w.run()
        assert not calls and snapshot(s.VAULT)==notes and snapshot(folder)==private
