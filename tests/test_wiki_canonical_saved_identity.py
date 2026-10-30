import copy,json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_saved_catalog_identity import brain,snapshot
from test_wiki import fixture
from test_wiki_remediation import explicit,fake_model

def record(*args):pass

@pytest.mark.parametrize('mode',['single-control','identical-duplicate','partial-legacy-known'])
def test_known_catalog_owner_change_refreshes_without_stale_call(brain,monkeypatch,mode):
    source,first,result=fixture(brain);result['pages'][0]['title']='Supplier evaluation';w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nAnother source evidence event.\n')
    packet=w.prepare();assert not packet['current_pages'] and len(packet['catalog'])==1
    changed=copy.deepcopy(packet)
    if mode=='identical-duplicate':changed['catalog'].append(copy.deepcopy(changed['catalog'][0]))
    elif mode=='partial-legacy-known':changed['catalog'].append({'path':w.BASE+'Concepts/legacy-absent.md','title':'Legacy omitted hash'})
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',changed)
    old=snapshot(folder);topic=s.safe_path(packet['catalog'][0]['path']);topic.write_bytes(topic.read_bytes()+b'\nOwner updates this existing catalog-only topic.\n')
    owner=topic.read_bytes();calls=fake_model(monkeypatch,explicit(packet,result));report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    with w.db() as conn:status=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    record('catalog-owner-'+mode,{'mode':mode,'model_calls':len(calls),'exception':type(error).__name__ if error else None,
       'report':report,'old_batch_status':status,'worker':w._read_worker_state(),'old_private_preserved':snapshot(folder)==old,
       'owner_preserved':topic.read_bytes()==owner,'selected_current_pages':len(packet['current_pages'])})
    assert error is None and report['status']=='complete' and report['batch_id']!=packet['batch_id']
    assert status=='superseded' and len(calls)==1 and snapshot(folder)==old


@pytest.mark.parametrize('mode',['valid-control','absent-current-part','wrong-parts-count'])
def test_saved_source_part_identity_matches_current_source(brain,monkeypatch,mode):
    source,packet,result=fixture(brain);changed=copy.deepcopy(packet)
    assert changed['sources'][0]['part']==1 and changed['sources'][0]['parts']==1
    if mode=='absent-current-part':
        changed['sources'][0]['part']=2;changed['sources'][0]['parts']=2
        changed['sources'][0]['job_key']=changed['sources'][0]['path']+'::part2'
    elif mode=='wrong-parts-count':changed['sources'][0]['parts']=2
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',changed)
    old=snapshot(folder);notes=snapshot(s.VAULT);calls=fake_model(monkeypatch,explicit(changed,result));report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    after=w.status()
    record('source-part-'+mode,{'mode':mode,'model_calls':len(calls),'exception':type(error).__name__ if error else None,
       'report':report,'worker':w._read_worker_state(),'status':after,'lint':w.lint(),
       'old_private_preserved':snapshot(folder)==old,'vault_preserved':snapshot(s.VAULT)==notes})
    if mode=='valid-control':assert error is None and report['status']=='complete' and len(calls)==1
    else:
        assert not calls and snapshot(folder)==old and snapshot(s.VAULT)==notes
        state=w._read_worker_state()
        assert state.get('attention_required') and state['integrity_field']=='recovery' and state['batch_id']==packet['batch_id'] and state['consecutive_failures']==0
        with w.db() as conn:assert not list(conn.execute('SELECT * FROM sources WHERE attempts>0'))


