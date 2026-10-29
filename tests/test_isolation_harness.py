"""Prove test defaults and process refusals before running the product suite."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import pytest
import brain_service as service
import brain_settings as cfg
from isolation_guard import check, SANDBOX, validate_sandbox


@pytest.mark.parametrize('arguments',[
    ['python','-m','brain_cli','service','start'],
    ['python','-c','import brain_service;brain_service.start()'],
    ['python','-c','import brain_supervisor'],
])
def test_module_and_inline_service_routes_are_refused(arguments):
    with pytest.raises(AssertionError,match='service-capable'):check(arguments)


def test_qualified_fixture_children_require_every_sandbox_boundary(tmp_path):
    script=tmp_path/'software/install.py';python=tmp_path/'software/.venv/Scripts/python.exe'
    arguments=[str(python),'-I',str(script),'--root',str(tmp_path/'runtime'),'--vault',str(tmp_path/'vault'),'--reuse-python',str(python)]
    check(arguments)
    check([*arguments,'--install-service','--validate-only'])
    with pytest.raises(AssertionError):check([*arguments,'--install-service'])
    for option in ('--root','--vault','--reuse-python'):
        altered=arguments.copy();altered[altered.index(option)+1]='C:/Outside-Reviewed-Fixture'
        with pytest.raises(AssertionError):check(altered)
    altered=arguments.copy();altered[2]='C:/Outside-Reviewed-Fixture/install.py'
    with pytest.raises(AssertionError):check(altered)
    for name in ('install.py','update.py'):
        check([str(python),'-I',str(script.with_name(name)),'--help'])
    with pytest.raises(AssertionError):check(['uv','sync','--project',str(script.parent)])


def test_default_home_clients_are_all_synthetic(isolated_host,monkeypatch):
    spec = importlib.util.spec_from_file_location('isolation_installer', Path(__file__).resolve().parents[1] / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    # This tests destination paths, independently of the owner's actual GUI.
    support=installer.setup_helpers()
    monkeypatch.setattr(support,'desktop_busy',lambda:False)
    monkeypatch.setattr(installer,'setup_helpers',lambda:support)
    assert all(path.is_relative_to(isolated_host) for path, _ in installer.client_plan(cfg.ROOT, cfg.PYTHON))
    assert cfg.ROOT.is_relative_to(SANDBOX) and cfg.VAULT.is_relative_to(SANDBOX)
    assert cfg.PORT != 8765 and service.TASK.startswith('SecondBrain-Test-')
    assert Path(os.environ['APPDATA']).is_relative_to(isolated_host)


@pytest.mark.parametrize('command', [
    ['python', 'E:/Second-Brain-System/app/brain_cli.py'],
    ['python', 'e:\\SECOND-BRAIN\\notes.md'],
    ['python', '//?/E:/Second-Brain-System/app/script.py'],
    ['python', '--root=E:/Second-Brain-System'],
    ['python', 'C:/fake;E:/Second-Brain;C:/fake2'],
    ['python', 'file:///E:/Second-Brain'],
    ['python', '\\\\.\\E:\\Second-Brain-System\\app'],
    ['python', '\\\\localhost\\E$\\Second-Brain\\new-dir'],
    ['schtasks', '/Run', '/TN', 'SecondBrainLocalService'],
    ['launchctl', 'kickstart', 'local.second-brain'],
    ['launchctl', 'kickstart', 'gui/501/local.second-brain'],
    ['systemctl', '--user', 'start', 'second-brain.service'],
    ['python', 'C:/fake/brain_cli.py', 'service', 'stop'],
    ['python', 'C:/fake/brain_client.py'],
    ['python', 'C:/fake/brain_supervisor.py'],
    ['python', 'C:/fake/install.py', '--install-service'],
    ['python', 'C:/fake/update.py'],
    ['git', 'clone', 'https://github.com/example/test'],
    ['git', 'push', 'git@example.invalid:repo'],
    ['uv', 'sync'], ['schtasks', '/Create', '/TN', 'Synthetic-But-Not-Reviewed'],
])
def test_dangerous_process_is_refused(command):
    with pytest.raises(AssertionError, match='Test guard'):
        check(command)


@pytest.mark.parametrize('argument', ['cwd', 'env'])
def test_live_context_is_refused(argument):
    kwargs = {argument: 'E:/Second-Brain' if argument == 'cwd' else {'BRAIN_ROOT': 'E:/Second-Brain-System'}}
    with pytest.raises(AssertionError, match='live'):
        check([sys.executable, '-c', 'pass'], **kwargs)


def test_local_git_roundtrip_and_synthetic_acl_are_allowed(tmp_path):
    remote, repo = tmp_path / 'remote.git', tmp_path / 'repo'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    subprocess.run(['git', 'clone', str(remote), str(repo)], check=True, capture_output=True)
    check(['git', '-C', str(repo), 'push', '-u', 'origin', 'HEAD'])
    check(['git', '-C', str(repo), 'fetch', 'origin'])
    check(['icacls.exe', str(tmp_path / 'synthetic'), '/inheritance:r'])
    subprocess.run(['git', '-C', str(repo), 'remote', 'set-url', 'origin', 'https://example.invalid/remote'], check=True)
    with pytest.raises(AssertionError, match='outside the sandbox'):
        check(['git', '-C', str(repo), 'push', 'origin', 'HEAD'])
    subprocess.run(['git', '-C', str(repo), 'remote', 'set-url', 'origin', str(tmp_path.parent.parent.parent / 'outside.git')], check=True)
    with pytest.raises(AssertionError, match='outside the sandbox'):
        check(['git', '-C', str(repo), 'push', 'origin', 'HEAD'])
    subprocess.run(['git', '-C', str(repo), 'remote', 'set-url', 'origin', str(remote)], check=True)
    with pytest.raises(AssertionError):
        check(['git', '-C', str(repo), '-c', 'remote.origin.pushurl=https://example.invalid/rewrite', 'push', 'origin', 'HEAD'])
    subprocess.run(['git', '-C', str(repo), 'config', 'url.https://example.invalid/.pushInsteadOf', str(tmp_path)], check=True)
    with pytest.raises(AssertionError, match='outside the sandbox'):
        check(['git', '-C', str(repo), 'push', 'origin', 'HEAD'])


@pytest.mark.parametrize('value', ['C:/', 'E:/', 'relative', str(Path(__file__).resolve().parents[1])])
def test_unsafe_sandbox_root_is_refused(value):
    with pytest.raises(AssertionError, match='sandbox'):
        validate_sandbox(value, Path('C:/OwnerHome'), Path(__file__).resolve().parents[1])


def test_relative_remote_name_never_counts_as_a_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssertionError, match='verify Git remote'):
        check(['git', 'push', 'origin', 'HEAD'])


@pytest.mark.skipif(os.name!='nt',reason='Windows extended drive syntax.')
def test_extended_drive_sandbox_identity_preserves_boundary(tmp_path):
    from isolation_guard import inside
    assert inside('\\\\?\\'+str(tmp_path))
    assert not inside('\\\\?\\C:\\Windows')


def test_exact_fixture_clone_forms_and_implicit_default_are_local(tmp_path):
    remote,repo=tmp_path/'remote.git',tmp_path/'repo'
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    command=['git','-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid',
             '-c','core.hooksPath='+os.devnull,'clone','--no-local','--config','core.autocrlf=true','--',str(remote),str(repo)]
    subprocess.run(command,cwd=tmp_path,check=True,capture_output=True)
    check(['git','push'],cwd=repo)
    check(['git','pull','--ff-only'],cwd=repo)
    check(['git','-C',str(tmp_path),'-C','repo','push'])
    check(['git','clone','--depth=1','--filter=blob:none','--no-checkout','--',remote.as_uri(),str(tmp_path/'uri-clone')],cwd=tmp_path)
    subprocess.run(['git','-C',str(repo),'config','remote.pushDefault',str(tmp_path.parent.parent/'outside.git')],check=True)
    with pytest.raises(AssertionError,match='implicit remote override'):
        check(['git','push'],cwd=repo)
    subprocess.run(['git','-C',str(repo),'config','--unset','remote.pushDefault'],check=True)
    subprocess.run(['git','-C',str(repo),'remote','rename','origin','other'],check=True)
    with pytest.raises(AssertionError,match='single configured origin'):
        check(['git','push'],cwd=repo)


@pytest.mark.parametrize('arguments', [
    ['clone','--config','url.https://example.invalid/.insteadOf=C:/','--','C:/fake','C:/fake2'],
    ['clone','-u','arbitrary-command','C:/fake','C:/fake2'],
    ['push','--repo=C:/outside'], ['push','--receive-pack=arbitrary-command'],
    ['fetch','--all'],
])
def test_unknown_or_executable_git_options_refused(arguments,tmp_path):
    with pytest.raises(AssertionError,match='Test guard'):
        check(['git',*arguments],cwd=tmp_path)


def test_subprocess_guard_is_installed():
    with pytest.raises(AssertionError, match='service-capable'):
        subprocess.run([sys.executable, 'C:/fake/brain_client.py'], check=True)


@pytest.mark.skipif(os.name != 'nt', reason='Windows uses a native process runner.')
def test_native_process_guard_is_installed():
    from brain_windows_process import run
    with pytest.raises(AssertionError, match='network Git'):
        run(['git', 'push', 'https://example.invalid/remote'])
