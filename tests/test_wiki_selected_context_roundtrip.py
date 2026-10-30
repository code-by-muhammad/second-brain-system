"""Selected context must pass the same decoded guards before durable input."""
import hashlib
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model


def durable_selection():
    with w.db() as conn:rows=[dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY id')]
    root=cfg.STATE/'wiki/batches'
    files={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}
    directories={path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_dir()}
    return rows,files,directories


def retired_fixture(brain):
    source,packet,result=fixture(brain)
    with w.db() as conn:conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(packet['batch_id'],))
    return source,result


def claim(extra):
    return {'text':'The source records a demonstrated prototype.','source':'01-Projects/Example.md',
            'quote':'A prototype was demonstrated.','status':'historical','extra':extra}


def actual_topic(value):
    topic=s.VAULT/w.BASE/'Projects/shared-alias.md';topic.parent.mkdir(parents=True,exist_ok=True)
    topic.write_text(s.note_text({'wiki_kind':'Projects','title':'Example Project','wiki_claims':[value]},'# Example Project\n'),encoding='utf-8')
    parsed=w.meta(topic.read_text(encoding='utf-8'))['wiki_claims'][0]
    return topic,parsed


def aliased_depth(expanded_depth):
    shared=0
    for _ in range(50):shared=[shared]
    wrapped=shared
    for _ in range(expanded_depth-52):wrapped=[wrapped]
    return claim([wrapped,shared])


def nodes(value):
    if isinstance(value,dict):return 1+sum(nodes(key)+nodes(child) for key,child in value.items())
    if isinstance(value,list):return 1+sum(nodes(child) for child in value)
    return 1


def depth(value):
    if isinstance(value,dict):return max((1+depth(child) for pair in value.items() for child in pair),default=0)
    if isinstance(value,list):return max((1+depth(child) for child in value),default=0)
    return 0


def identity(packet):
    base={key:value for key,value in packet.items() if key!='batch_id'}
    return hashlib.sha256(json.dumps(base,sort_keys=True,default=str).encode()).hexdigest()[:24]


@pytest.mark.parametrize('expanded_depth,accept',[(64,True),(65,False),(66,False)])
def test_actual_selected_yaml_depth_boundary_is_checked_before_durable_input(brain,expanded_depth,accept):
    source,_=retired_fixture(brain);source_raw=source.read_bytes()
    topic,original_claim=actual_topic(aliased_depth(expanded_depth));topic_raw=topic.read_bytes()
    w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),original_claim,0)
    decoded=json.loads(w._bounded_json(original_claim,ensure_ascii=False,allow_nan=False))
    assert depth(decoded)==expanded_depth
    before=durable_selection();packet=None;error=None
    try:packet=w.prepare(1)
    except w.WikiIntegrityError as exc:error=exc
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw
    if not accept:
        assert durable_selection()==before
        assert error is not None and error.field=='wiki_claims' and error.page==topic.relative_to(s.VAULT).as_posix()
        return
    assert error is None and packet is not None and packet['batch_id']==identity(packet)
    selected=packet['current_pages'][0]['meta']['wiki_claims'][0]
    assert selected==original_claim and packet['sources']==w.inventory()
    cursor=selected['extra'][0]
    for _ in range(expanded_depth-52):cursor=cursor[0]
    assert cursor is selected['extra'][1], 'Preflight must not mutate the original in-memory packet.'
    path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    saved=w._read_saved_packet(path,packet['batch_id']);w._validate_packet_context(saved)
    assert saved==packet


@pytest.mark.parametrize('expanded_nodes,accept',[(20_000,True),(20_001,False)])
def test_actual_yaml_claim_node_boundary_and_compact_omission_remain_distinct(brain,expanded_nodes,accept):
    source,_=retired_fixture(brain);source_raw=source.read_bytes();shared=[];extra=[shared]*10
    value=claim(extra);needed=expanded_nodes-nodes(value);count,remainder=divmod(needed,10)
    shared.extend([0]*count);extra.extend([0]*remainder)
    topic,parsed=actual_topic(value);topic_raw=topic.read_bytes()
    assert parsed['extra'][0] is parsed['extra'][1]
    w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),parsed,0)
    decoded=json.loads(w._bounded_json(parsed,ensure_ascii=False,allow_nan=False))
    assert nodes(decoded)==expanded_nodes
    if accept:w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),decoded,0)
    else:
        with pytest.raises(w.WikiIntegrityError) as caught:
            w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(),decoded,0)
        assert caught.value.field=='wiki_claims'
    # This many JSON nodes exceeds the earlier 40k compact-page character
    # budget. It must be omitted, not fabricated as a reachable selected case.
    packet=w.prepare(1)
    assert packet['current_pages']==[] and len(packet['catalog'])==1
    assert packet['catalog'][0]['path']==topic.relative_to(s.VAULT).as_posix()
    saved=w._read_saved_packet(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet['batch_id'])
    w._validate_packet_context(saved)
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw


