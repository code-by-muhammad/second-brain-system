"""Cached installer imports use a real synthetic venv; deployment effects are intercepted."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import venv

import filelock
import pytest
import tomlkit


SOURCE = Path(os.environ.get('R149_SOURCE', Path(__file__).resolve().parents[1])).resolve()


@pytest.mark.parametrize('selected_launch', ['preflight', 'install'])
def test_public_update_installer_cannot_import_untracked_checkout_module(tmp_path, monkeypatch, selected_launch):
    spec = importlib.util.spec_from_file_location('isolated_update', SOURCE / 'update.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    real_run = subprocess.run
    root, vault = tmp_path / 'runtime', tmp_path / 'vault'
    home, appdata = tmp_path / 'home', tmp_path / 'appdata'
    for path in (root / 'state', root / 'app', vault, home / '.codex', home / '.claude', appdata):
        path.mkdir(parents=True)
    (home / '.codex/config.toml').write_text('model = "synthetic-owner"\n', encoding='utf-8')
    (home / '.claude.json').write_bytes(b'{"owner":"preserve"}\n')
    (home / '.claude/settings.json').write_bytes(b'{"hooks":{}}\n')
    (root / 'app/original.py').write_bytes(b'# previous synthetic runtime\n')
    (root / 'device.json').write_text(json.dumps({'vault': str(vault), 'role': 'capture',
        'python': sys.executable, 'release_root': 'synthetic-previous-release', 'version': '0.1.6'}), encoding='utf-8')
    release = root / 'releases/v0.1.7'
    release.mkdir(parents=True)
    names = ['.gitignore', 'install.py', 'update.py', 'pyproject.toml', 'uv.lock', 'docs/AI-PROTOCOL.md']
    for name in names:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SOURCE / name).read_bytes())
    (release / 'app').mkdir()
    (release / 'app/brain_cli.py').write_bytes(b'# service calls are intercepted, never executed\n')
    names.append('app/brain_cli.py')
    hashes = {name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in names}
    (release / 'release-manifest.json').write_text(json.dumps({'version': '0.1.7',
        'repository': 'code-by-muhammad/second-brain-system', 'sha256': hashes}), encoding='utf-8', newline='\n')
    git_env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    git_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='',
                   GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')

    def git(*arguments):
        return real_run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
            '-c', 'core.autocrlf=false', '-C', str(release), *arguments], env=git_env,
            capture_output=True, check=True, timeout=30)

    git('init', '--initial-branch=main')
    git('config', 'user.name', 'Synthetic Installer Isolation')
    git('config', 'user.email', 'installer-isolation@example.invalid')
    git('config', 'commit.gpgsign', 'false')
    git('remote', 'add', 'origin', updater.REMOTE)
    git('add', '--all')
    git('commit', '-m', 'Synthetic tagged installer')
    git('-c', 'tag.gpgsign=false', 'tag', 'v0.1.7')
    environment = release / '.venv'
    venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = environment / ('Lib/site-packages' if os.name == 'nt' else
                           'lib/python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor) + '/site-packages')
    assert tomlkit.__version__ == '0.15.1'
    shutil.copytree(Path(tomlkit.__file__).parent, site / 'tomlkit', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    marker = tmp_path / 'untracked-tomllib-imported'
    (release / 'tomllib.py').write_text('from pathlib import Path\nPath(' + repr(str(marker))
        + ').write_bytes(b"harmless synthetic import marker")\nraise RuntimeError("Untracked cached module executed.")\n',
        encoding='utf-8', newline='\n')
    # The real R148 gate admits unrelated untracked files; it must not turn them into imports.
    assert updater.verify(release, version='v0.1.7')['git_identity']['tag'] == 'v0.1.7'
    scratch = tmp_path / 'private process temp'
    scratch.mkdir()
    child_env = {k: v for k, v in os.environ.items()
                 if not k.upper().startswith(('BRAIN_', 'BASIC_MEMORY_', 'PYTHON'))}
    child_env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(appdata),
        XDG_CONFIG_HOME=str(home / 'config'), TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
        PYTHONPATH=str(release), HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    children, effects = [], []

    def run(command, **kwargs):
        if Path(command[0]).name.lower() in ('git', 'git.exe'):
            assert 'clone' not in command
            return real_run(command, **kwargs)
        if command[0] == 'uv':
            effects.append('uv-intercepted')
            return subprocess.CompletedProcess(command, 0)
        if str(release / 'install.py') in command:
            stage = 'preflight' if '--validate-only' in command else 'install'
            if stage != selected_launch:
                return subprocess.CompletedProcess(command, 0)
            # Keep the real production launch prefix. -B only prevents fixture bytecode;
            # the deployment launch is restricted to its real validate-only path.
            child = [command[0], '-B', *command[1:]]
            if '--validate-only' not in child:
                child.append('--validate-only')
            result = real_run(child, env=child_env, capture_output=True, text=True,
                              encoding='utf-8', timeout=30, check=False)
            children.append(result)
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode, child, result.stdout, result.stderr)
            return result
        assert str(root / 'app/brain_cli.py') in command and command[-2] == 'service'
        assert command[-1] in ('install', 'start', 'stop')
        effects.append('service-intercepted')
        return subprocess.CompletedProcess(command, 0)

    def preserved():
        return ((root / 'device.json').read_bytes(), tuple(
            {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            for folder in (home, appdata, root / 'state', root / 'app')))

    monkeypatch.setattr(updater.subprocess, 'run', run)
    monkeypatch.setattr(updater, 'update_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(filelock, 'FileLock', lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(updater, 'snapshot', lambda *args: tmp_path / 'snapshot-not-created')
    monkeypatch.setattr(updater, 'restore', lambda *args: None)
    monkeypatch.setattr(updater, 'installer', lambda: SimpleNamespace(atomic=lambda *args: None))
    monkeypatch.setattr(updater, 'wait_health', lambda python, root, version: {'status': 'running', 'version': version})
    monkeypatch.setattr(sys, 'argv', ['update.py', '--root', str(root), '--version', 'v0.1.7'])
    before = preserved()
    failure = None
    try:
        updater.main()
    except subprocess.CalledProcessError as error:
        failure = error
    assert before == preserved(), 'Validate-only must preserve synthetic runtime/client settings.'
    assert not marker.exists(), 'The allowed untracked cached tomllib.py executed during ' + selected_launch
    assert failure is None and len(children) == 1 and children[0].returncode == 0
    assert json.loads(children[0].stdout)['status'] == 'validated'
    assert effects.count('uv-intercepted') == 1
