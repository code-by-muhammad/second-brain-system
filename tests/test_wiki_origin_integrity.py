"""Explicit malformed origins must preserve attribution and stop controlledly."""
import copy
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture
from test_wiki_remediation import explicit, fake_model


MALFORMED=[None,[], 'invalid-origin',True,{},
           {'sha256':'invalid','text_sha256':'a'*64},
           {'sha256':'a'*64,'text_sha256':[]},
           {'sha256':'a'*64,'text_sha256':'b'*64,'date':False}]


def topic_with_origin(brain,revision):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source_revision']=revision
    topic.write_bytes(s.note_text(info,'# Preserved existing topic and explicit attribution.').encode())
    another=source.with_name('Another.md'); another.write_bytes(source.read_bytes())
    packet=w.prepare(); result=copy.deepcopy(result)
    result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    return source,another,topic,packet,result


@pytest.mark.parametrize('revision',MALFORMED,ids=['null','list','string','bool','empty-map','bad-sha','bad-canonical-sha','bad-date'])
def test_malformed_origin_enters_immediate_attention_without_strikes_or_note_writes(brain,monkeypatch,revision):
    source,another,topic,packet,result=topic_with_origin(brain,revision)
    original={path.relative_to(s.VAULT).as_posix():path.read_bytes() for path in s.VAULT.rglob('*.md')}
    calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    assert caught.value.page==topic.relative_to(s.VAULT).as_posix() and caught.value.claim_index==0
    worker=json.loads((cfg.STATE/'wiki-worker-status.json').read_text())
    assert worker['attention_required'] is True and worker['reason']=='WikiIntegrityError'
    assert worker['consecutive_failures']==0 and worker['backoff_seconds']==worker['retry_after']==0
    assert w.run()['status']=='attention' and len(calls)==1
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0
    assert not (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists()
    assert original=={path.relative_to(s.VAULT).as_posix():path.read_bytes() for path in s.VAULT.rglob('*.md')}
    issues=w.lint()['issues']
    assert any(issue['issue']=='malformed source revision metadata' and issue['claim_index']==0 for issue in issues)
    assert w.meta(topic.read_text())['wiki_claims'][0]['source_revision']==revision


@pytest.mark.parametrize('revision',MALFORMED,ids=['null','list','string','bool','empty-map','bad-sha','bad-canonical-sha','bad-date'])
def test_explicit_malformed_origin_never_uses_current_or_legacy_fallback(brain,monkeypatch,revision):
    _,_,topic,_,_=topic_with_origin(brain,revision)
    info=w.meta(topic.read_text()); claim=info['wiki_claims'][0]
    monkeypatch.setattr(w._EvidenceHistory,'_git',lambda *_:pytest.fail('Malformed explicit origin must not use legacy Git fallback'))
    assert w._EvidenceHistory().claim({'path':topic.relative_to(s.VAULT).as_posix(),'meta':info},claim) is None


@pytest.mark.parametrize('mode',['absent-legacy','valid-dict','valid-dict-without-date'])
def test_valid_legacy_and_dict_origins_keep_fingerprints_and_publish(brain,mode):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
    old=copy.deepcopy(info['wiki_claims'][0]); fingerprint=w._claim_hash(old)
    if mode=='absent-legacy': info['wiki_claims'][0].pop('source_revision')
    if mode=='valid-dict-without-date': info['wiki_claims'][0]['source_revision'].pop('date')
    expected=copy.deepcopy(info['wiki_claims'][0].get('source_revision'))
    topic.write_bytes(s.note_text(info,'# Valid legacy/dict control.').encode())
    another=source.with_name('Another.md'); another.write_bytes(source.read_bytes())
    packet=w.prepare(); result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    w.apply(packet,result)
    claims=w.meta(topic.read_text())['wiki_claims']; assert len(claims)==2
    assert w._claim_hash(claims[0])==fingerprint and claims[0]['status']==old['status']
    assert claims[0]['source_revision']==(old['source_revision'] if mode=='absent-legacy' else expected)
    assert not w.lint()['issues']


@pytest.mark.parametrize('revision',[None,[], 'invalid-origin',True],ids=['null','list','string','bool'])
def test_historical_explicit_malformed_origin_cannot_turn_into_legacy_source_hash_proof(brain,tmp_path,revision):
    vault,_=brain; git=git_fixture(tmp_path,vault)
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=vault/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
    info['wiki_claims'][0]['source_revision']=revision
    topic.write_bytes(s.note_text(info,'# Explicit malformed old origin.').encode())
    git('add','--','.'); git('commit','-m','Malformed explicit origin and old source')
    source.write_bytes(b'# Owner revision\nThe owner replaced the original statement.\n')
    info['wiki_claims'][0].pop('source_revision')
    info['source_hashes'][source.relative_to(vault).as_posix()]=s.digest(source.read_bytes())
    topic.write_bytes(s.note_text(info,'# Current legacy topic with absent origin.').encode())
    git('add','--','.'); git('commit','-m','Current source and legacy topic')
    before=topic.read_bytes()
    assert w._EvidenceHistory().claim({'path':topic.relative_to(vault).as_posix(),'meta':info},info['wiki_claims'][0]) is None
    assert any(issue['issue']=='evidence quote missing' for issue in w.lint()['issues'])
    assert topic.read_bytes()==before


def test_structurally_valid_but_unverified_origin_is_preserved_and_never_rebound(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'; info=w.meta(topic.read_text())
    revision=copy.deepcopy(info['wiki_claims'][0]['source_revision']); revision['sha256']='f'*64
    info['wiki_claims'][0]['source_revision']=revision
    topic.write_bytes(s.note_text(info,'# Unknown supporting revision remains unknown.').encode())
    claim=info['wiki_claims'][0]
    assert w._EvidenceHistory().claim({'path':topic.relative_to(s.VAULT).as_posix(),'meta':info},claim) is None
    another=source.with_name('Another.md'); another.write_bytes(source.read_bytes())
    packet=w.prepare(); result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    w.apply(packet,result)
    assert w.meta(topic.read_text())['wiki_claims'][0]['source_revision']==revision
    assert any(issue['issue']=='source revision unverified' for issue in w.lint()['issues'])