@pytest.mark.parametrize('mode',['partial-legacy-known-owner-edit','full-catalog-control','partial-legacy-unchanged-control'])
def test_genuine_two_topic_catalog_honors_every_provided_hash(brain,monkeypatch,mode):
    source,first,result=fixture(brain);result['pages'][0]['title']='Supplier evaluation'
    extra=copy.deepcopy(result['pages'][0]);extra.update(category='Concepts',slug='other-legacy',title='Legacy administrative evidence')
    result['pages'].append(extra);w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nAn additional dated source event.\n')
    packet=w.prepare();assert not packet['current_pages'] and len(packet['catalog'])==2
    changed=copy.deepcopy(packet)
    if mode!='full-catalog-control':
        next(p for p in changed['catalog'] if p['path'].endswith('/other-legacy.md')).pop('sha256')
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',changed);old=(folder/'input.json').read_bytes()
    topic=s.safe_path(w.BASE+'Projects/example.md')
    if mode!='partial-legacy-unchanged-control':topic.write_bytes(topic.read_bytes()+b'\nOwner appends important derived context.\n')
    owner=topic.read_bytes();output=copy.deepcopy(result);output['pages']=output['pages'][:1]
    calls=fake_model(monkeypatch,explicit(packet,output));report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    with w.db() as conn:status=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    record={'mode':mode,'model_calls':len(calls),'error':type(error).__name__ if error else None,'report':report,
      'worker':w._read_worker_state(),'old_batch_status':status,'input_preserved':(folder/'input.json').read_bytes()==old,
      'owner_preserved':topic.read_bytes()==owner}
    if mode=='partial-legacy-known-owner-edit':
        assert error is None and report['status']=='complete' and report['batch_id']!=packet['batch_id'] and status=='superseded'
    elif mode=='full-catalog-control':assert error is None and report['status']=='complete' and report['batch_id']!=packet['batch_id']
    else:
        # The unknown legacy topic stays untouched; only the supplied known topic
        # is selected for output, so this is a supported legacy omission control.
        assert error is None and report['status']=='complete' and len(calls)==1


@pytest.mark.parametrize('mode',['appended-safe-text','equivalent-crlf-control','unchanged-control'])
def test_selected_saved_part_text_preserves_portable_identity(brain,monkeypatch,mode):
    source,packet,result=fixture(brain);changed=copy.deepcopy(packet);raw=source.read_bytes()
    if mode=='appended-safe-text':changed['sources'][0]['text']+='\nA harmless synthetic sentence absent from the unchanged original source.\n'
    elif mode=='equivalent-crlf-control':changed['sources'][0]['text']=changed['sources'][0]['text'].replace('\n','\r\n')
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',changed)
    before=snapshot(folder);notes=snapshot(s.VAULT);calls=fake_model(monkeypatch,explicit(changed,result));report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    after=w.status()
    record={'mode':mode,'model_calls':len(calls),'exception':type(error).__name__ if error else None,'report':report,
      'status':after,'worker':w._read_worker_state(),'source_preserved':source.read_bytes()==raw,
      'vault_preserved':snapshot(s.VAULT)==notes,'private_preserved':snapshot(folder)==before}
    if mode=='appended-safe-text':
        assert not calls and snapshot(folder)==before and snapshot(s.VAULT)==notes
        state=w._read_worker_state()
        assert state.get('attention_required') and state['integrity_field']=='recovery' and state['batch_id']==packet['batch_id'] and state['consecutive_failures']==0
        with w.db() as conn:assert not list(conn.execute('SELECT * FROM sources WHERE attempts>0'))
    else:assert error is None and report['status']=='complete' and len(calls)==1 and after['portable_receipt_parts']==1 and after['ready_source_parts']==0



