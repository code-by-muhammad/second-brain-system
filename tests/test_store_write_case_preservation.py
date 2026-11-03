"""Existing note spelling, cooperative CAS and actual filesystem alias identity."""
from contextlib import contextmanager
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unicodedata

import pytest
import brain_store as s
import brain_maintenance as maintenance
from brain_markdown import frontmatter


TARGET='02-Areas/AI-Memory/Overview.md'
ALIAS='02-Areas/AI-Memory/overview.md'
SOURCE='01-Projects/Source.md'


@pytest.fixture
def note(brain):
    vault,state=brain
    source=vault/SOURCE;source.parent.mkdir(parents=True);source.write_bytes(b'# Retain synthetic source bytes.\n')
    target=vault/TARGET;target.parent.mkdir(parents=True);target.write_bytes(b'# Existing synthetic derived page.\n')
    return vault,state,source,target


def update(path,expected):
    return s.update_current(path,'Synthetic summary','Source-backed synthetic revision.',[SOURCE],expected)


def test_existing_alias_keeps_disk_receipt_and_audit_spelling_or_refuses_missing_alias(note):
    vault,state,source,target=note;before=target.read_bytes();source_before=source.read_bytes();alias=vault/ALIAS
    if not alias.exists():
        with pytest.raises(ValueError,match='Concurrent change'):update(ALIAS,s.digest(before))
        assert target.read_bytes()==before and not alias.exists()
    else:
        assert alias.samefile(target)
        result=update(ALIAS,s.digest(before))
        assert result['path']==TARGET and target.name in {p.name for p in target.parent.iterdir()}
        audit=json.loads((state/'audit.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        assert audit['path']==TARGET and audit['before']==s.digest(before)
        assert (state/'write-backups'/s.digest(before)).read_bytes()==before
    assert source.read_bytes()==source_before


def test_unicode_filesystem_alias_preserves_actual_name_without_guessing(note):
    vault,_,source,target=note;source_before=source.read_bytes()
    created=target.with_name(unicodedata.normalize('NFD','Caf\u00e9.md'))
    created.write_bytes(b'# Synthetic normalization spelling.\n');before=created.read_bytes()
    actual=[p for p in created.parent.iterdir() if p.samefile(created)]
    assert len(actual)==1
    requested=created.with_name(unicodedata.normalize('NFC','Caf\u00e9.md'))
    if requested.exists():
        assert requested.samefile(created)
        result=update(requested.relative_to(vault).as_posix(),s.digest(before))
        assert result['path']==actual[0].relative_to(vault).as_posix()
        assert actual[0].name in {p.name for p in created.parent.iterdir()}
    else:
        with pytest.raises(ValueError,match='Concurrent change'):update(requested.relative_to(vault).as_posix(),s.digest(before))
        assert created.read_bytes()==before and not requested.exists()
    assert source.read_bytes()==source_before


def test_daily_link_preserves_existing_extension_spelling_and_links_once(note):
    vault,state,source,_=note;source_before=source.read_bytes()
    template=vault/'Templates/daily-note.md';template.parent.mkdir();template.write_bytes(b'# Synthetic template\n\n## Work Log\n')
    template_before=template.read_bytes()
    receipt=s.capture('case-daily-regression','Synthetic milestone','Synthetic outcome.','tests','disposable fixture')
    capture=vault/receipt['path'];capture_before=capture.read_bytes();raw=capture_before.decode('utf-8');meta=frontmatter(raw)
    when=datetime.fromisoformat(meta['captured_at'])
    requested=vault/('05-Daily/'+when.strftime('%Y-%m-%d-%A')+'.md');actual=requested.with_suffix('.MD')
    actual.parent.mkdir();actual.write_bytes(b'# Existing synthetic daily\n\n## Work Log\n')
    before=actual.read_bytes();alias=requested.exists() and requested.samefile(actual)
    assert maintenance._daily_link(capture,raw,meta,when) is True
    if alias:
        assert actual.name in {p.name for p in actual.parent.iterdir()}
        entries=[json.loads(line) for line in (state/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        assert entries[-1]['path']==actual.relative_to(vault).as_posix()
    else:assert actual.read_bytes()==before
    assert maintenance._daily_link(capture,raw,meta,when) is False
    assert source.read_bytes()==source_before and capture.read_bytes()==capture_before and template.read_bytes()==template_before


@pytest.mark.parametrize('sibling',['regular','symlink'])
def test_exact_existing_name_avoids_unrelated_casefold_sibling_metadata(note,monkeypatch,sibling):
    _,_,_,target=note;scan=s.os.scandir;link=Path.is_symlink;same=Path.samefile;inspected=[];alias='overview.MD'
    @contextmanager
    def ordered(path):
        if Path(path)==target.parent:yield iter([SimpleNamespace(name=alias),SimpleNamespace(name=target.name)])
        else:
            with scan(path) as entries:yield entries
    def is_link(path):
        if path.parent==target.parent and path.name==alias:
            inspected.append('alias-link');return sibling=='symlink'
        return link(path)
    def samefile(path,other):
        if Path(other).parent==target.parent and Path(other).name==alias:
            inspected.append('alias-stat');raise PermissionError('Synthetic unrelated sibling metadata denied')
        return same(path,other)
    monkeypatch.setattr(s.os,'scandir',ordered);monkeypatch.setattr(Path,'is_symlink',is_link);monkeypatch.setattr(Path,'samefile',samefile)
    assert s._existing_write_path(target).name==target.name and inspected==[]


def test_owner_case_rename_during_staging_is_refused_and_preserved(note,monkeypatch):
    _,state,_,target=note;before=target.read_bytes();create=s.tempfile.mkstemp;flush=s.os.fsync;staged=set();renamed=[]
    def staging(*args,**kwargs):
        fd,path=create(*args,**kwargs)
        if kwargs.get('prefix')=='.brain-':staged.add(fd)
        return fd,path
    def fsync(fd):
        flush(fd)
        if fd in staged and not renamed:
            temporary=target.with_name('owner-case-move.tmp');actual=target.with_name('overview.md')
            os.rename(target,temporary);os.rename(temporary,actual);renamed.append(actual)
    monkeypatch.setattr(s.tempfile,'mkstemp',staging);monkeypatch.setattr(s.os,'fsync',fsync)
    with pytest.raises(ValueError,match='(?i)concurrent.*change|spelling'):update(TARGET,s.digest(before))
    assert renamed and renamed[0].read_bytes()==before
    assert {p.name for p in target.parent.iterdir()}=={'overview.md'}
    assert not (state/'audit.jsonl').exists()
    assert (state/'write-backups'/s.digest(before)).read_bytes()==before
