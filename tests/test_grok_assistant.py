"""Grok Bot gets the same instruction text and one registration spec. Synthetic homes only."""
import importlib.util
import json
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
    with pytest.raises(ValueError,match='only the second-brain server'):
        helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])
    path.write_text(json.dumps({'mcpServers':{'second-brain':{'command':'/usr/bin/python3','args':['/other/brain_client.py']}}}),encoding='utf-8')
    with pytest.raises(ValueError,match='another or unknown runtime'):
        helpers.plan(root,python,PROTOCOL,installer._codex_toml_plan,home=home,skill='skill\n',selected=['grok'])


def test_check_accepts_one_owned_spec_without_starting_a_service(owner,monkeypatch,capsys):
    home,root,python=owner
    vault=owner[0].parent/'vault';vault.mkdir()
    (root/'state').mkdir(parents=True)
    (root/'device.json').write_text(json.dumps({'version':'0.2.14','role':'capture','python':str(python),'vault':str(vault)}),encoding='utf-8')
    folder=home/'agent-data/workflows/second-brain';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_text(helpers.composed_skill(PROTOCOL),encoding='utf-8')
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