@pytest.mark.parametrize('encoding',['lf','crlf','bom'])
def test_canonical_part_two_of_three_preserves_portable_raw_encoding(brain,monkeypatch,encoding):
    source,first,result=fixture(brain)
    text='# Long source\n'+('Canonical multipart evidence remains independently verifiable.\n'*900)
    raw=text.encode() if encoding=='lf' else text.replace('\n','\r\n').encode() if encoding=='crlf' else bytes([239,187,191])+text.encode()
    source.write_bytes(raw)
    with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(first['batch_id'],))
    rows=w.inventory();assert len(rows)==3
    with w.db() as conn:conn.execute('INSERT INTO sources(path,processed_hash) VALUES(?,?)',(rows[0]['job_key'],rows[0]['sha256']))
    packet=w.prepare();assert packet['sources'][0]['part']==2 and packet['sources'][0]['parts']==3
    result['pages'][0]['claims'][0].update(quote='Canonical multipart evidence remains independently verifiable.',text='The source preserves independently verifiable multipart evidence.')
    calls=fake_model(monkeypatch,explicit(packet,result));report=w.run()
    assert report['status']=='complete' and len(calls)==1 and source.read_bytes()==raw
    assert w.status()['portable_receipt_parts']==1

@pytest.mark.parametrize('change',['before-resume','during-inventory'])
def test_real_source_revision_change_reprepares_without_integrity_strike(brain,monkeypatch,change):
    source,packet,result=fixture(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];before=snapshot(folder)
    revised=source.read_bytes()+b'\nA real owner revision requires fresh source context.\n'
    inventory=w.inventory;changed=False
    def racing_inventory(*args,**kwargs):
        nonlocal changed
        if not changed:source.write_bytes(revised);changed=True
        return inventory(*args,**kwargs)
    if change=='before-resume':source.write_bytes(revised)
    else:monkeypatch.setattr(w,'inventory',racing_inventory)
    calls=fake_model(monkeypatch,explicit(packet,result));report=w.run()
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
    assert snapshot(folder)==before and source.read_bytes()==revised and w._read_worker_state()['consecutive_failures']==0

@pytest.mark.parametrize('progress',['unused','partial'])
def test_canonical_compare_cannot_be_bypassed_by_unused_durable_plan(brain,monkeypatch,progress):
    source,packet,result=fixture(brain);packet['sources'][0]['text']+='\nLegacy harmless saved-only suffix.\n'
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',packet)
    inventory=w.inventory;write=s._write
    def old_inventory(*args,**kwargs):
        rows=inventory(*args,**kwargs)
        for row in rows:
            if row['job_key']==packet['sources'][0]['job_key']:row['text']=packet['sources'][0]['text']
        return rows
    def stop(path,text,kind,*args):
        if kind==('wiki_index' if progress=='partial' else 'wiki_compile'):raise OSError('Synthetic legacy publication interruption')
        return write(path,text,kind,*args)
    calls=fake_model(monkeypatch,explicit(packet,result))
    with monkeypatch.context() as old:
        old.setattr(w,'inventory',old_inventory);old.setattr(s,'_write',stop)
        with pytest.raises(OSError):w.run()
    assert w._saved_publication_started(packet,folder/'publication.json')==(progress=='partial')
    w.reset_retries();before=snapshot(folder);notes=snapshot(s.VAULT)
    if progress=='partial':assert w.run()['status']=='complete' and len(calls)==1
    else:
        with pytest.raises(w.WikiIntegrityError):w.run()
        state=w._read_worker_state();assert state['integrity_field']=='recovery' and state['batch_id']==packet['batch_id'] and state['consecutive_failures']==0
        assert len(calls)==1 and snapshot(folder)==before and snapshot(s.VAULT)==notes

@pytest.mark.parametrize('change',['known-deleted','full-added'])
def test_catalog_known_deletion_and_full_catalog_addition_retire_unused_context(brain,monkeypatch,change):
    source,first,result=fixture(brain);result['pages'][0]['title']='Supplier evaluation';w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nAnother genuine owner source event.\n');packet=w.prepare();folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    topic=s.safe_path(packet['catalog'][0]['path'])
    if change=='known-deleted':
        packet['catalog'].append({'path':w.BASE+'Concepts/legacy-absent.md','title':'Legacy omitted hash'});w._atomic_json(folder/'input.json',packet);topic.unlink()
    else:
        extra=topic.with_name('extra.md');extra.write_bytes(topic.read_bytes())
    before=snapshot(folder)
    assert w._resume_packet() is None and snapshot(folder)==before
    with w.db() as conn:assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'

