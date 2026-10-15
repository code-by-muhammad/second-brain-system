"""Bound parser recursion and noncyclic alias expansion before JSON allocation."""
import copy
import json
from datetime import date
import pytest
import brain_markdown as md
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


@pytest.mark.parametrize('strict',[False,True])
def test_deep_actual_yaml_uses_controlled_parser_contract(strict):
    text='---\nowner_note: '+'['*500+'0'+']'*500+'\n---\n# Unchanged body.'
    if strict:
        with pytest.raises(ValueError,match='Invalid YAML frontmatter'): md.frontmatter(text,strict=True)
    else: assert md.frontmatter(text)=={}


def test_deep_yaml_topic_replaces_stale_success_with_attention_before_model(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain)
    topic.write_bytes(('---\nwiki_kind: Projects\ntitle: Example Project\nowner_note: '+'['*500+'0'+']'*500+'\n---\n# Preserve owner topic.').encode())
    w._atomic_json(cfg.STATE/'wiki-worker-status.json',{'status':'complete','at':s.stamp(),'consecutive_failures':0,'attention_required':False})
    before=note_snapshot(); calls=fake_model(monkeypatch,result); report=w.run()
    assert report['status']=='attention' and report['integrity_field']=='frontmatter' and report['consecutive_failures']==0 and len(calls)==0
    assert w.run()['status']=='attention' and note_snapshot()==before
    assert any(x.get('field')=='frontmatter' for x in w.lint()['issues'])


def test_deep_yaml_source_keeps_full_body_and_unknown_metadata_date(brain):
    source=s.VAULT/'01-Projects/Deep.md'; source.parent.mkdir()
    raw=('---\nowner_note: '+'['*500+'0'+']'*500+'\n---\n# Source\nHistorical evidence remains accessible.').encode(); source.write_bytes(raw)
    assert w.status()['eligible_sources']==1
    packet=w.prepare(); assert packet['sources'][0]['date']=='unknown' and packet['sources'][0]['sha256']==s.digest(raw)
    assert source.read_bytes()==raw and 'Historical evidence remains accessible.' in packet['sources'][0]['text']


def aliased_topic(brain,levels=19):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8')); value='safe'
    for _ in range(levels): value=[value,value]
    info['wiki_claims'][0]['owner_note']=value
    topic.write_bytes(s.note_text(info,'# Tiny physical note with shared YAML aliases.').encode())
    assert len(topic.read_bytes())<3000
    return topic,result


def measure_json(monkeypatch):
    original=json.dumps; sizes=[]
    def measured(*args,**kwargs):
        text=original(*args,**kwargs); sizes.append(len(text)); return text
    monkeypatch.setattr(w.json,'dumps',measured)
    return sizes


def test_lint_rejects_exponential_alias_before_materializing_large_json(brain,monkeypatch):
    topic,_=aliased_topic(brain); before=note_snapshot(); sizes=measure_json(monkeypatch); report=w.lint()
    assert max(sizes,default=0)<=2_000_000
    assert any(x.get('field')=='wiki_claims' and x.get('claim_index')==0 for x in report['issues'])
    assert note_snapshot()==before


def test_measurably_oversize_alias_context_is_omitted_before_model_but_merge_is_controlled(brain,monkeypatch):
    topic,result=aliased_topic(brain); before=note_snapshot(); sizes=measure_json(monkeypatch); packet=w.prepare()
    assert packet['current_pages']==[] and any(page['path']==topic.relative_to(s.VAULT).as_posix() for page in packet['catalog'])
    assert max(sizes,default=0)<=2_000_000
    calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError): w.run()
    assert len(calls)==1 and w.run()['status']=='attention' and note_snapshot()==before
    assert max(sizes,default=0)<=2_000_000


def test_saved_packet_total_context_is_bounded_before_model_input_allocation(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); packet=w.prepare()
    claim=packet['current_pages'][0]['meta']['wiki_claims'][0]
    claim['owner_note']='A'*30000
    packet['current_pages'][0]['meta']['wiki_claims']=[copy.deepcopy(claim) for _ in range(80)]
    w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    before=note_snapshot(); sizes=measure_json(monkeypatch); calls=fake_model(monkeypatch,explicit(packet,result))
    report=w.run(); assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==0
    assert max(sizes,default=0)<=2_000_000 and note_snapshot()==before
    assert report['integrity_page']==topic.relative_to(s.VAULT).as_posix()


def test_small_benign_alias_context_preserves_publication_claims_and_receipts(brain,monkeypatch):
    topic,result=aliased_topic(brain,6); old=w.meta(topic.read_text(encoding='utf-8'))['wiki_claims'][0]
    packet=w.prepare(); calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']
    claims=w.meta(topic.read_text(encoding='utf-8'))['wiki_claims']; assert claims[0]==old
    assert w.status()['pending_source_parts']==0 and w.status()['receipt_issue_count']==0


def test_oversize_context_with_hidden_cycle_or_unsupported_key_is_not_silently_omitted(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8'))
    info['wiki_claims'][0]['owner_note']={'large':'A'*41000}; info['wiki_claims'][0]['owner_note']['cycle']=info['wiki_claims'][0]['owner_note']
    topic.write_bytes(s.note_text(info,'# Preserve unmeasurable owner context.').encode()); before=note_snapshot(); calls=fake_model(monkeypatch,result)
    report=w.run(); assert report['status']=='attention' and len(calls)==0 and note_snapshot()==before


def test_standard_encoding_and_default_date_measurement_stay_compatible():
    value={'unicode':'caf\u00e9 \U0001f642','quoted':'a\tb"c','nested':[1,False,None]}
    for options in ({},{'sort_keys':True,'ensure_ascii':False},{'ensure_ascii':True,'allow_nan':False}):
        assert w._bounded_json(value,2000,**options)==json.dumps(value,**options)
    assert w._bounded_json({'date':date(2026,10,1)},2000,default=str)==json.dumps({'date':date(2026,10,1)},default=str)


@pytest.mark.parametrize('code',[127,0x1f642])
def test_large_escaped_scalar_is_rejected_before_encoder_creates_a_large_chunk(monkeypatch,code):
    value=chr(code)*500000;chunks=[];original=json.JSONEncoder.iterencode
    def observed(encoder,*args,**kwargs):
        for chunk in original(encoder,*args,**kwargs):chunks.append(len(chunk));yield chunk
    monkeypatch.setattr(w.json.JSONEncoder,'iterencode',observed)
    with pytest.raises(ValueError):w._bounded_json(value,2_000_000,ensure_ascii=True)
    assert not chunks
    if code==127:assert w._bounded_json(value,2_000_000,ensure_ascii=False)==json.dumps(value,ensure_ascii=False)
