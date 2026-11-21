"""Synthetic client settings and bootstrap selection; no actual clients/services."""
import importlib.util
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tomllib
from types import SimpleNamespace
import pytest

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sealed_install',SOURCE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
helpers=installer.setup_helpers()


@pytest.fixture
def owner(tmp_path,monkeypatch):
    home=tmp_path/'home';(home/'.codex').mkdir(parents=True)
    root=tmp_path/'runtime';python=tmp_path/'new-python.exe'
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    return home,root,python


def plan(owner,**options):
    home,root,python=owner
    return helpers.plan(root,python,'reviewed protocol',installer._codex_toml_plan,home=home,**options)


def test_refresh_preserves_server_options_and_creates_no_integration(owner):
    home,root,python=owner
    path=home/'.codex/config.toml'
    path.write_text('[mcp_servers.second-brain]\ncommand="old"\nargs=['+json.dumps(str(root/'app/brain_client.py'))+']\nstartup_timeout_sec=91\n[mcp_servers.second-brain.env]\nOWNER="keep"\n')
    result=plan(owner,selected=['codex'],refresh=True)
    toml=next(row for row in result['files'] if row['path'].endswith('config.toml'))
    assert [row['path'] for row in result['files']]==[str(path)]
    assert any(row['path']==str(home/'.codex/AGENTS.md') and row['code']=='missing_file' for row in result['skipped'])
    data=tomllib.loads(toml['after'])['mcp_servers']['second-brain']
    spec=helpers.connection_spec(root,python)
    assert data['env']=={'OWNER':'keep'} and data['startup_timeout_sec']==91
    assert data['command']==spec['command'] and data['args']==spec['args']


def test_refresh_migrates_an_owned_connection_to_the_platform_spec(owner):
    home,root,python=owner
    path=home/'.claude.json'
    path.write_text(json.dumps({'theme':'keep','mcpServers':{'second-brain':{
        'command':str(python),'args':[str(root/'app/brain_client.py')],'env':{'OWNER':'keep'}}}}))
    result=plan(owner,selected=['claude-code'],refresh=True)
    after=json.loads(next(row['after'] for row in result['files'] if row['path'].endswith('.claude.json')))
    assert list(after['mcpServers'])==['second-brain'] and after['theme']=='keep'
    server=after['mcpServers']['second-brain']
    spec=helpers.connection_spec(root,python)
    assert server['command']==spec['command'] and server['args']==spec['args'] and server['env']=={'OWNER':'keep'}
    path.write_text(next(row['after'] for row in result['files'] if row['path'].endswith('.claude.json')))
    again=plan(owner,selected=['claude-code'],refresh=True)
    assert any(row['path'].endswith('.claude.json') for row in again['files'])
    assert not any('unknown runtime' in row['reason'] for row in again['skipped'])


def test_declined_malformed_client_never_blocks_selected_plan(owner):
    home,root,python=owner
    (home/'.claude.json').write_text('{bad')
    result=plan(owner,selected=['codex'])
    assert all(row['client']=='codex' for row in result['files'])


@pytest.mark.parametrize('client',['claude-code','codex'])
def test_fresh_selection_preserves_and_refuses_a_foreign_second_brain_entry(owner,client):
    home,root,python=owner
    if client=='claude-code':
        path=home/'.claude.json';path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'foreign','args':['C:/foreign/app/brain_client.py']}}}))
    else:
        path=home/'.codex/config.toml';path.write_text('[mcp_servers.second-brain]\ncommand="foreign"\nargs=["C:/foreign/app/brain_client.py"]\n')
    before=path.read_bytes()
    result=plan(owner,selected=[client])
    assert all(Path(row['path'])!=path for row in result['files'])
    assert any('another or unknown runtime' in row['reason'] for row in result['skipped'])
    assert path.read_bytes()==before


