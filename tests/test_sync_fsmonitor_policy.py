"""Real local Git commands keep service-owned jobs free of fsmonitor daemons.

All repositories/configuration below are disposable. Transport observations
delegate to the saved shipping transport; none fall back to subprocess.run on
Windows. No key agent, account, remote network, service or real vault is used.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import time

import pytest

import brain_git as g


@pytest.fixture
def isolated_git(tmp_path,monkeypatch):
    for key in list(os.environ):
        if key.upper().startswith('GIT_'):
            monkeypatch.delenv(key,raising=False)
    empty=tmp_path/'empty-global-config';empty.write_bytes(b'')
    hooks=tmp_path/'empty-hooks';hooks.mkdir()
    for key,value in {'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':str(empty),
                      'GIT_ALLOW_PROTOCOL':'file','GIT_TERMINAL_PROMPT':'0',
                      'GCM_INTERACTIVE':'0'}.items():
        monkeypatch.setenv(key,value)
    return hooks


def _raw(repo,*args):
    """Fixture/inspection commands cannot auto-start a background monitor."""
    return subprocess.run(['git','-c','core.fsmonitor=false','-C',str(repo),*args],
                          stdin=subprocess.DEVNULL,capture_output=True,timeout=10,
                          check=True).stdout


def _init(repo,hooks,*,bare=False):
    _raw(repo.parent,'init',*(['--bare'] if bare else ['--initial-branch=main']),str(repo))
    for key,value in [('gc.auto','0'),('maintenance.auto','false'),
                      ('core.fsmonitor','false'),('core.hooksPath',str(hooks)),
                      ('commit.gpgsign','false'),('core.autocrlf','false')]:
        _raw(repo,'config','--local',key,value)
    if not bare:
        _raw(repo,'config','--local','user.name','Synthetic R145')
        _raw(repo,'config','--local','user.email','r145@example.invalid')


@pytest.mark.parametrize('inherited_override',[False,True],ids=['local-true','inherited-true'])
def test_actual_git_effective_config_is_false_without_mutating_owner_inputs(tmp_path,monkeypatch,isolated_git,inherited_override):
    repo=tmp_path/'local config';_init(repo,isolated_git)
    _raw(repo,'config','--local','core.fsmonitor','true')
    config=repo/'.git/config';before=config.read_bytes()
    env=os.environ.copy()
    if inherited_override:
        env.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='core.fsmonitor',GIT_CONFIG_VALUE_0='true')
    original_env=dict(env)
    command=['git','--literal-pathspecs','-C',str(repo),'config','--get','core.fsmonitor']
    original_command=list(command)
    # Use both common argv container shapes and the real Windows/POSIX branch.
    supplied=tuple(command) if inherited_override else command
    result=g._run_git(supplied,env=env,capture_output=True,text=True,encoding='utf-8',timeout=2.5)
    assert result.returncode==0 and result.stdout.strip()=='false'
    assert command==original_command and list(supplied)==original_command and env==original_env
    assert config.read_bytes()==before
    assert _raw(repo,'config','--local','--get','core.fsmonitor').strip()==b'true'


def _observe_transport(monkeypatch):
    """Observe only after the policy, then call the actual shipping transport."""
    if os.name=='nt':
        import brain_windows_process as wp
        target=wp;attribute='run'
    else:
        target=g.subprocess;attribute='run'
    actual=getattr(target,attribute);calls=[]
    def observed(command,**kwargs):
        calls.append(list(command))
        return actual(command,**kwargs)
    monkeypatch.setattr(target,attribute,observed)
    return calls


@pytest.mark.parametrize('kind',['ordinary','archive'])
def test_actual_staged_blob_reads_apply_policy_and_preserve_original_bytes(brain,tmp_path,monkeypatch,isolated_git,kind):
    vault,state=brain;_init(vault,isolated_git)
    raw='Harmless staged source اردو.\r\n'.encode('utf-8')
    relative=('03-Resources/Ordinary.md' if kind=='ordinary' else
              '02-Areas/AI-Memory/Wiki/Log-Archives/'+hashlib.sha256(raw).hexdigest()+'.md')
    note=vault/relative;note.parent.mkdir(parents=True,exist_ok=True);note.write_bytes(raw)
    _raw(vault,'add','--',relative)
    entry=_raw(vault,'ls-files','--stage','--',relative).decode('utf-8').split('\t',1)[0]
    _raw(vault,'config','--local','core.fsmonitor','true')
    config=vault/'.git/config';before=config.read_bytes()
    calls=_observe_transport(monkeypatch)
    if kind=='ordinary':
        assert g._ordinary_blob(vault,relative,entry)==raw.decode('utf-8').replace('\r\n','\n')
    else:
        assert g._archive_blob(vault,relative,entry,g._archive_file(relative,note)) is None
    assert len(calls)==2
    assert all(command[:3]==['git','-c','core.fsmonitor=false'] for command in calls)
    assert [command[6:8] for command in calls]==[['cat-file','-s'],['cat-file','blob']]
    assert note.read_bytes()==raw and config.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows fsmonitor daemon/Job regression.')
def test_actual_public_sync_with_local_fsmonitor_true_succeeds_for_two_cycles(brain,tmp_path,monkeypatch,isolated_git):
    vault,state=brain;remote=tmp_path/'remote.git'
    _init(remote,isolated_git,bare=True);_init(vault,isolated_git)
    initial=vault/'03-Resources/Initial.md';initial.parent.mkdir();initial_raw=b'Initial harmless source.\n';initial.write_bytes(initial_raw)
    _raw(vault,'add','--','03-Resources/Initial.md');_raw(vault,'commit','-m','Synthetic baseline')
    _raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','--set-upstream','origin','main')
    _raw(vault,'config','--local','core.fsmonitor','true')
    config=vault/'.git/config';owner_config=config.read_bytes();remote_config=(remote/'config').read_bytes()
    import brain_windows_process as wp
    actual=g._run_git;frontdoor=[]
    def shorter(command,**kwargs):
        frontdoor.append(list(command))
        kwargs=dict(kwargs);kwargs['timeout']=min(2.5,kwargs['timeout'])
        return actual(command,**kwargs)
    monkeypatch.setattr(g,'_run_git',shorter)
    transport=_observe_transport(monkeypatch)
    def daemon_status():
        return wp.run(['git','-c','core.fsmonitor=false','-C',str(vault),'fsmonitor--daemon','status'],
                      capture_output=True,text=True,encoding='utf-8',timeout=2.5).returncode
    try:
        assert daemon_status()==1, 'New disposable repository already has a monitor.'
        note=vault/'03-Resources/Ordinary space é.md'
        for cycle in (1,2):
            raw=('Controlled ordinary source اردو, cycle '+str(cycle)+'.\n').encode('utf-8');note.write_bytes(raw)
            started=time.monotonic()
            assert g.sync(vault)=='synced'
            assert time.monotonic()-started<12
            assert daemon_status()==1, 'Service Git command started a background daemon.'
            assert note.read_bytes()==raw and initial.read_bytes()==initial_raw
            assert _raw(remote,'show','main:03-Resources/'+note.name)==raw
            assert config.read_bytes()==owner_config and (remote/'config').read_bytes()==remote_config
        policy_calls=[command for command in transport if '--literal-pathspecs' in command]
        assert policy_calls and all(command[:3]==['git','-c','core.fsmonitor=false'] for command in policy_calls)
        assert any(command[6:8]==['cat-file','blob'] for command in policy_calls)
        assert frontdoor and all(command[:2]==['git','--literal-pathspecs'] for command in frontdoor)
        assert _raw(vault,'config','--local','--get','core.fsmonitor').strip()==b'true'
    finally:
        # Exact synthetic workspace only, through the existing owned transport.
        # No global daemon stop, PID sweep or caller configuration mutation.
        wp.run(['git','-c','core.fsmonitor=false','-C',str(vault),'fsmonitor--daemon','stop'],
               capture_output=True,text=True,encoding='utf-8',timeout=2.5)
        assert daemon_status()==1, 'Synthetic daemon shutdown is not verified.'
