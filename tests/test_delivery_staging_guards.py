"""Guarded real-file replacements and trusted native ACL tool resolution."""
import ctypes
import importlib.util
from pathlib import Path
import os
from types import SimpleNamespace
import pytest

@pytest.fixture
def installer():
    path=Path(__file__).resolve().parents[1]/'install.py'
    spec=importlib.util.spec_from_file_location('staging_installer',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize('exists',[False,True])
def test_atomic_preserves_owner_save_during_fsync(installer,tmp_path,monkeypatch,exists):
    path=tmp_path/'client.json';old=b'{"original":true}'
    if exists:path.write_bytes(old)
    owner=b'{"owner":true}';native=installer.os.fsync;fired=[]
    def fsync(fd):
        native(fd)
        if not fired:fired.append(True);path.write_bytes(owner)
    monkeypatch.setattr(installer.os,'fsync',fsync)
    with pytest.raises(installer.setup_helpers().ConcurrentWriteError):
        installer.atomic(path,b'{"planned":true}',expected_sha256=installer.setup_helpers().digest(old) if exists else None)
    assert path.read_bytes()==owner and not list(tmp_path.glob('*.tmp'))

def test_client_plan_reports_staging_conflict_as_deferred(installer,tmp_path,monkeypatch):
    root=tmp_path/'runtime';path=tmp_path/'client.json';path.write_bytes(b'{"old":true}')
    helper=installer.setup_helpers();after='{"planned":true}'
    plan={'format':1,'root':str(root.resolve()),'python':'synthetic-python',
          'files':[{'client':'claude-code','path':str(path),'before_sha256':helper.digest(path.read_bytes()),'after':after,'after_sha256':helper.digest(after.encode())}]}
    native=installer.os.fsync;fired=[]
    def fsync(fd):
        native(fd)
        if not fired:fired.append(True);path.write_bytes(b'{"owner":true}')
    monkeypatch.setattr(installer.os,'fsync',fsync)
    report=helper.apply(plan,installer.atomic,lambda *args:None,root)
    assert not report['changed'] and len(report['deferred'])==1 and path.read_bytes()==b'{"owner":true}'

@pytest.mark.skipif(os.name!='nt',reason='Canonical Windows ACL API selection.')
def test_acl_uses_system_directory_and_current_token_not_environment(installer,tmp_path,monkeypatch):
    system=tmp_path/'system';system.mkdir();(system/'icacls.exe').write_bytes(b'Synthetic system tool, never execute.')
    cwd=tmp_path/'checkout';cwd.mkdir();(cwd/'icacls.exe').write_bytes(b'Synthetic decoy, never execute.');monkeypatch.chdir(cwd)
    def directory(buffer,size):buffer.value=str(system);return len(str(system))
    monkeypatch.setattr(ctypes,'WinDLL',lambda *args,**kwargs:SimpleNamespace(GetSystemDirectoryW=directory))
    monkeypatch.setattr(installer,'_account_sid',lambda:'*S-1-5-21-12345')
    monkeypatch.setenv('USERNAME','UntrustedOtherAccount');monkeypatch.setenv('USERDOMAIN','UntrustedDomain')
    calls=[];monkeypatch.setattr(installer.subprocess,'run',lambda command,**kwargs:calls.append((command,kwargs)))
    path=tmp_path/'private-state';path.mkdir();installer.private(path,True)
    command,options=calls[0]
    assert Path(command[0])==system/'icacls.exe' and '*S-1-5-21-12345:(OI)(CI)F' in command
    assert '*S-1-5-18:(OI)(CI)F' in command and options['check'] and options['timeout']==30
    assert not any('Untrusted' in value for value in command)