from test_wiki_saved_plan_repair import partial_index_conflict

@pytest.mark.parametrize('mode',['source-changed','unchanged-control'])
def test_actual_partial_queue_cannot_be_bypassed_by_source_change(brain,monkeypatch,mode):
    source,packet,oldcalls,index,before,owner,plan=partial_index_conflict(brain,monkeypatch)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    assert w._saved_publication_started(packet,folder/'publication.json')
    if mode=='source-changed':source.write_bytes(source.read_bytes()+b'\nAn actual owner edit after partial publication.\n')
    notes=snapshot(s.VAULT);private=snapshot(folder)
    output=json.loads((folder/'output.json').read_text())
    calls=fake_model(monkeypatch,output)
    w.reset_retries();report=None;error=None
    try:report=w.run()
    except Exception as exc:error=type(exc).__name__
    with w.db() as conn:oldstatus=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    observation={'mode':mode,'new_model_calls':len(calls),'exception':error,'report':report,'worker':w._read_worker_state(),
      'old_batch_status':oldstatus,'owner_index_preserved':index.read_bytes()==owner,'private_preserved':snapshot(folder)==private,'vault_preserved':snapshot(s.VAULT)==notes}
    assert not calls and snapshot(folder)==private and snapshot(s.VAULT)==notes
    assert w._read_worker_state().get('integrity_field')=='publication_cas' if mode=='unchanged-control' else True

@pytest.mark.parametrize('change',['append','replace','remove'])
def test_changed_partial_source_holds_everything_until_explicit_verified_repair(brain,monkeypatch,change):
    source,packet,oldcalls,index,before,owner,plan=partial_index_conflict(brain,monkeypatch)
    original=source.read_bytes();folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    if change=='append':source.write_bytes(original+b'\nActual later owner evidence.\n')
    elif change=='replace':source.write_bytes(b'# Replacement source\nAn entirely new owner account must be preserved.\n')
    else:source.unlink()
    notes=snapshot(s.VAULT);private=snapshot(folder);calls=fake_model(monkeypatch,json.loads((folder/'output.json').read_text()))
    w.reset_retries()
    with w.db() as conn:oldstatus=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    with pytest.raises(w.WikiIntegrityError):w.run()
    state=w._read_worker_state();public=w.status()['worker'];repeated=w.run()
    for value in (state,public,repeated):
        assert value['batch_id']==packet['batch_id'] and value['recovery_action']=='resolve_saved_source_revision_then_retry' and value['integrity_field']=='source_revision'
    assert state['attention_required'] and state['consecutive_failures']==0 and not calls
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private and index.read_bytes()==owner
    with w.db() as conn:assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==oldstatus
    # Explicit fixture-owner reconciliation, never performed by the worker.
    source.write_bytes(original)
    if before is None:index.unlink()
    else:index.write_bytes(before)
    w.reset_retries();assert w.run()['status']=='complete' and not calls
    assert source.read_bytes()==original

@pytest.mark.parametrize('change',['append','remove'])
def test_unfinalized_complete_plan_source_change_holds_before_journal_repair(brain,monkeypatch,change):
    from test_wiki_saved_recovery_attention import complete_unfinalized
    source,packet,result,folder=complete_unfinalized(brain,monkeypatch);original=source.read_bytes()
    if change=='append':source.write_bytes(original+b'\nOwner evidence after the database commit.\n')
    else:source.unlink()
    notes=snapshot(s.VAULT);private=snapshot(folder);calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError):w.run()
    assert not calls and snapshot(s.VAULT)==notes and snapshot(folder)==private
    assert w._read_worker_state()['recovery_action']=='resolve_saved_source_revision_then_retry'
    source.write_bytes(original);w.reset_retries();assert w.run()['status']=='complete' and not calls
