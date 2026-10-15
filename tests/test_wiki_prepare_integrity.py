"""Derived repair errors must stop before context serialization or publication."""
import copy
from datetime import date
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture
from test_wiki_remediation import explicit, fake_model


def existing_topic(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'
    another=source.with_name('Another.md'); another.write_bytes(source.read_bytes())
    result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    return source,another,topic,result


def unquoted_origin_date(topic):
    raw=topic.read_bytes()
    assert b"    date: '2026-10-01'" in raw
    topic.write_bytes(raw.replace(b"    date: '2026-10-01'",b'    date: 2026-10-01'))
    assert isinstance(w.meta(topic.read_text())['wiki_claims'][0]['source_revision']['date'],date)


def test_actual_unquoted_yaml_origin_date_stops_prepare_with_controlled_integrity(brain):
    _,_,topic,_=existing_topic(brain); unquoted_origin_date(topic)
    with pytest.raises(w.WikiIntegrityError) as caught: w.prepare()
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix() and caught.value.claim_index==0


def test_existing_unsafe_claim_source_stops_render_with_controlled_integrity(brain):
    _,another,topic,result=existing_topic(brain); info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source']='../escape.md'
    topic.write_bytes(s.note_text(info,'# Preserved malformed source path.').encode())
    another.write_bytes(another.read_bytes().replace(b'Example Project',b'Unrelated subject'))
    packet=w.prepare()
    with pytest.raises(w.WikiIntegrityError) as caught: w.apply(packet,result)
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix() and caught.value.claim_index==0


def note_snapshot():
    return {path.relative_to(s.VAULT).as_posix():path.read_bytes() for path in s.VAULT.rglob('*.md')}


def test_unquoted_origin_date_updates_worker_attention_without_model_and_retries_after_cas_repair(brain,monkeypatch):
    source,another,topic,result=existing_topic(brain); unquoted_origin_date(topic)
    before=note_snapshot(); plans=set((cfg.STATE/'wiki/batches').rglob('publication.json'))
    w._atomic_json(cfg.STATE/'wiki-worker-status.json',{'status':'complete','at':s.stamp(),'consecutive_failures':0,'attention_required':False})
    calls=fake_model(monkeypatch,result)
    report=w.run()
    assert report['status']=='attention' and report['integrity_page']==topic.relative_to(s.VAULT).as_posix() and report['claim_index']==0
    assert report['reason']=='WikiIntegrityError' and report['consecutive_failures']==0
    assert w.run()['status']=='attention' and len(calls)==0
    assert note_snapshot()==before and set((cfg.STATE/'wiki/batches').rglob('publication.json'))==plans
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0]==0
    assert w.status()['pending_source_parts']==1
    # Repair from actual owner-reviewed evidence using the current note hash;
    # the compiler does not coerce or rewrite the malformed attribution itself.
    info=w.meta(topic.read_text()); info['wiki_claims'][0]['source_revision']['date']='2026-10-01'
    repair=s._write(topic,s.note_text(info,'# Owner-repaired quoted date.'),'owner_integrity_repair',s.digest(before[topic.relative_to(s.VAULT).as_posix()]))
    w.reset_retries(); packet=w.prepare()
    assert next(page['sha256'] for page in packet['catalog'] if page['path']==topic.relative_to(s.VAULT).as_posix())==repair['sha256']
    calls=fake_model(monkeypatch,explicit(packet,result)); completed=w.run()
    assert completed['status']=='complete' and len(calls)==1 and not completed['lint']['issues']
    assert w.status()['pending_source_parts']==0 and not w.status()['worker']['attention_required']
    assert source.read_bytes()==before[source.relative_to(s.VAULT).as_posix()]
    assert another.read_bytes()==before[another.relative_to(s.VAULT).as_posix()]


@pytest.mark.parametrize('unsafe',['../escape.md','01-Projects/Source.txt','C:/outside.md','01-Projects/.hidden.md','01-Projects/NUL.md'])
def test_relevant_unsafe_existing_source_stops_preflight_with_zero_calls_or_strikes(brain,monkeypatch,unsafe):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source']=unsafe
    topic.write_bytes(s.note_text(info,'# Owner history remains preserved.').encode()); before=note_snapshot()
    calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError) as caught: w.prepare()
    assert caught.value.claim_index==0
    assert w.run()['status']=='attention' and w.run()['status']=='attention' and len(calls)==0
    assert note_snapshot()==before
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0]==0
    assert any(issue['issue']=='malformed claim metadata' for issue in w.lint()['issues'])


