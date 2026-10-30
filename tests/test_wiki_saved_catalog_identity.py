"""Read-only candidate review; every runtime and vault is a disposable fixture."""
import copy, json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model
from test_wiki_saved_plan_repair import interrupted_plan

OUT=Path(__file__).resolve().parent

@pytest.fixture
def brain(tmp_path,monkeypatch):
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; state=root/'state'
    state.mkdir(parents=True); vault.mkdir(); (root/'logs').mkdir()
    for module in (cfg,s):
        monkeypatch.setattr(module,'ROOT',root); monkeypatch.setattr(module,'STATE',state); monkeypatch.setattr(module,'VAULT',vault)
    monkeypatch.setattr(cfg,'ROLE','maintainer'); monkeypatch.setattr(cfg,'DEVICE_ID','test-host')
    return vault,state

def snapshot(folder):
    return {p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}

def record(name, value):
    pass

@pytest.mark.parametrize('shape',['null','integer','malformed','boolean','empty','uppercase','short','valid-control'])
def test_present_catalog_snapshot_hash_is_validated_before_request(brain,monkeypatch,shape):
    source,first,result=fixture(brain)
    result['pages'][0]['title']='Supplier evaluation'
    w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nNew owner source evidence.\n')
    packet=w.prepare()
    assert packet['current_pages']==[] and len(packet['catalog'])==1
    saved=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    changed=copy.deepcopy(packet)
    if shape=='null':changed['catalog'][0]['sha256']=None
    elif shape=='integer':changed['catalog'][0]['sha256']=1
    elif shape=='malformed':changed['catalog'][0]['sha256']='invalid'
    elif shape=='boolean':changed['catalog'][0]['sha256']=True
    elif shape=='empty':changed['catalog'][0]['sha256']=''
    elif shape=='uppercase':changed['catalog'][0]['sha256']='A'*64
    elif shape=='short':changed['catalog'][0]['sha256']='a'*63
    w._atomic_json(saved,changed)
    before=snapshot(saved.parent); notes=snapshot(s.VAULT)
    calls=fake_model(monkeypatch,explicit(packet,result))
    report=None; error=None
    try:report=w.run()
    except Exception as exc:error=exc
    state=w._read_worker_state()
    record('catalog-'+shape,{'shape':shape,'model_calls':len(calls),'error':type(error).__name__ if error else None,
        'worker':state,'returned_status':report.get('status') if isinstance(report,dict) else None,
        'private_preserved':snapshot(saved.parent)==before,'vault_preserved':snapshot(s.VAULT)==notes})
    if shape=='valid-control':
        assert error is None and report['status']=='complete' and len(calls)==1
    else:
        assert not calls and snapshot(saved.parent)==before and snapshot(s.VAULT)==notes
        assert state.get('attention_required') and state.get('integrity_field')=='recovery'
        assert state['consecutive_failures']==0
        with w.db() as conn:
            assert not list(conn.execute('SELECT * FROM sources WHERE attempts>0'))

@pytest.mark.parametrize('artifact',['output','plan','valid-control'])
def test_corrupt_saved_json_recursion_is_controlled_recovery_attention(brain,monkeypatch,artifact):
    source,packet,result,folder,initial_calls=interrupted_plan(brain,monkeypatch)
    assert len(initial_calls)==1
    if artifact!='valid-control':
        (folder/('output.json' if artifact=='output' else 'publication.json')).write_bytes(b'['*2000+b'0'+b']'*2000)
    calls=fake_model(monkeypatch,explicit(packet,result))
    w.reset_retries(); before=snapshot(folder); notes=snapshot(s.VAULT)
    error=None; report=None
    try:report=w.run()
    except Exception as exc:error=exc
    state=w._read_worker_state()
    record('durable-'+artifact,{'artifact':artifact,'model_calls':len(calls),'error':type(error).__name__ if error else None,
        'worker':state,'returned_status':report.get('status') if isinstance(report,dict) else None,
        'private_preserved':snapshot(folder)==before,'vault_preserved':snapshot(s.VAULT)==notes})
    if artifact=='valid-control':
        assert error is None and report['status']=='complete' and not calls
    else:
        assert not calls and snapshot(folder)==before and snapshot(s.VAULT)==notes
        assert isinstance(error,w.WikiIntegrityError)
        assert state.get('attention_required') and state.get('integrity_field')=='recovery'
        assert state['consecutive_failures']==0