def test_actual_invalid_selected_alias_worker_never_calls_model_or_persists_input(brain,monkeypatch):
    source,_=retired_fixture(brain);source_raw=source.read_bytes()
    topic,_=actual_topic(aliased_depth(66));topic_raw=topic.read_bytes();before=durable_selection();calls=[]
    monkeypatch.setattr(w.shutil,'which',lambda name:'synthetic-codex')
    class Process:
        returncode=1
        def __init__(self,command,**kwargs):calls.append(command)
        def communicate(self,model_input,timeout):return None,None
    monkeypatch.setattr(w.subprocess,'Popen',Process)
    for _ in range(2):
        w.reset_retries()
        try:report=w.run(1)
        except RuntimeError:report=w._read_worker_state()
        assert not calls, 'Invalid decoded selected context must be rejected before invoking the provider.'
        assert report['status']=='attention' and report.get('integrity_field')=='wiki_claims'
        assert report['consecutive_failures']==0 and not calls and durable_selection()==before
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw


def fitting_selected_packet(brain):
    source,result=retired_fixture(brain);topic,_=actual_topic(aliased_depth(64));packet=w.prepare(1)
    assert len(packet['current_pages'])==1
    return source,topic,packet,explicit(packet,result)


def test_fitting_selected_alias_deferred_retry_preserves_identity_generation_and_original_fields(brain):
    source,topic,packet,_=fitting_selected_packet(brain);source_raw=source.read_bytes();topic_raw=topic.read_bytes()
    old=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json';old_raw=old.read_bytes()
    deferred={'pages':[],'dispositions':[{'source':packet['sources'][0]['path'],'part':1,'status':'deferred',
        'reason':'This synthetic source will be reconsidered after review.','quote':'A prototype was demonstrated.'}]}
    w.apply(packet,deferred);w.reset_retries();retry=w.prepare(1)
    assert retry['retry_generation']==1 and retry['batch_id']==identity(retry) and retry['batch_id']!=packet['batch_id']
    assert retry['sources']==packet['sources'] and retry['current_pages']==packet['current_pages']
    saved=w._read_saved_packet(cfg.STATE/'wiki/batches'/retry['batch_id']/'input.json',retry['batch_id'])
    w._validate_packet_context(saved)
    assert old.read_bytes()==old_raw and source.read_bytes()==source_raw and topic.read_bytes()==topic_raw


def test_fitting_selected_alias_saved_output_finishes_without_a_new_model_call(brain,monkeypatch):
    source,topic,packet,result=fitting_selected_packet(brain);source_raw=source.read_bytes();topic_raw=topic.read_bytes()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];input_raw=(folder/'input.json').read_bytes()
    output=folder/'output.json';w._atomic_json(output,result);output_raw=output.read_bytes()
    calls=fake_model(monkeypatch,result);report=w.run(1)
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and not calls
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw
    assert (folder/'input.json').read_bytes()==input_raw and output.read_bytes()==output_raw


@pytest.mark.parametrize('owner_conflict',[False,True],ids=['unchanged-partial','owner-index-conflict'])
def test_fitting_selected_alias_actual_partial_preserves_owner_and_recovers_without_new_call(brain,monkeypatch,owner_conflict):
    source,topic,packet,result=fitting_selected_packet(brain);source_raw=source.read_bytes();topic_raw=topic.read_bytes()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];input_raw=(folder/'input.json').read_bytes()
    write=s._write;calls=fake_model(monkeypatch,result)
    def stop_index(path,text,kind,*args,**kwargs):
        if kind=='wiki_index':raise OSError('Synthetic interruption after actual topic publication')
        return write(path,text,kind,*args,**kwargs)
    monkeypatch.setattr(s,'_write',stop_index)
    with pytest.raises(OSError):w.run(1)
    monkeypatch.setattr(s,'_write',write)
    assert w._saved_publication_started(packet,folder/'publication.json') and len(calls)==1
    published=(s.VAULT/w.BASE/'Projects/example.md').read_bytes()
    output_raw=(folder/'output.json').read_bytes();index=s.VAULT/w.BASE/'Index.md'
    prior=index.read_bytes() if index.exists() else None
    if owner_conflict:
        owner=b'# Owner preserved Index content\n';index.write_bytes(owner);w.reset_retries()
        with pytest.raises(w.WikiTransientError):w.run(1)
        assert index.read_bytes()==owner and len(calls)==1
        assert (s.VAULT/w.BASE/'Projects/example.md').read_bytes()==published
        if prior is None:index.unlink()
        else:index.write_bytes(prior)
    w.reset_retries();report=w.run(1)
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and len(calls)==1
    assert source.read_bytes()==source_raw and topic.read_bytes()==topic_raw
    assert (folder/'input.json').read_bytes()==input_raw and (folder/'output.json').read_bytes()==output_raw
    assert (s.VAULT/w.BASE/'Projects/example.md').read_bytes()==published
