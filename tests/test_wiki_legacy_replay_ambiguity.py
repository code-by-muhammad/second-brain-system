"""Legacy-format crash uncertainty remains a hold, never replay authority."""
import inspect,json,linecache
from pathlib import Path
import pytest
import brain_wiki as w
import brain_store as s
import brain_settings as cfg
from test_wiki import fixture
from test_wiki_model_context_preflight import brain,snapshot,fake_skip_model
from test_wiki_remediation import explicit,fake_model

@pytest.mark.parametrize('mode',['legacy-append','legacy-delete','current-intent-append','legacy-unchanged'])
@pytest.mark.parametrize('timing',['preflight-end','journal-reset'])
def test_legacy_replace_before_journal_uncertainty_survives_replay_checkpoints(brain,monkeypatch,mode,timing):
    source,packet,result=fixture(brain);raw=source.read_bytes();first_calls=fake_model(monkeypatch,explicit(packet,result))
    write=s._write;atomic=w._atomic_json
    class AbruptStop(BaseException):pass
    def old_format(path,value):
        if path.name=='journal.json' and value.get('state')=='applying' and mode.startswith('legacy'):
            value={key:val for key,val in value.items() if key not in ('semantic_intent_version','in_flight','prior_semantic','legacy_semantic_ambiguity')}
        return atomic(path,value)
    def crash(path,text,kind,*args):
        receipt=write(path,text,kind,*args)
        if kind=='wiki_compile':raise AbruptStop()
        return receipt
    with monkeypatch.context() as initial:
        initial.setattr(w,'_atomic_json',old_format);initial.setattr(s,'_write',crash)
        with pytest.raises(AbruptStop):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];plan=json.loads((folder/'publication.json').read_text())
    journal=json.loads((folder/'journal.json').read_text())
    assert journal['state']=='applying' and journal['completed']==[] and len(first_calls)==1
    assert bool(journal.get('in_flight'))==(mode=='current-intent-append')
    assert w._saved_publication_started(packet,folder/'publication.json')
    topic=s.VAULT/w.BASE/'Projects/example.md';published=topic.read_bytes();index=s.VAULT/w.BASE/'Index.md'
    last=s.safe_path(plan['writes'][-1]['path']);current_hash=w._current_hash
    hooked=False;notes=None;owner=b'# Owner Index after replay preflight\n'
    def edit():
        nonlocal hooked,notes
        if hooked or mode=='legacy-unchanged':return
        hooked=True
        if mode=='legacy-delete':topic.unlink()
        else:topic.write_bytes(published+b'\nOwner subsequent topic change\n')
        index.write_bytes(owner);notes=snapshot(s.VAULT)
    def at_hash(path):
        value=current_hash(path);frame=inspect.currentframe().f_back
        context=linecache.getline(frame.f_code.co_filename,frame.f_lineno).strip()
        if timing=='preflight-end' and Path(path)==last and frame.f_code.co_name=='apply' and context.startswith('current=_current_hash('):edit()
        return value
    def at_reset(path,value):
        if timing=='journal-reset' and path.name=='journal.json' and value.get('state')=='applying':edit()
        return atomic(path,value)
    monkeypatch.setattr(w,'_current_hash',at_hash);monkeypatch.setattr(w,'_atomic_json',at_reset)
    calls=fake_skip_model(monkeypatch);reports=[]
    for _ in range(2):
        w.reset_retries()
        try:reports.append(w.run())
        except (w.WikiIntegrityError,w.WikiTransientError):pass
        assert not calls and source.read_bytes()==raw
        if mode!='legacy-unchanged':assert hooked and snapshot(s.VAULT)==notes and index.read_bytes()==owner
    if mode=='legacy-unchanged':assert reports[0]['status']=='complete'
    else:
        state=w._read_worker_state()
        assert state['attention_required'] and state['batch_id']==packet['batch_id']
        assert state['recovery_action']=='reconcile_saved_publication_then_retry'
        # Owner reconciliation, not an inferred restoration by the compiler.
        topic.write_bytes(published)
        index_write=next(row for row in plan['writes'] if row['kind']=='wiki_index')
        assert index_write['before'] is None
        index.unlink();w.reset_retries()
        assert w.run()['status']=='complete' and not calls
