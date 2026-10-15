"""Systematic source identity and decoded model-context boundary probes."""
import copy, json, sys
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_saved_catalog_identity import brain, snapshot
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model

OUT=Path(__file__).resolve().parent

@pytest.mark.parametrize('shape',['duplicate-source','same-path-two-parts','credential-text','credential-title','credential-date','surrogate-text','surrogate-title','surrogate-date','valid-control'])
def test_saved_source_identity_and_model_strings_are_preflighted(brain,monkeypatch,shape):
    source,packet,result=fixture(brain)
    changed=copy.deepcopy(packet); marker='ghp_'+'SyntheticMarkerAlphaBetaGammaDelta'
    field=shape.rsplit('-',1)[-1]
    if shape=='duplicate-source':changed['sources'].append(copy.deepcopy(changed['sources'][0]))
    elif shape=='same-path-two-parts':
        changed['sources'][0]['parts']=2
        other=copy.deepcopy(changed['sources'][0]);other['part']=2;other['job_key']=other['path']+'::part2'
        changed['sources'].append(other)
    elif shape.startswith('credential-'):changed['sources'][0][field]+=' '+marker
    elif shape.startswith('surrogate-'):changed['sources'][0][field]+='\ud800'
    saved=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json'
    # The durable format can encode surrogate escapes even though they are not
    # UTF-8 model strings. This does not modify the underlying valid source.
    saved.write_bytes(json.dumps(changed).encode('ascii'))
    notes=snapshot(s.VAULT); artifacts=snapshot(saved.parent)
    output=explicit(changed,result)
    if shape=='duplicate-source':output['dispositions']=output['dispositions'][:1]
    calls=fake_model(monkeypatch,output)
    report=None;error=None
    try:report=w.run()
    except Exception as exc:error=exc
    state=w._read_worker_state()
    observation={'shape':shape,'model_calls':len(calls),'input_has_marker':any(marker in value for value in calls.inputs),
        'input_has_surrogate':any('\ud800' in value for value in calls.inputs),
        'error':type(error).__name__ if error else None,'worker':state,'returned_status':report.get('status') if isinstance(report,dict) else None,
        'private_preserved':snapshot(saved.parent)==artifacts,'vault_preserved':snapshot(s.VAULT)==notes,'recursion_limit':sys.getrecursionlimit()}
    if shape=='valid-control':
        assert error is None and report['status']=='complete' and len(calls)==1
    else:
        assert not calls and snapshot(saved.parent)==artifacts and snapshot(s.VAULT)==notes
        assert state.get('attention_required') and state.get('integrity_field') in ('recovery','content')
        assert state['consecutive_failures']==0

def test_distinct_selected_source_paths_compile_together(brain,monkeypatch):
    source,first,result=fixture(brain)
    second=source.with_name('Second.md');second.write_bytes(source.read_bytes())
    packet=w.prepare(2);assert len(packet['sources'])==2
    claim=copy.deepcopy(result['pages'][0]['claims'][0]);claim['source']=second.relative_to(s.VAULT).as_posix();result['pages'][0]['claims'].append(claim)
    calls=fake_model(monkeypatch,explicit(packet,result));report=w.run(2)
    assert report['status']=='complete' and len(calls)==1

@pytest.mark.parametrize('field',['text','title','date'])
def test_source_context_accepts_ordinary_api_key_discussion(brain,monkeypatch,field):
    _,packet,result=fixture(brain);packet['sources'][0][field]+=' API key rotation is discussed without storing a credential.'
    path=cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json';w._atomic_json(path,packet)
    calls=fake_model(monkeypatch,explicit(packet,result));assert w.run()['status']=='complete' and len(calls)==1

@pytest.mark.parametrize('progress',['partial','unused'])
def test_legacy_source_context_exemption_requires_validated_semantic_partial(brain,monkeypatch,progress):
    _,packet,result=fixture(brain);packet['sources'][0]['title']+=' ghp_'+'SyntheticMarkerAlphaBetaGammaDelta'
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',packet)
    calls=fake_model(monkeypatch,explicit(packet,result));write=s._write
    def stop(path,text,kind,*args):
        if kind==('wiki_index' if progress=='partial' else 'wiki_compile'):raise OSError('Synthetic legacy interruption')
        return write(path,text,kind,*args)
    # Construct an old, fully hash-bound publication using only synthetic data.
    # The current guard is restored before the actual recovery assertion.
    with monkeypatch.context() as old:
        old.setattr(w,'_validate_packet_context',lambda packet:None)
        old.setattr(s,'clean_text',lambda value,*args:value)
        old.setattr(s,'_write',stop)
        with pytest.raises(OSError):w.run()
    assert len(calls)==1
    assert w._saved_publication_started(packet,folder/'publication.json')==(progress=='partial')
    before=snapshot(folder);notes=snapshot(s.VAULT);w.reset_retries()
    report=w.run()
    if progress=='partial':assert report['status']=='complete' and len(calls)==1
    else:
        assert report['status']=='attention' and report['integrity_field']=='content' and report['consecutive_failures']==0
        assert len(calls)==1 and snapshot(folder)==before and snapshot(s.VAULT)==notes
