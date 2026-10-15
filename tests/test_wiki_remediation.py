"""Failure-injection coverage for real worker replay and portable part proofs."""
import copy
import json
from pathlib import Path

import pytest
from filelock import Timeout

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture


def explicit(packet,result):
    result=copy.deepcopy(result)
    cited={claim['source'] for page in result['pages'] for claim in page['claims']}
    result['dispositions']=[{'source':row['path'],'part':row['part'],'status':'published' if row['path'] in cited else 'deferred',
                             'reason':'' if row['path'] in cited else 'This source needs a later focused review.',
                             'quote':'' if row['path'] in cited else row['text'].strip()[:100]} for row in packet['sources']]
    return result


def fake_model(monkeypatch,result,after=None):
    class Calls(list): pass
    calls=Calls(); calls.inputs=[]
    monkeypatch.setattr(w.shutil,'which',lambda name:'synthetic-codex')
    class Process:
        returncode=0
        def __init__(self,command,**kwargs):
            self.output=Path(command[command.index('-o')+1]); calls.append(command)
        def communicate(self,model_input,timeout):
            calls.inputs.append(model_input)
            self.output.write_text(json.dumps(result),encoding='utf-8')
            if after: after()
    monkeypatch.setattr(w.subprocess,'Popen',Process)
    return calls


def source_rows():
    with w.db() as conn: return {row['path']:dict(row) for row in conn.execute('SELECT * FROM sources')}


@pytest.mark.parametrize('tags',['', 'null','notes','123','[notes]','{custom: yes}'])
def test_null_scalar_and_nonlist_tags_do_not_abort_inventory(brain,tags):
    vault,_=brain; source=vault/'01-Projects/Tags.md'; source.parent.mkdir()
    source.write_text('---\ntags: '+tags+'\n---\n# Tags\nA reusable historical source statement.',encoding='utf-8')
    assert w.status()['eligible_sources']==1
    assert len(w.prepare()['sources'])==1


def test_scalar_capture_tag_is_recognized(brain):
    vault,_=brain; source=vault/'00-Inbox/Excluded.md'; source.parent.mkdir()
    source.write_text('---\ntags: ai-capture\nproject: Second Brain\n---\n# Internal capture',encoding='utf-8')
    assert w.inventory()==[]


def two_sources(brain):
    source,initial,result=fixture(brain)
    # This helper intentionally discards its preliminary one-source prepare.
    # Retire that synthetic packet so worker tests receive the two-source
    # contract their fake model output is built for, independent of timestamps.
    with w.db() as conn: conn.execute("UPDATE batches SET status='stale' WHERE id=? AND status='pending'",(initial['batch_id'],))
    second=source.with_name('Unrelated.md')
    second.write_text('# Unrelated Project\nA separate historical requirement needs review.',encoding='utf-8')
    return source,second,w.prepare(),result


def test_omitted_source_never_silently_completes(brain):
    _,_,packet,result=two_sources(brain)
    with pytest.raises(w.WikiOutputError,match='Every input part'): w.apply(packet,result)
    assert not (s.VAULT/w.BASE/'Projects').exists()
    assert w.status()['pending_source_parts']==2


def test_explicit_defer_stays_pending_without_failure_and_source_edit_requeues(brain):
    _,second,packet,result=two_sources(brain)
    result=explicit(packet,result); w.apply(packet,result)
    status=w.status()
    assert status['pending_source_parts']==1 and status['ready_source_parts']==0
    assert status['deferred_source_parts']==1 and status['failed_source_parts']==0
    assert w.prepare() is None
    second.write_text(second.read_text()+'\nNew evidence makes this review ready.',encoding='utf-8')
    assert w.status()['ready_source_parts']==1


def test_zero_pages_requires_explicit_evidence_backed_skip(brain):
    _,packet,_=fixture(brain)
    with pytest.raises(w.WikiOutputError): w.apply(packet,{'pages':[]})
    row=packet['sources'][0]
    result={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'skipped',
             'reason':'This synthetic administrative note was explicitly reviewed without a reusable topic.',
             'quote':'A prototype was demonstrated.'}]}
    assert w.apply(packet,result)==[]
    assert w.status()['pending_source_parts']==0
    assert not w.lint()['issues']
    assert (s.VAULT/w.BASE/'Receipts'/f'{packet["batch_id"]}.md').exists()


def test_model_schema_is_strict_but_legacy_direct_call_schema_remains_compatible(brain):
    from jsonschema import validate,ValidationError
    _,packet,result=fixture(brain)
    validate(result,w.output_schema(packet))
    with pytest.raises(ValidationError): validate(result,w.output_schema(packet,strict=True))
    validate(explicit(packet,result),w.output_schema(packet,strict=True))