@pytest.mark.parametrize('choice',['missing','wrong','old-version'])
def test_refresh_only_requires_same_release_and_recorded_python_before_provision(owner,monkeypatch,choice):
    home,root,python=owner;root.mkdir();vault=root.parent/'vault';vault.mkdir()
    other=root.parent/'other-python.exe';other.write_bytes(b'not executed')
    device={'device_id':'fixture','version':'0.1.7' if choice=='old-version' else installer.VERSION,
            'python':sys.executable,'vault':str(vault),'role':'capture','release_root':str(SOURCE)}
    (root/'device.json').write_text(json.dumps(device))
    monkeypatch.setattr(helpers,'host_context',lambda:None)
    monkeypatch.setattr(helpers,'provision',lambda *args:pytest.fail('Refresh-only must not provision'))
    monkeypatch.setattr(installer,'setup_helpers',lambda:helpers)
    flags=[] if choice=='missing' else ['--reuse-python',str(other) if choice=='wrong' else sys.executable]
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--refresh-clients','--refresh-only','--client','codex',*flags])
    with pytest.raises(ValueError,match='same installed release|exact Python recorded'):installer.main()
    assert not (home/'.codex/config.toml').exists()


def test_refresh_preserves_unowned_parse_failure_and_duplicate_json(owner):
    home,root,_=owner;path=home/'.claude.json'
    path.write_text('{unrelated-bad')
    preserved=plan(owner,selected=['claude-code'],refresh=True)
    assert all(not row['path'].endswith('.claude.json') for row in preserved['files'])
    assert any(row['path']==str(path) and row['code']=='unparseable_unowned' for row in preserved['skipped'])
    path.write_text('{"root":'+json.dumps(str(root))+',"mcpServers":{},"mcpServers":{}}')
    before=path.read_bytes()
    preserved=plan(owner,selected=['claude-code'],refresh=True)
    assert all(row['path']!=str(path) for row in preserved['files'])
    assert any(row['path']==str(path) and row['code']=='duplicate_json' for row in preserved['skipped'])
    assert path.read_bytes()==before


@pytest.mark.parametrize('hooks',[[],{'Stop':{}},{'Stop':[{'hooks':{}}]},{'Stop':[{'hooks':[0]}]}])
def test_refresh_skips_malformed_hook_shapes_naming_this_runtime(owner,hooks):
    home,root,_=owner;path=home/'.claude/settings.json';path.parent.mkdir()
    path.write_text(json.dumps({'root':str(root),'hooks':hooks}))
    before=path.read_bytes()
    result=plan(owner,selected=['hooks'],refresh=True)
    assert result['files']==[]
    assert result['skipped'][0]['code']=='malformed'
    assert path.read_bytes()==before


@pytest.mark.parametrize('client',['claude-code','codex'])
def test_refresh_aborts_on_syntax_error_naming_this_runtime(owner,client):
    home,root,_=owner
    if client=='claude-code':
        path=home/'.claude.json'
        raw='{"root":'+json.dumps(str(root))+',"a":0,"a":1} trailing content'
        parse_error=json.JSONDecodeError
    else:
        path=home/'.codex/config.toml'
        raw='root = '+json.dumps(str(root))+'\n[unterminated\n'
        parse_error=tomllib.TOMLDecodeError
    path.write_text(raw)
    with pytest.raises(helpers.ClientPlanError,match='Unparseable client file names this runtime') as refused:
        plan(owner,selected=[client],refresh=True)
    assert isinstance(refused.value.__cause__,parse_error)


def test_client_planning_uses_types_instead_of_diagnostic_words(owner,monkeypatch):
    home,root,python=owner
    path=home/'tomlkit-config.json';path.write_text('{unrelated malformed')
    result=plan(owner,selected=['claude-desktop'],refresh=True,desktop=path)
    assert result['files']==[] and result['skipped'][0]['code']=='unparseable_unowned'

    def unavailable(*args):raise helpers.TomlSupportError('changed diagnostic text')
    monkeypatch.setattr(installer,'_codex_toml_plan',unavailable)
    with pytest.raises(helpers.TomlSupportError,match='changed diagnostic'):
        plan(owner,selected=['codex'])


def test_grok_only_no_hooks_and_empty_refresh_preserve_other_clients(owner):
    home,root,python=owner
    hook=home/'.claude/settings.json';hook.parent.mkdir()
    hook.write_text(json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command',
        'command':'"old interpreter" "'+str(root/'app/brain_hooks.py')+'"'}]}]}}))
    codex=home/'.codex/config.toml';codex.write_text('model = "unselected"')
    registration=helpers.grok_directory(home)/'mcp-registration.json';registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'old','args':[str(root/'app/brain_client.py')]}}}))
    before=(hook.read_bytes(),codex.read_bytes())
    selected=helpers.selected_clients(['grok'],refresh=True,no_hooks=True)
    value=plan(owner,selected=selected,refresh=True)
    assert [row['path'] for row in value['files']]==[str(registration)]
    assert not helpers.apply(value,installer.write_client,lambda *args:None,root)['deferred']
    assert (hook.read_bytes(),codex.read_bytes())==before
    empty=helpers.selected_clients([],refresh=True)
    assert plan(owner,selected=empty,refresh=True)['files']==[]


