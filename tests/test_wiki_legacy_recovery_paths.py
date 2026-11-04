"""Legacy actual publication repairs without re-sending invalid context."""
import copy
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


def legacy_context(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain);info=w.meta(topic.read_text(encoding='utf-8'));old=copy.deepcopy(info['wiki_claims'][0]);claims=[copy.deepcopy(old) for _ in range(20)]
    for index,claim in enumerate(claims): claim['text']+=f' Historical statement number {index}.'
    claims[18]['owner_note']={'password':'synthetic_mock_value_1234567890'};info['wiki_claims']=claims
    topic.write_bytes(s.note_text(info,'# Synthetic pre-upgrade metadata.').encode());screen=w._screen_decoded_context
    monkeypatch.setattr(w,'_screen_decoded_context',lambda value,**kwargs:None);packet=w.prepare()
    assert packet['current_pages'][0]['claim_count']==20 and len(packet['current_pages'][0]['meta']['wiki_claims'])==12
    return topic,result,packet,screen


def test_verified_legacy_actual_partial_recovery_never_sends_future_invalid_context(brain,monkeypatch):
    topic,result,packet,screen=legacy_context(brain,monkeypatch);source_bytes={p:p.read_bytes() for p in (s.VAULT/'01-Projects').glob('*.md')}
    calls=fake_model(monkeypatch,explicit(packet,result));write=s._write
    def interrupted(path,*args,**kwargs):
        receipt=write(path,*args,**kwargs)
        if path==topic: raise OSError('Pre-upgrade replace-before-journal interruption')
        return receipt
    monkeypatch.setattr(s,'_write',interrupted)
    with pytest.raises(OSError):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved={name:(folder/name).read_bytes() for name in ('input.json','output.json','publication.json')}
    assert w._saved_publication_started(packet,folder/'publication.json')
    monkeypatch.setattr(s,'_write',write);monkeypatch.setattr(w,'_screen_decoded_context',screen)
    report=w.run();assert report['status']=='complete' and len(calls)==1
    assert any(issue.get('claim_index')==18 for issue in report['lint']['issues'])
    assert all((folder/name).read_bytes()==raw for name,raw in saved.items()) and all(path.read_bytes()==raw for path,raw in source_bytes.items())
    w.reset_retries();assert w.run()['status']=='idle' and len(calls)==1


@pytest.mark.parametrize('has_count',[True,False])
def test_saved_bounded_claim_window_reports_actual_index_or_safe_legacy_fallback(brain,monkeypatch,has_count):
    topic,result,packet,screen=legacy_context(brain,monkeypatch)
    if not has_count:
        packet['current_pages'][0].pop('claim_count');w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    monkeypatch.setattr(w,'_screen_decoded_context',screen);calls=fake_model(monkeypatch,explicit(packet,result));report=w.run()
    assert report['status']=='attention' and report['claim_index']==(18 if has_count else 10) and len(calls)==0
    assert report['integrity_page']==topic.relative_to(s.VAULT).as_posix()


def test_tokenlike_source_filename_is_filtered_before_model_and_not_rewritten(brain,monkeypatch):
    source,_,result=fixture(brain);renamed=source.with_name('SK-Hynix-supplier-evaluation.md');source.rename(renamed);original=renamed.read_bytes()
    # The established detector deliberately treats this plausible filename as
    # token-like. The source file remains available to ordinary file retrieval.
    with pytest.raises(ValueError,match='credential'):s.clean_text(renamed.relative_to(s.VAULT).as_posix(),400)
    calls=fake_model(monkeypatch,result);report=w.run()
    assert len(calls)==0 and report['status']=='idle' and w.inventory()==[]
    assert renamed.read_bytes()==original and not (s.VAULT/w.BASE/'Projects/example.md').exists()


def test_saved_tokenlike_selected_source_is_blocked_before_model(brain,monkeypatch):
    source,packet,result=fixture(brain);renamed=source.with_name('SK-Hynix-supplier-evaluation.md');source.rename(renamed)
    row=packet['sources'][0];row['path']=renamed.relative_to(s.VAULT).as_posix();row['job_key']=row['path']+'::part1'
    w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    before=note_snapshot();calls=fake_model(monkeypatch,result);report=w.run()
    assert report['status'] in ('idle','attention') and len(calls)==0 and note_snapshot()==before


def test_tokenlike_new_output_target_is_rejected_before_any_publication(brain):
    _,packet,result=fixture(brain);result=copy.deepcopy(result);result['pages'][0]['slug']='sk-hynix-supplier-evaluation'
    before=note_snapshot()
    with pytest.raises(ValueError):w.apply(packet,result)
    assert note_snapshot()==before and not list((s.VAULT/w.BASE).glob('Receipts/*.md'))


@pytest.mark.parametrize('location',['source','target'])
@pytest.mark.parametrize('started',[True,False])
def test_legacy_path_policy_only_exempts_fully_verified_actual_publication(brain,monkeypatch,location,started):
    source,_,result=fixture(brain);result=copy.deepcopy(result)
    if location=='source':
        renamed=source.with_name('SK-Hynix-supplier-evaluation.md');source.rename(renamed);source=renamed
        result['pages'][0]['claims'][0]['source']=source.relative_to(s.VAULT).as_posix()
    else:result['pages'][0]['slug']='sk-hynix-supplier-evaluation'
    clean=s.clean_text
    def legacy_clean(value,*args,**kwargs):
        if isinstance(value,str) and 'sk-hynix-supplier-evaluation' in value.lower():return value
        return clean(value,*args,**kwargs)
    monkeypatch.setattr(s,'clean_text',legacy_clean);packet=w.prepare();calls=fake_model(monkeypatch,explicit(packet,result));write=s._write
    def interrupt(path,*args,**kwargs):
        if args and args[1]=='wiki_compile':
            if started:write(path,*args,**kwargs)
            raise OSError('Authentic old-plan interruption')
        return write(path,*args,**kwargs)
    monkeypatch.setattr(s,'_write',interrupt)
    with pytest.raises(OSError):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];saved={name:(folder/name).read_bytes() for name in ('input.json','output.json','publication.json')};original=source.read_bytes()
    monkeypatch.setattr(s,'clean_text',clean);monkeypatch.setattr(s,'_write',write)
    assert w._saved_publication_started(packet,folder/'publication.json') is started
    if started:assert w.run()['status']=='complete'
    elif location=='source':
        assert w.run()['status']=='idle'
        with w.db() as conn:assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='superseded'
    else:
        with pytest.raises(ValueError):w.run()
    assert len(calls)==1 and source.read_bytes()==original and all((folder/name).read_bytes()==raw for name,raw in saved.items())
    if not started:assert not (s.VAULT/w.BASE/'Projects'/('sk-hynix-supplier-evaluation.md' if location=='target' else 'example.md')).exists()
