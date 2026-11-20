"""Use a real synthetic venv and tagged release while intercepting native service calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import venv

import pytest
import tomlkit


SOURCE = Path(__file__).resolve().parents[1]


def test_public_update_preflight_and_owned_deploy_do_not_import_untracked_checkout_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('isolated_update', SOURCE / 'update.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    real_run = subprocess.run
    root, vault, home = (tmp_path / name for name in ('runtime', 'vault', 'home'))
    for path in (root / 'state', root / 'app', vault, home / '.codex', home / '.claude'):
        path.mkdir(parents=True)
    codex = home / '.codex/config.toml'
    codex.write_text('model = "synthetic-owner"\n[mcp_servers.second-brain]\ncommand = '+json.dumps(sys.executable)
                     +'\nargs = ['+json.dumps(str(root/'app/brain_client.py'))+']\n', encoding='utf-8')
    claude, hooks = home / '.claude.json', home / '.claude/settings.json'
    claude.write_bytes(b'{"owner":"preserve"}\n')
    hooks.write_bytes(b'{"hooks":{}}\n')
    original = b'# previous synthetic runtime\n'
    (root / 'app/original.py').write_bytes(original)
    previous = root / 'old-release'
    (previous / 'app').mkdir(parents=True)
    (previous / 'app/original.py').write_bytes(original)
    (root / 'device.json').write_text(json.dumps({'vault': str(vault), 'role': 'capture',
        'python': sys.executable, 'release_root': str(previous), 'version': '0.1.6',
        'device_id': 'synthetic-isolation', 'service_name': 'SecondBrain-Test-'+uuid.uuid4().hex}), encoding='utf-8')
    release = root / 'releases/v0.1.7'
    release.mkdir(parents=True)
    old_names = ['install.py', 'update.py', 'setup_runtime.py', 'pyproject.toml', 'uv.lock']
    for name in old_names:
        (previous / name).write_bytes(b'# inert predecessor evidence\n')
    names = [*old_names, '.gitignore', 'docs/AI-PROTOCOL.md', 'app/brain_connection.py', 'app/brain_release.py']
    for name in names:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (SOURCE / name).read_bytes()
        if name == 'install.py':
            raw, count = re.subn(rb"(?m)^VERSION='[\d.]+'$", b"VERSION='0.1.7'", raw)
            assert count == 1
        target.write_bytes(raw)
    (release / 'app/brain_cli.py').write_bytes(b'# service calls are intercepted, never executed\n')
    names.append('app/brain_cli.py')

    git_env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    git_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='',
                   GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')

    def tag(checkout, version, files):
        hashes = {name: hashlib.sha256((checkout / name).read_bytes()).hexdigest() for name in files}
        (checkout / 'release-manifest.json').write_text(json.dumps({'version': version,
            'repository': 'code-by-muhammad/second-brain-system',
            'capabilities': ['sealed-client-refresh-v1', 'owned-service-control-v1'],
            'sha256': hashes}), encoding='utf-8', newline='\n')

        def git(*arguments):
            real_run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
                      '-c', 'core.autocrlf=false', '-C', str(checkout), *arguments], env=git_env,
                     capture_output=True, check=True, timeout=30)

        git('init', '--initial-branch=main')
        git('config', 'user.name', 'Synthetic Installer Isolation')
        git('config', 'user.email', 'installer-isolation@example.invalid')
        git('config', 'commit.gpgsign', 'false')
        git('remote', 'add', 'origin', updater.REMOTE)
        git('add', '--all')
        git('commit', '-m', 'Synthetic tagged installer')
        git('-c', 'tag.gpgsign=false', 'tag', 'v'+version)
        return hashes

    tag(previous, '0.1.6', [*old_names, 'app/original.py'])
    candidate = tag(release, '0.1.7', names)
    environment = release / '.venv'
    # The test guard resolves the actual executable before proving sandbox containment.
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = environment / ('Lib/site-packages' if os.name == 'nt' else
                           f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
    assert tomlkit.__version__ == '0.15.1'
    shutil.copytree(Path(tomlkit.__file__).parent, site / 'tomlkit', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    marker = tmp_path / 'untracked-tomllib-imported'
    (release / 'tomllib.py').write_text('from pathlib import Path\nPath(' + repr(str(marker))
        + ').write_bytes(b"harmless synthetic import marker")\nraise RuntimeError("Untracked cached module executed.")\n',
        encoding='utf-8', newline='\n')
    assert updater.verify(release, version='v0.1.7')['git_identity']['tag'] == 'v0.1.7'

    scratch = tmp_path / 'private process temp'
    scratch.mkdir()
    child_env = {k: v for k, v in os.environ.items()
                 if not k.upper().startswith(('BRAIN_', 'BASIC_MEMORY_', 'PYTHON'))}
    child_env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / 'AppData/Roaming'),
        XDG_CONFIG_HOME=str(home / 'config'), TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
        PYTHONPATH=str(release), HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    children, effects = [], []
    monkeypatch.setattr(updater, '__file__', str(release / 'update.py'))
    install = updater.installer()
    support = install.setup_helpers()
    monkeypatch.setattr(install, 'private', lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(support, 'host_context', lambda: None)
    # The retained Python binding on this synthetic predecessor is the running test interpreter.
    monkeypatch.setattr(support, 'service_python', lambda selected: None)

    def provision(*args):
        effects.append('provision')
        return python

    monkeypatch.setattr(support, 'provision', provision)
    deploy_installed = install.deploy

    def deploy(selected, selected_python, selected_vault, **kwargs):
        assert (selected, selected_python, selected_vault) == (root, python, vault)
        effects.append('owned-deploy')
        return deploy_installed(selected, selected_python, selected_vault, **kwargs)

    monkeypatch.setattr(install, 'deploy', deploy)

    def run(command, **kwargs):
        assert str(release / 'install.py') in command and '--validate-only' in command
        child = real_run(command, env=child_env, capture_output=True, text=True, encoding='utf-8',
                         timeout=30, check=True)
        children.append(json.loads(child.stdout))
        return child

    monkeypatch.setattr(support, 'run', run)
    registration = {'kind': sys.platform, 'definition_sha256': 'a'*64, 'enabled': True, 'interpreter': sys.executable}
    if sys.platform == 'win32':
        registration.update(xml='<Task/>', owner_sid='S-1-5-21-101-102-103-1001')

    def controller(python, checkout, selected, command, action, *flags):
        assert selected == root and command == 'service' and action in ('inspect', 'hold', 'stop', 'install', 'start', 'finish')
        effects.append(action)
        transaction = root / 'state/service-transaction.json'
        if action == 'inspect':
            return {'status': 'inspected', 'task_present': True, 'task_enabled': True, 'registration': registration}
        if action == 'hold':
            saved = Path(flags[flags.index('--snapshot')+1])
            transaction.write_text(json.dumps({'hold_id': flags[flags.index('--hold-id')+1],
                'root': str(root), 'snapshot': str(saved), 'stage': 'prepared',
                'snapshot_sha256': hashlib.sha256((saved/'snapshot.json').read_bytes()).hexdigest()}))
        elif action == 'stop':
            transaction.write_text(json.dumps({**json.loads(transaction.read_bytes()), 'stage': 'stopped'}))
        elif action == 'finish':
            transaction.unlink()
            return {'status': 'transaction released', 'hold_id': flags[flags.index('--hold-id')+1]}
        return {'status': 'stopped' if action == 'stop' else action, 'empty_at_filetime': 123}

    monkeypatch.setattr(updater, 'controller', controller)

    def wait_health(selected_python, selected, version, **kwargs):
        installed = json.loads((root / 'device.json').read_bytes())
        assert selected_python == python and str(selected_python) == installed['python']
        assert selected == root and version == installed['version']
        return {'status': 'running', 'version': version}

    monkeypatch.setattr(updater, 'wait_health', wait_health)
    result = updater.perform(root, 'v0.1.7', skip_desktop=True)
    saved = Path(result['snapshot'])
    sealed = json.loads((saved / 'snapshot.json').read_bytes())
    assert sealed['format'] == 3 and sealed['registration'] == registration
    assert sealed['predecessor']['source'] == 'recorded checkout' and result['status'] == 'updated'
    assert (root / 'state/previous-release.json').read_bytes() == (saved / 'previous-binding.after').read_bytes()
    assert (saved / 'app/original.py').read_bytes() == original
    assert {f'app/{path.name}': hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / 'app').iterdir()} == {
        name: sha for name, sha in candidate.items() if name.startswith('app/')}
    assert claude.read_bytes() == b'{"owner":"preserve"}\n' and hooks.read_bytes() == b'{"hooks":{}}\n'
    assert tomlkit.parse(codex.read_text())['model'] == 'synthetic-owner'
    assert not marker.exists()
    assert len(children) == 1 and children[0]['status'] == 'validated'
    plans = list((root/'state').glob('upgrade-client-plan-*.json'))
    assert len(plans) == 1
    summary = json.loads(plans[0].read_bytes())
    assert [row['path'] for row in summary['files']] == [str(codex)]
    assert hashlib.sha256((root/'device.json').read_bytes()).hexdigest() == summary['device_after_sha256']
    assert effects == ['inspect', 'provision', 'hold', 'stop', 'owned-deploy', 'install', 'start', 'finish']
