"""Native filesystem/Git casing contracts, using disposable local repositories."""
import subprocess
from pathlib import Path

import pytest

import brain_git as syncer
from test_sync import git, pair


def _source(repo, relative='01-Projects/Case Folder/Example.md'):
    path=repo/relative; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'# Synthetic owner source\r\nExact retained bytes.\r\n')
    git(repo,'add','--',relative); git(repo,'commit','-m','owner source')
    git(repo,'push','origin','HEAD')
    return path


def _rename(path, name):
    """Two filesystem moves ensure a real directory-entry change on NTFS."""
    temporary=path.with_name('r93-synthetic-intermediate')
    path.rename(temporary); result=path.with_name(name); temporary.rename(result)
    assert result.name in {entry.name for entry in result.parent.iterdir()}
    return result


def _trace(monkeypatch):
    native=subprocess.run; calls=[]
    def traced(command,**kwargs):
        # All repositories/remotes are local synthetic fixtures.
        if command[:2]==['git','--literal-pathspecs']:
            calls.append(tuple(command[4:]))
        return native(command,**kwargs)
    monkeypatch.setattr(syncer.subprocess,'run',traced)
    return calls


@pytest.mark.parametrize('kind',['file','extension','directory','top-directory','unicode-file'])
@pytest.mark.parametrize('edited',[False,True],ids=['unchanged-bytes','owner-edited'])
def test_native_case_only_rename_pauses_before_network_without_rewriting_source(brain,tmp_path,monkeypatch,kind,edited):
    a,_=pair(tmp_path)
    git(a,'config','core.ignorecase','true')
    original='01-Projects/Case Folder/'+('Ärham.md' if kind=='unicode-file' else 'Example.md')
    source=_source(a,original)
    if kind=='file': source=_rename(source,'example.md')
    elif kind=='extension': source=_rename(source,'Example.MD')
    elif kind=='unicode-file': source=_rename(source,'ärham.md')
    elif kind=='directory': source=_rename(source.parent,'case folder')/source.name
    else: source=_rename(a/'01-Projects','01-projects')/'Case Folder'/source.name
    if edited: source.write_bytes(source.read_bytes()+b'Owner clarified the source.\r\n')
    exact=source.read_bytes(); config=(a/'.git/config').read_bytes()
    index=git(a,'ls-files','--stage','-z'); head=git(a,'rev-parse','HEAD')
    remote=git(a,'rev-parse','@{upstream}')
    # A pre-existing wiki update must not be committed/pushed using stale casing.
    derived=a/'02-Areas/AI-Memory/Wiki/Index.md'; derived.parent.mkdir(parents=True)
    derived.write_text('# Synthetic wiki\n'+source.relative_to(a).as_posix()+'\n',encoding='utf-8')
    alias_exists=(a/original).is_file()
    calls=_trace(monkeypatch)
    outcome=syncer.sync(a)
    if alias_exists:
        assert outcome.startswith('paused: tracked Markdown path casing'),(outcome,calls)
        assert not any(command[0] in {'add','commit','fetch','merge','push','reset'} for command in calls),calls
        assert git(a,'ls-files','--stage','-z')==index
        assert git(a,'rev-parse','HEAD')==head
    else:
        # On case-sensitive filesystems this remains an ordinary source deletion.
        assert outcome.startswith('paused: other local changes or source deletions'),outcome
        assert not any(command[0] in {'fetch','merge','push'} for command in calls),calls
    assert git(a,'rev-parse','@{upstream}')==remote
    assert source.read_bytes()==exact and (a/'.git/config').read_bytes()==config
    assert derived.read_text(encoding='utf-8').endswith(source.relative_to(a).as_posix()+'\n')


@pytest.mark.parametrize('kind',['file','directory'])
def test_explicit_two_step_git_rename_and_owner_commit_resume_portable_sync(brain,tmp_path,kind):
    a,b=pair(tmp_path); git(a,'config','core.ignorecase','true')
    source=_source(a)
    original=source.read_bytes()
    if kind=='file': old='01-Projects/Case Folder/Example.md'; new='01-Projects/Case Folder/example.md'
    else: old='01-Projects/Case Folder'; new='01-Projects/case folder'
    temporary='01-Projects/r93-owner-git-intermediate'
    git(a,'mv','--',old,temporary); git(a,'mv','--',temporary,new)
    git(a,'commit','-m','owner explicitly reconciled path casing')
    relative=new if kind=='file' else new+'/Example.md'
    derived=a/'02-Areas/AI-Memory/Wiki/Index.md'; derived.parent.mkdir(parents=True)
    derived.write_text('# Synthetic wiki\n'+relative+'\n',encoding='utf-8')
    assert syncer.sync(a)=='synced' and syncer.sync(b)=='synced'
    assert (a/relative).read_bytes()==original and (b/relative).read_bytes()==original
    assert relative in git(b,'ls-tree','-r','--name-only','HEAD').splitlines()
    assert (b/'02-Areas/AI-Memory/Wiki/Index.md').read_text().endswith(relative+'\n')


