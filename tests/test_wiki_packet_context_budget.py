"""Executed synthetic full-worker triage of Claude R16 F1; no real providers."""
import copy,json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_saved_catalog_identity import brain
from test_wiki import fixture
from test_wiki_model_context_preflight import fake_skip_model

OUT=Path(__file__).resolve().parent
def snapshot(root):return {p.relative_to(root).as_posix():s.digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
def record(name,value):pass
def attempt():
    try:return w.run(limit=1),None
    except Exception as error:return None,type(error).__name__
def state():return w._read_worker_state()

@pytest.mark.parametrize('mode',['fresh','saved'])
@pytest.mark.parametrize('topic_count',[1000,2300])
def test_actual_catalog_within_packet_budget_compiles_with_bounded_model_context(brain,monkeypatch,mode,topic_count):
    source,initial,result=fixture(brain);original=source.read_bytes()
    with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(initial['batch_id'],))
    topics=s.VAULT/w.BASE/'Concepts';topics.mkdir(parents=True)
    for number in range(topic_count):
        (topics/('catalog-'+str(number).zfill(5)+'.md')).write_bytes(s.note_text({'wiki_kind':'Concepts','title':'Synthetic catalog topic '+str(number),'wiki_claims':[]},'# Harmless catalog topic').encode())
    assert len(w._checked_pages())==topic_count
    packet=w.prepare(1) if mode=='saved' else None
    before=snapshot(s.VAULT);calls=fake_skip_model(monkeypatch)
    report,error=attempt();first=state();first_calls=len(calls)
    batch=first.get('batch_id') or (packet['batch_id'] if packet else None)
    if packet is None:
        with w.db() as conn:batch=first.get('batch_id') or conn.execute("SELECT id FROM batches WHERE status='pending' ORDER BY updated DESC LIMIT 1").fetchone()['id']
        packet=json.loads((cfg.STATE/'wiki/batches'/batch/'input.json').read_text(encoding='utf-8'))
    encoded=w._bounded_json(packet,ensure_ascii=False)
    assert len(encoded)<2_000_000 and len(packet['catalog'])==topic_count
    model_packets=[json.loads(text.split('INPUT_JSON:\n',1)[1]) for text in calls.inputs]
    if topic_count==2300:
        originals=snapshot(s.VAULT);artifacts=snapshot(cfg.STATE/'wiki/batches'/packet['batch_id'])
        w.reset_retries();retry,retry_error=attempt();retry_state=state()
        record('catalog-'+mode+'-'+str(topic_count),{'mode':mode,'actual_disk_topics':topic_count,'private_packet_characters':len(encoded),'bounded_json_accepted':True,'first_report':report,'first_exception':error,'first_worker':first,'first_model_calls':first_calls,'retry_report':retry,'retry_exception':retry_error,'retry_worker':retry_state,'total_model_calls':len(calls),'vault_preserved':snapshot(s.VAULT)==before,'retry_batch_artifacts_preserved':snapshot(cfg.STATE/'wiki/batches'/packet['batch_id'])==artifacts,'retry_notes_preserved':snapshot(s.VAULT)==originals})
        assert report is not None and report['status']=='complete' and error is None
        assert first_calls==1 and len(model_packets[0]['catalog'])==160 and len(calls.inputs[0])<100000
        assert source.read_bytes()==original and not retry_state['attention_required']
    else:
        record('catalog-'+mode+'-'+str(topic_count),{'mode':mode,'actual_disk_topics':topic_count,'private_packet_characters':len(encoded),'bounded_json_accepted':True,'report':report,'exception':error,'worker':first,'model_calls':len(calls),'model_catalog_counts':[len(p['catalog']) for p in model_packets],'model_input_characters':[len(x) for x in calls.inputs],'source_preserved':source.read_bytes()==original})
        assert error is None and report['status']=='complete' and len(calls)==1
        assert len(model_packets[0]['catalog'])==160 and len(calls.inputs[0])<100000
        assert source.read_bytes()==original