@pytest.mark.parametrize('failure_point',['before-second-topic','after-first-topic','before-database'])
def test_real_run_recovers_same_output_without_another_model_call(brain,monkeypatch,failure_point):
    _,packet,result=fixture(brain)
    other=copy.deepcopy(result['pages'][0]); other['slug']='second'; result['pages'].append(other)
    result=explicit(packet,result); calls=fake_model(monkeypatch,result)
    original_write=s._write; original_finish=w._finish_database; seen=0
    def interrupted_write(path,*args,**kwargs):
        nonlocal seen
        if args[1]=='wiki_compile':
            seen+=1
            if failure_point=='before-second-topic' and seen==2: raise OSError('injected interrupted write')
            receipt=original_write(path,*args,**kwargs)
            if failure_point=='after-first-topic' and seen==1: raise OSError('injected post-replace interruption')
            return receipt
        return original_write(path,*args,**kwargs)
    def interrupted_finish(*args): raise OSError('injected database interruption')
    monkeypatch.setattr(s,'_write',interrupted_write)
    if failure_point=='before-database': monkeypatch.setattr(w,'_finish_database',interrupted_finish)
    with pytest.raises(OSError): w.run()
    assert len(calls)==1
    monkeypatch.setattr(s,'_write',original_write); monkeypatch.setattr(w,'_finish_database',original_finish)
    recovered=w.run()
    assert recovered['status']=='complete' and recovered['batch_id']==packet['batch_id']
    assert len(calls)==1 and w.status()['pending_source_parts']==0
    assert not recovered['lint']['issues']
    journal=json.loads((cfg.STATE/'wiki/batches'/packet['batch_id']/'journal.json').read_text())
    with w.db() as conn: saved=conn.execute('SELECT * FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()
    assert journal['state']=='complete' and journal['receipts']==json.loads(saved['receipt'])
    assert (s.VAULT/w.BASE/'Log.md').read_text().count('<!-- wiki-batch:')==1


@pytest.mark.parametrize('failure',[SystemExit,OSError])
def test_database_commit_before_journal_finalization_is_repaired_by_real_run(brain,monkeypatch,failure):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=w._atomic_json
    def crash_finalization(path,value):
        if path.name=='journal.json' and value.get('state')=='complete': raise failure('simulated process death')
        return original(path,value)
    monkeypatch.setattr(w,'_atomic_json',crash_finalization)
    with pytest.raises(failure): w.run()
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='complete'
    monkeypatch.setattr(w,'_atomic_json',original)
    assert w.run()['batch_id']==packet['batch_id'] and len(calls)==1
    assert json.loads((cfg.STATE/'wiki/batches'/packet['batch_id']/'journal.json').read_text())['state']=='complete'


def test_cleanup_preserves_artifacts_for_database_journal_crash_window(brain):
    from datetime import datetime,timedelta,timezone
    _,packet,result=fixture(brain); w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; journal=folder/'journal.json'
    saved=json.loads(journal.read_text()); saved['state']='published'; journal.write_text(json.dumps(saved),encoding='utf-8')
    old=(datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
    with w.db() as conn: conn.execute('UPDATE batches SET updated=?',(old,))
    assert w.cleanup()==0
    assert (folder/'input.json').exists() and (folder/'publication.json').exists()


def test_source_revision_race_does_not_charge_untouched_batchmates(brain,monkeypatch):
    first,_,packet,result=two_sources(brain)
    calls=fake_model(monkeypatch,explicit(packet,result),lambda:first.write_text(first.read_text()+'\nOwner edit.',encoding='utf-8'))
    with pytest.raises(w.WikiTransientError,match='Source changed'): w.run()
    assert len(calls)==1 and all(row['attempts']==0 for row in source_rows().values())
    assert len(w.prepare()['sources'])==2


def test_writer_lock_timeout_preserves_saved_output_and_does_not_charge_sources(brain,monkeypatch):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=w.FileLock
    class Blocked:
        def __enter__(self): raise Timeout('synthetic writer contention')
        def __exit__(self,*args): pass
    monkeypatch.setattr(w,'FileLock',lambda path,**kwargs:Blocked() if path.endswith('writer.lock') else original(path,**kwargs))
    with pytest.raises(Timeout): w.run()
    assert not any(row['attempts'] for row in source_rows().values())
    monkeypatch.setattr(w,'FileLock',original)
    assert w.run()['status']=='complete' and len(calls)==1


def test_page_revision_race_does_not_charge_sources(brain,monkeypatch):
    source,packet,result=fixture(brain); first=w.apply(packet,result)[0]
    other=source.with_name('Continuation.md')
    other.write_text('# Example continuation\nA new historical requirement was discussed.',encoding='utf-8')
    packet=w.prepare(); result['pages'][0]['claims']=[{'text':'A historical requirement was discussed.',
        'source':'01-Projects/Continuation.md','quote':'A new historical requirement was discussed.','status':'historical'}]
    target=s.safe_path(first['path'])
    fake_model(monkeypatch,explicit(packet,result),lambda:target.write_text(target.read_text()+'\nOwner edit.',encoding='utf-8'))
    with pytest.raises(w.WikiTransientError,match='Wiki page changed'): w.run()
    assert not any(row['attempts'] for row in source_rows().values())
    assert w.status()['ready_source_parts']==1


def test_invalid_evidence_charges_only_its_source_and_new_hash_resets_retry_budget(brain,monkeypatch):
    first,_,packet,result=two_sources(brain); result=explicit(packet,result)
    result['pages'][0]['claims'][0]['quote']='A fabricated quotation that is not in the source.'
    fake_model(monkeypatch,result)
    with pytest.raises(w.WikiOutputError): w.run()
    rows=source_rows(); first_row=next(row for row in packet['sources'] if row['path'].endswith('Example.md'))
    assert rows[first_row['job_key']]['attempts']==1
    assert all(row['attempts']==0 for key,row in rows.items() if key!=first_row['job_key'])
    with w.db() as conn: conn.execute('UPDATE sources SET attempts=3 WHERE path=?',(first_row['job_key'],))
    assert all(row['job_key']!=first_row['job_key'] for row in w.prepare()['sources'])
    first.write_text(first.read_text()+'\nCorrected source evidence.',encoding='utf-8')
    assert any(row['job_key']==first_row['job_key'] for row in w.prepare()['sources'])


def test_per_source_navigation_uses_only_actual_claim_citations(brain):
    _,_,packet,result=two_sources(brain)
    row=next(row for row in packet['sources'] if row['path'].endswith('Unrelated.md'))
    result['pages'].append({'category':'Projects','slug':'unrelated','title':'Unrelated','related':[],
        'claims':[{'text':'A separate historical requirement was documented.','source':row['path'],
                   'quote':'A separate historical requirement needs review.','status':'historical'}]})
    w.apply(packet,result)
    for row in packet['sources']:
        info=w.meta((s.VAULT/w.BASE/'Sources'/f'{s.digest(row["path"].encode())[:16]}.md').read_text())
        assert len(info['wiki_pages'])==1
        assert info['wiki_pages'][0].endswith('/unrelated.md')==(row['path'].endswith('Unrelated.md'))


def test_fresh_device_hydrates_only_verified_shared_part_receipts(brain):
    _,packet,result=fixture(brain); w.apply(packet,result)
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.status()['portable_receipt_parts']==1
    assert w.prepare() is None and w.status()['pending_source_parts']==0


def test_shared_source_record_is_not_full_part_completion_proof(brain):
    source,_,result=fixture(brain)
    source.write_text(source.read_text()+'x'*25000,encoding='utf-8')
    packet=w.prepare(); assert packet['sources'][0]['parts']==2
    w.apply(packet,result)
    (cfg.STATE/'wiki-jobs.db').unlink()
    status=w.status()
    assert status['portable_receipt_parts']==1 and status['pending_source_parts']==1
    assert w.prepare()['sources'][0]['part']==2


def test_edited_or_unproved_shared_receipt_cannot_complete_a_part(brain):
    _,packet,result=fixture(brain); w.apply(packet,result)
    receipt=s.VAULT/w.BASE/'Receipts'/f'{packet["batch_id"]}.md'
    info=w.meta(receipt.read_text()); info['wiki_receipt']['parts'][0]['proofs'][0]['claims']=['0'*64]
    info['receipt_sha256']=w._json_hash(info['wiki_receipt'])
    receipt.write_text(s.note_text(info,'# Synthetic edited receipt'),encoding='utf-8')
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.status()['pending_source_parts']==1 and w.status()['portable_receipt_parts']==0


@pytest.mark.parametrize('malformed',['part-string','proof-string','proof-fingerprint-array','claim-string'])
def test_malformed_optional_receipt_or_claim_does_not_break_other_receipts(brain,malformed):
    _,packet,result=fixture(brain); w.apply(packet,result)
    folder=s.VAULT/w.BASE/'Receipts'; target=folder/'malformed.md'
    good=w.meta(next(folder.glob('*.md')).read_text())
    bad=copy.deepcopy(good)
    if malformed=='part-string': bad['wiki_receipt']['parts']=['x']
    if malformed=='proof-string': bad['wiki_receipt']['parts'][0]['proofs']=['x']
    if malformed=='proof-fingerprint-array': bad['wiki_receipt']['parts'][0]['proofs'][0]['claims']=[['x']]
    if malformed=='claim-string':
        topic=s.VAULT/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
        info['wiki_claims'].append('malformed optional legacy claim')
        topic.write_text(s.note_text(info,'# Topic'),encoding='utf-8')
    else:
        bad['receipt_sha256']=w._json_hash(bad['wiki_receipt'])
        target.write_text(s.note_text(bad,'# Malformed optional receipt'),encoding='utf-8')
    (cfg.STATE/'wiki-jobs.db').unlink()
    status=w.status()
    assert status['pending_source_parts']==0 and status['portable_receipt_parts']==1
    assert status['receipt_issue_count']==(0 if malformed=='claim-string' else 1)
    assert w.prepare() is None


def test_unattributed_invalid_output_gets_batch_backoff_without_source_blame(brain,monkeypatch):
    _,packet,result=fixture(brain)
    calls=fake_model(monkeypatch,result)  # Missing the strict model dispositions.
    with pytest.raises(w.WikiOutputError): w.run()
    state=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert state['status']=='output_backoff' and state['backoff_seconds']==3600
    assert not any(row['attempts'] for row in source_rows().values())
    with w.db() as conn: assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==1
    assert w.run()['status']=='blocked' and len(calls)==1
    assert w.status()['pending_source_parts']==1
    # Expiry retries the same durable batch and exponentially backs off again.
    monkeypatch.setattr(w.time,'time',lambda:state['retry_after']+1)
    with pytest.raises(w.WikiOutputError): w.run()
    state=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert state['backoff_seconds']==7200 and len(calls)==2
    assert not any(row['attempts'] for row in source_rows().values())
    w.reset_retries()
    assert json.loads((cfg.STATE/'wiki-worker-status.json').read_text())['retry_after']==0
    with w.db() as conn: assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0
    assert w.status()['pending_source_parts']==1


def make_legacy(packet,receipts):
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    (folder/'journal.json').write_text(json.dumps({'state':'complete','receipts':receipts}),encoding='utf-8')
    for path in (s.VAULT/w.BASE/'Receipts').glob('*.md'): path.unlink()
    if (folder/'output.json').exists(): (folder/'output.json').unlink()
    return folder


def test_legacy_migration_is_explicit_idempotent_and_works_without_output(brain):
    _,packet,result=fixture(brain); receipts=w.apply(packet,result); make_legacy(packet,receipts)
    audit=w.migrate_legacy_receipts(dry_run=True)
    assert audit['verified_parts']==1 and not audit['unverified_parts'] and not audit['receipts_created']
    assert not list((s.VAULT/w.BASE/'Receipts').glob('*.md'))
    migrated=w.migrate_legacy_receipts()
    assert len(migrated['receipts_created'])==1
    assert not w.migrate_legacy_receipts()['receipts_created']
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.prepare() is None


def test_legacy_omitted_parts_are_reported_and_only_explicitly_requeued(brain):
    _,second,packet,result=two_sources(brain)
    # Recreate the old behavior: publication succeeded for A, B was silently
    # marked processed by the historical implementation.
    narrowed={**packet,'sources':[row for row in packet['sources'] if row['path']!=second.relative_to(s.VAULT).as_posix()]}
    receipts=w.apply(narrowed,result)
    folder=make_legacy(narrowed,receipts)
    (folder/'input.json').write_text(json.dumps(packet),encoding='utf-8')
    omitted=next(row for row in packet['sources'] if row['path'].endswith('Unrelated.md'))
    with w.db() as conn:
        conn.execute('INSERT INTO sources(path,processed_hash) VALUES(?,?)',(omitted['job_key'],omitted['sha256']))
    audit=w.migrate_legacy_receipts(dry_run=True)
    assert audit['verified_parts']==1 and [row['job_key'] for row in audit['unverified_parts']]==[omitted['job_key']]
    assert source_rows()[omitted['job_key']]['processed_hash']==omitted['sha256']
    migrated=w.migrate_legacy_receipts(requeue_unverified=True)
    assert migrated['requeued_parts']==1 and w.status()['pending_source_parts']==1
    assert w.prepare()['sources'][0]['job_key']==omitted['job_key']


def test_rebuild_historical_cohort_links_preserves_metadata_and_extra_sections(brain):
    _,packet,result=fixture(brain); w.apply(packet,result)
    target=next((s.VAULT/w.BASE/'Sources').glob('*.md'))
    info=w.meta(target.read_text()); info['wiki_pages'].append(w.BASE+'Projects/not-cited.md'); info['custom_history']='preserve me'
    body='# Source\n\n## Wiki pages\n- [[old cohort]]\n\n## Review history\nPreserve this evidence.'
    target.write_text(s.note_text(info,body),encoding='utf-8')
    report=w.rebuild_source_links()
    assert report['source_records_updated']==1
    repaired=target.read_text(); assert 'Preserve this evidence.' in repaired
    assert w.meta(repaired)['custom_history']=='preserve me'
    assert w.meta(repaired)['wiki_pages']==[w.BASE+'Projects/example.md']
    assert w.rebuild_source_links()['source_records_updated']==0


@pytest.mark.parametrize('contents',['','# Hi\n',' \n\t'])
def test_short_parts_are_deterministically_skipped_without_model_call(brain,monkeypatch,contents):
    vault,_=brain; path=vault/'01-Projects/Draft.md'; path.parent.mkdir()
    original=contents.encode(); path.write_bytes(original)
    monkeypatch.setattr(w.shutil,'which',lambda name:None)
    def forbidden(*args,**kwargs): raise AssertionError('Short-only batch must not invoke a model.')
    monkeypatch.setattr(w.subprocess,'Popen',forbidden)
    completed=w.run()
    assert completed['status']=='complete' and completed['receipts']==[]
    assert w.status()['pending_source_parts']==0 and path.read_bytes()==original
    receipt=w.meta(next((vault/w.BASE/'Receipts').glob('*.md')).read_text())['wiki_receipt']['parts'][0]
    assert receipt['status']=='skipped' and receipt['quote']==''
    assert receipt['sha256']==s.digest(original) and receipt['part_sha256']==s.digest(w.normalized(contents).encode())
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.status()['portable_receipt_parts']==1 and w.prepare() is None


def test_mixed_short_and_substantive_batch_keeps_short_parts_out_of_model(brain,monkeypatch):
    source,initial,result=fixture(brain); empty=source.with_name('Blank.md'); empty.write_bytes(b'')
    # This test models the mixed prepare, not the fixture's unused earlier
    # one-source packet. Retire that synthetic packet deterministically.
    with w.db() as conn: conn.execute("UPDATE batches SET status='stale' WHERE id=? AND status='pending'",(initial['batch_id'],))
    packet=w.prepare(); modeled={**packet,'sources':[row for row in packet['sources'] if row['path']!=empty.relative_to(s.VAULT).as_posix()]}
    calls=fake_model(monkeypatch,explicit(modeled,result))
    completed=w.run()
    assert completed['status']=='complete' and len(calls)==1
    supplied=json.loads(calls.inputs[0].split('INPUT_JSON:\n',1)[1])
    assert [row['path'] for row in supplied['sources']]==['01-Projects/Example.md']
    assert w.status()['pending_source_parts']==0 and empty.read_bytes()==b''
    entries=w.meta((s.VAULT/w.BASE/'Receipts'/f'{packet["batch_id"]}.md').read_text())['wiki_receipt']['parts']
    assert sorted(entry['status'] for entry in entries)==['published','skipped']
    assert len(completed['receipts'])==1
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.status()['portable_receipt_parts']==2 and w.prepare() is None


def test_substantive_part_still_rejects_empty_skip_quote(brain):
    _,packet,_=fixture(brain); row=packet['sources'][0]
    result={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'skipped',
        'reason':'Substantive evidence requires an exact supporting source quotation.','quote':''}]}
    with pytest.raises(w.WikiOutputError): w.apply(packet,result)