@pytest.mark.parametrize('edited',[False,True],ids=['unchanged-bytes','owner-edited'])
def test_unchanged_path_spelling_still_syncs_normal_source_and_wiki(brain,tmp_path,edited):
    a,b=pair(tmp_path); source=_source(a); git(a,'config','core.ignorecase','true')
    if edited: source.write_bytes(source.read_bytes()+b'Ordinary owner edit.\r\n')
    exact=source.read_bytes()
    derived=a/'02-Areas/AI-Memory/Wiki/Index.md'; derived.parent.mkdir(parents=True)
    derived.write_text('# Synthetic wiki\n01-Projects/Case Folder/Example.md\n',encoding='utf-8')
    assert syncer.sync(a)=='synced' and syncer.sync(b)=='synced'
    assert source.read_bytes()==exact and (b/source.relative_to(a)).read_bytes()==exact


@pytest.mark.parametrize('staging',['ordinary','intent-to-add'])
def test_case_only_rename_preserves_existing_owner_staging(brain,tmp_path,monkeypatch,staging):
    a,_=pair(tmp_path); source=_source(a); git(a,'config','core.ignorecase','true')
    source=_rename(source,'example.md')
    owner=a/'01-Projects/Owner-Staged.md'; owner.write_bytes(b'Owner staging.\r\n')
    if staging=='intent-to-add': git(a,'add','--intent-to-add','--',owner.relative_to(a).as_posix())
    else: git(a,'add','--',owner.relative_to(a).as_posix())
    index=git(a,'ls-files','--stage','-z'); exact=source.read_bytes(); staged=owner.read_bytes()
    calls=_trace(monkeypatch)
    assert syncer.sync(a).startswith('paused: existing staged changes preserved')
    assert git(a,'ls-files','--stage','-z')==index
    assert source.read_bytes()==exact and owner.read_bytes()==staged
    assert not any(command[0] in {'add','commit','fetch','merge','push','reset'} for command in calls)


def test_ordinary_different_name_rename_keeps_existing_deletion_review(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a); git(a,'config','core.ignorecase','true')
    source=_rename(source,'Different-Name.md'); exact=source.read_bytes()
    remote=git(a,'rev-parse','@{upstream}'); calls=_trace(monkeypatch)
    assert syncer.sync(a).startswith('paused: other local changes or source deletions')
    assert source.read_bytes()==exact and git(a,'rev-parse','@{upstream}')==remote
    assert not any(command[0] in {'fetch','merge','push'} for command in calls)


