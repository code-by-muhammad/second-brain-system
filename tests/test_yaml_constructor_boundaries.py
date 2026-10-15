"""Pinned SafeLoader constructor errors must obey shared parser contracts."""
from datetime import date
import json
import pytest
import brain_markdown as md
import brain_maintenance as maintenance
import brain_git
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import fake_model


@pytest.mark.parametrize('value',["reviewed: !!bool maybe","reviewed: !!timestamp later","count: !!int ''","reviewed: !!timestamp {=: later}"])
@pytest.mark.parametrize('strict',[False,True])
def test_explicit_invalid_safe_tags_have_controlled_parser_contract(value,strict):
    text='---\n'+value+'\n---\n# Full original body remains unchanged.'
    if strict:
        with pytest.raises(ValueError,match='Invalid YAML frontmatter'):md.frontmatter(text,strict=True)
    else:assert md.frontmatter(text)=={}


@pytest.mark.parametrize('value',["reviewed: !!bool maybe","reviewed: !!timestamp later","count: !!int ''","reviewed: !!timestamp {=: later}"])
def test_invalid_explicit_source_tags_keep_inventory_and_exact_source_body(brain,value):
    path=s.VAULT/'01-Projects/Tagged.md';path.parent.mkdir();raw=('---\n'+value+'\n---\n# Tagged source\nHistorical evidence remains available.').encode();path.write_bytes(raw)
    assert w.status()['eligible_sources']==1
    packet=w.prepare();row=packet['sources'][0]
    assert row['date']=='unknown' and row['sha256']==s.digest(raw) and 'Historical evidence remains available.' in row['text'] and path.read_bytes()==raw


@pytest.mark.parametrize('value',["reviewed: !!bool maybe","reviewed: !!timestamp later","count: !!int ''","reviewed: !!timestamp {=: later}"])
def test_invalid_explicit_topic_tags_stop_worker_without_model_or_source_strikes(brain,monkeypatch,value):
    _,_,topic,result=existing_topic(brain);topic.write_bytes(('---\nwiki_kind: Projects\ntitle: Example Project\n'+value+'\n---\n# Owner metadata needs review.').encode())
    before=note_snapshot();calls=fake_model(monkeypatch,result);report=w.run()
    assert report['status']=='attention' and report['integrity_field']=='frontmatter' and report['consecutive_failures']==0 and len(calls)==0
    assert w.run()['status']=='attention' and note_snapshot()==before
    assert any(issue.get('field')=='frontmatter' for issue in w.lint()['issues'])


@pytest.mark.parametrize('value',["reviewed: !!bool maybe","reviewed: !!timestamp later","count: !!int ''","reviewed: !!timestamp {=: later}"])
@pytest.mark.parametrize('location',['capture','derived'])
def test_invalid_explicit_tags_never_abort_remaining_maintenance_git(brain,monkeypatch,value,location):
    folder=s.VAULT/('00-Inbox/AI-Captures' if location=='capture' else '02-Areas/AI-Memory');folder.mkdir(parents=True,exist_ok=True)
    path=folder/'invalid-tag.md';raw=('---\n'+value+'\n---\n# Preserve owner evidence.').encode();path.write_bytes(raw)
    calls=[];monkeypatch.setattr(brain_git,'sync',lambda:calls.append(True) or 'synthetic sync completed')
    report=maintenance.maintenance(sync=True)
    assert calls==[True] and report['status']=='completed' and report['git']=='synthetic sync completed' and path.read_bytes()==raw
    if location=='capture':assert any(item['path']==path.relative_to(s.VAULT).as_posix() for item in report['review'])


def test_valid_explicit_types_quoted_tag_words_and_aliases_remain_supported():
    text='---\nactive: !!bool true\ncount: !!int 42\nwhen: !!timestamp 2026-10-15\nword: "!!bool maybe"\nfirst: &guide [review, evidence]\nsecond: *guide\n---\n# Body unchanged.'
    parsed=md.frontmatter(text,strict=True)
    assert parsed['active'] is True and parsed['count']==42 and parsed['when']==date(2026,10,15)
    assert parsed['word']=='!!bool maybe' and parsed['first'] is parsed['second']


@pytest.mark.parametrize('failure',[RuntimeError('synthetic implementation error'),MemoryError('synthetic allocation error')])
@pytest.mark.parametrize('strict',[False,True])
def test_unrelated_programming_or_fatal_resource_errors_are_not_hidden(monkeypatch,failure,strict):
    def failed(*args,**kwargs):raise failure
    monkeypatch.setattr(md.yaml,'safe_load',failed)
    with pytest.raises(type(failure)):md.frontmatter('---\ntitle: Valid\n---\n# Body',strict=strict)
