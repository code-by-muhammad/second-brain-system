"""Maintenance mutations obey the real after-lock update admission gate."""
import json
import pytest
import brain_settings as cfg
import brain_wiki as wiki


def seed():
    with wiki.db() as db:
        db.execute("INSERT INTO sources(path,error,attempts,attempt_hash,deferred_until) VALUES('synthetic.md','retry me',3,'old',999)")
        db.execute("INSERT INTO batches(id,status,receipt,updated,attempts) VALUES('synthetic','failed','{}','before',4)")
    (cfg.STATE/'wiki-worker-status.json').write_bytes(b'{"status":"keep worker backoff"}\n')
    (cfg.STATE/'wiki-model.json').write_bytes(b'{"format":1,"executable":"keep-original.exe"}\n')
    return {name:(cfg.STATE/name).read_bytes() for name in ('wiki-jobs.db','wiki-worker-status.json','wiki-model.json')}

@pytest.mark.parametrize('stage',['stopped','start failed'])
@pytest.mark.parametrize('operation',['reset_retries','configure_model'])
def test_hold_installed_during_worker_lock_blocks_maintenance_before_any_write(brain,monkeypatch,stage,operation):
    before=seed();calls=[];actual=wiki.FileLock
    class HeldAtAcquisition:
        def __init__(self,path,**kwargs):self.lock=actual(path,**kwargs)
        def __enter__(self):
            self.lock.__enter__()
            (cfg.STATE/'service-transaction.json').write_text(json.dumps({'root':str(cfg.ROOT),'hold_id':'a'*32,'stage':stage}))
            return self
        def __exit__(self,*args):return self.lock.__exit__(*args)
    monkeypatch.setattr(wiki,'FileLock',HeldAtAcquisition)
    monkeypatch.setattr(wiki,'_native_model_identity',lambda value:calls.append(value) or {'path':value,'sha256':'b'*64,'bytes':1})
    def no_model(*args,**kwargs):pytest.fail('Maintenance must never invoke a model')
    monkeypatch.setattr(wiki,'_run_model',no_model)
    with pytest.raises(ValueError,match='held for'):
        if operation=='configure_model':wiki.configure_model('synthetic-never-executed.exe')
        else:wiki.reset_retries()
    assert calls==[]
    assert {name:(cfg.STATE/name).read_bytes() for name in before}==before


def test_no_hold_retry_really_resets_existing_db_and_backoff(brain,monkeypatch):
    seed()
    monkeypatch.setattr(wiki,'_native_model_identity',lambda *a:pytest.fail('Retry must not inspect a model executable'))
    result=wiki.reset_retries()
    with wiki.db() as db:
        source=db.execute('SELECT error,attempts,attempt_hash,deferred_until FROM sources').fetchone()
        batch=db.execute('SELECT attempts FROM batches').fetchone()
    assert tuple(source)==(None,0,None,0) and batch[0]==0
    assert json.loads((cfg.STATE/'wiki-worker-status.json').read_text())['status']=='retry requested'
    assert 'pending work preserved' in result['status']


def test_no_hold_model_configuration_keeps_identity_validation_and_db_status(brain,monkeypatch):
    before=seed();calls=[]
    identity={'path':'synthetic-never-executed.exe','sha256':'b'*64,'bytes':1}
    monkeypatch.setattr(wiki,'_native_model_identity',lambda value:calls.append(value) or identity)
    result=wiki.configure_model(identity['path'])
    assert calls==[identity['path']] and result['executable']==identity
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())=={'format':1,'executable':identity['path']}
    assert (cfg.STATE/'wiki-jobs.db').read_bytes()==before['wiki-jobs.db']
    assert (cfg.STATE/'wiki-worker-status.json').read_bytes()==before['wiki-worker-status.json']
