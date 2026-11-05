"""No model inference: selected executable identity and synthetic child lifetime."""
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import Mock
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
    cwd=w._temporary_model_cwd(cwd)
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

def test_fresh_worker_canonicalizes_short_temp_alias_before_model_boundary(brain,tmp_path,monkeypatch):
    from test_wiki import fixture
    from test_wiki_remediation import explicit,fake_model
    source,packet,result=fixture(brain);original=source.read_bytes()
    calls=fake_model(monkeypatch,explicit(packet,result));invoke=w._run_model
    canonical=(tmp_path/'fresh model & literal %NO_EXPANSION%').resolve();canonical.mkdir()
    alias=str(tmp_path/'SHORT~1'/'brain-wiki-alias');events=[]
    raw=Mock(spec=Path);raw.is_absolute.return_value=True;raw.is_dir.return_value=True
    raw.is_symlink.return_value=False;raw.is_junction.return_value=False
    raw.parents=canonical.parents;raw.resolve.return_value=canonical
    original_path=w.Path
    monkeypatch.setattr(w,'Path',lambda value:raw if value==alias else original_path(value))
    original_tempfile=w.tempfile
    @contextmanager
    def fresh(*args,**kwargs):
        assert kwargs=={'prefix':'brain-wiki-'}
        events.append('created')
        try:yield alias
        finally:
            assert canonical.is_dir();canonical.rmdir();events.append('cleaned')
    class TempProxy:
        TemporaryDirectory=staticmethod(fresh)
        def __getattr__(self,name):return getattr(original_tempfile,name)
    monkeypatch.setattr(w,'tempfile',TempProxy())
    def capture(command,model_input,timeout,**kwargs):
        assert kwargs['cwd']==canonical and kwargs['cwd']!=alias
        return invoke(command,model_input,timeout,**kwargs)
    monkeypatch.setattr(w,'_run_model',capture)
    report=w.run(limit=1)
    assert report['status']=='complete' and len(calls)==1
    assert events==['created','cleaned'] and not canonical.exists()
    raw.resolve.assert_called_once_with(strict=True)
    assert source.read_bytes()==original

@pytest.mark.parametrize('shape',['relative','missing','file','leaf-symlink','leaf-junction','ancestor-symlink','ancestor-junction'])
def test_temp_cwd_refuses_unsafe_raw_paths_before_resolve(shape,monkeypatch):
    raw=Mock(spec=Path);near=Mock(spec=Path);far=Mock(spec=Path)
    raw.is_absolute.return_value=shape!='relative';raw.is_dir.return_value=shape not in ('missing','file')
    for item in (raw,near,far):
        item.is_symlink.return_value=False;item.is_junction.return_value=False
    raw.parents=(near,far)
    if shape=='leaf-symlink':raw.is_symlink.return_value=True
    elif shape=='leaf-junction':raw.is_junction.return_value=True
    elif shape=='ancestor-symlink':far.is_symlink.return_value=True
    elif shape=='ancestor-junction':far.is_junction.return_value=True
    raw.resolve.side_effect=AssertionError('Unsafe raw path must never be resolved.')
    monkeypatch.setattr(w,'Path',lambda value:raw)
    with pytest.raises(ValueError,match='plain absolute model working directory'):
        w._temporary_model_cwd('synthetic private path')
    raw.resolve.assert_not_called()
