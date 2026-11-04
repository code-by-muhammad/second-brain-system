"""Whole-packet capacity is distinct from malformed metadata and compact omission."""
import copy
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_model_context_preflight import fake_skip_model


def snapshot():
    return {p.relative_to(s.VAULT).as_posix():s.digest(p.read_bytes())
            for p in s.VAULT.rglob('*') if p.is_file()}


def capacity_value(shape):
    if shape=='nodes': return [0]*100000
    if shape=='characters': return 'x'*2_000_000
    value='ordinary nested context'
    for _ in range(130): value=[value]
    return value


@pytest.mark.parametrize('shape',['nodes','depth'])
def test_structural_capacity_has_a_distinct_non_character_exception(shape):
    with pytest.raises(ValueError) as caught: w._bounded_json(capacity_value(shape))
    assert type(caught.value).__name__=='_JSONStructureTooLarge'
    assert not isinstance(caught.value,w._JSONTooLarge)


@pytest.mark.parametrize('shape',['nodes','depth','characters'])
def test_whole_packet_context_reports_operation_capacity(brain,shape):
    _,packet,_=fixture(brain)
    packet['synthetic_extra']=capacity_value(shape)
    if shape!='characters': assert len(json.dumps(packet))<2_000_000
    with pytest.raises(w.WikiIntegrityError) as caught: w._validate_packet_context(packet)
    assert caught.value.field=='packet' and caught.value.page==w.BASE+'Index.md'
    assert all(text in str(caught.value) for text in ('2,000,000','100,000','128'))


@pytest.mark.parametrize('shape',['nodes','depth','characters'])
def test_saved_packet_capacity_remains_pending_without_model_or_strikes(brain,monkeypatch,shape):
    source,packet,_=fixture(brain)
    packet['synthetic_extra']=capacity_value(shape)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];path=folder/'input.json'
    path.write_text(json.dumps(packet),encoding='utf-8')
    raw=path.read_bytes();notes=snapshot();calls=fake_skip_model(monkeypatch)
    with pytest.raises(w.WikiIntegrityError) as caught: w._read_saved_packet(path,packet['batch_id'])
    assert caught.value.field=='packet' and caught.value.batch_id==packet['batch_id']
    report=w.run(limit=1);state=w._read_worker_state()
    assert report['status']=='attention' and state['integrity_field']=='packet'
    assert state['consecutive_failures']==0 and not calls
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='pending'
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0 OR processed_hash IS NOT NULL').fetchone()[0]==0
    assert path.read_bytes()==raw and snapshot()==notes and source.is_file()


@pytest.mark.parametrize('mode',['fresh','saved'])
def test_finite_packet_below_structural_capacity_still_compiles(brain,monkeypatch,mode):
    source,packet,_=fixture(brain);original=source.read_bytes()
    packet['synthetic_extra']=[{'ordinary':index} for index in range(1000)]
    w._validate_packet_context(packet)
    if mode=='saved':
        path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
        path.write_text(json.dumps(packet),encoding='utf-8')
        assert w._read_saved_packet(path,packet['batch_id'])==packet
    else:
        with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(packet['batch_id'],))
    calls=fake_skip_model(monkeypatch)
    assert w.run(limit=1)['status']=='complete' and len(calls)==1
    assert source.read_bytes()==original


@pytest.mark.parametrize('invalid',['cycle','unsupported','nonfinite'])
def test_malformed_whole_context_does_not_become_capacity(brain,invalid):
    _,packet,_=fixture(brain)
    value={}
    if invalid=='cycle':value['nested']=value
    elif invalid=='unsupported':value['nested']=object()
    else:value['nested']=float('nan')
    packet['synthetic_extra']=value
    with pytest.raises(w.WikiIntegrityError) as caught:w._validate_packet_context(packet)
    assert caught.value.field==('content' if invalid=='nonfinite' else 'wiki_claims')


def test_unsortable_json_keys_remain_type_error_at_sorted_serializer():
    with pytest.raises(TypeError) as caught:w._bounded_json({1:'ordinary','mixed':'ordinary'},sort_keys=True)
    assert type(caught.value).__name__ not in ('_JSONTooLarge','_JSONStructureTooLarge')


@pytest.mark.parametrize('invalid',['cycle','unsupported','nonfinite','sort-keys','nodes','depth'])
def test_malformed_or_overcomplex_individual_claim_remains_metadata_attention(brain,invalid):
    _,packet,result=fixture(brain);w.apply(packet,result)
    page=w.pages()[0];claim=copy.deepcopy(page['meta']['wiki_claims'][0])
    value={}
    if invalid=='cycle':value['nested']=value
    elif invalid=='unsupported':value['nested']=object()
    elif invalid=='nonfinite':value['nested']=float('nan')
    elif invalid=='sort-keys':value={'nested':{1:'ordinary','mixed':'ordinary'}}
    else:value=capacity_value(invalid)
    claim['owner_context']=value
    with pytest.raises(w.WikiIntegrityError) as caught:w._validate_existing_claim(page['path'],claim,0)
    assert caught.value.field=='wiki_claims' and caught.value.page==page['path']


@pytest.mark.parametrize('invalid',['nodes','depth'])
def test_overcomplex_relevant_topic_is_not_omitted_as_character_oversize(brain,monkeypatch,invalid):
    _,packet,_=fixture(brain)
    with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(packet['batch_id'],))
    claim={'text':'A synthetic recorded observation.','source':packet['sources'][0]['path'],
           'quote':'A prototype was demonstrated.','status':'historical',
           'owner_context':{'structure':capacity_value(invalid),'oversized':'x'*41000}}
    page={'path':w.BASE+'Projects/example.md','sha256':'a'*64,
          'meta':{'wiki_kind':'Projects','title':'Example Project','wiki_claims':[claim]}}
    monkeypatch.setattr(w,'_checked_pages',lambda:[page])
    with pytest.raises(w.WikiIntegrityError) as caught:w.prepare(1)
    assert caught.value.field=='wiki_claims' and caught.value.page==page['path']


def test_actual_valid_catalog_capacity_blocks_fresh_worker_as_packet_attention(brain,monkeypatch):
    source,initial,_=fixture(brain)
    with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(initial['batch_id'],))
    root=s.VAULT/w.BASE/'People';root.mkdir(parents=True)
    for number in range(11200):
        (root/f'p{number:05d}.md').write_bytes(s.note_text(
            {'wiki_kind':'People','title':f'P{number:05d}','wiki_claims':[]},'# Valid synthetic topic').encode())
    assert len(w._checked_pages())==11200
    before=snapshot();raw=source.read_bytes();calls=fake_skip_model(monkeypatch)
    report=w.run(limit=1);state=w._read_worker_state()
    assert report['status']=='attention' and state['integrity_field']=='packet'
    assert state['consecutive_failures']==0 and not calls
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0 OR processed_hash IS NOT NULL').fetchone()[0]==0
    assert snapshot()==before and source.read_bytes()==raw