def test_small_final_overlapping_part_is_skipped_without_changing_partition(brain,monkeypatch):
    source,_,result=fixture(brain); base=source.read_text()
    source.write_text(base+'x'*(23005-len(base)),encoding='utf-8'); original=source.read_bytes()
    packet=w.prepare(); assert packet['sources'][0]['part']==1 and packet['sources'][0]['parts']==2
    fake_model(monkeypatch,explicit(packet,result))
    assert w.run()['status']=='complete' and w.status()['pending_source_parts']==1
    assert w.prepare()['sources'][0]['text']=='xxxxx'
    monkeypatch.setattr(w.shutil,'which',lambda name:None)
    def forbidden(*args,**kwargs): raise AssertionError('Short final part must not invoke a model.')
    monkeypatch.setattr(w.subprocess,'Popen',forbidden)
    assert w.run()['receipts']==[] and source.read_bytes()==original
    assert w.status()['pending_source_parts']==0
    (cfg.STATE/'wiki-jobs.db').unlink()
    assert w.status()['portable_receipt_parts']==2 and w.prepare() is None


@pytest.mark.parametrize('retry_method',['expiry','explicit-reset'])
def test_deferred_part_retry_gets_new_attempt_and_can_publish(brain,monkeypatch,retry_method):
    _,packet,published=fixture(brain); row=packet['sources'][0]
    deferred={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'deferred',
        'reason':'Source needs a later focused review.','quote':'A prototype was demonstrated.'}]}
    fake_model(monkeypatch,deferred); first=w.run()
    assert first['receipts']==[] and w.status()['pending_source_parts']==1
    if retry_method=='expiry':
        later=w.time.time()+86401; monkeypatch.setattr(w.time,'time',lambda:later)
    else: w.reset_retries()
    retry=w.prepare(); assert retry['batch_id']!=first['batch_id']
    fake_model(monkeypatch,explicit(retry,published)); second=w.run()
    assert second['batch_id']==retry['batch_id'] and len(second['receipts'])==1
    assert (s.VAULT/w.BASE/'Projects/example.md').exists() and w.status()['pending_source_parts']==0


