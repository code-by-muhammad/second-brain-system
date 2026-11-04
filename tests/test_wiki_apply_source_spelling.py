"""Actual filesystem rename between prepare/planning/apply, no model calls."""
import pytest
import brain_wiki as w
import brain_store as s
import brain_settings as cfg
from test_wiki import fixture

def recase(source):
    original=source.read_bytes();target=source.with_name('example.md');temporary=source.with_name('RecaseTemporary.md')
    source.rename(temporary);temporary.rename(target)
    assert 'example.md' in {p.name for p in target.parent.iterdir()}
    assert 'Example.md' not in {p.name for p in target.parent.iterdir()}
    assert target.read_bytes()==original
    return target,original

@pytest.mark.parametrize('when',['before-apply','during-planning'])
def test_case_only_source_rename_is_refused_before_any_vault_publication(brain,monkeypatch,when):
    source,packet,result=fixture(brain);observed=[]
    if when=='before-apply':observed.append(recase(source))
    else:
        original=w._publication_plan
        def plan(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(recase(source));return result
        monkeypatch.setattr(w,'_publication_plan',plan)
    with pytest.raises(w.WikiTransientError,match='Source changed'):w.apply(packet,result)
    assert observed and observed[0][0].read_bytes()==observed[0][1]
    assert not (s.VAULT/w.BASE/'Projects').exists()
    assert not (s.VAULT/w.BASE/'Sources').exists()
    assert not (s.VAULT/w.BASE/'Log.md').exists()
    with w.db() as db:assert not db.execute("SELECT 1 FROM batches WHERE status='complete'").fetchone()

def test_unchanged_source_still_publishes_without_source_mutation(brain):
    source,packet,result=fixture(brain);before=source.read_bytes()
    receipt=w.apply(packet,result)
    assert receipt and source.read_bytes()==before

def test_existing_source_root_alias_remains_accepted(brain):
    source,packet,result=fixture(brain);before=source.read_bytes();original=source.parent
    temporary=original.with_name('RootTemporary');target=original.with_name('01-projects')
    original.rename(temporary);temporary.rename(target)
    if not original.exists():pytest.skip('Filesystem has no source-root case alias.')
    assert original.samefile(target)
    assert '01-projects' in {p.name for p in s.VAULT.iterdir()}
    assert w.apply(packet,result) and (target/source.name).read_bytes()==before

def test_spelling_scan_error_is_transient_and_never_publishes(brain,monkeypatch):
    source,packet,result=fixture(brain);before=source.read_bytes()
    def refuse(*args,**kwargs):raise ValueError('Synthetic bounded directory scan failure')
    monkeypatch.setattr(s,'_existing_write_path',refuse)
    with pytest.raises(OSError,match='Source spelling could not be verified'):w.apply(packet,result)
    assert source.read_bytes()==before and not (s.VAULT/w.BASE/'Projects').exists()
    assert not (s.VAULT/w.BASE/'Log.md').exists()
    with w.db() as db:assert not db.execute("SELECT 1 FROM batches WHERE status='complete'").fetchone()


@pytest.mark.parametrize('when',['before-apply','during-planning'])
def test_case_only_folder_rename_below_source_root_never_publishes(brain,monkeypatch,when):
    source,_,result=fixture(brain)
    folder=source.parent/'Alpha';folder.mkdir();source.rename(folder/source.name);source=folder/source.name
    packet=w.prepare(2);result['pages'][0]['claims'][0]['source']=source.relative_to(s.VAULT).as_posix()
    original=source.read_bytes();renamed=[]
    def recase_folder():
        temporary=folder.with_name('FolderTemporary');target=folder.with_name('alpha')
        folder.rename(temporary);temporary.rename(target)
        assert 'alpha' in {p.name for p in target.parent.iterdir()}
        assert 'Alpha' not in {p.name for p in target.parent.iterdir()}
        renamed.append(target/source.name)
    if when=='before-apply':recase_folder()
    else:
        planning=w._publication_plan
        def plan(*args,**kwargs):
            value=planning(*args,**kwargs);recase_folder();return value
        monkeypatch.setattr(w,'_publication_plan',plan)
    with pytest.raises(w.WikiTransientError,match='Source changed'):w.apply(packet,result)
    assert renamed[0].read_bytes()==original
    assert not (s.VAULT/w.BASE/'Projects').exists()
    assert not (s.VAULT/w.BASE/'Sources').exists()
    assert not (s.VAULT/w.BASE/'Log.md').exists()
    with w.db() as db:assert not db.execute("SELECT 1 FROM batches WHERE status='complete'").fetchone()