@pytest.mark.skipif(os.name!='posix',reason='uses POSIX symlinks and no-follow directory descriptors')
def test_authorized_in_home_client_leaf_and_parent_swap_are_confined(owner,monkeypatch,tmp_path):
    home,root,python=owner
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    directory=home/'real';directory.mkdir()
    target=directory/'claude.json';target.write_bytes(b'{}')
    logical=home/'.claude.json';logical.symlink_to(target)
    value=plan(owner,selected=['claude-code'])
    row=next(row for row in value['files'] if row['path']==str(target))
    value['files']=[row]
    helpers.authorize_plan(value,selected=['claude-code'])
    first=helpers.apply(value,installer.write_client,lambda *args:None,root)
    assert first['changed']==[str(target)] and logical.is_symlink()

    external=tmp_path/'external';external.mkdir()
    (external/target.name).write_bytes(target.read_bytes())
    directory.rename(home/'moved');directory.symlink_to(external,target_is_directory=True)
    with pytest.raises(ValueError,match='outside the selected'):
        helpers.authorize_plan(value,selected=['claude-code'])
    after=(external/target.name).read_bytes()
    with pytest.raises(OSError):
        installer.write_client(target,b'{}',expected_sha256=row['after_sha256'])
    assert (external/target.name).read_bytes()==after


@pytest.mark.skipif(os.name!='posix',reason='uses POSIX file permission bits')
@pytest.mark.parametrize('refresh',[False,True])
def test_user_owned_group_writable_client_paths_are_accepted(owner,monkeypatch,refresh):
    home,root,_=owner
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    codex=home/'.codex/config.toml'
    codex.parent.chmod(0o775)
    codex.write_text('[mcp_servers.second-brain]\nargs=['+json.dumps(str(root/'app/brain_client.py'))+']\n')
    claude=home/'.claude.json'
    claude.write_text(json.dumps({'mcpServers':{'second-brain':{'args':[str(root/'app/brain_client.py')]}}}))
    claude.chmod(0o664)
    selected=['claude-code','codex']
    value=plan(owner,selected=selected,refresh=refresh)
    helpers.authorize_plan(value,selected=selected)
    receipt=helpers.apply(value,installer.write_client,lambda *args:None,root)
    assert {str(claude),str(codex)}<=set(receipt['changed'])
    assert not receipt['deferred']
    assert stat.S_IMODE(claude.stat().st_mode)==0o664


@pytest.mark.skipif(os.name!='posix',reason='injects a POSIX directory swap during guarded rollback staging')
def test_rollback_staging_cannot_publish_inside_a_swapped_client_directory(owner,monkeypatch,tmp_path):
    home,root,_=owner
    folder=home/'selected';folder.mkdir();target=folder/'settings.json'
    target.write_bytes(b'candidate')
    external=tmp_path/'external';external.mkdir();(external/target.name).write_bytes(b'candidate')
    displaced=home/'displaced'
    sync=os.fsync
    swapped=False
    def fsync(descriptor):
        nonlocal swapped
        sync(descriptor)
        if not swapped and stat.S_ISREG(os.fstat(descriptor).st_mode):
            folder.rename(displaced)
            folder.symlink_to(external,target_is_directory=True)
            swapped=True
    monkeypatch.setattr(os,'fsync',fsync)
    with pytest.raises((OSError,helpers.ConcurrentWriteError)):
        installer.write_client(target,b'predecessor',expected_sha256=helpers.digest(b'candidate'),mode=0o640)
    assert swapped and list(displaced.iterdir())==[displaced/target.name]
    assert (displaced/target.name).read_bytes()==(external/target.name).read_bytes()==b'candidate'


