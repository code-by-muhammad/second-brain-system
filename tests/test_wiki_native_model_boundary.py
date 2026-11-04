"""No model inference: selected executable identity and synthetic child lifetime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import brain_wiki as w
from brain_wiki import _run_model as actual_model_transport
import brain_settings as cfg
import brain_git as g

@pytest.mark.skipif(os.name!='nt',reason='Windows-qualified compiler configuration.')
def test_native_selection_is_explicit_and_never_uses_a_shell_shim(brain,tmp_path,monkeypatch):
    with pytest.raises(RuntimeError,match='Configure'):w._model_executable()
    tool=tmp_path/'trusted-tools/codex.exe';tool.parent.mkdir();tool.write_bytes(b'Synthetic inert identity, never execute.')
    identity=w.configure_model(str(tool))['executable']
    assert identity['path']==str(tool.resolve()) and w._model_executable()==identity
    assert json.loads((cfg.STATE/'wiki-model.json').read_text())=={'format':1,'executable':str(tool.resolve())}
    shim=tool.with_suffix('.cmd');shim.write_bytes(b'Never execute this shell shim.')
    with pytest.raises(ValueError,match='codex.exe'):w.configure_model(str(shim))
    for parent in (cfg.ROOT,cfg.VAULT,Path.cwd()):
        if parent==Path.cwd():continue  # Never create a witness in the real checkout.
        planted=parent/'codex.exe';planted.write_bytes(b'Never execute.')
        with pytest.raises(ValueError,match='outside'):w.configure_model(str(planted))

@pytest.mark.skipif(os.name!='nt',reason='Actual Windows Job-backed private model transport.')
def test_contained_transport_preserves_literal_paths_stdin_and_native_cwd(brain,tmp_path):
    cwd=tmp_path/'work & literal %NO_EXPANSION%';cwd.mkdir()
    script=tmp_path/'synthetic_model.py';script.write_text('import json,os,sys\nprint(json.dumps({"cwd":os.getcwd(),"input":sys.stdin.read()}))\n',encoding='utf-8')
    started=[];log=tmp_path/'private-model.log'
    with log.open('w+b') as stream:
        result=actual_model_transport([sys.executable,'-I','-B',str(script)],'Synthetic input',10,cwd=str(cwd),log=stream,
                                      on_started=lambda:started.append(True),on_cleanup_wait=lambda:None)
    assert result.returncode==0 and started==[True]
    value=json.loads(log.read_text());assert Path(value['cwd'])==cwd and value['input']=='Synthetic input'

@pytest.mark.skipif(os.name!='nt',reason='Windows executable PATH policy.')
def test_git_resolver_ignores_cwd_runtime_vault_and_relative_entries(brain,tmp_path,monkeypatch):
    tools=tmp_path/'trusted';tools.mkdir();expected=tools/'git.exe';expected.write_bytes(b'Never execute resolver fixture.')
    cwd=tmp_path/'cwd';cwd.mkdir();monkeypatch.chdir(cwd)
    for folder in (cwd,cfg.ROOT,cfg.VAULT):
        folder.mkdir(exist_ok=True);(folder/'git.exe').write_bytes(b'Never execute planted fixture.')
    env={'PATH':os.pathsep.join(['','.',str(cwd),str(cfg.ROOT),str(cfg.VAULT),str(tools)])}
    assert g.git_executable(env)==str(expected)
    with pytest.raises(FileNotFoundError):g.git_executable({'PATH':os.pathsep.join([str(cwd),str(cfg.VAULT),'.',''])})
