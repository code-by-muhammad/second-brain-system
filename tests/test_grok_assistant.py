"""Grok Bot gets the same instruction text and one registration spec. Synthetic homes only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
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
    assert entry=={'command':str(root/'bin'/'second-brain-mcp'),'args':[],'env':{}}
    helpers.authorize_plan(plan,selected=['grok'])


def test_an_owned_release_python_spec_is_rewritten_to_the_stable_launcher(owner):
    home,root,python=owner
    path=home/'agent-data/workflows/second-brain/mcp-registration.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':str(python),'args':[str(root/'app/brain_client.py')]}}}),encoding='utf-8')
    plan=helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])
    spec=json.loads(next(row['after'] for row in plan['files'] if row['path'].endswith('mcp-registration.json')))
    assert spec['mcpServers']['second-brain']=={'command':str(root/'bin'/'second-brain-mcp'),'args':[],'env':{}}


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
    assert any(row['path']==str(target/'workflows'/'second-brain'/'mcp-registration.json') for row in plan['files'])
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


@pytest.mark.skipif(os.name!='posix',reason='directory mode bits are checked on POSIX')
def test_world_writable_link_parent_is_refused(tmp_path):
    home=tmp_path/'home';target=home/'real';(target/'workflows').mkdir(parents=True)
    link=home/'agent-data';link.symlink_to(target,target_is_directory=True)
    home.chmod(0o777)
    with pytest.raises(ValueError,match='not private'):
        helpers.resolve_client_path(link/'workflows'/'second-brain',home)


def test_launcher_runs_the_boot_check_before_the_client(tmp_path):
    root=tmp_path/'runtime';python=tmp_path/'python'
    path=installer.write_mcp_launcher(root,python)
    text=path.read_text(encoding='utf-8')
    assert 'brain_linux_user_service.py' in text and ' boot ' in text
    assert text.strip().splitlines()[-1].startswith('exec ')
    assert 'brain_client.py' in text.splitlines()[-1]


def test_check_accepts_one_owned_spec_without_starting_a_service(owner,monkeypatch,capsys):
    home,root,python=owner
    vault=owner[0].parent/'vault';vault.mkdir()
    (root/'state').mkdir(parents=True)
    (root/'device.json').write_text(json.dumps({'version':'0.2.14','role':'capture','python':str(python),'vault':str(vault)}),encoding='utf-8')
    folder=home/'agent-data/workflows/second-brain';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_bytes((SOURCE/'skills/second-brain/SKILL.md').read_bytes())
    launcher=root/'bin'/'second-brain-mcp'
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec '+str(python)+' '+str(root/'app'/'brain_client.py')+'\n',encoding='utf-8')
    launcher.chmod(0o700)
    (folder/'mcp-registration.json').write_text(json.dumps({'mcpServers':{'second-brain':{
        'command':str(launcher),'args':[],'env':{}}}},indent=2)+'\n',encoding='utf-8')
    import check_setup
    monkeypatch.setattr(sys,'argv',['check_setup.py','--root',str(root),'--home',str(home),'--clients','grok','--no-service','--expect-version','0.2.14','--release',str(SOURCE)])
    assert check_setup.main()==1
    output=capsys.readouterr().out
    assert 'PASS   Grok Bot connection:' in output
    assert 'PASS   Grok Bot instructions:' in output
    assert 'PASS   Grok Bot skill:' in output
    assert 'PASS   Grok Bot hooks:' in output
    assert 'FAIL   Grok Bot server:' in output
    assert 'not started' in output