def test_multiple_consecutive_defers_keep_distinct_attempt_history(brain,monkeypatch):
    _,packet,_=fixture(brain); row=packet['sources'][0]
    deferred={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'deferred',
        'reason':'Source needs a later focused review.','quote':'A prototype was demonstrated.'}]}
    calls=fake_model(monkeypatch,deferred); ids=[]; now=w.time.time()
    for cycle in range(3):
        monkeypatch.setattr(w.time,'time',lambda:now+cycle*86401)
        ids.append(w.run()['batch_id'])
    assert len(set(ids))==3 and len(calls)==3
    assert w.status()['pending_source_parts']==1
    assert len(list((s.VAULT/w.BASE/'Receipts').glob('*.md')))==3


def test_fresh_device_does_not_overwrite_a_shared_completed_defer_receipt(brain,monkeypatch):
    _,packet,published=fixture(brain); row=packet['sources'][0]
    deferred={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'deferred',
        'reason':'Source needs a later focused review.','quote':'A prototype was demonstrated.'}]}
    fake_model(monkeypatch,deferred); first=w.run()
    original=(s.VAULT/w.BASE/'Receipts'/f'{first["batch_id"]}.md').read_bytes()
    (cfg.STATE/'wiki-jobs.db').unlink()
    retry=w.prepare(); assert retry['batch_id']!=first['batch_id']
    fake_model(monkeypatch,explicit(retry,published)); assert len(w.run()['receipts'])==1
    assert (s.VAULT/w.BASE/'Receipts'/f'{first["batch_id"]}.md').read_bytes()==original


