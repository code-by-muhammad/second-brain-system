import json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_saved_plan_repair import brain, interrupted_plan, partial_index_conflict, row
from test_wiki_remediation import explicit, fake_model

def observe(*args): pass

def snapshot(root): return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def caught(call):
    try: return call(),None
    except Exception as error: return None,error


def test_r69_unused_plan_must_not_promise_partial_cas_repair(brain,monkeypatch):
    source,packet,result,folder,calls=interrupted_plan(brain,monkeypatch)
    original=source.read_bytes(); private=snapshot(folder)
    assert not w._saved_publication_started(packet,folder/'publication.json')
    index=s.VAULT/w.BASE/'Index.md'; owner=b'# Owner concurrent Index\n'; index.parent.mkdir(parents=True,exist_ok=True); index.write_bytes(owner)
    w.reset_retries()
    _,error=caught(w.run)
    first=w._read_worker_state(); first_row=row(packet)
    unchanged_owner=index.read_bytes()==owner
    # Follow the current documented before-image repair in this disposable test.
    index.unlink(); w.reset_retries()
    second,second_error=caught(w.run)
    observe('R69-unused-plan',{'first_error':type(error).__name__ if error else None,'first_worker':first,'first_batch_status':first_row['status'],'owner_preserved_during_conflict':unchanged_owner,'second_status':second.get('status') if second else None,'second_error':type(second_error).__name__ if second_error else None,'model_calls_total':len(calls),'old_plan_preserved':all((folder/name).read_bytes()==raw for name,raw in private.items() if name in ('input.json','output.json','publication.json')),'source_preserved':source.read_bytes()==original})
    assert unchanged_owner and source.read_bytes()==original
    assert first.get('recovery_action')!='resolve_saved_publication_cas_then_retry'
    assert first.get('integrity_field')!='publication_cas'


def test_r69_actual_partial_plan_still_holds_and_recovers_without_new_call_control(brain,monkeypatch):
    source,packet,calls,index,before,owner,plan=partial_index_conflict(brain,monkeypatch)
    assert index.read_bytes()==owner and len(calls)==1
    state=w._read_worker_state()
    assert state['integrity_field']=='publication_cas' and state['recovery_action']=='resolve_saved_publication_cas_then_retry'
    assert w.run()['status']=='attention'
    if before is None: index.unlink()
    else: index.write_bytes(before)
    w.reset_retries(); assert w.run()['status']=='complete' and len(calls)==1


@pytest.mark.parametrize('corruption',['missing','invalid-json','scalar','wrong-batch','wrong-source'])
def test_r72_corrupt_saved_input_sets_controlled_attention_preserving_artifacts(brain,monkeypatch,corruption):
    source,packet,result,folder,calls=interrupted_plan(brain,monkeypatch)
    w.reset_retries(); input_path=folder/'input.json'
    if corruption=='missing': input_path.unlink()
    elif corruption=='invalid-json': input_path.write_bytes(b'{not valid json')
    elif corruption=='scalar': input_path.write_bytes(b'42')
    elif corruption=='wrong-batch':
        changed=json.loads(input_path.read_text()); changed['batch_id']='0'*24; input_path.write_text(json.dumps(changed),encoding='utf-8')
    else:
        changed=json.loads(input_path.read_text()); changed['sources'][0]['path']='01-Projects/Nonexistent.md'; input_path.write_text(json.dumps(changed),encoding='utf-8')
    private=snapshot(folder); originals=snapshot(s.VAULT); before=w._read_worker_state()
    calls2=fake_model(monkeypatch,explicit(packet,result))
    result,error=caught(w.run); after=w._read_worker_state()
    observe('R72-'+corruption,{'exception_type':type(error).__name__ if error else None,'result':result,'worker_before':before,'worker_after':after,'model_calls':len(calls2),'private_artifacts_preserved':snapshot(folder)==private,'vault_preserved':snapshot(s.VAULT)==originals})
    assert len(calls2)==0 and snapshot(folder)==private and snapshot(s.VAULT)==originals
    assert after.get('attention_required') is True and after.get('integrity_field')=='recovery'
    assert error is None or isinstance(error,w.WikiIntegrityError)



@pytest.mark.parametrize('kind',['wiki_index','wiki_source','archive-only'])
def test_unused_cas_names_fresh_generation_and_preserves_owner_and_plan(brain,monkeypatch,kind):
    if kind=='archive-only':
        log=s.VAULT/w.BASE/'Log.md'; log.parent.mkdir(parents=True); log.write_bytes(b'# History'+b'x'*1_000_001)
    source,packet,result,folder,calls=interrupted_plan(brain,monkeypatch)
    plan=json.loads((folder/'publication.json').read_text(encoding='utf-8'))
    assert not w._saved_publication_started(packet,folder/'publication.json')
    if kind=='archive-only': assert list((s.VAULT/w.BASE/'Log-Archives').glob('*.md'))
    write=next(row for row in plan['writes'] if row['kind']==('wiki_index' if kind=='archive-only' else kind))
    target=s.VAULT/write['path']; target.parent.mkdir(parents=True,exist_ok=True); owner=b'# Owner concurrent edit';target.write_bytes(owner)
    private=snapshot(folder); originals=snapshot(s.VAULT)
    w.reset_retries(); _,error=caught(w.run)
    assert isinstance(error,w.WikiTransientError)
    state=w._read_worker_state()
    assert state['integrity_field']=='unused_publication_cas'
    assert state['recovery_action']=='retry_fresh_generation_preserving_owner_edits'
    assert state['integrity_page']==write['path']
    assert w.status()['worker']['recovery_action']==state['recovery_action']
    assert snapshot(folder)==private and snapshot(s.VAULT)==originals and len(calls)==1
    assert row(packet)['status']=='stale' and state['backoff_seconds']>0

