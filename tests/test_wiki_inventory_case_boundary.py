"""Derived memory never becomes original evidence after a real case rename."""
import json
import sys
from pathlib import Path

import pytest

import brain_store as s
import brain_wiki as w
from test_wiki import fixture


def snapshot():
    return {p.relative_to(s.VAULT).as_posix():s.digest(p.read_bytes())
            for p in s.VAULT.rglob('*') if p.is_file()}


@pytest.mark.parametrize('spelling',['AI-Memory','ai-memory','AI-memory','Ai-MeMoRy'])
@pytest.mark.parametrize('include_excluded',[False,True])
def test_real_derived_directory_spelling_cannot_be_source(brain,spelling,include_excluded):
    vault,_=brain
    source=vault/'02-Areas/Original.md';source.parent.mkdir(parents=True)
    source.write_text('# Original source\n\nA synthetic owner decision is recorded here.\n',encoding='utf-8')
    derived=source.parent/spelling;derived.mkdir()
    (derived/'Profile.md').write_text('# Derived profile\n\nOnly explicit owner evidence can support this summary.\n',encoding='utf-8')
    (derived/'Wiki').mkdir()
    (derived/'Wiki/Index.md').write_text('# Derived index\n\nThis navigation is compiler output.\n',encoding='utf-8')
    before=snapshot()
    rows=w.inventory(include_excluded=include_excluded)
    assert [row['path'] for row in rows]==['02-Areas/Original.md']
    assert snapshot()==before


@pytest.mark.parametrize('spelling',['AI-Memory-Notes','My-AI-Memory'])
def test_similarly_named_original_area_remains_eligible(brain,spelling):
    vault,_=brain
    source=vault/'02-Areas'/spelling/'Original.md';source.parent.mkdir(parents=True)
    source.write_text('# Original source\n\nThis owner-authored area has a distinct directory name.\n',encoding='utf-8')
    before=snapshot()
    assert [row['path'] for row in w.inventory()]==[source.relative_to(vault).as_posix()]
    assert snapshot()==before


@pytest.mark.skipif(sys.platform!='win32',reason='Actual Windows case-insensitive directory rename required.')
@pytest.mark.parametrize('spelling',['ai-memory','AI-memory'])
def test_actual_case_rename_does_not_start_two_self_ingestion_rounds(brain,monkeypatch,spelling):
    source,packet,result=fixture(brain);w.apply(packet,result)
    root=s.VAULT/'02-Areas/AI-Memory'
    temporary=root.with_name('temporary-case-rename')
    root.rename(temporary);temporary.rename(root.with_name(spelling))
    canonical=s.VAULT/'02-Areas/AI-Memory'
    assert canonical.is_dir() and (canonical/'Wiki/Index.md').is_file()
    before=snapshot();raw=source.read_bytes();calls=[]
    monkeypatch.setattr(w.shutil,'which',lambda name:'synthetic-codex')
    class Process:
        returncode=0
        def __init__(self,command,**options):self.output=Path(command[command.index('-o')+1])
        def communicate(self,model_input,timeout):
            selected=json.loads(model_input.split('INPUT_JSON:\n',1)[1]);calls.append(selected)
            result={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'skipped',
                'reason':'This synthetic derived item has no new reusable original evidence.','quote':row['text'][:100]}
                for row in selected['sources']]}
            self.output.write_text(json.dumps(result),encoding='utf-8')
    monkeypatch.setattr(w.subprocess,'Popen',Process)
    reports=[w.run(limit=4),w.run(limit=4)]
    assert not calls and [row['status'] for row in reports]==['idle','idle']
    assert w.status()['pending_source_parts']==0
    assert snapshot()==before and source.read_bytes()==raw