def test_nonrelevant_unsafe_existing_source_still_stops_render_after_one_call(brain,monkeypatch):
    _,another,topic,result=existing_topic(brain); info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source']='01-Projects/Source.txt'
    topic.write_bytes(s.note_text(info,'# Preserve malformed attribution.').encode())
    another.write_bytes(another.read_bytes().replace(b'Example Project',b'Unrelated subject'))
    before=note_snapshot(); packet=w.prepare(); assert packet['current_pages']==[]
    calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix() and caught.value.claim_index==0
    assert w.run()['status']=='attention' and len(calls)==1 and note_snapshot()==before
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0


def test_nonexistent_safe_historical_markdown_source_can_still_publish_from_actual_git_evidence(brain,tmp_path):
    vault,_=brain; git=git_fixture(tmp_path,vault)
    source,packet,result=fixture(brain); w.apply(packet,result); original=source.read_bytes()
    git('add','--','.'); git('commit','-m','Actual historical source and anchored claim')
    source.unlink(); git('add','--','.'); git('commit','-m','Owner removed original note')
    another=source.with_name('Another.md'); another.write_bytes(original)
    packet=w.prepare(); result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(vault).as_posix()
    w.apply(packet,result)
    report=w.lint()
    assert not source.exists() and another.read_bytes()==original
    assert not report['issues'] and report['historical_evidence_count']==1
    claims=w.meta((vault/w.BASE/'Projects/example.md').read_text())['wiki_claims']
    assert claims[0]['source']=='01-Projects/Example.md' and claims[0]['source_revision']['sha256']==s.digest(original)


@pytest.mark.parametrize('title',[date(2026,10,1),123,None],ids=['yaml-date','integer','null'])
def test_explicit_bad_catalog_title_is_controlled_before_any_model_call(brain,monkeypatch,title):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text()); info['title']=title
    topic.write_bytes(s.note_text(info,'# Preserved explicit malformed title.').encode()); before=note_snapshot()
    calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError) as caught: w.prepare()
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix() and caught.value.claim_index is None
    report=w.run()
    assert report['status']=='attention' and report['claim_index'] is None and len(calls)==0
    assert w.run()['status']=='attention' and note_snapshot()==before
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0


def test_missing_legacy_title_keeps_path_fallback_and_publishes_normally(brain,monkeypatch):
    source,another,topic,result=existing_topic(brain); info=w.meta(topic.read_text()); info.pop('title')
    topic.write_bytes(s.note_text(info,'# Legacy topic without explicit title.').encode())
    packet=w.prepare(); relative=topic.relative_to(s.VAULT).as_posix()
    assert next(page['title'] for page in packet['catalog'] if page['path']==relative)==relative
    calls=fake_model(monkeypatch,explicit(packet,result)); completed=w.run()
    assert completed['status']=='complete' and len(calls)==1 and not completed['lint']['issues']


def test_oversize_matching_claim_context_is_skipped_before_claim_preflight(brain):
    _,_,topic,_=existing_topic(brain); info=w.meta(topic.read_text())
    info['wiki_claims'][0]['quote']='x'*41000
    info['wiki_claims'][0]['source_revision']['date']=date(2026,10,1)
    topic.write_bytes(s.note_text(info,'# Oversize context is not sent to the model.').encode()); before=topic.read_bytes()
    packet=w.prepare()
    assert packet['current_pages']==[]
    assert any(page['path']==topic.relative_to(s.VAULT).as_posix() for page in packet['catalog'])
    assert topic.read_bytes()==before


@pytest.mark.parametrize('malformed',['recursive-alias','bytes-key'])
def test_unmeasurable_actual_yaml_context_stops_worker_before_model_and_preserves_notes(brain,monkeypatch,malformed):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text()); claim=info['wiki_claims'][0]
    claim['extra']=claim if malformed=='recursive-alias' else {b'key':'value'}
    topic.write_bytes(s.note_text(info,'# Preserve owner-written unsupported context.').encode())
    parsed=w.meta(topic.read_text())['wiki_claims'][0]
    if malformed=='recursive-alias': assert parsed['extra'] is parsed
    else: assert b'key' in parsed['extra']
    before=note_snapshot(); calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError) as caught: w.prepare()
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix()
    report=w.run()
    assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==0
    assert w.run()['status']=='attention' and note_snapshot()==before
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0]==0
