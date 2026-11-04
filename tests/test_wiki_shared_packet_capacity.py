"""Expanded durable JSON must be readable before it can enter the work queue."""
import copy
import hashlib
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model


def durable_selection():
    with w.db() as conn:
        rows=[dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY id')]
    root=cfg.STATE/'wiki/batches'
    files={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}
    directories={path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_dir()}
    return rows,files,directories


def retired_fixture(brain):
    source,packet,result=fixture(brain)
    with w.db() as conn:
        conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(packet['batch_id'],))
    return source,result


def nodes(value):
    if isinstance(value,dict):return 1+sum(nodes(key)+nodes(child) for key,child in value.items())
    if isinstance(value,list):return 1+sum(nodes(child) for child in value)
    return 1


def depth(value):
    if isinstance(value,dict):return max((1+depth(child) for pair in value.items() for child in pair),default=0)
    if isinstance(value,list):return max((1+depth(child) for child in value),default=0)
    return 0


def base_packet(row):
    return {'version':2,'sources':[row],'current_pages':[],'catalog':[]}


def check_preflight(source,before,accept):
    raw=source.read_bytes();packet=None;error=None
    try:packet=w.prepare(1)
    except w.WikiIntegrityError as exc:error=exc
    assert source.read_bytes()==raw
    if not accept:
        assert durable_selection()==before
        assert error is not None and error.field=='packet' and error.page==w.BASE+'Index.md'
        return
    assert error is None and packet is not None
    ident=hashlib.sha256(json.dumps({key:value for key,value in packet.items() if key!='batch_id'},sort_keys=True,default=str).encode()).hexdigest()[:24]
    assert packet['batch_id']==ident
    path=cfg.STATE/'wiki/batches'/ident/'input.json'
    decoded=json.loads(path.read_text(encoding='utf-8'))
    assert w._read_saved_packet(path,ident)==decoded
    assert decoded==packet and nodes(decoded)<=100000 and depth(decoded)<=128
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(ident,)).fetchone()[0]=='pending'


@pytest.mark.parametrize('expanded_nodes,accept',[(99_999,True),(100_000,True),(100_001,False),(100_010,False)])
def test_expanded_alias_node_boundary_never_persists_unreadable_selection(brain,monkeypatch,expanded_nodes,accept):
    source,_=retired_fixture(brain);row=copy.deepcopy(w.inventory()[0])
    shared=[];extra=[shared]*10;row['synthetic_finite_context']=extra
    packet={**base_packet(row),'batch_id':'0'*24}
    needed=expanded_nodes-nodes(packet);quotient,remainder=divmod(needed,10)
    shared.extend([0]*quotient);extra.extend([0]*remainder)
    assert extra[0] is extra[1] and nodes(packet)==expanded_nodes
    encoded=w._bounded_json(packet,sort_keys=True,default=str)
    assert nodes(json.loads(encoded))==expanded_nodes and len(encoded)<2_000_000
    monkeypatch.setattr(w,'inventory',lambda *args,**kwargs:[row])
    monkeypatch.setattr(w,'_checked_pages',lambda:[])
    check_preflight(source,durable_selection(),accept)


@pytest.mark.parametrize('expanded_depth,accept',[(128,True),(129,False)])
def test_expanded_alias_depth_boundary_never_persists_unreadable_selection(brain,monkeypatch,expanded_depth,accept):
    source,_=retired_fixture(brain);row=copy.deepcopy(w.inventory()[0])
    shared=0
    for _ in range(110):shared=[shared]
    wrapped=shared
    for _ in range(expanded_depth-114):wrapped=[wrapped]
    row['synthetic_finite_context']=[wrapped,shared]
    packet={**base_packet(row),'batch_id':'0'*24}
    assert depth(packet)==expanded_depth
    # The shallow alias is visited first by the finite object-graph walker.
    # Only the decoded JSON reveals the deeper repeated occurrence's children.
    encoded=w._bounded_json(packet,sort_keys=True,default=str)
    assert depth(json.loads(encoded))==expanded_depth
    monkeypatch.setattr(w,'inventory',lambda *args,**kwargs:[row])
    monkeypatch.setattr(w,'_checked_pages',lambda:[])
    check_preflight(source,durable_selection(),accept)