@pytest.mark.parametrize('shape',['list','missing-sources','source-scalar','missing-text','bad-part','bad-hash','bad-key','catalog-scalar','page-meta','oversize','invalid-utf8','deep-json'])
def test_malformed_saved_packet_shape_stops_before_model_without_mutation(brain,monkeypatch,shape):
    _,packet,result,folder,_=interrupted_plan(brain,monkeypatch)
    path=folder/'input.json'; value=json.loads(path.read_text(encoding='utf-8'))
    if shape=='list': value=[]
    elif shape=='missing-sources': del value['sources']
    elif shape=='source-scalar': value['sources']=[1]
    elif shape=='missing-text': del value['sources'][0]['text']
    elif shape=='bad-part': value['sources'][0]['part']=True
    elif shape=='bad-hash': value['sources'][0]['sha256']='bad'
    elif shape=='bad-key': value['sources'][0]['job_key']='bad'
    elif shape=='catalog-scalar': value['catalog']=[False]
    elif shape=='page-meta': value['current_pages']=[{'path':w.BASE+'Projects/example.md','meta':None}]
    raw=b'x'*2_000_001 if shape=='oversize' else bytes([255]) if shape=='invalid-utf8' else b'['*2000+b']'*2000 if shape=='deep-json' else json.dumps(value).encode()
    path.write_bytes(raw); before=snapshot(folder); notes=snapshot(s.VAULT); original_row=row(packet)
    calls=fake_model(monkeypatch,explicit(packet,result)); w.reset_retries(); original_row=row(packet)
    if shape=='oversize': assert w.run()['status']=='attention'
    else:
        with pytest.raises(w.WikiIntegrityError): w.run()
    state=w._read_worker_state()
    assert state['attention_required'] and state['integrity_field']==('packet' if shape=='oversize' else 'recovery') and state['consecutive_failures']==0
    assert not calls and snapshot(folder)==before and snapshot(s.VAULT)==notes and row(packet)==original_row
    with w.db() as conn: assert not list(conn.execute('SELECT * FROM sources WHERE attempts>0'))

@pytest.mark.parametrize('failure',['io','memory'])
def test_saved_input_io_is_retryable_and_fatal_error_is_not_hidden(brain,monkeypatch,failure):
    _,packet,result,folder,_=interrupted_plan(brain,monkeypatch); path=folder/'input.json'
    original=Path.open
    def fail(self,*args,**kwargs):
        if self==path: raise PermissionError('Synthetic temporary IO') if failure=='io' else MemoryError('Synthetic fatal error')
        return original(self,*args,**kwargs)
    calls=fake_model(monkeypatch,explicit(packet,result)); w.reset_retries(); before=snapshot(folder)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path,'open',fail)
        if failure=='memory':
            with pytest.raises(MemoryError): w.run()
        else:
            report=w.run(); assert report['status']=='retryable' and report['model_called'] is False and report['backoff_seconds']==60
    assert not calls and snapshot(folder)==before

@pytest.mark.parametrize('status',['complete','error'])
def test_legacy_quarantine_and_generic_error_do_not_load_untrusted_input(brain,monkeypatch,status):
    from test_wiki import fixture
    _,packet,result=fixture(brain); folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    (folder/'input.json').write_bytes(b'broken legacy input')
    with w.db() as conn: conn.execute('UPDATE batches SET status=? WHERE id=?',(status,packet['batch_id']))
    assert w._resume_packet() is None
    assert row(packet)['status']==('legacy_unverified' if status=='complete' else 'error')
    assert (folder/'input.json').read_bytes()==b'broken legacy input'

@pytest.mark.parametrize('corruption',['missing','scalar'])
def test_actual_partial_with_broken_input_is_retained_for_recovery(brain,monkeypatch,corruption):
    _,packet,calls,index,before,owner,plan=partial_index_conflict(brain,monkeypatch)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; path=folder/'input.json'
    if corruption=='missing':path.unlink()
    else:path.write_bytes(b'42')
    private=snapshot(folder); notes=snapshot(s.VAULT); w.reset_retries()
    with pytest.raises(w.WikiIntegrityError): w.run()
    assert w._read_worker_state()['integrity_field']=='recovery'
    assert len(calls)==1 and snapshot(folder)==private and snapshot(s.VAULT)==notes
