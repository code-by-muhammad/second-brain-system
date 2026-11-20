"""Grok Bot gets the same instruction text and one registration spec. Synthetic homes only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('grok_install',SOURCE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
helpers=installer.setup_helpers()
PROTOCOL=(SOURCE/'docs/AI-PROTOCOL.md').read_text(encoding='utf-8')


def test_skill_file_is_the_shared_protocol():
    assert (SOURCE/'skills/second-brain/SKILL.md').read_text(encoding='utf-8')==helpers.composed_skill(PROTOCOL)
    text=(SOURCE/'skills/second-brain/SKILL.md').read_text(encoding='utf-8')
    assert text.count(helpers.BEGIN)==1 and PROTOCOL.strip() in text


@pytest.fixture
def owner(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();(home/'agent-data'/'workflows').mkdir(parents=True)
    monkeypatch.setattr(helpers,'desktop_busy',lambda:False)
    return home,tmp_path/'runtime',tmp_path/'python'


def test_grok_plan_writes_one_spec_and_the_shared_skill(owner,monkeypatch):
    home,root,python=owner
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    skill=helpers.composed_skill(PROTOCOL)
    plan=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill=skill,selected=['grok'])
    files={Path(row['path']):row for row in plan['files']}
    registration=home/'agent-data/workflows/second-brain/mcp-registration.json'
    skill_path=home/'agent-data/workflows/second-brain/SKILL.md'
    assert set(files)=={registration,skill_path}
    assert files[skill_path]['after']==skill
    spec=json.loads(files[registration]['after'])
    assert set(spec['mcpServers'])=={'second-brain'}
    entry=spec['mcpServers']['second-brain']
    assert entry=={**helpers.connection_spec(root,python),'env':{}}
    helpers.authorize_plan(plan,selected=['grok'])


def test_an_owned_release_python_spec_is_rewritten_to_the_platform_command(owner):
    home,root,python=owner
    path=home/'agent-data/workflows/second-brain/mcp-registration.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':str(python),'args':[str(root/'app/brain_client.py')]}}}),encoding='utf-8')
    plan=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])
    spec=json.loads(next(row['after'] for row in plan['files'] if row['path'].endswith('mcp-registration.json')))
    assert spec['mcpServers']['second-brain']=={**helpers.connection_spec(root,python),'env':{}}


def test_windows_grok_uses_the_recorded_python_and_adapter(owner):
    _,root,python=owner
    shared=helpers.connection()
    document=shared.grok_spec(root,python,windows=True)
    assert shared.validate_grok_spec(json.dumps(document).encode(),root,python,windows=True)=={
        'command':str(python),'args':[str(root/'app/brain_client.py')],'env':{}}


def test_a_second_server_or_a_foreign_runtime_is_refused(owner):
    home,root,python=owner
    path=home/'agent-data/workflows/second-brain/mcp-registration.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'/usr/bin/python3','args':['/other/brain_client.py']},'other':{}}}),encoding='utf-8')
    extra=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok','claude-code'])
    assert any('only the second-brain server' in row['reason'] for row in extra['skipped'])
    assert any(row['path'].endswith('.claude.json') for row in extra['files'])
    assert '"other"' in path.read_text(encoding='utf-8')
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'/usr/bin/python3','args':['/other/brain_client.py']}}}),encoding='utf-8')
    foreign=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])
    assert foreign['files']==[] or all(not row['path'].endswith('mcp-registration.json') for row in foreign['files'])
    assert any('another or unknown runtime' in row['reason'] for row in foreign['skipped'])


def test_an_extended_windows_link_target_is_a_normal_path():
    assert helpers._link_text('\\\\?\\C:\\home\\agent-data-real')=='C:\\home\\agent-data-real'
    assert helpers._link_text('\\\\?\\UNC\\server\\share\\agent-data-real')=='\\\\server\\share\\agent-data-real'
    assert helpers._link_text('/home/box/agent-data-real')=='/home/box/agent-data-real'


def test_home_symlink_is_planned_at_the_real_directory(tmp_path,monkeypatch):
    home=tmp_path/'home';target=home/'agent-data-real';(target/'workflows').mkdir(parents=True)
    (home/'agent-data').symlink_to(target,target_is_directory=True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    root=tmp_path/'runtime';python=tmp_path/'python'
    resolved=helpers.resolve_client_path(home/'agent-data'/'workflows'/'second-brain',home)
    assert resolved==target/'workflows'/'second-brain'
    plan=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])
    wanted=target/'workflows'/'second-brain'/'mcp-registration.json'
    assert any(Path(row['path'])==wanted for row in plan['files'])
    helpers.authorize_plan(plan,selected=['grok'])


def test_a_link_outside_home_skips_only_grok(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();outside=tmp_path/'outside';(outside/'workflows').mkdir(parents=True)
    (home/'agent-data').symlink_to(outside,target_is_directory=True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    root=tmp_path/'runtime';python=tmp_path/'python'
    plan=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok','claude-code'])
    assert any('leaves the home folder' in row['reason'] for row in plan['skipped'])
    assert any(row['path'].endswith('.claude.json') for row in plan['files'])
    assert not (outside/'workflows'/'second-brain'/'mcp-registration.json').exists()


@pytest.mark.skipif(os.name!='posix',reason='uses POSIX file symlinks to exercise canonical Grok leaf targets')
def test_grok_resolves_each_leaf_and_refuses_an_external_registration(owner,monkeypatch,tmp_path):
    home,root,python=owner
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    folder=helpers.grok_directory(home);folder.mkdir()
    skill=home/'shared-skill';skill.write_bytes(b'old')
    external=tmp_path/'external-spec';external.write_bytes(b'{}')
    (folder/'SKILL.md').symlink_to(skill)
    (folder/'mcp-registration.json').symlink_to(external)
    value=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='new',selected=['grok'])
    assert [row['path'] for row in value['files']]==[str(skill)]
    assert value['skipped'][0]['code']=='outside_home'
    helpers.authorize_plan(value,selected=['grok'])
    assert external.read_bytes()==b'{}'


@pytest.mark.skipif(os.name!='posix',reason='directory mode bits are checked on POSIX')
def test_world_writable_link_parent_is_refused(tmp_path):
    home=tmp_path/'home';target=home/'real';(target/'workflows').mkdir(parents=True)
    link=home/'agent-data';link.symlink_to(target,target_is_directory=True)
    home.chmod(0o777)
    with pytest.raises(ValueError,match='not private'):
        helpers.resolve_client_path(link/'workflows'/'second-brain',home)


def test_launcher_executes_the_client_with_exactly_rendered_bytes(tmp_path):
    root=tmp_path/'runtime';python=tmp_path/'python with spaces'
    path=installer.write_mcp_launcher(root,python)
    assert path.read_bytes()==helpers.connection().launcher_bytes(root,python)
    text=path.read_text(encoding='utf-8').splitlines()
    assert len(text)==2 and text[1].startswith('exec ') and 'brain_client.py' in text[1]


@pytest.fixture
def acceptance(owner,monkeypatch):
    home,root,_=owner;python=Path(sys.executable)
    vault=owner[0].parent/'vault';vault.mkdir()
    (root/'state').mkdir(parents=True)
    (root/'device.json').write_text(json.dumps({'version':'0.2.14','role':'capture','python':str(python),'vault':str(vault)}),encoding='utf-8')
    folder=home/'agent-data/workflows/second-brain';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_bytes((SOURCE/'skills/second-brain/SKILL.md').read_bytes())
    launcher=installer.write_mcp_launcher(root,python)
    spec_path=folder/'mcp-registration.json'
    spec_path.write_text(json.dumps(helpers.connection().grok_spec(root,python)),encoding='utf-8')
    import check_setup
    argv=['check_setup.py','--root',str(root),'--home',str(home),'--clients','grok','--expect-version','0.2.14','--release',str(SOURCE)]
    monkeypatch.setattr(sys,'argv',argv)
    return check_setup,launcher,spec_path,argv,root,python


def test_check_accepts_one_owned_spec_without_starting_a_service(acceptance,monkeypatch,capsys):
    check_setup,_,_,argv,_,_=acceptance
    argv.append('--no-service')
    monkeypatch.setattr(check_setup,'probe_stdio',lambda *args:pytest.fail('service-free setup must not probe'))
    assert check_setup.main()==0
    output=capsys.readouterr().out
    assert 'PASS   Grok Bot connection:' in output
    assert 'PASS   Grok Bot instructions:' in output
    assert 'PASS   Grok Bot skill:' in output
    assert 'PASS   Grok Bot hooks:' in output
    assert 'SKIP   Grok Bot server:' in output
    assert 'Grok Bot MCP registration' in output


@pytest.mark.parametrize('problem',['stale','duplicate','env','oversize','syntax','mode','launcher-code'])
def test_grok_refusal_prevents_launch_even_when_health_is_running(acceptance,monkeypatch,capsys,problem):
    check_setup,launcher,spec_path,_,_,_=acceptance
    raw=spec_path.read_bytes()
    if problem=='stale':spec_path.write_bytes(raw.replace(b'brain',b'foreign'))
    elif problem=='duplicate':spec_path.write_bytes(raw.replace(b'"env": {}',b'"env": {}, "env": {}'))
    elif problem=='env':spec_path.write_bytes(raw.replace(b'"env": {}',b'"env": {"OWNER": "value"}'))
    elif problem=='oversize':spec_path.write_bytes(raw+b' '*helpers.connection().GROK_SPEC_LIMIT)
    elif problem=='syntax':spec_path.write_bytes(raw[:-1])
    else:
        if os.name!='posix':pytest.skip('POSIX launcher validation does not run on Windows')
        if problem=='mode':launcher.chmod(0o722)
        else:launcher.write_bytes(launcher.read_bytes().replace(b'#!/bin/sh\n',b'#!/bin/sh\n: unexpected shell command\n'))
    health={'status':'running','version':'0.2.14','vault_exists':True,'database_exists':True}
    monkeypatch.setattr(check_setup.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(health)))
    monkeypatch.setattr(check_setup,'probe_stdio',lambda *args:pytest.fail('invalid registration must not probe'))
    assert check_setup.main()==1
    output=capsys.readouterr().out
    assert 'PASS   service health:' in output and 'FAIL   Grok Bot connection:' in output
    assert 'Grok Bot MCP registration' not in output


def test_grok_probe_is_given_generated_platform_argv(acceptance,monkeypatch,capsys):
    check_setup,_,_,_,root,python=acceptance
    health={'status':'running','version':'0.2.14','vault_exists':True,'database_exists':True}
    monkeypatch.setattr(check_setup.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(health)))
    seen=[]
    def probe(command,args):
        seen.append({'command':command,'args':args})
        return True,set()
    monkeypatch.setattr(check_setup,'probe_stdio',probe)
    assert check_setup.main()==0
    assert seen==[helpers.connection_spec(root,python)]
    assert 'PASS   Grok Bot server:' in capsys.readouterr().out


def test_grok_health_blocker_preserves_reason_without_running_stdio(acceptance,monkeypatch,capsys):
    check_setup,_,_,_,_,_=acceptance;commands=[]
    def run(argv,**kwargs):
        commands.append(argv[-1])
        return SimpleNamespace(returncode=1,stdout=json.dumps({'status':'blocked','reason':'cleanup_pending','guidance':'request recovery'}))
    monkeypatch.setattr(check_setup.subprocess,'run',run)
    monkeypatch.setattr(check_setup,'probe_stdio',lambda *args:pytest.fail('blocked service must not probe'))
    assert check_setup.main()==1 and commands==['health']
    assert 'cleanup_pending. request recovery' in capsys.readouterr().out
