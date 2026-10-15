"""Historical metadata parsing must be cached and stop at its CPU deadline."""
import json
import time

import pytest

import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture


def history_fixture(brain,tmp_path,claims=8,revisions=6):
    vault,_=brain; git=git_fixture(tmp_path,vault)
    relative='01-Projects/Historical.md'; source=vault/relative; source.parent.mkdir()
    statements=[f'Historical requirement number {i} remains explicitly recorded.' for i in range(claims)]
    original='---\ndate: 2026-10-01\n---\n# Historical source\n'+'\n'.join(statements)+'\n'
    source.write_bytes(original.encode()); original_sha=s.digest(source.read_bytes())
    entries=[{'text':f'The source records historical requirement {i}.','source':relative,'quote':quote,'status':'historical'} for i,quote in enumerate(statements)]
    topic=vault/w.BASE/'Projects/historical.md'; topic.parent.mkdir(parents=True)
    info={'date':'2026-10-01','wiki_kind':'Projects','title':'Historical topic','wiki_claims':entries,'source_hashes':{relative:original_sha}}
    for revision in range(revisions):
        topic.write_bytes(s.note_text(info,f'# Historical topic\n\nRevision {revision}.').encode())
        git('add','--','.'); git('commit','-m',f'Synthetic topic revision {revision}')
    source.write_bytes(b'---\ndate: 2026-10-02\n---\n# Current source\nThe owner replaced historical requirements.\n')
    info['source_hashes'][relative]=s.digest(source.read_bytes())
    topic.write_bytes(s.note_text(info,'# Current topic with overwritten source map.').encode())
    git('add','--','.'); git('commit','-m','Current source revision')
    page={'path':topic.relative_to(vault).as_posix(),'meta':info}
    return page,entries,revisions+1


def topic_parse_counter(monkeypatch):
    calls=[]; original=w.meta
    def counted(text):
        if '\nwiki_kind: Projects\n' in text: calls.append(len(text))
        return original(text)
    monkeypatch.setattr(w,'meta',counted)
    return calls


def test_actual_git_topic_headers_parse_once_across_many_legacy_claims(brain,tmp_path,monkeypatch):
    page,claims,revisions=history_fixture(brain,tmp_path)
    calls=topic_parse_counter(monkeypatch); history=w._EvidenceHistory(); started=time.perf_counter()
    proofs=[history.claim(page,claim) for claim in claims]
    print(json.dumps({'claims':len(claims),'topic_revisions':revisions,'topic_yaml_parses':len(calls),'seconds':round(time.perf_counter()-started,4)}))
    assert all(proof and proof['mode']=='git-history' for proof in proofs)
    assert len(calls)<=revisions


def test_expired_deadline_does_not_reparse_cached_actual_git_headers(brain,tmp_path,monkeypatch):
    page,claims,revisions=history_fixture(brain,tmp_path)
    history=w._EvidenceHistory()
    list(history._versions(page['path']))
    list(history._versions(claims[0]['source']))
    history._current(claims[0]['source'])
    history.deadline=w.time.monotonic()-1
    calls=topic_parse_counter(monkeypatch); started=time.perf_counter()
    proofs=[history.claim(page,claim) for claim in claims]
    print(json.dumps({'cached_topic_revisions':revisions,'after_deadline_topic_yaml_parses':len(calls),'seconds':round(time.perf_counter()-started,4)}))
    assert len(calls)==0
    assert all(proof is None for proof in proofs)


def test_deadline_expiring_during_one_topic_parse_stops_further_metadata_work(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=3,revisions=3)
    history=w._EvidenceHistory(); original=w.meta; calls=[]
    def expires_after_parse(text):
        value=original(text)
        if '\nwiki_kind: Projects\n' in text:
            calls.append(True); history.deadline=w.time.monotonic()-1
        return value
    monkeypatch.setattr(w,'meta',expires_after_parse)
    assert history.claim(page,claims[0]) is None
    commands=history.commands
    assert all(history.claim(page,claim) is None for claim in claims[1:])
    assert calls==[True] and history.commands==commands


def test_expired_history_budget_keeps_current_checks_without_parsing_historical_headers(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=1)
    history=w._EvidenceHistory(); history.deadline=w.time.monotonic()-1
    original=w.meta; parsed=[]
    def current_only(text):
        assert '\nwiki_kind: Projects\n' not in text
        parsed.append(text); return original(text)
    monkeypatch.setattr(w,'meta',current_only)
    assert history.claim(page,claims[0]) is None
    raw=(s.VAULT/claims[0]['source']).read_bytes()
    proof=history.source(claims[0]['source'],s.digest(raw),'The owner replaced historical requirements.')
    assert proof and proof['mode']=='current' and proof['date']=='2026-10-02'
    assert len(parsed)==1 and history.commands==history.bytes==0


@pytest.mark.parametrize('warm',[False,True],ids=['cold','cached'])
def test_exact_current_proof_stays_available_when_history_budget_expires(brain,warm):
    source,packet,result=fixture(brain); row=packet['sources'][0]; raw=source.read_bytes()
    history=w._EvidenceHistory()
    if warm: assert history._current(row['path'])
    history.deadline=w.time.monotonic()-1
    proof=history.source(row['path'],s.digest(raw),result['pages'][0]['claims'][0]['quote'])
    assert proof and proof['mode']=='current' and proof['sha256']==s.digest(raw)
    assert history.commands==history.bytes==0 and source.read_bytes()==raw


def expired_history(monkeypatch):
    original=w._EvidenceHistory.__init__
    def expired(self): original(self); self.deadline=w.time.monotonic()-1
    monkeypatch.setattr(w._EvidenceHistory,'__init__',expired)


def test_new_publication_always_retains_current_origin_when_history_budget_expired(brain,monkeypatch):
    source,packet,result=fixture(brain); original=source.read_bytes(); expired_history(monkeypatch)
    w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'; claim=w.meta(topic.read_text())['wiki_claims'][0]
    assert claim['source_revision']=={'sha256':s.digest(original),'text_sha256':s.digest(w.normalized(original.decode('utf-8-sig')).encode()),'date':'2026-10-01'}
    assert 'evidence requires review' not in topic.read_text() and source.read_bytes()==original
    report=w.lint()
    assert not report['issues'] and report['history_verification']['git_commands']==0


def test_unchanged_current_claims_and_anchors_lint_clean_after_history_budget_expired(brain,monkeypatch):
    source,packet,result=fixture(brain); w.apply(packet,result); original=source.read_bytes()
    topic=s.VAULT/w.BASE/'Projects/example.md'; old=topic.read_bytes()
    expired_history(monkeypatch); report=w.lint()
    assert not report['issues'] and report['history_verification']['git_commands']==0
    assert source.read_bytes()==original and topic.read_bytes()==old


def test_cached_verified_proof_remains_reusable_after_deadline_and_contains_no_metadata(brain,tmp_path):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=2)
    history=w._EvidenceHistory(); proof=history.claim(page,claims[0]); assert proof
    history.deadline=w.time.monotonic()-1
    assert history.claim(page,claims[0])==proof
    assert set(proof)=={'text_sha256','date','git_commit','git_blob','sha256','mode','representation'}
    assert '_meta' not in proof and 'wiki_claims' not in proof and 'text' not in proof


def test_exhausted_blob_budget_does_not_parse_topic_history_or_guess_proof(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=2,revisions=2)
    calls=topic_parse_counter(monkeypatch); history=w._EvidenceHistory(); history.bytes=16_000_000
    assert all(history.claim(page,claim) is None for claim in claims)
    assert calls==[] and history.bytes==16_000_000 and history.commands<=256
