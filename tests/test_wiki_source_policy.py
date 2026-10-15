"""Owner exclusions alter only the shared wiki queue, never original evidence."""
import copy
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model


def write_policy(paths=(),prefixes=(),*,raw=None,newline='\n',bom=False):
    path=s.VAULT/w.SOURCE_POLICY; path.parent.mkdir(parents=True,exist_ok=True)
    rules={'version':1,'exclude_paths':list(paths),'exclude_prefixes':list(prefixes)}
    body=raw if raw is not None else '# Owner source policy\n\nOriginal notes remain searchable.\n\n```json\n'+json.dumps(rules,ensure_ascii=False)+'\n```\n'
    path.write_bytes((('\ufeff' if bom else '')+body.replace('\n',newline)).encode())
    return path


def test_absent_policy_preserves_prior_queue_contract(brain):
    source,packet,result=fixture(brain)
    assert 'source_policy_rules_sha256' not in packet
    assert w.status()['source_policy']['present'] is False
    assert len(w.inventory())==len(w.inventory(include_excluded=True))==1
    w.apply(packet,result)
    report=w.status()
    assert report['portable_receipt_parts']==report['eligible_portable_receipt_parts']==1
    assert report['excluded_verified_source_parts']==report['excluded_source_parts']==0


def test_exact_and_directory_boundary_exclusions_leave_originals_retrievable_and_pending_history_intact(brain):
    source,_,_=fixture(brain); relative=source.relative_to(s.VAULT).as_posix()
    excluded=s.VAULT/'01-Projects/Temporary/a.md'; excluded.parent.mkdir()
    excluded.write_bytes(('A temporary research handoff.\n'+'x'*48000).encode())
    included=s.VAULT/'01-Projects/Temporary-Useful/b.md'; included.parent.mkdir()
    included.write_bytes(b'A concise meaningful reusable project decision.')
    original={path:path.read_bytes() for path in (source,excluded,included)}
    all_rows=w.inventory(include_excluded=True)
    write_policy([relative],['01-Projects/Temporary/'])
    report=w.status()
    assert report['eligible_sources']==report['eligible_source_parts']==report['pending_source_parts']==1
    excluded_rows=[row for row in all_rows if row['path']!=included.relative_to(s.VAULT).as_posix()]
    assert report['excluded_sources']==2 and report['excluded_source_parts']==len(excluded_rows)
    assert report['excluded_pending_source_parts']==len(excluded_rows)
    assert report['excluded_verified_source_parts']==0
    assert {row['path'] for row in w.inventory()}=={included.relative_to(s.VAULT).as_posix()}
    assert len(w.inventory(include_excluded=True))==len(all_rows)
    assert w.prepare()['sources'][0]['path']==included.relative_to(s.VAULT).as_posix()
    assert 'prototype' in s.read(relative)['content']
    assert all(path.read_bytes()==raw for path,raw in original.items())
    with w.db() as conn:
        assert all(row['processed_hash'] is None for row in conn.execute('SELECT * FROM sources'))


def test_excluded_historical_receipts_remain_verified_and_reinclude_without_model(brain,monkeypatch):
    source,packet,result=fixture(brain); w.apply(packet,result)
    original=source.read_bytes(); topic=s.VAULT/w.BASE/'Projects/example.md'; topic_raw=topic.read_bytes()
    relative=source.relative_to(s.VAULT).as_posix(); policy=write_policy([relative])
    (cfg.STATE/'wiki-jobs.db').unlink()
    report=w.status()
    assert report['portable_receipt_parts']==report['excluded_verified_source_parts']==1
    assert report['eligible_portable_receipt_parts']==report['eligible_source_parts']==report['pending_source_parts']==0
    assert report['excluded_source_parts']==1 and report['excluded_pending_source_parts']==0
    monkeypatch.setattr(w.shutil,'which',lambda name:pytest.fail('Excluded work must not resolve or call the model'))
    assert w.run()['status']=='idle'
    policy.unlink()
    report=w.status()
    assert report['eligible_portable_receipt_parts']==1 and report['pending_source_parts']==0
    assert source.read_bytes()==original and topic.read_bytes()==topic_raw


