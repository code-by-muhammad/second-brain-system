"""Invalid topic metadata stops compilation; invalid source metadata keeps evidence."""
from datetime import date
import copy
import pytest
import brain_markdown as md
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


@pytest.mark.parametrize('scalar',['2026-02-30','2026-13-01','2026-10-01T25:00:00Z'])
def test_impossible_yaml_timestamp_preserves_strict_nonstrict_contract(scalar):
    text='---\ndate: '+scalar+'\n---\n# Preserved source body.\nEvidence remains unchanged.'
    assert md.frontmatter(text)=={}
    with pytest.raises(ValueError,match='Invalid YAML frontmatter'): md.frontmatter(text,strict=True)
    assert '# Preserved source body.' in text


@pytest.mark.parametrize('newline,bom',[('\n',False),('\r\n',False),('\n',True)])
def test_impossible_source_date_is_inventoried_with_unknown_date_and_exact_body(brain,newline,bom):
    source=s.VAULT/'01-Projects/Dated.md'; source.parent.mkdir()
    text='---\ndate: 2026-02-30\n---\n# Dated\nA reusable historical statement remains available.'
    raw=(('\ufeff' if bom else '')+text.replace('\n',newline)).encode(); source.write_bytes(raw)
    report=w.status(); assert report['eligible_sources']==1 and report['pending_source_parts']==1
    packet=w.prepare(); assert packet['sources'][0]['date']=='unknown' and packet['sources'][0]['sha256']==s.digest(raw)
    assert 'A reusable historical statement remains available.' in packet['sources'][0]['text'] and source.read_bytes()==raw


def test_quoted_impossible_date_stays_literal_and_valid_date_stays_date():
    assert md.frontmatter('---\ndate: "2026-02-30"\n---\n# Body',strict=True)['date']=='2026-02-30'
    assert md.frontmatter('---\ndate: 2026-02-28\n---\n# Body',strict=True)['date']==date(2026,2,28)


@pytest.mark.parametrize('kind',['yaml-syntax','impossible-date','missing-kind','invalid-kind'])
def test_invalid_existing_topic_has_global_preflight_attention_zero_calls(brain,monkeypatch,kind):
    _,_,topic,result=existing_topic(brain)
    raw=topic.read_bytes()
    if kind=='yaml-syntax': raw=raw.replace(b'title: Example Project',b'title: Example: Phase 2')
    elif kind=='impossible-date': raw=raw.replace(b"    date: '2026-10-01'",b'    date: 2026-02-30')
    elif kind=='missing-kind': raw=raw.replace(b'wiki_kind: Projects\n',b'')
    else: raw=raw.replace(b'wiki_kind: Projects',b'wiki_kind: NotATopic')
    topic.write_bytes(raw); before=note_snapshot(); rows=[row for row in w.inventory() if row['path']==result['pages'][0]['claims'][0]['source']]
    calls=fake_model(monkeypatch,explicit({'sources':rows},result)); report=w.run()
    assert report['status']=='attention' and report['integrity_page']==topic.relative_to(s.VAULT).as_posix()
    assert report['integrity_field']==('frontmatter' if kind in ('yaml-syntax','impossible-date') else 'wiki_kind')
    assert report['consecutive_failures']==0 and len(calls)==0 and note_snapshot()==before
    assert w.run()['status']=='attention' and len(calls)==0
    assert any(x.get('field')==report['integrity_field'] for x in w.lint()['issues'])


def test_pending_empty_catalog_cannot_hide_new_invalid_topic(brain,monkeypatch):
    _,packet,result=fixture(brain)
    topic=s.VAULT/w.BASE/'Concepts/broken.md'; topic.parent.mkdir(parents=True)
    topic.write_bytes(b'---\ntitle: [broken\n---\n# Preserved invalid topic')
    before=note_snapshot(); calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='attention' and report['integrity_page']==topic.relative_to(s.VAULT).as_posix() and len(calls)==0
    assert note_snapshot()==before


def test_actual_partial_publication_repairs_without_new_model_despite_unrelated_invalid_topic(brain,monkeypatch):
    _,packet,result=fixture(brain); calls=fake_model(monkeypatch,explicit(packet,result)); original_write=s._write
    def crash(path,*args,**kwargs):
        receipt=original_write(path,*args,**kwargs)
        if path.as_posix().endswith('/Projects/example.md'): raise OSError('Crash after actual planned topic replacement')
        return receipt
    monkeypatch.setattr(s,'_write',crash)
    with pytest.raises(OSError): w.run()
    broken=s.VAULT/w.BASE/'Concepts/broken.md'; broken.parent.mkdir(parents=True)
    broken.write_bytes(b'---\ntitle: [broken\n---\n# Owner invalid unrelated topic'); before=broken.read_bytes()
    monkeypatch.setattr(s,'_write',original_write)
    report=w.run(); assert report['status']=='complete' and len(calls)==1 and broken.read_bytes()==before
    assert any(x['page']==broken.relative_to(s.VAULT).as_posix() for x in report['lint']['issues'])


def test_cited_current_impossible_date_uses_actual_prior_git_origin(brain,tmp_path,monkeypatch):
    git=git_fixture(tmp_path,s.VAULT); source,packet,result=fixture(brain); w.apply(packet,result)
    git('add','--','.'); git('commit','-m','Exact prior source and origin')
    original=source.read_bytes(); source.write_bytes(original.replace(b'2026-10-01',b'2026-02-30'))
    another=source.with_name('Another.md'); another.write_bytes(original); packet=w.prepare()
    result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    result=explicit(packet,result)
    for disposition in result['dispositions']:
        if disposition['source']==source.relative_to(s.VAULT).as_posix():
            disposition.update(status='skipped',reason='Owner malformed date metadata retained; prior evidence was reviewed without rewriting.',quote='A prototype was demonstrated.')
    before=source.read_bytes(); calls=fake_model(monkeypatch,result); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues'] and report['lint']['historical_evidence_count']==1
    assert source.read_bytes()==before