@pytest.mark.parametrize('text',[helpers.BEGIN+'\n',helpers.BEGIN+helpers.END+helpers.BEGIN+helpers.END,helpers.END+helpers.BEGIN])
def test_bad_protocol_markers_refused(owner,text):
    home,root,python=owner;(home/'.codex/AGENTS.md').write_text(text)
    refused=plan(owner,selected=['codex'],refresh=True)
    assert not any(row['path'].endswith('AGENTS.md') for row in refused['files'])
    assert any('marker' in row['reason'] or 'protocol' in row['reason'] for row in refused['skipped'])


def test_refresh_cas_before_stop_and_deferred_concurrent_edit(owner):
    home,root,python=owner;path=home/'.claude.json'
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'old','args':[str(root/'app/brain_client.py')]}}}))
    value=plan(owner,selected=['claude-code'],refresh=True)
    helpers.current(value)
    original=path.read_bytes();path.write_text('{"new owner edit":true}')
    with pytest.raises(ValueError,match='old service is healthy'):helpers.current(value)
    writes=[]
    receipt=helpers.apply(value,lambda *args,**kwargs:writes.append(args),lambda *args:None,root)
    assert not any(str(item[0]).endswith('.claude.json') for item in writes)
    assert any(item['path'].endswith('.claude.json') for item in receipt['deferred']) and path.read_text()=='{"new owner edit":true}'
    path.write_bytes(original)
    def write(path,text,**kwargs):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(text.encode('utf-8'))
    receipt=helpers.apply(value,write,lambda *args:None,root)
    assert str(path) in receipt['changed'] and helpers.after_check(value)==[]
    path.write_text('{"later app edit":true}')
    assert helpers.after_check(value)==[str(path)]


def test_hooks_with_other_runtime_and_extra_tokens_are_preserved(owner):
    home,root,python=owner;(home/'.claude').mkdir()
    path=home/'.claude/settings.json'
    exact='"old python.exe" "'+str(root/'app/brain_hooks.py')+'"'
    commands=[exact,exact+' --owner-flag','"python" "'+str(root/'other/app/brain_hooks.py')+'"']
    path.write_text(json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':command} for command in commands]}]}}))
    value=plan(owner,selected=['hooks'],refresh=True)
    data=json.loads(value['files'][0]['after']);after=[row['command'] for row in data['hooks']['Stop'][0]['hooks']]
    assert str(python).replace('\\','/') in after[0]
    assert after[1:]==commands[1:] and 'SessionStart' not in data['hooks']


def test_busy_desktop_refuses_preflight_and_reopen_defers_write(owner,monkeypatch):
    home,root,python=owner;path=home/'desktop.json'
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'args':[str(root/'app/brain_client.py')]}}}))
    monkeypatch.setattr(helpers,'desktop_busy',lambda:True)
    with pytest.raises(ValueError,match='Desktop is open'):plan(owner,selected=['claude-desktop'],refresh=True,desktop=path)
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    value=plan(owner,selected=['claude-desktop'],refresh=True,desktop=path)
    monkeypatch.setattr(helpers,'desktop_busy',lambda:True)
    receipt=helpers.apply(value,lambda *args:pytest.fail('busy Desktop write'),lambda *args:None,root)
    assert receipt['deferred'][0]['reason']=='Desktop reopened'


def test_oversize_config_fails_instead_of_assuming_unowned(owner,monkeypatch):
    home,root,python=owner;    (home/'.claude.json').write_bytes(b'x'*100)
    monkeypatch.setattr(helpers,'MAX_CONFIG',99)
    refused=plan(owner,selected=['claude-code'],refresh=True)
    assert all(not row['path'].endswith('.claude.json') for row in refused['files'])
    assert any('bound' in row['reason'] for row in refused['skipped'])