def test_fitting_actual_yaml_alias_is_preserved_and_saved_reader_agrees(brain):
    source,_=retired_fixture(brain);raw=source.read_bytes();shared=[0]*1000
    claim={'text':'The source records a demonstrated prototype.','source':'01-Projects/Example.md',
           'quote':'A prototype was demonstrated.','status':'historical','extra':[shared]*10}
    topic=s.VAULT/w.BASE/'Projects/shared-alias.md';topic.parent.mkdir(parents=True,exist_ok=True)
    topic.write_text(s.note_text({'wiki_kind':'Projects','title':'Example Project','wiki_claims':[claim]},'# Example Project\n'),encoding='utf-8')
    original=topic.read_bytes();parsed=w._checked_pages()[0]
    extra=parsed['meta']['wiki_claims'][0]['extra'];assert extra[0] is extra[1]
    packet=w.prepare(1);w._validate_packet_context(packet)
    path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    saved=w._read_saved_packet(path,packet['batch_id']);w._validate_packet_context(saved)
    assert saved==packet and nodes(saved)<100000
    assert source.read_bytes()==raw and topic.read_bytes()==original


@pytest.mark.parametrize('bad',['cycle','unsupported-type'])
def test_generic_alias_serializer_keeps_cycle_and_type_guards(bad):
    shared=['finite'];value=[shared]*10
    assert json.loads(w._bounded_json(value))==[['finite']]*10
    if bad=='cycle':shared.append(value)
    else:shared.append(object())
    with pytest.raises((ValueError,TypeError)):
        w._bounded_json(value,default=str)


def fitting_alias_packet(brain,monkeypatch):
    source,result=retired_fixture(brain);inventory=w.inventory
    def aliased_inventory(*args,**kwargs):
        rows=inventory(*args,**kwargs)
        for row in rows:
            shared=[0]*100;row['synthetic_finite_context']=[shared]*10
        return rows
    monkeypatch.setattr(w,'inventory',aliased_inventory)
    packet=w.prepare(1)
    assert nodes(json.loads((cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json').read_text()))<100000
    return source,packet,explicit(packet,result)


def test_fitting_aliased_saved_output_recovers_without_new_model_call(brain,monkeypatch):
    source,packet,result=fitting_alias_packet(brain,monkeypatch);raw=source.read_bytes()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];input_raw=(folder/'input.json').read_bytes()
    output=folder/'output.json';w._atomic_json(output,result);output_raw=output.read_bytes()
    calls=fake_model(monkeypatch,result);report=w.run(1)
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and not calls
    assert source.read_bytes()==raw and (folder/'input.json').read_bytes()==input_raw and output.read_bytes()==output_raw


@pytest.mark.parametrize('owner_conflict',[False,True],ids=['unchanged-partial','owner-index-conflict'])
def test_fitting_alias_actual_partial_preserves_owner_and_recovers_without_new_call(brain,monkeypatch,owner_conflict):
    source,packet,result=fitting_alias_packet(brain,monkeypatch);raw=source.read_bytes()
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
    index=s.VAULT/w.BASE/'Index.md';prior=index.read_bytes() if index.exists() else None
    output_raw=(folder/'output.json').read_bytes()
    if owner_conflict:
        owner=b'# Owner preserved Index content\n';index.write_bytes(owner);w.reset_retries()
        with pytest.raises(w.WikiTransientError):w.run(1)
        assert index.read_bytes()==owner and len(calls)==1
        assert (s.VAULT/w.BASE/'Projects/example.md').read_bytes()==published
        if prior is None:index.unlink()
        else:index.write_bytes(prior)
    w.reset_retries();report=w.run(1)
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and len(calls)==1
    assert source.read_bytes()==raw and (folder/'input.json').read_bytes()==input_raw
    assert (folder/'output.json').read_bytes()==output_raw and (s.VAULT/w.BASE/'Projects/example.md').read_bytes()==published
