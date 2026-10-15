"""Editor artifacts, canonical topic paths and review/relink safety boundaries."""
import os
import subprocess
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
import brain_git
import brain_maintenance as maintenance
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


@pytest.mark.parametrize('relative',['Projects/.draft.md','Projects/.#example.md','Projects/._example.md','Projects/.cache/private.md'])
def test_hidden_editor_artifacts_are_never_read_or_allowed_to_block_idle(brain,monkeypatch,relative):
    _,packet,result=fixture(brain);w.apply(packet,result);hidden=s.VAULT/w.BASE/relative;hidden.parent.mkdir(parents=True,exist_ok=True);hidden.write_bytes(b'# Preserve private editor artifact.')
    real=type(hidden).read_bytes;before={path:real(path) for path in s.VAULT.rglob('*.md')}
    def guarded(path):
        if path==hidden:raise AssertionError('Compiler read an ignored editor artifact')
        return real(path)
    monkeypatch.setattr(type(hidden),'read_bytes',guarded);calls=fake_model(monkeypatch,result)
    report=w.run();assert report['status']=='idle' and len(calls)==0 and not w.lint()['issues']
    assert all(real(path)==raw for path,raw in before.items())


@pytest.mark.parametrize('name',['Example.md','Example Project.md','archive/example.md','example.MD'])
def test_noncanonical_topic_paths_stop_before_model_and_owner_repair_retries(brain,monkeypatch,name):
    _,_,topic,result=existing_topic(brain);renamed=topic.parent/name;renamed.parent.mkdir(parents=True,exist_ok=True);topic.rename(renamed)
    before=note_snapshot();rows=[row for row in w.inventory() if row['path']==result['pages'][0]['claims'][0]['source']];calls=fake_model(monkeypatch,explicit({'sources':rows},result))
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='path' and report['consecutive_failures']==0 and len(calls)==0 and note_snapshot()==before
    assert any(issue.get('field')=='path' and issue['page']==renamed.relative_to(s.VAULT).as_posix() for issue in w.lint()['issues'])
    renamed.rename(topic);w.reset_retries();packet=w.prepare();calls=fake_model(monkeypatch,explicit(packet,result));report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']


def test_case_renamed_category_is_not_a_duplicate_namespace(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain);folder=topic.parent;temporary=folder.with_name('temporary-kind');folder.rename(temporary);renamed=folder.with_name('projects');temporary.rename(renamed)
    before=note_snapshot();calls=fake_model(monkeypatch,result);report=w.run()
    assert report['status']=='attention' and report['integrity_field']=='path' and len(calls)==0 and note_snapshot()==before


def test_unsafe_wiki_file_is_controlled_without_reading_it_or_echoing_credential_path(brain,monkeypatch):
    _,packet,result=fixture(brain);w.apply(packet,result);target=s.VAULT/w.BASE/'Projects/unsafe.md';target.write_bytes(b'# Keep owner unsafe evidence.')
    original=s.safe_path
    def refused(relative):
        if relative==target.relative_to(s.VAULT).as_posix():raise ValueError('Synthetic unsafe path')
        return original(relative)
    monkeypatch.setattr(s,'safe_path',refused);real=type(target).read_bytes
    def guarded(path):
        if path==target:raise AssertionError('Unsafe path was read')
        return real(path)
    monkeypatch.setattr(type(target),'read_bytes',guarded);calls=fake_model(monkeypatch,result)
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='path' and len(calls)==0
    assert any(issue.get('field')=='path' for issue in w.lint()['issues'])


