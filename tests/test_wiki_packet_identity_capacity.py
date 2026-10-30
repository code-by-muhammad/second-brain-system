"""The saved selection must fit its budget after its immutable identity is added."""
import copy
import hashlib
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture


def durable_selection():
    with w.db() as conn:
        rows=[dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY id')]
    root=cfg.STATE/'wiki/batches'
    files={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}
    directories={path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_dir()}
    return rows,files,directories


def retired_fixture(brain):
    source,initial,_=fixture(brain)
    with w.db() as conn:
        conn.execute("UPDATE batches SET status='superseded' WHERE id=?",(initial['batch_id'],))
    return source


def identity(packet):
    base={key:value for key,value in packet.items() if key!='batch_id'}
    return hashlib.sha256(json.dumps(base,sort_keys=True,default=str).encode()).hexdigest()[:24]


def assert_prepare_boundary(source,before,accept):
    original=source.read_bytes();error=None;packet=None
    try:packet=w.prepare(1)
    except w.WikiIntegrityError as exc:error=exc
    assert source.read_bytes()==original
    if not accept:
        # No folder, durable input or queue row may outlive the failed preflight.
        assert durable_selection()==before
        assert error is not None and error.field=='packet' and error.page==w.BASE+'Index.md'
        return
    assert error is None and packet is not None
    assert packet['batch_id']==identity(packet)
    path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    assert w._read_saved_packet(path,packet['batch_id'])==packet
    assert len(path.read_text(encoding='utf-8'))<=2_000_000
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='pending'


@pytest.mark.parametrize('base_characters,accept',[
    (1_999_959,True),(1_999_960,True),(1_999_961,False),(1_999_980,False),
])
def test_identity_character_boundary_never_persists_unreadable_selection(brain,monkeypatch,base_characters,accept):
    source=retired_fixture(brain)
    page={'path':w.BASE+'People/synthetic.md','sha256':'a'*64,
          'meta':{'wiki_kind':'People','title':'Synthetic catalog title','wiki_claims':[]}}
    catalog={'path':page['path'],'title':page['meta']['title'],'sha256':page['sha256'],'claim_count':0}
    base={'version':2,'sources':w.inventory(),'current_pages':[],'catalog':[catalog]}
    padding=base_characters-len(json.dumps(base,sort_keys=True,default=str))
    assert padding>0
    page['meta']['title']+='T'*padding;catalog['title']=page['meta']['title']
    assert len(json.dumps(base,sort_keys=True,default=str))==base_characters
    w._bounded_json(base,sort_keys=True,default=str)
    assert len(json.dumps({**base,'batch_id':'0'*24},sort_keys=True,default=str))==base_characters+40
    monkeypatch.setattr(w,'_checked_pages',lambda:[page])
    assert_prepare_boundary(source,durable_selection(),accept)


def node_count(value):
    # These fixtures contain no shared containers or cycles. Count JSON keys and
    # values independently, including the two scalar nodes added by batch_id.
    if isinstance(value,dict):return 1+sum(node_count(key)+node_count(child) for key,child in value.items())
    if isinstance(value,list):return 1+sum(node_count(child) for child in value)
    return 1


@pytest.mark.parametrize('base_nodes,accept',[(99_998,True),(99_999,False),(100_000,False)])
def test_identity_node_boundary_never_persists_unreadable_selection(brain,monkeypatch,base_nodes,accept):
    source=retired_fixture(brain);row=copy.deepcopy(w.inventory()[0])
    row['synthetic_finite_context']=[]
    base={'version':2,'sources':[row],'current_pages':[],'catalog':[]}
    row['synthetic_finite_context']=[0]*(base_nodes-node_count(base))
    assert node_count(base)==base_nodes
    assert node_count({**base,'batch_id':'0'*24})==base_nodes+2
    assert len(json.dumps(base))<2_000_000
    w._bounded_json(base,sort_keys=True,default=str)
    monkeypatch.setattr(w,'inventory',lambda *args,**kwargs:[row])
    monkeypatch.setattr(w,'_checked_pages',lambda:[])
    assert_prepare_boundary(source,durable_selection(),accept)


def test_fitting_deferred_retry_retains_generation_identity_and_history(brain):
    source,packet,_=fixture(brain);original=source.read_bytes()
    old_path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json';old_raw=old_path.read_bytes()
    result={'pages':[],'dispositions':[{'source':packet['sources'][0]['path'],'part':1,'status':'deferred',
        'reason':'This synthetic source will be reconsidered after its review.',
        'quote':'A prototype was demonstrated.'}]}
    w.apply(packet,result);w.reset_retries();retry=w.prepare(1)
    assert retry['retry_generation']==1
    assert retry['batch_id']==identity(retry) and retry['batch_id']!=packet['batch_id']
    path=cfg.STATE/'wiki/batches'/retry['batch_id']/'input.json'
    assert w._read_saved_packet(path,retry['batch_id'])==retry
    with w.db() as conn:
        assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='complete'
        assert conn.execute('SELECT status FROM batches WHERE id=?',(retry['batch_id'],)).fetchone()[0]=='pending'
    assert old_path.read_bytes()==old_raw and source.read_bytes()==original
