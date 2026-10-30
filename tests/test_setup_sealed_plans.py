"""Synthetic client settings and bootstrap selection; no actual clients/services."""
import importlib.util
import json
import os
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
    assert len(result['files'])==1 and not (home/'.codex/AGENTS.md').exists()
    data=tomllib.loads(result['files'][0]['after'])['mcp_servers']['second-brain']
    assert data['env']=={'OWNER':'keep'} and data['startup_timeout_sec']==91 and data['command']==str(python)


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
    with pytest.raises(ValueError,match='another or unknown runtime'):plan(owner,selected=[client])
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


def test_malformed_unowned_refresh_preserved_but_owned_duplicate_json_refused(owner):
    home,root,python=owner;path=home/'.claude.json'
    path.write_text('{unrelated-bad')
    assert not plan(owner,selected=['claude-code'],refresh=True)['files']
    path.write_text('{"root":'+json.dumps(str(root))+',"mcpServers":{},"mcpServers":{}}')
    with pytest.raises(ValueError,match='Duplicate'):plan(owner,selected=['claude-code'],refresh=True)


@pytest.mark.parametrize('text',[helpers.BEGIN+'\n',helpers.BEGIN+helpers.END+helpers.BEGIN+helpers.END,helpers.END+helpers.BEGIN])
def test_bad_protocol_markers_refused(owner,text):
    home,root,python=owner;(home/'.codex/AGENTS.md').write_text(text)
    with pytest.raises(ValueError,match='marker|protocol'):plan(owner,selected=['codex'],refresh=True)


def test_refresh_cas_before_stop_and_deferred_concurrent_edit(owner):
    home,root,python=owner;path=home/'.claude.json'
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'old','args':[str(root/'app/brain_client.py')]}}}))
    value=plan(owner,selected=['claude-code'],refresh=True)
    helpers.current(value)
    original=path.read_bytes();path.write_text('{"new owner edit":true}')
    with pytest.raises(ValueError,match='old service is healthy'):helpers.current(value)
    writes=[]
    receipt=helpers.apply(value,lambda *args:writes.append(args),lambda *args:None,root)
    assert not writes and len(receipt['deferred'])==1 and path.read_text()=='{"new owner edit":true}'
    path.write_bytes(original)
    receipt=helpers.apply(value,lambda path,text:path.write_bytes(text.encode('utf-8')),lambda *args:None,root)
    assert receipt['changed']==[str(path)] and helpers.after_check(value)==[]
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
    home,root,python=owner;(home/'.claude.json').write_bytes(b'x'*100)
    monkeypatch.setattr(helpers,'MAX_CONFIG',99)
    with pytest.raises(ValueError,match='bound'):plan(owner,selected=['claude-code'],refresh=True)


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
    assert len(calls)==2 and calls[1][-2:]==['--python',str(base)] and not any('install' in command for command in calls)
    assert calls[0][1:]==['python','find','--system','--no-project','--no-python-downloads','3.12']


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
        assert len(calls)==1 and (environment/'preserve-marker').read_bytes()==b'previous venv'
        assert not list(repo.glob('.venv-quarantined-*'))
    else:
        helpers.provision(repo,root,str(uv))
        saved=list(repo.glob('.venv-quarantined-*'))
        assert len(calls)==2 and len(saved)==1 and (saved[0]/'preserve-marker').read_bytes()==b'previous venv'
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
        assert len(calls)==1 and (target/'preserve-marker').read_bytes()==b'cached target bytes'
        assert not list(repo.glob('.venv-quarantined-*'))
    else:
        assert helpers.provision(repo,root,str(uv))==target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        assert len(calls)==2 and len(list(repo.glob('.venv-quarantined-*')))==1


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
    assert [command[1:3] for command in calls]==[['python','find'],['python','install'],['python','find'],['sync','--locked']]


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