def test_migration_recognizes_new_explicit_short_skip_proofs(brain,monkeypatch):
    vault,_=brain; source=vault/'01-Projects/Blank.md'; source.parent.mkdir(); source.write_bytes(b'')
    monkeypatch.setattr(w.shutil,'which',lambda name:None)
    assert w.run()['status']=='complete'
    report=w.migrate_legacy_receipts(dry_run=True,requeue_unverified=True)
    assert report['verified_parts']==1 and not report['unverified_parts'] and report['requeued_parts']==0


@pytest.mark.parametrize('bad',[[], {'pages':None,'dispositions':[]}, {'dispositions':[]},
    {'pages':[{'category':'Projects','slug':'example','title':'Example','related':[],'claims':None}],'dispositions':[]},
    {'pages':[],'dispositions':[None]}])
def test_parsed_malformed_model_shapes_get_controlled_batch_backoff(brain,monkeypatch,bad):
    fixture(brain); fake_model(monkeypatch,bad)
    with pytest.raises(Exception): w.run()
    saved=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert saved['status']=='output_backoff' and saved['backoff_seconds']==3600
    assert saved['retry_after']>w.time.time()
    assert not any(row['attempts'] for row in source_rows().values())


@pytest.mark.parametrize('newline,bom',[('\r\n',False),('\r',False),('\n',True),('\r\n',True)])
def test_rebuild_source_links_handles_crlf_cr_and_bom_without_duplicate_frontmatter(brain,newline,bom):
    _,packet,result=fixture(brain); w.apply(packet,result)
    target=next((s.VAULT/w.BASE/'Sources').glob('*.md'))
    info=w.meta(target.read_text()); info['wiki_pages'].append(w.BASE+'Projects/not-cited.md'); info['custom_history']='preserve me'
    body='# Source\n\n## Wiki pages\n- [[old cohort not-cited.md]]\n\n## Review history\nPreserve this evidence.'
    text=s.note_text(info,body).replace('\n',newline)
    target.write_bytes((('\ufeff' if bom else '')+text).encode('utf-8'))
    assert w.rebuild_source_links()['source_records_updated']==1
    repaired=target.read_text(encoding='utf-8-sig')
    assert repaired.splitlines().count('---')==2
    assert 'Preserve this evidence.' in repaired and 'not-cited.md' not in repaired
    assert w.meta(repaired)['custom_history']=='preserve me'