def test_uv_uses_one_absolute_find_result_for_sync(tmp_path,monkeypatch):
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime'
    tools=tmp_path/'trusted-tools';tools.mkdir();uv=tools/'uv.exe';uv.touch();base=tools/'python.exe';base.touch()
    calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None)
    monkeypatch.setattr(helpers,'service_python',lambda value:None)
    for name in ('UV_PYTHON','VIRTUAL_ENV','PYTHONPATH'):monkeypatch.setenv(name,'synthetic-ambient')
    def run(command,**kwargs):
        calls.append(command)
        assert all(not key.startswith('UV_') or key=='UV_NO_CONFIG' for key in kwargs['env'])
        assert kwargs['env']['UV_NO_CONFIG']=='1' and not any(key.startswith(('VIRTUAL_ENV','PYTHON')) for key in kwargs['env'])
        return subprocess.CompletedProcess(command,0,str(base)+'\n')
    monkeypatch.setattr(helpers,'run',run)
    helpers.provision(repo,root,str(uv))
    # find, a probe of that base for SQLite extension loading, then sync on the same base.
    assert len(calls)==3 and calls[2][-2:]==['--python',str(base)] and not any('install' in command for command in calls)
    assert calls[0][1:]==['python','find','--system','--no-project','--no-python-downloads','3.12']
    assert calls[1][0]==str(base) and 'enable_load_extension' in calls[1][-1]


@pytest.mark.parametrize('location',['checkout','runtime'])
def test_provision_refuses_a_base_inside_device_or_checkout(tmp_path,monkeypatch,location):
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime';root.mkdir()
    tools=tmp_path/'tools';tools.mkdir();uv=tools/'uv.exe';uv.touch()
    base=(repo if location=='checkout' else root)/'python.exe';base.touch();calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None)
    def run(command,**options):calls.append(command);return subprocess.CompletedProcess(command,0,str(base))
    monkeypatch.setattr(helpers,'run',run)
    with pytest.raises(ValueError,match='base inside'):helpers.provision(repo,root,str(uv))
    assert len(calls)==1 and not (repo/'.venv').exists()


@pytest.mark.parametrize('configured',[False,True])
def test_provision_preserves_cached_venv_and_refuses_configured_runtime(tmp_path,monkeypatch,configured):
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime';root.mkdir()
    tools=tmp_path/'tools';tools.mkdir();uv=tools/'uv.exe';uv.touch();base=tools/'python.exe';base.touch()
    environment=repo/'.venv';environment.mkdir();(environment/'preserve-marker').write_bytes(b'previous venv')
    if configured:(root/'device.json').write_text(json.dumps({'python':str(environment/'Scripts/python.exe')}))
    calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None);monkeypatch.setattr(helpers,'service_python',lambda value:None)
    def run(command,**options):calls.append(command);return subprocess.CompletedProcess(command,0,str(base))
    monkeypatch.setattr(helpers,'run',run)
    if configured:
        with pytest.raises(ValueError,match='configured service'):helpers.provision(repo,root,str(uv))
        assert len(calls)==2 and (environment/'preserve-marker').read_bytes()==b'previous venv'  # find and the base probe
        assert not list(repo.glob('.venv-quarantined-*'))
    else:
        helpers.provision(repo,root,str(uv))
        saved=list(repo.glob('.venv-quarantined-*'))
        assert len(calls)==3 and len(saved)==1 and (saved[0]/'preserve-marker').read_bytes()==b'previous venv'
        assert not environment.exists()


@pytest.mark.parametrize('caller',['retained','target'])
def test_forward_updater_parent_must_be_outside_target_venv(tmp_path,monkeypatch,caller):
    repo=tmp_path/'runtime/releases/v0.1.8';repo.mkdir(parents=True);root=tmp_path/'runtime'
    old=root/'releases/v0.1.7/.venv/Scripts/python.exe';old.parent.mkdir(parents=True);old.touch()
    target=repo/'.venv';target.mkdir();(target/'preserve-marker').write_bytes(b'cached target bytes')
    (root/'device.json').write_text(json.dumps({'python':str(old)}))
    tools=tmp_path/'trusted-tools';tools.mkdir();uv=tools/'uv.exe';uv.touch();base=tools/'python.exe';base.touch();calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None);monkeypatch.setattr(helpers,'service_python',lambda *args:None)
    monkeypatch.setattr(sys,'executable',str(old if caller=='retained' else target/'Scripts/python.exe'))
    def run(command,**options):calls.append(command);return subprocess.CompletedProcess(command,0,str(base))
    monkeypatch.setattr(helpers,'run',run)
    if caller=='target':
        with pytest.raises(ValueError,match='outside the target venv'):helpers.provision(repo,root,str(uv))
        assert len(calls)==2 and (target/'preserve-marker').read_bytes()==b'cached target bytes'  # find and the base probe
        assert not list(repo.glob('.venv-quarantined-*'))
    else:
        assert helpers.provision(repo,root,str(uv))==target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        assert len(calls)==3 and len(list(repo.glob('.venv-quarantined-*')))==1


