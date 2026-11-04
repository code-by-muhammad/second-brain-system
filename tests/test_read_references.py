"""Read app-generated references without broadening write or path authority."""
import json
import os
from pathlib import Path
import subprocess
import pytest
import brain_store as store


@pytest.fixture
def note(brain):
    vault, _ = brain
    path = vault / '01-Projects/Team Notes.md'
    path.parent.mkdir()
    path.write_text('Verified synthetic note.\nSecond line.\n', encoding='utf-8')
    return path


@pytest.mark.parametrize('reference', [
    '01-Projects/Team Notes', '01-Projects/Team Notes.md',
    '[[01-Projects/Team Notes]]', '[[01-Projects/Team Notes|Team]]',
    '[[01-Projects/Team Notes.md#Heading|Team]]',
    '[[01-Projects/Team Notes^block]]',
    '[[01-Projects/Team Notes|alias with ]] and | punctuation]]',
])
def test_exact_references_return_full_path_and_hash(note, reference):
    result = store.read(reference)
    assert result['path'] == '01-Projects/Team Notes.md'
    assert result['sha256'] == store.digest(note.read_bytes())
    assert 'Verified synthetic' in result['content']


def test_actual_generated_note_link_resolves(note):
    reference = store.note_link('01-Projects/Team Notes.md', 'Maps/AI-Context.md')
    assert store.read(reference)['path'] == '01-Projects/Team Notes.md'


def test_literal_selector_filename_is_preserved(brain):
    vault, _ = brain
    (vault / 'Literal#Heading.md').write_text('Exact literal.', encoding='utf-8')
    assert 'Exact literal.' in store.read('Literal#Heading.md')['content']
    assert 'Exact literal.' in store.read('Literal#Heading')['content']
    (vault / 'Literal.md').write_text('Heading target.', encoding='utf-8')
    assert 'Heading target.' in store.read('[[Literal#Heading]]')['content']
    assert 'Exact literal.' in store.read('Literal#Heading')['content']


def test_double_suffix_link_never_opens_other_note(brain):
    vault, _ = brain
    (vault / 'Literal.md').write_text('Wrong evidence.', encoding='utf-8')
    (vault / 'Literal.md.md').write_text('Correct evidence.', encoding='utf-8')
    link = store.note_link('Literal.md.md', 'Maps/AI-Context.md')
    assert 'Correct evidence.' in store.read(link)['content']


def test_unicode_backslash_reference(note, brain):
    path = note.parent / 'تجربہ.md'
    path.write_text('Unicode source.', encoding='utf-8')
    assert store.read('[[01-Projects\\تجربہ]]')['path'] == '01-Projects/تجربہ.md'


@pytest.mark.skipif(os.name != 'nt', reason='Windows case aliases are resolved by the filesystem.')
def test_returned_path_uses_real_casing(note):
    assert store.read('[[01-projects/team notes]]')['path'] == '01-Projects/Team Notes.md'


def test_credential_like_filename_read_contract(brain):
    vault, _ = brain
    name = 'sk-' + 'A'*24 + '.md'
    (vault / name).write_text('Synthetic filename only.', encoding='utf-8')
    assert store.read(name)['path'] == name


def test_no_recursive_basename_guess(note, brain):
    vault, _ = brain
    (vault / '02-Areas').mkdir()
    (vault / '02-Areas/Team Notes.md').write_text('Other content.', encoding='utf-8')
    with pytest.raises(FileNotFoundError):
        store.read('Team Notes')


@pytest.mark.parametrize('reference', [
    '[[../outside]]', '[[.obsidian/secret]]', '[[CON]]', '[[C:/private]]',
    '![[01-Projects/Team Notes]]', '[[#Heading]]', '[[]]',
    '[[01-Projects/[[Team Notes]]]]', '[[01-Projects/Team Notes]',
])
def test_unsafe_or_malformed_references_refused_before_any_witness_read(note,brain,monkeypatch,reference):
    vault,_=brain;outside=vault.parent/'outside.md';hidden=vault/'.obsidian/secret.md'
    hidden.parent.mkdir();outside.write_text('Outside synthetic witness.');hidden.write_text('Hidden synthetic witness.')
    original=Path.open
    def opened(path,*args,**kwargs):
        assert path not in (outside,hidden),'Policy refusal must precede opening either real witness.'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    with pytest.raises(ValueError):
        store.read(reference)

@pytest.mark.parametrize('reference',['01-Projects/Team Notes.md#Heading','not-markdown.txt'])
def test_missing_literal_filename_references_are_distinct_from_policy_refusal(note,reference):
    with pytest.raises(FileNotFoundError):store.read(reference)

@pytest.mark.parametrize('folder',['','Folder/'])
def test_exact_bracket_filename_wins_before_link_parsing(brain,folder):
    vault,_=brain;literal=vault/(folder+'[[Original]].md');literal.parent.mkdir(parents=True,exist_ok=True)
    literal.write_text('Exact bracket witness.',encoding='utf-8')
    decoy=vault/(folder+'Original.md');decoy.write_text('Ordinary wikilink witness.',encoding='utf-8')
    result=store.read(folder+'[[Original]].md')
    assert result['path']==folder+'[[Original]].md' and result['sha256']==store.digest(literal.read_bytes())
    assert 'Exact bracket witness.' in result['content']
    assert 'Ordinary wikilink witness.' in store.read('[['+folder+'Original]]')['content']
    literal.unlink()
    with pytest.raises(ValueError):store.read(folder+'[[Original]].md')

def synthetic_symlink(path,target,**kwargs):
    try:path.symlink_to(target,**kwargs)
    except OSError as error:
        if os.name=='nt' and getattr(error,'winerror',None)==1314:
            pytest.skip('Windows privilege1314 prevents this synthetic symlink.')
        raise

@pytest.mark.parametrize('winerror',[1314,5,None])
def test_symlink_fixture_skips_only_documented_windows_privilege(tmp_path,monkeypatch,winerror):
    error=OSError('Synthetic link error');error.winerror=winerror
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(Path,'symlink_to',fail)
    expected=pytest.skip.Exception if os.name=='nt' and winerror==1314 else OSError
    with pytest.raises(expected):synthetic_symlink(tmp_path/'link',tmp_path/'target')


def test_write_boundary_still_requires_exact_markdown(note):
    for value in ('01-Projects/Team Notes', '[[01-Projects/Team Notes]]'):
        with pytest.raises(ValueError):
            store.safe_path(value)


def test_missing_context_note_exception_contract(brain):
    with pytest.raises(FileNotFoundError):
        store.read('Maps/AI-Context.md')


def test_linked_target_refused(note, brain):
    vault, _ = brain
    link = vault / 'Linked.md'
    synthetic_symlink(link,note)
    with pytest.raises(ValueError, match='Linked/reparse'):
        store.read('[[Linked]]')


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_linked_parent_refused(note, brain, kind):
    vault, _ = brain
    link = vault / 'LinkedFolder'
    if kind == 'junction':
        if os.name != 'nt': pytest.skip('Junctions are Windows-only.')
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(note.parent)], capture_output=True)
        assert result.returncode == 0
    else:
        synthetic_symlink(link,note.parent,target_is_directory=True)
    with pytest.raises(ValueError, match='Linked/reparse'):
        store.read('[[LinkedFolder/Team Notes]]')
