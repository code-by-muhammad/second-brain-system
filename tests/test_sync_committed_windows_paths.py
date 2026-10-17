"""Outgoing committed and incoming fetched note names use the same admission.

All unsafe Git trees below are constructed as harmless local objects; Windows
need never create an invalid filename. Actual POSIX originals additionally prove
that a clean owner commit cannot bypass public-sync path admission.
"""
import json,os,subprocess
from pathlib import Path
import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git,_raw
from test_sync_windows_path_portability import repository,snapshot,observe_transport,PREFIX,SUFFIX

BAD='01-Projects/aux.md'


def _input(repo,*args,data):
    return subprocess.run(['git','-c','core.fsmonitor=false','--literal-pathspecs','-C',str(repo),*args],
        input=data,capture_output=True,timeout=10,check=True).stdout.strip()


def incoming(vault,remote,name):
    # Commit objects may contain platform-incompatible entries without placing
    # them in the Windows index or relaxing core.protectNTFS.
    blob=_input(vault,'hash-object','-w','--stdin',data=b'# Incoming source\nHarmless remote evidence.\n')
    subtree=_input(vault,'mktree','-z',data=b'100644 blob '+blob+b'\t'+name.encode('utf-8')+b'\0')
    tree=_input(vault,'mktree','-z',data=b'040000 tree '+subtree+b'\t01-Projects\0')
    parent=_raw(remote,'rev-parse','refs/heads/main').strip()
    commit=_input(vault,'commit-tree',tree.decode('ascii'),'-p',parent.decode('ascii'),data=b'Synthetic remote object tree\n')
    _raw(vault,'push','origin',commit.decode('ascii')+':refs/heads/main')
    # Keep baseline local HEAD/index/worktree clean while the remote advances.
    _raw(vault,'checkout','--','01-Projects/Ordinary.md')
    return commit


@pytest.mark.skipif(os.name=='nt',reason='Actual clean incompatible owner commits require POSIX.')
def test_clean_owner_commit_cannot_publish_incompatible_original(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    p=vault/BAD;p.write_bytes(b'# Owner-committed source\nHarmless original stays unchanged.\n')
    _raw(vault,'add','--',BAD);_raw(vault,'commit','-m','explicit synthetic incompatible owner commit')
    _raw(vault,'checkout','--','01-Projects/Ordinary.md')  # Genuinely clean owner tree; read-only fetch may discover a peer repair.
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    assert g.sync()==PREFIX+json.dumps(BAD)+SUFFIX
    assert snapshot(vault,remote)==before
    assert not any(cmd[4] in {'add','commit','merge','push','reset'} for cmd in commands)


def test_fetched_incompatible_tree_pauses_before_checkout_on_every_os(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    incoming(vault,remote,'aux.md');before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    try:result=g.sync()
    except g.GitSyncError as exc:result='GitSyncError: '+str(exc)
    assert result==PREFIX+json.dumps(BAD)+SUFFIX
    assert snapshot(vault,remote)==before
    assert any(cmd[4]=='fetch' for cmd in commands)
    assert not any(cmd[4] in {'add','commit','merge','merge-tree','push','reset'} for cmd in commands)


@pytest.mark.skipif(os.name=='nt',reason='Actual explicit rename of incompatible originals requires POSIX.')
def test_explicit_rename_owner_commit_can_repair_a_poisoned_upstream(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    p=vault/BAD;p.write_bytes(b'# Original source\nHarmless source evidence is retained.\n');original=p.read_bytes()
    _raw(vault,'add','--',BAD);_raw(vault,'commit','-m','synthetic historical incompatible owner commit')
    _raw(vault,'push','origin','HEAD')
    renamed='01-Projects/Auxiliary.md';_raw(vault,'mv','--',BAD,renamed)
    _raw(vault,'commit','-m','owner explicitly repaired the filename')
    assert g.sync()=='synced'
    assert (vault/renamed).read_bytes()==original and not p.exists()
    assert _raw(remote,'show','refs/heads/main:'+renamed)==original
    assert _raw(vault,'rev-parse','HEAD')==_raw(remote,'rev-parse','refs/heads/main')


def test_ordinary_incoming_tree_uses_local_file_backed_transport_and_merges(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    commit=incoming(vault,remote,'Incoming.md');shipping=g._run_git;queries=[]
    def observed(command,**kwargs):
        if list(command)[4:5]==['ls-tree']:
            queries.append((list(command),dict(kwargs)))
            assert kwargs['stdout'] not in (None,subprocess.PIPE) and kwargs['stderr']==subprocess.DEVNULL
            assert kwargs['timeout']==90
            assert kwargs['env']['GIT_ALLOW_PROTOCOL']=='' and kwargs['env']['GIT_NO_LAZY_FETCH']=='1'
            assert kwargs['env']['GIT_NO_REPLACE_OBJECTS']=='1' and kwargs['env']['GIT_OPTIONAL_LOCKS']=='0'
        return shipping(command,**kwargs)
    monkeypatch.setattr(g,'_run_git',observed)
    assert g.sync()=='synced' and len(queries)==1
    assert _raw(vault,'rev-parse','HEAD').strip()==commit
    assert (vault/'01-Projects/Incoming.md').read_bytes()==b'# Incoming source\nHarmless remote evidence.\n'


def test_incoming_tree_output_bound_fails_closed_before_merge(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git)
    incoming(vault,remote,'Incoming.md');before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    monkeypatch.setattr(g,'TREE_PATH_BYTES_LIMIT',1,raising=False)
    with pytest.raises(g.GitSyncError,match='Incoming note paths could not be verified locally'):
        g.sync()
    assert snapshot(vault,remote)==before
    assert not any(cmd[4] in {'add','commit','merge','merge-tree','push','reset'} for cmd in commands)
