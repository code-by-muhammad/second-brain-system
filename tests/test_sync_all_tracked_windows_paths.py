"""Every tracked checkout path is admitted; automatic staging stays Markdown-only."""
import json,os
import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git,_raw
from test_sync_windows_path_portability import repository,snapshot,observe_transport,SUFFIX
from test_sync_committed_windows_paths import _input

BAD=[('shared-non-markdown','03-Resources/aux.txt'),('outside-shared-roots','attachments/con.png')]


def remote_tree(vault,remote,relative):
    folder,name=relative.split('/')
    blob=_input(vault,'hash-object','-w','--stdin',data=b'Harmless remotely tracked bytes.\n')
    sub=_input(vault,'mktree','-z',data=b'100644 blob '+blob+b'\t'+name.encode()+b'\0')
    tree=_input(vault,'mktree','-z',data=b'040000 tree '+sub+b'\t'+folder.encode()+b'\0')
    parent=_raw(remote,'rev-parse','refs/heads/main').strip()
    commit=_input(vault,'commit-tree',tree.decode(),'-p',parent.decode(),data=b'Synthetic all-type remote tree\n')
    _raw(vault,'push','origin',commit.decode()+':refs/heads/main')
    _raw(vault,'checkout','--','01-Projects/Ordinary.md')
    return commit


@pytest.mark.skipif(os.name=='nt',reason='Actual incompatible non-Markdown owner commits require POSIX.')
@pytest.mark.parametrize('relative',[path for _,path in BAD],ids=[label for label,_ in BAD])
def test_clean_owner_commit_checks_every_tracked_path(brain,tmp_path,monkeypatch,isolated_git,relative):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    p=vault/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'Harmless non-Markdown owner original.\n')
    _raw(vault,'add','--',relative);_raw(vault,'commit','-m','synthetic incompatible non-Markdown commit')
    _raw(vault,'checkout','--','01-Projects/Ordinary.md')  # Genuinely clean owner tree; read-only fetch may discover a peer repair.
    before=snapshot(vault,remote);original=p.read_bytes();commands=observe_transport(monkeypatch)
    assert g.sync()=='paused: Repository path is not compatible with Windows: '+json.dumps(relative)+SUFFIX
    assert snapshot(vault,remote)==before and p.read_bytes()==original
    assert not any(cmd[4] in {'add','commit','merge','push','reset'} for cmd in commands)


@pytest.mark.parametrize('relative',[path for _,path in BAD],ids=[label for label,_ in BAD])
def test_incoming_checkout_guard_checks_every_root_and_file_type(brain,tmp_path,monkeypatch,isolated_git,relative):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    remote_tree(vault,remote,relative);before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    try:result=g.sync()
    except g.GitSyncError as exc:result='GitSyncError: '+str(exc)
    assert result=='paused: Repository path is not compatible with Windows: '+json.dumps(relative)+SUFFIX
    assert snapshot(vault,remote)==before
    assert not (vault/relative).exists()
    assert not any(cmd[4] in {'add','commit','merge','merge-tree','push','reset'} for cmd in commands)


def test_ordinary_owner_tracked_non_markdown_and_attachment_names_still_push(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    originals={'03-Resources/Reference.txt':b'Harmless owner text.\n','attachments/Image.png':b'\x89PNG\0Harmless synthetic binary.'}
    for relative,raw in originals.items():
        p=vault/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        _raw(vault,'add','--',relative)
    _raw(vault,'commit','-m','owner deliberately tracks ordinary non-Markdown files')
    assert g.sync()=='synced'
    for relative,raw in originals.items():assert (vault/relative).read_bytes()==raw==_raw(remote,'show','refs/heads/main:'+relative)


@pytest.mark.skipif(os.name=='nt',reason='Actual owner rename of incompatible attachments requires POSIX.')
def test_explicit_attachment_rename_commit_can_repair_old_remote(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    old='attachments/aux.txt';new='attachments/Auxiliary.txt';p=vault/old;p.parent.mkdir();original=b'Harmless attachment bytes.\n';p.write_bytes(original)
    _raw(vault,'add','--',old);_raw(vault,'commit','-m','synthetic old incompatible attachment');_raw(vault,'push','origin','HEAD')
    _raw(vault,'mv','--',old,new);_raw(vault,'commit','-m','owner explicitly repairs attachment name')
    assert g.sync()=='synced'
    assert (vault/new).read_bytes()==original==_raw(remote,'show','refs/heads/main:'+new) and not p.exists()


def test_automatic_staging_does_not_expand_to_untracked_attachments(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    relative='attachments/Ordinary.png';p=vault/relative;p.parent.mkdir();original=b'Harmless untracked attachment.';p.write_bytes(original)
    remote_before=_raw(remote,'rev-parse','refs/heads/main')
    assert g.sync()=='paused: other local changes or source deletions require review'
    assert p.read_bytes()==original and relative.encode() not in _raw(vault,'ls-files','-z').split(b'\0')
    assert _raw(remote,'rev-parse','refs/heads/main')==remote_before
