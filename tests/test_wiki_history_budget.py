"""Historical metadata parsing must be cached and stop at its CPU deadline."""
import json
import time

import brain_store as s
import brain_wiki as w
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


def test_expired_budget_never_starts_new_source_yaml_parse(brain,tmp_path,monkeypatch):
    page,claims,_=history_fixture(brain,tmp_path,claims=1,revisions=1)
    history=w._EvidenceHistory(); history.deadline=w.time.monotonic()-1
    def forbidden(text): raise AssertionError('Expired budget started another metadata parse')
    monkeypatch.setattr(w,'meta',forbidden)
    assert history.claim(page,claims[0]) is None
    assert history.commands==0


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