@pytest.mark.parametrize('corruption',['original-target','changed-text','missing-required-write','changed-source-disposition'])
def test_saved_recovery_plan_cannot_modify_originals_or_bypass_planned_evidence(brain,monkeypatch,corruption):
    source,packet,result=fixture(brain); original=source.read_bytes()
    result=explicit(packet,result); fake_model(monkeypatch,result)
    original_write=s._write
    def fail_before_write(*args,**kwargs): raise OSError('injected before first note write')
    monkeypatch.setattr(s,'_write',fail_before_write)
    with pytest.raises(OSError): w.run()
    publication=cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json'
    plan=json.loads(publication.read_text())
    if corruption=='original-target':
        plan['writes'][0]['path']=source.relative_to(s.VAULT).as_posix()
        plan['writes'][0]['before']=s.digest(original)
    if corruption=='changed-text': plan['writes'][0]['text']+='\nUnverified alteration.'
    if corruption=='missing-required-write': plan['writes'].pop()
    if corruption=='changed-source-disposition': plan['entries'][0]['status']='skipped'
    publication.write_text(json.dumps(plan),encoding='utf-8')
    monkeypatch.setattr(s,'_write',original_write)
    with pytest.raises(w.WikiOutputError): w.run()
    assert source.read_bytes()==original
    assert not (s.VAULT/w.BASE/'Projects').exists()
    assert not any(row['attempts'] for row in source_rows().values())


def test_stale_non_catalog_target_gets_new_attempt_and_preserves_owner_log(brain,monkeypatch):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=w._atomic_json; edited=False; log=s.VAULT/w.BASE/'Log.md'
    def edit_after_plan(path,value):
        nonlocal edited
        original(path,value)
        if path.name=='publication.json' and not edited:
            log.parent.mkdir(parents=True,exist_ok=True)
            log.write_text('# Wiki compilation log\n\nOwner review evidence must survive retry.\n',encoding='utf-8')
            edited=True
    monkeypatch.setattr(w,'_atomic_json',edit_after_plan)
    with pytest.raises(w.WikiTransientError): w.run()
    assert len(calls)==1 and w.status()['pending_source_parts']==1
    retry=w.prepare(); assert retry['batch_id']!=packet['batch_id']
    saved=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert saved['backoff_seconds']==3600 and w.run()['status']=='blocked' and len(calls)==1
    later=saved['retry_after']+1; monkeypatch.setattr(w.time,'time',lambda:later)
    completed=w.run()
    assert completed['status']=='complete' and completed['batch_id']==retry['batch_id'] and len(calls)==2
    assert 'Owner review evidence must survive retry.' in log.read_text()
    assert w.status()['pending_source_parts']==0
    assert (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists()


@pytest.mark.parametrize('field',['reason','quote'])
@pytest.mark.parametrize('value',['password = syntheticabcdefghijk123456789','invalid\x00text'])
def test_published_disposition_metadata_is_guarded_before_any_publication(brain,field,value):
    source,packet,result=fixture(brain); original=source.read_bytes(); result=explicit(packet,result)
    result['dispositions'][0][field]=value
    with pytest.raises(ValueError): w.apply(packet,result)
    assert not (s.VAULT/w.BASE/'Projects').exists()
    assert not (s.VAULT/w.BASE/'Receipts').exists()
    assert source.read_bytes()==original and w.status()['pending_source_parts']==1


@pytest.mark.parametrize('mode',['nonzero','no-output','timeout','persistent-stale','render-error','invalid-schema'])
def test_repeated_worker_failures_stop_after_three_without_source_blame(brain,monkeypatch,mode):
    source,packet,result=fixture(brain); result=explicit(packet,result); calls=[]
    original_note=w._source_note; original_atomic=w._atomic_json
    monkeypatch.setattr(w.shutil,'which',lambda name:'synthetic-codex')
    monkeypatch.setattr(w.subprocess,'run',lambda *args,**kwargs:None)
    if hasattr(w.os,'killpg'): monkeypatch.setattr(w.os,'killpg',lambda *args:None)
    class Process:
        pid=123456789
        def __init__(self,command,**kwargs):
            calls.append(command); self.output=Path(command[command.index('-o')+1]); self.returncode=1 if mode=='nonzero' else 0
        def communicate(self,model_input,timeout):
            if mode=='timeout': raise w.subprocess.TimeoutExpired('synthetic-codex',timeout)
            if mode not in ('nonzero','no-output'):
                self.output.write_text(json.dumps({'pages':None} if mode=='invalid-schema' else result),encoding='utf-8')
        def wait(self,timeout): return 1
    monkeypatch.setattr(w.subprocess,'Popen',Process)
    if mode=='render-error':
        def fail_render(*args): raise RuntimeError('synthetic persistent rendering failure')
        monkeypatch.setattr(w,'_source_note',fail_render)
    if mode=='persistent-stale':
        def edit_log_after_plan(path,value):
            original_atomic(path,value)
            if path.name=='publication.json':
                log=s.VAULT/w.BASE/'Log.md'; log.parent.mkdir(parents=True,exist_ok=True)
                log.write_text(f'# Owner log revision {len(calls)}\n',encoding='utf-8')
        monkeypatch.setattr(w,'_atomic_json',edit_log_after_plan)
    clock=[w.time.time()]; monkeypatch.setattr(w.time,'time',lambda:clock[0])
    for attempt in range(3):
        with pytest.raises(Exception): w.run()
        saved=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
        clock[0]=max(clock[0]+1,saved.get('retry_after',0)+1)
    try: fourth=w.run()
    except Exception: fourth={'status':'unhandled'}
    assert fourth['status']=='attention' and len(calls)==3
    assert not any(row['attempts'] for row in source_rows().values())
    assert w.status()['pending_source_parts']==1 and source.exists()
    saved=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert saved['consecutive_failures']==3 and saved['attention_required'] is True
    w.reset_retries(); monkeypatch.setattr(w,'_source_note',original_note); monkeypatch.setattr(w,'_atomic_json',original_atomic)
    fake_model(monkeypatch,result)
    assert w.run()['status']=='complete' and w.status()['pending_source_parts']==0


def test_missing_input_migration_cannot_infer_adjacent_overlap_part(brain):
    source,_,result=fixture(brain); quote='The overlap contains a reusable historical statement.'
    source.write_text('a'*23000+quote+'b'*2000,encoding='utf-8')
    packet=w.prepare(); first=packet['sources'][0]; assert first['parts']==2
    result['pages'][0]['claims'][0]['quote']=quote
    receipts=w.apply(packet,result); folder=make_legacy(packet,receipts); (folder/'input.json').unlink()
    second=next(row for row in w.inventory() if row['part']==2)
    with w.db() as conn: conn.execute('INSERT INTO sources(path,processed_hash) VALUES(?,?)',(second['job_key'],second['sha256']))
    report=w.migrate_legacy_receipts(dry_run=True)
    assert report['verified_parts']==0
    assert {entry['job_key'] for entry in report['unverified_parts']}=={first['job_key'],second['job_key']}


@pytest.mark.parametrize('malformed',['scalar-entry','null-collection','mapping-collection'])
def test_unrelated_run_reports_malformed_legacy_claim_without_crashing(brain,monkeypatch,malformed):
    source,packet,result=fixture(brain); receipt=w.apply(packet,result)[0]
    topic=s.safe_path(receipt['path']); info=w.meta(topic.read_text())
    if malformed=='scalar-entry': info['wiki_claims'].append('preserve malformed legacy evidence')
    else: info['wiki_claims']=None if malformed=='null-collection' else {'owner':'preserve malformed legacy evidence'}
    topic.write_text(s.note_text(info,'# Existing derived page\n\nPreserve owner history.'),encoding='utf-8'); before=topic.read_bytes()
    other=source.with_name('Other.md'); other.write_text('# Separate subject\nA separate historical requirement was discussed.',encoding='utf-8')
    packet=w.prepare(); result['pages'][0]['slug']='other'; result['pages'][0]['claims']=[{
        'text':'A separate requirement was documented.','source':'01-Projects/Other.md',
        'quote':'A separate historical requirement was discussed.','status':'historical'}]
    fake_model(monkeypatch,explicit(packet,result)); completed=w.run()
    assert completed['status']=='complete' and w.status()['pending_source_parts']==0
    assert topic.read_bytes()==before
    assert any(issue['page']==receipt['path'] and issue['issue']=='malformed claim metadata' for issue in completed['lint']['issues'])


def test_same_topic_with_malformed_claim_stops_for_integrity_attention(brain,monkeypatch):
    source,packet,result=fixture(brain); receipt=w.apply(packet,result)[0]
    target=s.safe_path(receipt['path']); info=w.meta(target.read_text()); info['wiki_claims'].append('preserve malformed legacy evidence')
    target.write_text(s.note_text(info,'# Existing derived page\n\nPreserve owner history.'),encoding='utf-8'); before=target.read_bytes()
    source.write_text(source.read_text()+'\nA later historical review remains pending.',encoding='utf-8')
    calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError): w.prepare()
    assert w.run()['status']=='attention' and len(calls)==0
    saved=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert saved['attention_required'] is True and saved['integrity_page']==receipt['path']
    assert target.read_bytes()==before and w.status()['pending_source_parts']==1
    assert any(issue['issue']=='malformed claim metadata' for issue in w.lint()['issues'])


