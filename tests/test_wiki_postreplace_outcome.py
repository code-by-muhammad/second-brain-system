"""Actual successful replacements retain intent across later I/O failures."""
import inspect,json
from pathlib import Path
import pytest
import brain_wiki as w
import brain_store as s
import brain_settings as cfg
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic
from test_wiki_remediation import explicit,fake_model
from test_wiki_model_context_preflight import brain,snapshot,fake_skip_model

@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('failure',['audit-open','audit-write','audit-close','metadata','cleanup-check','cleanup-unlink','caller-verification'])
def test_actual_postreplace_failure_retains_hold_when_owner_restores_beforeimage(brain,monkeypatch,existing,failure):
    if existing:
        source,another,topic,result=existing_topic(brain);packet=w.prepare();source=another
    else:
        source,packet,result=fixture(brain);topic=s.VAULT/w.BASE/'Projects/example.md'
    before=topic.read_bytes() if topic.exists() else None
    index=s.VAULT/w.BASE/'Index.md';index_before=index.read_bytes() if index.exists() else None
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];source_raw=source.read_bytes()
    calls=fake_model(monkeypatch,explicit(packet,result));native_open=Path.open;native_exists=s.os.path.exists;native_unlink=s.os.unlink;native_replace=s.os.replace;native_write=s._write;native_stamp=s.stamp
    fired=False;published=None;owner=b'# Owner Index retained after real replacement\n';error=OSError('Synthetic failure after successful replacement')
    def change_owner():
        nonlocal fired,published
        assert not fired;fired=True;published=topic.read_bytes()
        plan=json.loads((folder/'publication.json').read_text(encoding='utf-8'))
        assert s.digest(published)==next(row['after'] for row in plan['writes'] if row['path']==topic.relative_to(s.VAULT).as_posix())
        if before is None:topic.unlink()
        else:topic.write_bytes(before)
        index.write_bytes(owner)
    def is_temp(path):return Path(path).parent==topic.parent and Path(path).name.startswith('.brain-')
    class AuditProxy:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):
            self.stream.close()
            if failure=='audit-close':change_owner();raise error
        def write(self,value):
            if failure=='audit-close':return self.stream.write(value)
            change_owner();raise error
    def audit(path,*args,**kwargs):
        if Path(path)==cfg.STATE/'audit.jsonl' and args and args[0]=='a' and not fired:
            if failure=='audit-open':change_owner();raise error
            if failure in ('audit-write','audit-close'):return AuditProxy(native_open(path,*args,**kwargs))
        return native_open(path,*args,**kwargs)
    def stamp():
        if failure=='metadata' and not fired and inspect.currentframe().f_back.f_code.co_name=='_write':change_owner();raise error
        return native_stamp()
    def exists(path):
        if failure=='cleanup-check' and not fired and is_temp(path) and topic.is_file():change_owner();raise error
        return native_exists(path)
    def replace(src,dst):
        result=native_replace(src,dst)
        if failure=='cleanup-unlink' and Path(dst)==topic:Path(src).write_bytes(b'Synthetic raced temporary file')
        return result
    def unlink(path,*args,**kwargs):
        if failure=='cleanup-unlink' and not fired and is_temp(path):change_owner();raise error
        return native_unlink(path,*args,**kwargs)
    def after_return(path,*args,**kwargs):
        receipt=native_write(path,*args,**kwargs)
        if failure=='caller-verification' and path==topic and not fired:change_owner()
        return receipt
    with monkeypatch.context() as fault:
        fault.setattr(Path,'open',audit);fault.setattr(s.os.path,'exists',exists);fault.setattr(s.os,'replace',replace);fault.setattr(s.os,'unlink',unlink);fault.setattr(s,'_write',after_return);fault.setattr(s,'stamp',stamp)
        with pytest.raises(OSError) as caught:w.run()
    assert fired and len(calls)==1 and source.read_bytes()==source_raw
    if failure!='caller-verification':assert caught.value is error and str(caught.value)=='Synthetic failure after successful replacement'
    notes,private=snapshot(s.VAULT),snapshot(folder);retry_calls=fake_skip_model(monkeypatch)
    for _ in range(2):
        w.reset_retries()
        try:w.run()
        except (w.WikiIntegrityError,w.WikiTransientError):pass
        assert not retry_calls and snapshot(s.VAULT)==notes and snapshot(folder)==private
        state=w._read_worker_state()
        assert state['batch_id']==packet['batch_id'] and state['attention_required'] and state['consecutive_failures']==0
        with w.db() as conn:
            assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0
            assert all(row[0]==0 for row in conn.execute('SELECT attempts FROM sources'))
    topic.write_bytes(published)
    if index_before is None:index.unlink()
    else:index.write_bytes(index_before)
    w.reset_retries();assert w.run()['status']=='complete' and not retry_calls

@pytest.mark.parametrize('mode',['abrupt-owner-delete','normal-no-owner-edit','normal-prewrite'])
def test_postreplace_classification_preserves_abrupt_and_genuine_prewrite_controls(brain,monkeypatch,mode):
    source,packet,result=fixture(brain);calls=fake_model(monkeypatch,explicit(packet,result));topic=s.VAULT/w.BASE/'Projects/example.md';index=s.VAULT/w.BASE/'Index.md'
    native=Path.open;write=s._write;fired=False
    class Abrupt(BaseException):pass
    def audit(path,*args,**kwargs):
        nonlocal fired
        if Path(path)==cfg.STATE/'audit.jsonl' and args and args[0]=='a' and not fired:
            fired=True
            if mode=='abrupt-owner-delete':topic.unlink();index.write_bytes(b'# Owner Index\n');raise Abrupt()
            raise OSError('Normal postreplace audit failure')
        return native(path,*args,**kwargs)
    def prewrite(path,*args,**kwargs):
        if args[1]=='wiki_compile':raise OSError('Genuine before replacement error')
        return write(path,*args,**kwargs)
    with monkeypatch.context() as fault:
        if mode=='normal-prewrite':fault.setattr(s,'_write',prewrite)
        else:fault.setattr(Path,'open',audit)
        with pytest.raises(Abrupt if mode=='abrupt-owner-delete' else OSError):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];journal=json.loads((folder/'journal.json').read_text(encoding='utf-8'))
    if mode=='normal-prewrite':assert journal.get('attempted_without_write') and not journal.get('in_flight')
    else:assert journal.get('in_flight')
    w.reset_retries()
    if mode=='abrupt-owner-delete':
        notes=snapshot(s.VAULT)
        with pytest.raises(w.WikiIntegrityError):w.run()
        assert snapshot(s.VAULT)==notes and len(calls)==1
    else:assert w.run()['status']=='complete' and len(calls)==1
