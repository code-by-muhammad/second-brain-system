import json,copy
from pathlib import Path
import pytest
import brain_wiki as w
import brain_settings as cfg
import brain_store as s
from test_wiki_model_context_preflight import brain,snapshot,fake_skip_model
from test_wiki_saved_plan_repair import interrupted_plan

@pytest.mark.parametrize('edited_after',['no-derived-edit-control','append-published-topic','remove-published-topic'])
def test_started_publication_retains_hold_after_owner_edits_published_topic(brain,monkeypatch,edited_after):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    plan=json.loads((folder/'publication.json').read_text())
    journal=json.loads((folder/'journal.json').read_text())
    assert journal['state']=='applying' and len(journal['completed'])==1
    topic=s.safe_path(w.BASE+'Projects/example.md')
    assert journal['completed'][0]['path']==topic.relative_to(s.VAULT).as_posix()
    assert journal['completed'][0]['sha256']==s.digest(topic.read_bytes())
    if edited_after=='append-published-topic':topic.write_bytes(topic.read_bytes()+b'\nOwner adds preserved topic commentary after interruption.\n')
    elif edited_after=='remove-published-topic':topic.unlink()
    index=s.safe_path(w.BASE+'Index.md');index.write_bytes(b'# Owner concurrent Index content\n')
    owner=index.read_bytes()
    source.write_bytes(source.read_bytes()+b'\nOwner actual source revision after interrupted publication.\n')
    before=snapshot(s.VAULT);private=snapshot(folder)
    calls=fake_skip_model(monkeypatch)
    w.reset_retries()
    error=None;report=None
    try:report=w.run()
    except Exception as exc:error=type(exc).__name__
    with w.db() as conn:oldstatus=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    observation=dict(mode=edited_after,journal=journal,status_before_current_owner_edits='retryable',recognizes_current_partial=w._saved_publication_started(packet,folder/'publication.json'),new_calls=len(calls),report=report,exception=error,worker=w._read_worker_state(),old_batch_status=oldstatus,owner_index_preserved=index.read_bytes()==owner,private_preserved=snapshot(folder)==private,vault_preserved=snapshot(s.VAULT)==before)
    assert not calls and snapshot(s.VAULT)==before and snapshot(folder)==private and oldstatus!='stale'


from test_wiki_saved_plan_repair import row
from test_wiki_remediation import explicit, fake_model
from test_wiki import fixture


def policy_exclude(source):
    policy=s.VAULT/w.SOURCE_POLICY;policy.parent.mkdir(parents=True,exist_ok=True)
    policy.write_text('```json\n'+json.dumps({'version':1,'exclude_paths':[source.relative_to(s.VAULT).as_posix()],'exclude_prefixes':[]})+'\n```\n',encoding='utf-8')


def caught():
    try: return w.run(),None
    except Exception as error: return None,error


@pytest.mark.parametrize('edit',['append','delete'])
@pytest.mark.parametrize('context',['unchanged','source','catalog','policy'])
def test_prior_publication_hold_survives_explicit_retries_and_context_changes(brain,monkeypatch,edit,context):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    topic=s.safe_path(w.BASE+'Projects/example.md')
    if edit=='delete':topic.unlink()
    else:topic.write_bytes(topic.read_bytes()+b'\nOwner topic commentary.\n')
    index=s.safe_path(w.BASE+'Index.md');index.write_bytes(b'# Owner Index must survive\n')
    if context=='source':source.write_bytes(source.read_bytes()+b'\nOwner source revision.\n')
    elif context=='policy':policy_exclude(source)
    elif context=='catalog':
        added=s.safe_path(w.BASE+'Concepts/owner-added.md');added.parent.mkdir(parents=True,exist_ok=True)
        added.write_text('---\nwiki_kind: topic\ntitle: Owner topic\nwiki_claims: []\n---\n# Owner topic\n',encoding='utf-8')
    private,notes=snapshot(folder),snapshot(s.VAULT);calls=fake_skip_model(monkeypatch)
    for _ in range(2):
        w.reset_retries();original_row=row(packet);_,error=caught();state=w._read_worker_state()
        assert isinstance(error,w.WikiIntegrityError) and state['attention_required']
        assert state['batch_id']==packet['batch_id'] and state['recovery_action']=='reconcile_saved_publication_then_retry'
        assert state['consecutive_failures']==0 and not calls and row(packet)==original_row
        assert snapshot(folder)==private and snapshot(s.VAULT)==notes
        assert w.run()['recovery_action']=='reconcile_saved_publication_then_retry'
        assert w.status()['worker']['recovery_action']=='reconcile_saved_publication_then_retry'
        assert not w._saved_publication_started(packet,folder/'publication.json')