def test_attributed_part_failures_retire_only_that_part_and_peers_proceed(brain,monkeypatch):
    _,_,packet,result=two_sources(brain); result=explicit(packet,result)
    result['pages'][0]['claims'][0]['quote']='An invalid model quotation absent from the selected source.'
    calls=fake_model(monkeypatch,result)
    for attempt in range(3):
        with pytest.raises(w.WikiOutputError): w.run()
    assert len(calls)==3 and not w.status()['worker']['attention_required']
    packet=w.prepare(); assert len(packet['sources'])==1 and packet['sources'][0]['path'].endswith('Unrelated.md')
    row=packet['sources'][0]
    result={'pages':[{'category':'Projects','slug':'unrelated','title':'Unrelated','related':[],
        'claims':[{'text':'A separate requirement was documented.','source':row['path'],
                   'quote':'A separate historical requirement needs review.','status':'historical'}]}]}
    fake_model(monkeypatch,explicit(packet,result))
    assert w.run()['status']=='complete'
    assert w.status()['pending_source_parts']==1 and w.status()['failed_source_parts']==1


def test_repeated_writer_contention_does_not_exhaust_attention_or_repeat_model(brain,monkeypatch):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result)); original=w.FileLock
    class Blocked:
        def __enter__(self): raise Timeout('synthetic writer contention')
        def __exit__(self,*args): pass
    monkeypatch.setattr(w,'FileLock',lambda path,**kwargs:Blocked() if path.endswith('writer.lock') else original(path,**kwargs))
    for attempt in range(4):
        with pytest.raises(Timeout): w.run()
    assert len(calls)==1
    assert w.status()['worker']['consecutive_failures']==0 and not w.status()['worker']['attention_required']
    monkeypatch.setattr(w,'FileLock',original)
    assert w.run()['status']=='complete' and len(calls)==1