def test_direct_setup_refuses_existing_device_before_provision(tmp_path,monkeypatch):
    root=tmp_path/'runtime';root.mkdir();vault=tmp_path/'vault';vault.mkdir()
    device=root/'device.json';device.write_text(json.dumps({'device_id':'fixture','vault':str(vault)}));before=device.read_bytes()
    support=installer.setup_helpers();monkeypatch.setattr(support,'host_context',lambda:None)
    monkeypatch.setattr(support,'provision',lambda *args:pytest.fail('Existing device must refuse before uv/provision.'))
    monkeypatch.setattr(installer,'setup_helpers',lambda:support)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault)])
    with pytest.raises(ValueError,match='approved update.py'):installer.main()
    assert device.read_bytes()==before and list(root.iterdir())==[device]


def test_uv_failure_installs_then_finds_before_sync(tmp_path,monkeypatch):
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime';tools=tmp_path/'tools';tools.mkdir()
    uv=tools/'uv.exe';uv.touch();base=tools/'python.exe';base.touch();calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None);monkeypatch.setattr(helpers,'service_python',lambda value:None)
    def run(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,1 if len(calls)==1 else 0,str(base)+'\n')
    monkeypatch.setattr(helpers,'run',run)
    helpers.provision(repo,root,str(uv))
    assert [command[1:3] for command in calls]==[['python','find'],['python','install'],['python','find'],['-I','-c'],['sync','--locked']]


@pytest.mark.parametrize('platform',['linux','darwin','darwin-none'])
def test_base_that_cannot_run_the_index_is_replaced(tmp_path,monkeypatch,platform):
    """Such a base runs the service with a dead index. Elsewhere uv's build replaces it; a Mac needs a framework build."""
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime';tools=tmp_path/'tools';tools.mkdir()
    uv=tools/'uv.exe';uv.touch();system=tools/'system-python.exe';system.touch();better=tools/'better-python.exe';better.touch();calls=[]
    monkeypatch.setattr(helpers,'host_context',lambda:None);monkeypatch.setattr(helpers,'service_python',lambda value:None)
    monkeypatch.setattr(helpers.sys,'platform',platform.split('-')[0])
    monkeypatch.setattr(helpers,'MAC_FRAMEWORK_PYTHONS',(str(tools/'absent'),) if platform=='darwin-none' else (str(tools/'absent'),str(better)))
    def run(command,**kwargs):
        calls.append(command)
        if command[0]==str(system):return subprocess.CompletedProcess(command,1,'','AttributeError')
        return subprocess.CompletedProcess(command,0,str(better if '--managed-python' in command else system)+'\n')
    monkeypatch.setattr(helpers,'run',run)
    if platform=='darwin-none':
        with pytest.raises(ValueError,match='brew install python@3.12'):helpers.provision(repo,root,str(uv))
        assert not any('sync' in command for command in calls)
        return
    helpers.provision(repo,root,str(uv))
    expected=[['python','find'],['-I','-c'],['python','install'],['python','find'],['sync','--locked']] if platform=='linux' else [['python','find'],['-I','-c'],['-I','-c'],['sync','--locked']]
    assert [command[1:3] for command in calls]==expected and calls[-1][-2:]==['--python',str(better)]


@pytest.mark.parametrize('prefix',['/usr/local','/opt/homebrew'])
def test_mac_provision_uses_unlinked_homebrew_python(tmp_path,monkeypatch,prefix):
    repo=tmp_path/'checkout';repo.mkdir();root=tmp_path/'runtime'
    uv=tmp_path/'uv';uv.touch();system=tmp_path/'system-python';system.touch()
    keg=tmp_path/'python3.12';keg.touch()
    keg_path=prefix+'/opt/python@3.12/bin/python3.12'
    monkeypatch.setattr(helpers,'host_context',lambda:None)
    monkeypatch.setattr(helpers,'service_python',lambda value:None)
    monkeypatch.setattr(helpers.sys,'platform','darwin')
    class HostPath:
        cwd=staticmethod(Path.cwd)

        def __new__(cls,value):
            if isinstance(value,str) and value.startswith(('/usr/local/','/opt/homebrew/')):
                return keg if value==keg_path else tmp_path/'missing'
            return Path(value)
    monkeypatch.setattr(helpers,'Path',HostPath)
    selected=[]
    def run(command,**kwargs):
        if command[1:3]==['python','find']:
            return subprocess.CompletedProcess(command,0,str(system)+'\n')
        if command[1:3]==['-I','-c']:
            return subprocess.CompletedProcess(command,int(command[0]!=keg_path))
        if command[1]=='sync':
            selected.append(command[-1])
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(helpers,'run',run)
    helpers.provision(repo,root,str(uv))
    assert selected==[str(keg)]