def test_actual_linked_wiki_directory_is_not_traversed(brain,tmp_path,monkeypatch):
    _,packet,result=fixture(brain);w.apply(packet,result);outside=tmp_path/'outside';outside.mkdir();secret=outside/'external.md';secret.write_bytes(b'# Synthetic external content must not be read.')
    linked=s.VAULT/w.BASE/'Projects/linked'
    if os.name=='nt':
        created=subprocess.run(['cmd','/d','/c','mklink','/J',str(linked),str(outside)],capture_output=True,**cfg.child_options())
        assert created.returncode==0
    else:linked.symlink_to(outside,target_is_directory=True)
    real=type(secret).read_bytes
    def guarded(path):
        if path==secret or linked in path.parents:raise AssertionError('Compiler traversed a linked directory')
        return real(path)
    monkeypatch.setattr(type(secret),'read_bytes',guarded);calls=fake_model(monkeypatch,result)
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='path' and len(calls)==0
    assert any(issue.get('field')=='path' for issue in w.lint()['issues'])


@pytest.mark.parametrize('bad',['reviewed: 2026-02-30','reviewed: [broken','reviewed: !!bool maybe'])
def test_invalid_derived_metadata_review_keeps_capture_link_and_git_work(brain,monkeypatch,bad):
    (s.VAULT/'Templates').mkdir();(s.VAULT/'Templates/daily-note.md').write_bytes(b'# Synthetic daily\n\n## Work Log\n')
    folder=s.VAULT/'02-Areas/AI-Memory';folder.mkdir(parents=True);path=folder/'Optional.md';raw=('---\nsources: []\n'+bad+'\n---\n# Preserve owner review metadata.').encode();path.write_bytes(raw)
    s.capture('review-contract','Synthetic milestone','Preserved evidence.','test','synthetic-local-proof',project='Synthetic')
    calls=[];monkeypatch.setattr(brain_git,'sync',lambda:calls.append(True) or 'synthetic synced');report=maintenance.maintenance(sync=True)
    assert calls==[True] and report['captures_linked']==1 and path.read_bytes()==raw
    assert any(item['path']==path.relative_to(s.VAULT).as_posix() and item['reasons']==['derived metadata could not be read; original preserved'] for item in report['review'])


@pytest.mark.parametrize('bad',['missing-kind','invalid-date','syntax'])
def test_relink_preflight_preserves_every_source_record_until_owner_repairs_topic(brain,bad):
    source,packet,result=fixture(brain);w.apply(packet,result);topic=s.VAULT/w.BASE/'Projects/example.md';raw=topic.read_bytes()
    if bad=='missing-kind':invalid=raw.replace(b'wiki_kind: Projects\n',b'')
    elif bad=='invalid-date':invalid=raw.replace(b"    date: '2026-10-01'",b'    date: 2026-02-30')
    else:invalid=raw.replace(b'title: Example Project',b'title: [broken')
    topic.write_bytes(invalid);before=note_snapshot()
    with pytest.raises(w.WikiIntegrityError):w.rebuild_source_links()
    assert note_snapshot()==before
    topic.write_bytes(raw);report=w.rebuild_source_links();assert report['source_records_updated']==0
    record=s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode())[:16]+'.md')
    assert w.meta(record.read_text(encoding='utf-8'))['wiki_pages']==[topic.relative_to(s.VAULT).as_posix()]



def test_credential_like_unsafe_wiki_filename_is_withheld_from_public_diagnostics(brain,monkeypatch):
    _,packet,result=fixture(brain);w.apply(packet,result);name='ghp_'+'syntheticmarker'*4+'.md';target=s.VAULT/w.BASE/'Projects'/name;raw=b'# Synthetic filename only; preserve owner bytes.';target.write_bytes(raw)
    calls=fake_model(monkeypatch,result);report=w.run();assert report['status']=='attention' and report['integrity_page']==w.BASE+'Index.md' and len(calls)==0
    import json
    assert name not in json.dumps(w.lint()) and name not in json.dumps(w.status()['worker']) and target.read_bytes()==raw


def test_topic_kind_must_match_its_canonical_category_without_silent_reclassification(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain);raw=topic.read_bytes().replace(b'wiki_kind: Projects',b'wiki_kind: People');topic.write_bytes(raw);calls=fake_model(monkeypatch,result)
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='wiki_kind' and len(calls)==0 and topic.read_bytes()==raw
    assert any(issue.get('field')=='wiki_kind' for issue in w.lint()['issues'])