@pytest.mark.parametrize('edit',['append','delete'])
def test_explicit_owner_reconciliation_restores_independent_proof_before_replay(brain,monkeypatch,edit):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    raw=source.read_bytes();topic=s.safe_path(w.BASE+'Projects/example.md');published=topic.read_bytes()
    if edit=='delete':topic.unlink()
    else:topic.write_bytes(published+b'\nOwner commentary preserved externally for explicit repair.\n')
    index=s.safe_path(w.BASE+'Index.md');index.write_bytes(b'# Owner conflict\n');source.write_bytes(raw+b'\nOwner source revision\n')
    calls=fake_skip_model(monkeypatch);w.reset_retries();_,error=caught();assert isinstance(error,w.WikiIntegrityError) and not calls
    # Only this explicit owner fixture restores bytes; worker never performs it.
    topic.write_bytes(published);source.write_bytes(raw);index.unlink()
    assert w._saved_publication_started(packet,folder/'publication.json')
    w.reset_retries();report,error=caught()
    assert error is None and report['status']=='complete' and report['batch_id']==packet['batch_id'] and not calls


@pytest.mark.parametrize('edit',['append','delete'])
def test_replace_before_journal_crash_retains_hold_only_intent(brain,monkeypatch,edit):
    source,packet,result=fixture(brain);calls=fake_model(monkeypatch,explicit(packet,result));write=s._write
    class AbruptStop(BaseException):pass
    def crash(path,text,kind,*args):
        receipt=write(path,text,kind,*args)
        if kind=='wiki_compile':raise AbruptStop()
        return receipt
    monkeypatch.setattr(s,'_write',crash)
    with pytest.raises(AbruptStop):w.run()
    monkeypatch.setattr(s,'_write',write);folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    journal=json.loads((folder/'journal.json').read_text());assert not journal['completed'] and journal['in_flight']
    topic=s.safe_path(w.BASE+'Projects/example.md')
    if edit=='delete':topic.unlink()
    else:topic.write_bytes(topic.read_bytes()+b'\nOwner after crash\n')
    source.write_bytes(source.read_bytes()+b'\nOwner source revision\n')
    index=s.safe_path(w.BASE+'Index.md');index.write_bytes(b'# Owner Index conflict\n')
    notes,private=snapshot(s.VAULT),snapshot(folder);newcalls=fake_skip_model(monkeypatch)
    w.reset_retries();_,error=caught()
    assert isinstance(error,w.WikiIntegrityError) and not newcalls and len(calls)==1
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private
    assert w._read_worker_state()['recovery_action']=='reconcile_saved_publication_then_retry'
    assert not w._saved_publication_started(packet,folder/'publication.json')


@pytest.mark.parametrize('archive',[False,True])
def test_normal_caught_prewrite_failure_remains_unused_after_owner_exclusion(brain,monkeypatch,archive):
    if archive:
        log=s.VAULT/w.BASE/'Log.md';log.parent.mkdir(parents=True);log.write_bytes(b'# History\n'+b'x'*1_000_001)
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch)
    journal=json.loads((folder/'journal.json').read_text());assert journal.get('attempted_without_write') and not journal.get('in_flight')
    policy_exclude(source);notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch)
    w.reset_retries();report,error=caught()
    assert error is None and report['status']=='idle' and not calls
    assert row(packet)['status']=='superseded' and snapshot(s.VAULT)==notes and snapshot(folder)==private


def test_legacy_empty_journal_with_changed_context_holds_without_replay_authority(brain,monkeypatch):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch)
    journal=json.loads((folder/'journal.json').read_text());journal.pop('attempted_without_write',None);journal.pop('semantic_intent_version',None)
    w._atomic_json(folder/'journal.json',journal)
    source.write_bytes(source.read_bytes()+b'\nOwner changed actual source.\n')
    notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch);w.reset_retries()
    _,error=caught();assert isinstance(error,w.WikiIntegrityError) and not calls
    assert not w._saved_publication_started(packet,folder/'publication.json')
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private


def test_saved_plan_without_journal_never_gains_prior_publication_authority(brain,monkeypatch):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch)
    (folder/'journal.json').unlink();policy_exclude(source)
    calls=fake_skip_model(monkeypatch);w.reset_retries();report,error=caught()
    assert error is None and report['status']=='idle' and not calls and row(packet)['status']=='superseded'


@pytest.mark.parametrize('edit',['append','delete'])
def test_replay_journal_reset_cannot_erase_prior_hold_evidence(brain,monkeypatch,edit):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    topic=s.safe_path(w.BASE+'Projects/example.md');index=s.safe_path(w.BASE+'Index.md')
    original=w._atomic_json;changed=False;owner=b'# Owner Index at replay checkpoint\n'
    durable={name:(folder/name).read_bytes() for name in ('input.json','output.json','publication.json')}
    def owner_at_checkpoint(path,value):
        nonlocal changed
        original(path,value)
        if Path(path)==folder/'journal.json' and value.get('state')=='applying' and not value.get('completed') and not changed:
            changed=True
            if edit=='delete':topic.unlink()
            else:topic.write_bytes(topic.read_bytes()+b'\nOwner topic after checkpoint\n')
            index.write_bytes(owner)
    monkeypatch.setattr(w,'_atomic_json',owner_at_checkpoint);calls=fake_skip_model(monkeypatch);w.reset_retries()
    _,error=caught();assert changed and isinstance(error,w.WikiIntegrityError) and not calls
    state=w._read_worker_state();assert state['recovery_action']=='reconcile_saved_publication_then_retry' and state['consecutive_failures']==0
    assert index.read_bytes()==owner and json.loads((folder/'journal.json').read_text())['prior_semantic']
    private,notes=snapshot(folder),snapshot(s.VAULT)
    w.reset_retries();_,error=caught();assert isinstance(error,w.WikiIntegrityError) and not calls
    assert snapshot(folder)==private and snapshot(s.VAULT)==notes
    assert all((folder/name).read_bytes()==raw for name,raw in durable.items())