def test_policy_hash_and_case_sensitive_matching_are_deterministic_across_eol_bom_and_rule_order(brain):
    source,_,_=fixture(brain); relative=source.relative_to(s.VAULT).as_posix()
    paths=[relative,'01-Projects/Other.md']; write_policy(paths,['03-Resources/Research/'])
    first=w.source_policy(); assert w.inventory()==[]
    write_policy(reversed(paths),['03-Resources/Research/'],newline='\r\n',bom=True)
    second=w.source_policy()
    assert first['rules_sha256']==second['rules_sha256'] and first['sha256']!=second['sha256']
    assert w.inventory()==[]
    write_policy([relative.replace('Example.md','example.md')])
    assert len(w.inventory())==1  # Exact case is portable and never OS-casefolded.
    assert w.status()['excluded_sources']==0


INVALID_RULES=[
    {'version':True,'exclude_paths':[],'exclude_prefixes':[]},
    {'version':1.0,'exclude_paths':[],'exclude_prefixes':[]},
    {'version':2,'exclude_paths':[],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':[],'exclude_prefixes':[],'extra':True},
    {'version':1,'exclude_paths':'01-Projects/Example.md','exclude_prefixes':[]},
    {'version':1,'exclude_paths':[None],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/Example.md']*2,'exclude_prefixes':[]},
    {'version':1,'exclude_paths':[],'exclude_prefixes':['01-Projects/']*2},
    {'version':1,'exclude_paths':['01-Projects/*.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/[ab].md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/../Outside.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects\\Example.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/Hidden\n.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['C:/Outside.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['/01-Projects/Example.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['02-Areas/AI-Memory/Profile.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['Unknown/Example.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/Example.MD'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/.hidden.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':['01-Projects/CON.md'],'exclude_prefixes':[]},
    {'version':1,'exclude_paths':[],'exclude_prefixes':['01-Projects/Temporary']},
    {'version':1,'exclude_paths':[],'exclude_prefixes':['02-Areas/AI-Memory/']},
    {'version':1,'exclude_paths':['01-Projects/'+str(i)+'.md' for i in range(2049)],'exclude_prefixes':[]},
]


@pytest.mark.parametrize('rules',INVALID_RULES)
def test_invalid_policy_stops_status_and_run_without_omitting_or_completing_sources(brain,monkeypatch,rules):
    source,packet,_=fixture(brain); original=source.read_bytes(); database=(cfg.STATE/'wiki-jobs.db').read_bytes()
    write_policy(raw='```json\n'+json.dumps(rules)+'\n```\n')
    monkeypatch.setattr(w.shutil,'which',lambda name:pytest.fail('Invalid policy must block model access'))
    report=w.status()
    assert report['status']=='attention' and report['source_policy']['valid'] is False
    assert report['pending_source_parts'] is None and report['eligible_source_parts'] is None
    assert report['excluded_source_parts'] is None and w.SOURCE_POLICY in report['reason']
    assert w.run()['status']=='attention'
    with pytest.raises(w.WikiPolicyError): w.prepare()
    assert source.read_bytes()==original and (cfg.STATE/'wiki-jobs.db').read_bytes()==database


@pytest.mark.parametrize('body',[
    '{}',
    '```json\n{"version":1,"version":1,"exclude_paths":[],"exclude_prefixes":[]}\n```',
    '```json\n{"version":1,"exclude_paths":[],"exclude_prefixes":[],"x":NaN}\n```',
    '```json\n[]\n```',
    '```json\n{invalid private json}\n```',
    '```json\n'+json.dumps({'version':1,'exclude_paths':[],'exclude_prefixes':[]})+'\n```\n```text\nextra\n```',
    '```json\n'+'['*2000+'0'+']'*2000+'\n```',
    'x'*131073,
],ids=['missing-fence','duplicate-keys','nonfinite','scalar-root','invalid-json','multiple-blocks','deep-json','oversize'])
def test_invalid_fencing_json_duplicate_keys_nonfinite_and_bounds_are_controlled(brain,body):
    fixture(brain); write_policy(raw=body)
    report=w.status()
    assert report['status']=='attention' and report['pending_source_parts'] is None
    assert 'invalid private json' not in report['reason']


def test_policy_change_after_prepare_prevents_new_publication(brain):
    source,packet,result=fixture(brain); write_policy([source.relative_to(s.VAULT).as_posix()])
    with pytest.raises(w.WikiPolicyChanged): w.apply(packet,result)
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='pending'


def test_unused_pending_output_is_superseded_after_exclusion_without_model(brain,monkeypatch):
    source,packet,result=fixture(brain)
    output=cfg.STATE/'wiki/batches'/packet['batch_id']/'output.json'
    output.write_text(json.dumps(explicit(packet,result)),encoding='utf-8')
    write_policy([source.relative_to(s.VAULT).as_posix()])
    monkeypatch.setattr(w.shutil,'which',lambda name:pytest.fail('Excluded unused work must not call model'))
    assert w.run()['status']=='idle'
    assert output.exists()
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()


def test_exclusion_between_resume_and_model_call_blocks_quota_and_preserves_batch(brain,monkeypatch):
    source,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=w._resume_packet
    def owner_decision(policy=None):
        selected=original(policy); write_policy([source.relative_to(s.VAULT).as_posix()]); return selected
    monkeypatch.setattr(w,'_resume_packet',owner_decision)
    report=w.run()
    assert report['status']=='policy_changed' and report['model_called'] is False and len(calls)==0
    assert w.status()['pending_source_parts']==0 and w.status()['excluded_pending_source_parts']==1


def test_malformed_policy_between_resume_and_model_call_blocks_without_quota(brain,monkeypatch):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=w._resume_packet
    def owner_edit(policy=None):
        selected=original(policy); write_policy(raw='```json\n{bad}\n```'); return selected
    monkeypatch.setattr(w,'_resume_packet',owner_edit)
    assert w.run()['status']=='attention' and len(calls)==0
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()


def test_exclusion_during_model_response_prevents_publication_without_source_blame(brain,monkeypatch):
    source,packet,result=fixture(brain)
    calls=fake_model(monkeypatch,explicit(packet,result),lambda:write_policy([source.relative_to(s.VAULT).as_posix()]))
    assert w.run()['status']=='policy_changed' and len(calls)==1
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
    assert (cfg.STATE/'wiki/batches'/packet['batch_id']/'output.json').exists()


def test_malformed_policy_after_model_preserves_output_and_repair_reuses_it(brain,monkeypatch):
    _,packet,result=fixture(brain)
    calls=fake_model(monkeypatch,explicit(packet,result),lambda:write_policy(raw='```json\n{bad}\n```'))
    assert w.run()['status']=='attention' and len(calls)==1
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    assert (cfg.STATE/'wiki/batches'/packet['batch_id']/'output.json').exists()
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
    write_policy()
    assert w.run()['status']=='complete' and len(calls)==1


def test_exclusion_during_planning_prevents_plan_commit_and_first_note_write(brain,monkeypatch):
    source,packet,result=fixture(brain); original=w._publication_plan
    def owner_decision(*args):
        plan=original(*args); write_policy([source.relative_to(s.VAULT).as_posix()]); return plan
    monkeypatch.setattr(w,'_publication_plan',owner_decision)
    with pytest.raises(w.WikiPolicyChanged): w.apply(packet,result)
    assert not (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists()
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()


def test_partial_publication_recovery_can_finish_excluded_source_without_another_model_call(brain,monkeypatch):
    source,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=s._write
    def stop_after_first_note(*args,**kwargs):
        receipt=original(*args,**kwargs)
        if (args[2] if len(args)>2 else kwargs.get('kind'))=='wiki_compile':
            raise OSError('Injected replace-before-journal interruption')
        return receipt
    monkeypatch.setattr(s,'_write',stop_after_first_note)
    with pytest.raises(OSError): w.run()
    assert (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists() and len(calls)==1
    write_policy([source.relative_to(s.VAULT).as_posix()]); monkeypatch.setattr(s,'_write',original)
    report=w.run()
    assert report['status']=='complete' and report['recovered_excluded_source_parts']==1 and len(calls)==1
    status=w.status()
    assert status['eligible_source_parts']==status['eligible_portable_receipt_parts']==0
    assert status['excluded_verified_source_parts']==status['portable_receipt_parts']==1
    assert status['excluded_pending_source_parts']==0
    assert not report['lint']['issues']


def test_unused_durable_plan_is_superseded_after_exclusion_without_new_model(brain,monkeypatch):
    source,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=s._write
    monkeypatch.setattr(s,'_write',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('Injected interruption before note writes')))
    with pytest.raises(OSError): w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    assert (folder/'publication.json').exists() and len(calls)==1
    write_policy([source.relative_to(s.VAULT).as_posix()]); monkeypatch.setattr(s,'_write',original)
    assert w.run()['status']!='complete' and len(calls)==1
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
        assert conn.execute('SELECT count(*) FROM sources').fetchone()[0]==0
    assert (folder/'publication.json').exists() and (folder/'output.json').exists()


@pytest.mark.parametrize('stage',['input.json','output.json','publication.json','journal.json'])
def test_owner_exclusion_during_fresh_persistence_cannot_gain_recovery_exemption(brain,monkeypatch,stage):
    source,packet,result=fixture(brain); original_source=source.read_bytes()
    original=w._atomic_json; changed=[]
    def owner_edit(path,value):
        if path.name==stage and not changed:
            changed.append(stage); write_policy([source.relative_to(s.VAULT).as_posix()])
        return original(path,value)
    monkeypatch.setattr(w,'_atomic_json',owner_edit)
    with pytest.raises(w.WikiPolicyChanged): w.apply(packet,result)
    assert changed and source.read_bytes()==original_source
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    calls=fake_model(monkeypatch,explicit(packet,result))
    assert w.run()['status']!='complete' and len(calls)==0
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
        assert conn.execute('SELECT count(*) FROM sources').fetchone()[0]==0


def test_partial_publication_stops_on_invalid_policy_and_repairs_without_new_call(brain,monkeypatch):
    source,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result))
    original=s._write; changed=[]
    def owner_invalidates(path,text,kind,*args,**kwargs):
        receipt=original(path,text,kind,*args,**kwargs)
        if kind=='wiki_compile' and not changed:
            changed.append(True); write_policy(raw='```json\n{bad}\n```')
        return receipt
    monkeypatch.setattr(s,'_write',owner_invalidates)
    assert w.run()['status']=='attention' and len(calls)==1
    assert (s.VAULT/w.BASE/'Projects/example.md').exists()
    assert not (s.VAULT/w.BASE/'Index.md').exists()
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources').fetchone()[0]==0
    write_policy([source.relative_to(s.VAULT).as_posix()])
    report=w.run()
    assert report['status']=='complete' and report['recovered_excluded_source_parts']==1 and len(calls)==1
    assert not report['lint']['issues']


def test_unchanged_planned_notes_do_not_prove_partial_publication(brain,monkeypatch):
    source,packet,_=fixture(brain)
    monkeypatch.setattr(s,'stamp',lambda:'2026-10-15T10:00:00+05:00')
    row=packet['sources'][0]
    result={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'deferred',
             'reason':'The source needs a later focused evidence review.','quote':'A prototype was demonstrated.'}]}
    w.apply(packet,result)
    now=w.time.time(); monkeypatch.setattr(w.time,'time',lambda:now+86401)
    retry=w.prepare(); assert retry and retry['batch_id']!=packet['batch_id']
    original=s._write
    def interrupted(*args,**kwargs): raise OSError('Interrupted before any changed note')
    monkeypatch.setattr(s,'_write',interrupted)
    with pytest.raises(OSError): w.apply(retry,result)
    publication=cfg.STATE/'wiki/batches'/retry['batch_id']/'publication.json'
    plan=json.loads(publication.read_text())
    assert any(write['before']==write['after'] for write in plan['writes'])
    assert not w._saved_publication_started(retry,publication)
    write_policy([source.relative_to(s.VAULT).as_posix()]); monkeypatch.setattr(s,'_write',original)
    monkeypatch.setattr(w.shutil,'which',lambda *_:pytest.fail('Unused excluded plan cannot call model'))
    assert w.run()['status']=='idle'
    assert '<!-- wiki-batch:'+retry['batch_id'] not in (s.VAULT/w.BASE/'Log.md').read_text()
    with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(retry['batch_id'],)).fetchone()[0]=='superseded'
