import importlib.util, json, os, plistlib, sys, tomllib
from pathlib import Path
import pytest
import brain_service as service
import brain_settings as cfg

spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().parents[1]/'install.py')
installer=importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)

@pytest.mark.parametrize('platform',['win32','darwin','linux'])
def test_startup_definitions(brain,platform):
    text=service.definition(platform)
    assert 'brain_supervisor.py' in text
    if platform=='win32':
        import xml.etree.ElementTree as ET
        ET.fromstring(text); assert 'LeastPrivilege' in text
    elif platform=='darwin':
        data=plistlib.loads(text.encode()); assert data['RunAtLoad']
    else: assert '[Service]' in text and 'Restart=on-failure' in text

def test_client_config_preservation(brain,tmp_path,monkeypatch):
    home=tmp_path/'home'; (home/'.codex').mkdir(parents=True)
    original='model = "user-choice"\n\n[mcp_servers.other]\ncommand = "keep"\n\n[mcp_servers.second-brain]\ncommand = "old"\n\n[mcp_servers.second-brain.env]\nOLD = "value"\n'
    (home/'.codex/config.toml').write_text(original)
    (home/'.claude.json').write_text(json.dumps({'theme':'dark','mcpServers':{'other':{'command':'keep'}}}))
    monkeypatch.setattr(sys,'platform','linux')
    installer.clients(cfg.ROOT,cfg.PYTHON,home)
    data=tomllib.loads((home/'.codex/config.toml').read_text())
    assert data['model']=='user-choice' and data['mcp_servers']['other']['command']=='keep'
    assert 'env' not in data['mcp_servers']['second-brain']
    claude=json.loads((home/'.claude.json').read_text())
    assert claude['theme']=='dark' and claude['mcpServers']['other']['command']=='keep'

def test_upgrade_preserves_identity_role_token(brain):
    vault,state=brain
    a=installer.deploy(cfg.ROOT,cfg.PYTHON,vault,'maintainer')
    token=(state/'client-access.json').read_bytes()
    b=installer.deploy(cfg.ROOT,cfg.PYTHON,vault,'capture')
    assert a['device_id']==b['device_id'] and b['role']=='maintainer'
    assert (state/'client-access.json').read_bytes()==token

def test_runtime_cannot_live_inside_vault(brain):
    vault,state=brain
    with pytest.raises(ValueError): installer.deploy(vault/'runtime',cfg.PYTHON,vault)