def test_legacy_exact_packet_output_proves_overlap_only_for_selected_part(brain):
    source,_,result=fixture(brain); quote='The overlap contains a reusable historical statement.'
    source.write_text('a'*23000+quote+'b'*2000,encoding='utf-8'); packet=w.prepare()
    result['pages'][0]['claims'][0]['quote']=quote
    receipts=w.apply(packet,result); folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    (folder/'journal.json').write_text(json.dumps({'state':'complete','receipts':receipts}),encoding='utf-8')
    for path in (s.VAULT/w.BASE/'Receipts').glob('*.md'): path.unlink()
    second=next(row for row in w.inventory() if row['part']==2)
    with w.db() as conn: conn.execute('INSERT INTO sources(path,processed_hash) VALUES(?,?)',(second['job_key'],second['sha256']))
    report=w.migrate_legacy_receipts(dry_run=True)
    assert report['verified_parts']==1 and report['verified_source_parts'][0]['part']==1
    assert report['verified_source_parts'][0]['proof_mode']=='exact-packet-and-output'
    assert [row['part'] for row in report['unverified_parts']]==[2]


@pytest.mark.parametrize('state',[None,[],{'status':'error','retry_after':'later'},'invalid json'])
def test_worker_status_is_compact_controlled_and_malformed_state_never_calls_model(brain,monkeypatch,state):
    fixture(brain)
    if state is not None:
        (cfg.STATE/'wiki-worker-status.json').write_text(state if isinstance(state,str) else json.dumps(state),encoding='utf-8')
    summary=w.status()['worker']
    assert set(summary)<={'status','attention_required','consecutive_failures','retry_after','reason','batch_id','integrity_page'}
    if state is None:
        assert summary=={'status':'idle','attention_required':False,'consecutive_failures':0,'retry_after':0}
    else:
        assert summary['attention_required'] is True
        def forbidden(*args,**kwargs): raise AssertionError('Malformed worker state requires explicit operator action.')
        monkeypatch.setattr(w.subprocess,'Popen',forbidden)
        assert w.run()['status']=='attention'


def test_legacy_committed_unfinished_journal_is_quarantined_without_fabricating_proof(brain,monkeypatch):
    source,packet,result=fixture(brain); receipts=w.apply(packet,result); folder=make_legacy(packet,receipts)
    (folder/'publication.json').unlink()
    journal=folder/'journal.json'; journal.write_text(json.dumps({'state':'applying','targets':[w.BASE+'Projects/example.md']}),encoding='utf-8'); unfinished=journal.read_bytes()
    with w.db() as conn: conn.execute('UPDATE batches SET updated=? WHERE id=?',('2000-01-01T00:00:00+00:00',packet['batch_id']))
    other=source.with_name('Other.md'); quote='A separate historical requirement needs review.'
    other.write_text('# Other\n'+quote,encoding='utf-8'); fresh=w.prepare()
    result['pages'][0].update(slug='other',title='Other'); result['pages'][0]['claims'][0].update(source='01-Projects/Other.md',quote=quote)
    calls=fake_model(monkeypatch,explicit(fresh,result)); completed=w.run()
    assert completed['batch_id']==fresh['batch_id'] and len(calls)==1 and w.run()['status']=='idle'
    assert journal.read_bytes()==unfinished
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='legacy_unverified'
    assert w.status()['recovery_issue_count']==1
    migration=w.migrate_legacy_receipts(dry_run=True)
    assert any(item['batch_id']==packet['batch_id'] for item in migration['rejected_batches'])
    assert any(item['path']=='01-Projects/Example.md' for item in migration['unverified_parts'])


@pytest.mark.parametrize('disposition',['published','deferred'])
def test_obsolete_no_plan_packet_honors_new_verified_completion_or_deferral(brain,monkeypatch,disposition):
    source,old,result=fixture(brain)
    with w.db() as conn: conn.execute('UPDATE batches SET updated=? WHERE id=?',('2000-01-01T00:00:00+00:00',old['batch_id']))
    other=source.with_name('Other.md'); quote='A separate historical requirement needs review.'
    other.write_text('# Other\n'+quote,encoding='utf-8'); newer=w.prepare()
    second=copy.deepcopy(result['pages'][0]); second.update(slug='other',title='Other'); second['claims'][0].update(source='01-Projects/Other.md',quote=quote)
    combined={'pages':result['pages']+[second] if disposition=='published' else [second]}
    w.apply(newer,explicit(newer,combined))
    # Preserve an old, unused output as evidence; superseding cannot delete it.
    abandoned=cfg.STATE/'wiki/batches'/old['batch_id']/'output.json'; abandoned.write_text('unused historical model output',encoding='utf-8')
    if disposition=='published':
        third=source.with_name('Third.md'); third_quote='A third source has a historical requirement.'
        third.write_text('# Third\n'+third_quote,encoding='utf-8'); fresh=w.prepare()
        result['pages'][0].update(slug='third',title='Third'); result['pages'][0]['claims'][0].update(source='01-Projects/Third.md',quote=third_quote)
        calls=fake_model(monkeypatch,explicit(fresh,result)); assert w.run()['batch_id']==fresh['batch_id'] and len(calls)==1
    else:
        def forbidden(*args,**kwargs): raise AssertionError('An abandoned packet cannot bypass a later explicit defer.')
        monkeypatch.setattr(w.subprocess,'Popen',forbidden)
        assert w.run()['status']=='idle' and w.status()['deferred_source_parts']==1
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(old['batch_id'],)).fetchone()[0]=='superseded'
    assert abandoned.read_text()=='unused historical model output'
