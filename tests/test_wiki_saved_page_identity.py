import copy, json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import fake_model, explicit

@pytest.fixture
def brain(tmp_path,monkeypatch):
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; state=root/'state'
    state.mkdir(parents=True); vault.mkdir(); (root/'logs').mkdir()
    for module in (cfg,s):
        monkeypatch.setattr(module,'ROOT',root); monkeypatch.setattr(module,'STATE',state); monkeypatch.setattr(module,'VAULT',vault)
    monkeypatch.setattr(cfg,'ROLE','maintainer'); monkeypatch.setattr(cfg,'DEVICE_ID','test-host')
    return vault,state

def snapshot(folder):
    return {path.relative_to(folder).as_posix():path.read_bytes() for path in folder.rglob('*') if path.is_file()}

@pytest.mark.parametrize('shape',['missing-sha','null-sha','integer-sha','malformed-sha','boolean-sha','uppercase-sha','short-sha','valid-control'])
def test_saved_current_page_identity_is_checked_before_model(brain,monkeypatch,shape):
    source,first,result=fixture(brain)
    w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nNew owner evidence for another compilation.\n')
    packet=w.prepare()
    assert len(packet['current_pages'])==1
    path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    changed=copy.deepcopy(packet)
    if shape=='missing-sha':del changed['current_pages'][0]['sha256']
    elif shape=='null-sha':changed['current_pages'][0]['sha256']=None
    elif shape=='integer-sha':changed['current_pages'][0]['sha256']=1
    elif shape=='malformed-sha':changed['current_pages'][0]['sha256']='invalid'
    elif shape=='boolean-sha':changed['current_pages'][0]['sha256']=True
    elif shape=='uppercase-sha':changed['current_pages'][0]['sha256']='A'*64
    elif shape=='short-sha':changed['current_pages'][0]['sha256']='a'*63
    path.write_text(json.dumps(changed),encoding='utf-8')
    artifacts=snapshot(path.parent); notes=snapshot(s.VAULT)
    calls=fake_model(monkeypatch,explicit(packet,result))
    returned=None; error=None
    try:returned=w.run()
    except Exception as exc:error=exc
    state=w._read_worker_state()
    observation={'shape':shape,'model_calls':len(calls),'exception':type(error).__name__ if error else None,
        'worker':state,'returned':returned,'private_preserved':snapshot(path.parent)==artifacts,
        'vault_preserved':snapshot(s.VAULT)==notes}
    if shape=='valid-control':
        assert error is None and returned['status']=='complete' and len(calls)==1
    else:
        assert not calls and snapshot(path.parent)==artifacts and snapshot(s.VAULT)==notes
        assert state.get('attention_required') is True and state.get('integrity_field')=='recovery'
        assert state['consecutive_failures']==0
        with w.db() as conn:
            assert not list(conn.execute('SELECT * FROM sources WHERE attempts>0'))
            assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0


@pytest.mark.parametrize('mode',['legacy-catalog','owner-changed','partial-replay'])
def test_valid_current_page_identity_preserves_legacy_catalog_and_recovery(brain,monkeypatch,mode):
    source,first,result=fixture(brain); w.apply(first,result)
    source.write_bytes(source.read_bytes()+b'\nNew owner evidence for another compilation.\n')
    packet=w.prepare(); assert packet['current_pages']
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; saved=folder/'input.json'
    if mode=='legacy-catalog':
        for page in packet['catalog']:page.pop('sha256',None)
        w._atomic_json(saved,packet)
        assert w._full_catalog_snapshot(packet) is None
    original=saved.read_bytes(); source_raw=source.read_bytes()
    calls=fake_model(monkeypatch,explicit(packet,result))
    if mode=='owner-changed':
        topic=s.safe_path(packet['current_pages'][0]['path'])
        topic.write_bytes(topic.read_bytes()+b'\nOwner updated this derived page before compilation.\n')
        report=w.run()
        assert report['batch_id']!=packet['batch_id']
        with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
    elif mode=='partial-replay':
        write=s._write
        def stop(path,text,kind,*args):
            if kind=='wiki_index':raise OSError('Synthetic interruption after actual topic publication')
            return write(path,text,kind,*args)
        with monkeypatch.context() as scope:
            scope.setattr(s,'_write',stop)
            with pytest.raises(OSError):w.run()
        assert w._saved_publication_started(packet,folder/'publication.json')
        w.reset_retries();report=w.run()
    else:report=w.run()
    assert report['status']=='complete' and len(calls)==1
    assert saved.read_bytes()==original and source.read_bytes()==source_raw