def test_planted_uv_refused(tmp_path):
    repo=tmp_path/'checkout';repo.mkdir();uv=repo/'uv.exe';uv.touch()
    with pytest.raises(ValueError,match='Refusing'):helpers.executable('uv',uv,tmp_path/'runtime',repo)


@pytest.mark.parametrize('path',[
    'C:/Program Files/WindowsApps/Claude_4.29.0.0_x64__pzs8sxrjxfjjc/app/claude.exe',
    'C:/Users/fixture/AppData/Local/AnthropicClaude/app-1.2.3/claude.exe',
    'C:/Users/fixture/AppData/Local/Programs/Claude/claude.exe'])
def test_desktop_paths_include_observed_msix_and_standard_installs(path):
    assert helpers.desktop_executable(path)


@pytest.mark.parametrize('path',['C:/Users/fixture/.local/bin/claude.exe','C:/Users/fixture/AppData/Local/claude-cli-nodejs/claude.exe'])
def test_cli_executables_do_not_count_as_desktop(path):
    assert not helpers.desktop_executable(path)


@pytest.mark.skipif(os.name!='nt',reason='Windows native runner boundary.')
def test_setup_owned_runner_cannot_bypass_installed_test_guard(tmp_path):
    with pytest.raises(AssertionError,match='dependency/model provisioning'):
        helpers.run(['uv','sync'],source_app=SOURCE/'app',capture_output=True,timeout=1)
    with pytest.raises(AssertionError,match='inline service-capable'):
        helpers.run([sys.executable,'-I','-B','-c','import runpy;runpy.run_path("brain_cli.py")','service','install'],
                    source_app=SOURCE/'app',capture_output=True,timeout=1)


def test_installer_service_child_has_canonical_environment_and_one_json_receipt(tmp_path,monkeypatch,capsys):
    root=tmp_path/'runtime';vault=tmp_path/'vault';vault.mkdir()
    support=installer.setup_helpers();calls=[]
    monkeypatch.setattr(installer,'private',lambda *args:None)
    def run(command,**options):
        calls.append(command)
        assert command[1:4]==['-I','-B','-X']
        assert options['env']['BRAIN_SYSTEM_ROOT']==str(root) and options['env']['BRAIN_PORT']=='8765'
        assert 'PYTHONPATH' not in options['env'] and options['capture_output']
        return SimpleNamespace(stdout='{"status":"installed"}\n')
    monkeypatch.setattr(support,'run',run)
    monkeypatch.setattr(installer,'setup_helpers',lambda:support)
    monkeypatch.setenv('BRAIN_SYSTEM_ROOT',str(tmp_path/'ambient-wrong-root'))
    monkeypatch.setenv('BRAIN_PORT','9999');monkeypatch.setenv('PYTHONPATH','untrusted')
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',sys.executable,'--install-service'])
    installer.main()
    result=json.loads(capsys.readouterr().out)
    assert result['service']=={'status':'installed'} and len(calls)==1


def test_deploy_swaps_clean_payload_and_preserves_old_extras_in_backup(tmp_path,monkeypatch):
    root=tmp_path/'runtime';vault=tmp_path/'vault';vault.mkdir()
    monkeypatch.setattr(installer,'private',lambda *args:None)
    installer.deploy(root,Path(sys.executable),vault)
    (root/'app/stale_shadow.py').write_bytes(b'# stale module')
    installer.deploy(root,Path(sys.executable),vault)
    assert not (root/'app/stale_shadow.py').exists()
    assert any((path/'stale_shadow.py').exists() for path in (root/'backups/app').iterdir())
