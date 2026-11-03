"""After-lock transaction admission and safe untouched-runtime deferral."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_portable_remediation import portable

@pytest.mark.parametrize('stage',['prepared','starting','started','stopped','start failed'])
def test_after_lock_writer_gate_and_exact_migration_exception(brain,stage):
    _,state=brain;hold='a'*32
    (state/'service-transaction.json').write_text(json.dumps({'root':str(cfg.ROOT),'hold_id':hold,'stage':stage}))
    if stage in ('stopped','start failed'):
        with pytest.raises(ValueError,match='held for'):cfg.write_admission()
        with pytest.raises(ValueError,match='held for'):cfg.write_admission('b'*32)
        with pytest.raises(ValueError,match='held for'):s.capture('held','Held capture','Synthetic event','test','synthetic admission')
        assert not (s.VAULT/'00-Inbox').exists()
    else:cfg.write_admission()
    cfg.write_admission(hold)

def test_post_stop_lock_refusal_restarts_unchanged_runtime_without_second_work_lock(portable,monkeypatch):
    from upgrade_fixture import prepare
    h=prepare(portable,monkeypatch);calls=[]
    def busy(*args,**kwargs):
        calls.append(kwargs.get('timeout'))
        raise TimeoutError('Synthetic legacy writer entered after stop')
    monkeypatch.setattr(h.updater,'_work_locks',busy)
    before=(h.root/'device.json').read_bytes()
    with pytest.raises(RuntimeError,match='previous runtime health verified'):h.updater.perform(h.root,'v0.1.6')
    assert calls==[300] and h.running and (h.root/'device.json').read_bytes()==before
    assert not (h.root/'state/service-transaction.json').exists()
    assert not (h.root/'state/previous-release.json').exists()
    assert [e['action'] for e in h.events if e['kind']=='controller']==['inspect','hold','stop','start','finish']