@pytest.mark.parametrize('mode',['legacy-absent','owner-changed','partial-replay'])
def test_catalog_identity_preserves_optional_legacy_hash_and_valid_recovery(brain,monkeypatch,mode):
    source,first,result=fixture(brain);result['pages'][0]['title']='Supplier evaluation';w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nNew owner source evidence.\n')
    packet=w.prepare();assert packet['current_pages']==[] and len(packet['catalog'])==1
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved=folder/'input.json'
    if mode=='legacy-absent':
        del packet['catalog'][0]['sha256'];w._atomic_json(saved,packet)
    original=saved.read_bytes();source_raw=source.read_bytes();calls=fake_model(monkeypatch,explicit(packet,result))
    if mode=='legacy-absent':
        notes=snapshot(s.VAULT);assert w._read_saved_packet(saved,packet['batch_id'])==packet
        assert w._resume_packet()==packet and not calls
        assert w._full_catalog_snapshot(packet) is None
        # Absence preserves old packet compatibility, not authority to overwrite
        # an existing catalog-only topic without its actual before-image hash.
        with pytest.raises(w.WikiTransientError):w.apply(packet,explicit(packet,result))
        assert snapshot(s.VAULT)==notes and not calls
    elif mode=='owner-changed':
        topic=s.safe_path(packet['catalog'][0]['path']);topic.write_bytes(topic.read_bytes()+b'\nOwner edit before compilation.\n')
        report=w.run();assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
        with w.db() as conn:assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
    else:
        write=s._write
        def stop(path,text,kind,*args):
            if kind=='wiki_index':raise OSError('Synthetic interruption after topic publication')
            return write(path,text,kind,*args)
        with monkeypatch.context() as scope:
            scope.setattr(s,'_write',stop)
            with pytest.raises(OSError):w.run()
        assert w._saved_publication_started(packet,folder/'publication.json')
        w.reset_retries();assert w.run()['status']=='complete' and len(calls)==1
    assert saved.read_bytes()==original and source.read_bytes()==source_raw

@pytest.mark.parametrize('shape',['duplicate-conflicting-hash','duplicate-identical-control','legacy-absent-control'])
def test_catalog_identity_is_unambiguous_without_removing_legacy_support(brain,monkeypatch,shape):
    source,first,result=fixture(brain); result['pages'][0]['title']='Supplier evaluation'; w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nAnother owner evidence entry.\n')
    packet=w.prepare(); assert packet['current_pages']==[]
    changed=copy.deepcopy(packet)
    if shape.startswith('duplicate-'):
        extra=copy.deepcopy(changed['catalog'][0])
        if shape=='duplicate-conflicting-hash':extra['sha256']='0'*64
        changed['catalog'].append(extra)
    else:
        changed['catalog'][0].pop('sha256')
        result['pages'][0]['slug']='legacy-new-topic'
    saved=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json';w._atomic_json(saved,changed)
    before=snapshot(saved.parent); notes=snapshot(s.VAULT)
    calls=fake_model(monkeypatch,explicit(changed,result));report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    state=w._read_worker_state()
    if shape.endswith('control'):
        assert error is None and report['status']=='complete' and len(calls)==1
    else:
        assert not calls and snapshot(saved.parent)==before and snapshot(s.VAULT)==notes
        assert state.get('attention_required') and state.get('integrity_field')=='recovery'