def test_unchanged_tracked_source_content_is_not_read_for_casing(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a)
    native=Path.read_text
    def refused(path,*args,**kwargs):
        if path==source: pytest.fail('Casing inspection must not read unchanged source content.')
        return native(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',refused)
    assert syncer.sync(a)=='synced'


def test_case_guard_does_not_bypass_new_note_credential_validation(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); _source(a)
    secret=a/'01-Projects/Blocked.md'; raw=b'password = syntheticabcdefghijk123456789\r\n'; secret.write_bytes(raw)
    head=git(a,'rev-parse','HEAD'); calls=_trace(monkeypatch)
    with pytest.raises(syncer.GitSyncError,match='possible credential'):
        syncer.sync(a)
    assert secret.read_bytes()==raw and git(a,'rev-parse','HEAD')==head
    assert not git(a,'diff','--cached','--name-only')
    assert not any(command[0] in {'add','commit','fetch','merge','push'} for command in calls)


def test_case_guard_does_not_bypass_linked_ancestor_validation(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a)
    source.write_bytes(source.read_bytes()+b'Owner edit.\r\n'); exact=source.read_bytes()
    native=syncer.os.path.isjunction
    monkeypatch.setattr(syncer.os.path,'isjunction',lambda path:Path(path)==source.parent or native(path))
    native_scan=syncer.os.scandir
    def no_link_traversal(path):
        if Path(path)==source.parent: pytest.fail('Casing inspection must not traverse a linked ancestor.')
        return native_scan(path)
    monkeypatch.setattr(syncer.os,'scandir',no_link_traversal)
    head=git(a,'rev-parse','HEAD'); calls=_trace(monkeypatch)
    with pytest.raises(syncer.GitSyncError,match='Linked note'):
        syncer.sync(a)
    assert source.read_bytes()==exact and git(a,'rev-parse','HEAD')==head
    assert not git(a,'diff','--cached','--name-only')
    assert not any(command[0] in {'add','commit','fetch','merge','push'} for command in calls)


def test_casing_pause_screens_unsafe_raw_locators(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); marker='sk-'+'SyntheticAlphaBetaGammaDelta'
    source=_source(a,'01-Projects/'+marker+'-Example.md')
    source=_rename(source,marker+'-example.md'); exact=source.read_bytes()
    calls=_trace(monkeypatch); outcome=syncer.sync(a)
    if (source.parent/(marker+'-Example.md')).is_file():
        assert outcome.startswith('paused: tracked Markdown path casing')
        assert marker not in outcome and '[withheld locator; path_sha256=' in outcome
    else: assert outcome.startswith('paused: other local changes or source deletions')
    assert source.read_bytes()==exact
    assert not any(command[0] in {'fetch','merge','push'} for command in calls)


def test_unreadable_tracked_parent_pauses_without_leaking_os_error(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a); native=syncer.os.scandir
    def failed(path):
        if Path(path)==source.parent: raise OSError('Synthetic private path detail must stay private.')
        return native(path)
    monkeypatch.setattr(syncer.os,'scandir',failed)
    head=git(a,'rev-parse','HEAD'); index=git(a,'ls-files','--stage','-z'); calls=_trace(monkeypatch)
    with pytest.raises(syncer.GitSyncError,match='casing could not be inspected') as caught:
        syncer.sync(a)
    assert 'private path detail' not in str(caught.value)
    assert git(a,'rev-parse','HEAD')==head and git(a,'ls-files','--stage','-z')==index
    assert not any(command[0] in {'add','commit','fetch','merge','push'} for command in calls)


def test_tracked_parent_listings_are_shared_and_do_not_scan_unrelated_trees(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a)
    for number in range(24):
        (source.parent/('Sibling-'+str(number)+'.md')).write_text('Synthetic sibling.\n',encoding='utf-8')
    unrelated=a/'Ignored-Tree'; (unrelated/'Deep/Deeper').mkdir(parents=True)
    (unrelated/'Deep/Deeper/Unrelated.md').write_text('Unrelated ignored file.\n',encoding='utf-8')
    (a/'.gitignore').write_text('Ignored-Tree/\n',encoding='utf-8')
    git(a,'add','.'); git(a,'commit','-m','owner added synthetic siblings and ignore rule')
    native=syncer.os.scandir; scanned=[]
    def traced(path):
        path=Path(path)
        assert not path.is_relative_to(unrelated),'Casing check must not recursively scan unrelated trees.'
        scanned.append(path)
        return native(path)
    monkeypatch.setattr(syncer.os,'scandir',traced)
    assert syncer.sync(a)=='synced'
    assert scanned.count(source.parent)==2
    assert all(scanned.count(path)<=2 for path in set(scanned))


def test_case_change_during_fetch_is_rechecked_before_push(brain,tmp_path,monkeypatch):
    a,_=pair(tmp_path); source=_source(a); original=source.read_bytes()
    git(a,'config','core.ignorecase','true'); native=subprocess.run; renamed=[]; calls=[]
    def traced(command,**kwargs):
        if command[:2]==['git','--literal-pathspecs']:
            action=command[4]; calls.append(action)
            if action=='fetch' and not renamed:
                renamed.append(_rename(source,'example.md'))
        return native(command,**kwargs)
    monkeypatch.setattr(syncer.subprocess,'run',traced)
    head=git(a,'rev-parse','HEAD'); outcome=syncer.sync(a)
    assert renamed and renamed[0].read_bytes()==original
    if source.is_file(): assert outcome.startswith('paused: tracked Markdown path casing')
    else: assert outcome.startswith('paused: local changes arrived during fetch')
    assert 'push' not in calls and git(a,'rev-parse','HEAD')==head