@pytest.mark.parametrize('source_changed',[False,True])
def test_missing_prior_plan_preserves_held_queue_across_retries(brain,monkeypatch,source_changed):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    plan=(folder/'publication.json').read_bytes();(folder/'publication.json').unlink()
    topic=s.safe_path(w.BASE+'Projects/example.md');topic.write_bytes(topic.read_bytes()+b'\nOwner topic comment\n')
    index=s.safe_path(w.BASE+'Index.md');index.write_bytes(b'# Owner Index\n')
    if source_changed:source.write_bytes(source.read_bytes()+b'\nOwner source revision\n')
    private,notes=snapshot(folder),snapshot(s.VAULT);calls=fake_skip_model(monkeypatch)
    for _ in range(2):
        w.reset_retries();before=row(packet);_,error=caught();state=w._read_worker_state()
        assert isinstance(error,w.WikiIntegrityError) and not calls and row(packet)==before
        assert snapshot(folder)==private and snapshot(s.VAULT)==notes
        assert state['batch_id']==packet['batch_id'] and state['recovery_action']=='reconcile_saved_publication_then_retry'
        assert state['attention_required'] and state['consecutive_failures']==0
    # Restoring the genuine saved plan does not itself authorize owner overwrite.
    (folder/'publication.json').write_bytes(plan);w.reset_retries();_,error=caught()
    assert isinstance(error,w.WikiIntegrityError) and not calls and snapshot(s.VAULT)==notes


@pytest.mark.parametrize('journal_shape',['malformed','mismatched'])
def test_missing_plan_with_untrusted_journal_is_hold_only(brain,monkeypatch,journal_shape):
    _,packet,result,folder,_=interrupted_plan(brain,monkeypatch,kind='wiki_source')
    (folder/'publication.json').unlink()
    (folder/'journal.json').write_bytes(b'{broken' if journal_shape=='malformed' else b'{"state":"applying","packet_sha256":"wrong"}')
    private,notes=snapshot(folder),snapshot(s.VAULT);calls=fake_skip_model(monkeypatch);w.reset_retries();_,error=caught()
    assert isinstance(error,w.WikiIntegrityError) and not calls
    assert snapshot(folder)==private and snapshot(s.VAULT)==notes
    assert w._read_worker_state()['recovery_action']=='reconcile_saved_publication_then_retry'


def test_ordinary_unused_packet_without_plan_or_journal_compiles(brain,monkeypatch):
    source,packet,result=fixture(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    assert not (folder/'journal.json').exists() and not (folder/'publication.json').exists()
    calls=fake_model(monkeypatch,explicit(packet,result));report,error=caught()
    assert error is None and report['status']=='complete' and len(calls)==1


def test_legacy_empty_journal_partial_catalog_known_hash_change_holds(brain,monkeypatch):
    source,first,result=fixture(brain);result['pages'][0]['title']='Supplier evaluation';w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nA later evidence event.\n')
    packet=w.prepare();assert not packet['current_pages'] and len(packet['catalog'])==1
    known=s.safe_path(packet['catalog'][0]['path'])
    packet['catalog'].append({'path':w.BASE+'Concepts/unknown-legacy.md','title':'Unknown legacy hash'})
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',packet)
    result=copy.deepcopy(result);result['pages'][0].update(slug='new-topic',title='New topic')
    write=s._write
    def stop(path,text,kind,*args):
        if kind=='wiki_compile':raise OSError('Normal before-write fixture')
        return write(path,text,kind,*args)
    monkeypatch.setattr(s,'_write',stop);first_calls=fake_model(monkeypatch,explicit(packet,result));_,error=caught()
    assert isinstance(error,OSError) and len(first_calls)==1
    monkeypatch.setattr(s,'_write',write)
    journal=json.loads((folder/'journal.json').read_text());journal.pop('attempted_without_write',None);journal.pop('semantic_intent_version',None)
    w._atomic_json(folder/'journal.json',journal);assert w._full_catalog_snapshot(packet) is None
    known.write_bytes(known.read_bytes()+b'\nOwner changed known catalog-only topic\n')
    notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch);w.reset_retries();_,error=caught()
    assert isinstance(error,w.WikiIntegrityError) and not calls
    assert w._read_worker_state()['recovery_action']=='reconcile_saved_publication_then_retry'
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private