@pytest.mark.parametrize('mode',['fresh','saved'])
@pytest.mark.parametrize('nesting',[50,60,61,62,63])
def test_valid_per_claim_nested_metadata_retains_packet_preflight_contract(brain,monkeypatch,mode,nesting):
    source,first,result=fixture(brain);w.apply(first,result)
    topic=s.safe_path(w.BASE+'Projects/example.md');text=topic.read_text(encoding='utf-8');meta=w.frontmatter(text,strict=True)
    extra='Harmless owner context'
    for _ in range(nesting):extra={'nested':extra}
    meta['wiki_claims'][0]['owner_context']=extra
    claim=meta['wiki_claims'][0];w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),claim,0)
    topic.write_bytes(s.note_text(meta,text.split('---',2)[2]).encode())
    # Original evidence stays unchanged and valid; a separate genuine source
    # creates pending work while selecting this same relevant existing topic.
    other=source.with_name('Followup.md');other.write_bytes(source.read_bytes())
    assert not w.lint()['issues']
    packet=w.prepare(1) if mode=='saved' else None
    before=snapshot(s.VAULT);calls=fake_skip_model(monkeypatch);report,error=attempt();first_state=state()
    if packet is None:
        with w.db() as conn:batch=conn.execute("SELECT id FROM batches WHERE status IN ('pending','complete') ORDER BY updated DESC,id DESC").fetchall()
        packet=next(json.loads((cfg.STATE/'wiki/batches'/r['id']/'input.json').read_text(encoding='utf-8')) for r in batch if json.loads((cfg.STATE/'wiki/batches'/r['id']/'input.json').read_text(encoding='utf-8'))['sources'][0]['path'].endswith('/Followup.md'))
    encoded=w._bounded_json(packet,ensure_ascii=False);assert len(encoded)<2_000_000 and len(packet['current_pages'])==1
    record('depth-'+mode+'-'+str(nesting),{'mode':mode,'owner_metadata_nesting':nesting,'per_claim_validator_accepted':True,'original_lint_issues':0,'private_packet_characters':len(encoded),'bounded_json_accepted':True,'report':report,'exception':error,'worker':first_state,'model_calls':len(calls),'model_input_characters':[len(x) for x in calls.inputs],'vault_preserved':snapshot(s.VAULT)==before,'original_source_unchanged':source.read_bytes()==other.read_bytes()})
    assert error is None and report is not None and report['status']=='complete' and len(calls)==1

@pytest.mark.parametrize('invalid',['credential','nul','surrogate','nodes','depth'])
def test_whole_packet_screen_retains_security_and_outer_resource_bounds(brain,monkeypatch,invalid):
    _,packet,_=fixture(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    value={'credential':{'password':'SyntheticPrivateValueForBoundary'},'nul':chr(0),'surrogate':chr(0xD800),'nodes':[0]*100001}.get(invalid)
    if invalid=='depth':
        value=0
        for _ in range(129): value={'nested':value}
    packet['legacy_owner_context']=value
    (folder/'input.json').write_text(json.dumps(packet),encoding='utf-8')
    notes,private=snapshot(s.VAULT),snapshot(folder);calls=fake_skip_model(monkeypatch)
    with pytest.raises(w.WikiIntegrityError): w._validate_packet_context(packet)
    assert not calls and snapshot(s.VAULT)==notes and snapshot(folder)==private

@pytest.mark.parametrize('invalid',['nodes','depth'])
def test_individual_claim_budget_remains_narrower_than_packet_budget(brain,monkeypatch,invalid):
    _,packet,result=fixture(brain);w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md';text=topic.read_text(encoding='utf-8');metadata=w.frontmatter(text,strict=True)
    value=[0]*20001
    if invalid=='depth':
        value=0
        for _ in range(65): value={'nested':value}
    metadata['wiki_claims'][0]['owner_context']=value
    topic.write_bytes(s.note_text(metadata,text.split('---',2)[2]).encode())
    calls=fake_skip_model(monkeypatch);notes=snapshot(s.VAULT)
    with pytest.raises(w.WikiIntegrityError): w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),metadata['wiki_claims'][0],0)
    assert not calls and snapshot(s.VAULT)==notes
