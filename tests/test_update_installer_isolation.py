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
import uuid
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
    (home / '.codex/config.toml').write_text('model = "synthetic-owner"\n[mcp_servers.second-brain]\ncommand = '+json.dumps(sys.executable)+'\nargs = ['+json.dumps(str(root/'app/brain_client.py'))+']\n', encoding='utf-8')
    (home / '.claude.json').write_bytes(b'{"owner":"preserve"}\n')
    (home / '.claude/settings.json').write_bytes(b'{"hooks":{}}\n')
    (root / 'app/original.py').write_bytes(b'# previous synthetic runtime\n')
    (root / 'device.json').write_text(json.dumps({'vault': str(vault), 'role': 'capture',
        'python': sys.executable, 'release_root': 'synthetic-previous-release', 'version': '0.1.6',
        'device_id':'synthetic-isolation','service_name':'SecondBrain-Test-'+uuid.uuid4().hex}), encoding='utf-8')
    release = root / 'releases/v0.1.7'
    release.mkdir(parents=True)
    names = ['.gitignore', 'install.py', 'update.py', 'setup_runtime.py', 'pyproject.toml', 'uv.lock', 'docs/AI-PROTOCOL.md']
    for name in names:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SOURCE / name).read_bytes())
    (release / 'app').mkdir()
    (release / 'app/brain_cli.py').write_bytes(b'# service calls are intercepted, never executed\n')
    names.append('app/brain_cli.py')
    hashes = {name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in names}
    (release / 'release-manifest.json').write_text(json.dumps({'version': '0.1.7',
        'repository': 'code-by-muhammad/second-brain-system', 'capabilities':['sealed-client-refresh-v1','owned-service-control-v1'], 'sha256': hashes}), encoding='utf-8', newline='\n')
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
    spec=importlib.util.spec_from_file_location('isolation_parent_installer',SOURCE/'install.py')
    install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)
    support=install.setup_helpers()
    monkeypatch.setattr(install,'setup_helpers',lambda:support)
    monkeypatch.setattr(updater,'installer',lambda:install)
    monkeypatch.setattr(updater,'__file__',str(release/'update.py'))
    monkeypatch.setattr(support,'host_context',lambda:None)
    def provision(*args):effects.append('provision-intercepted');return python
    monkeypatch.setattr(support,'provision',provision)

    def run(command, **kwargs):
        if Path(command[0]).name.lower() in ('git', 'git.exe'):
            assert 'clone' not in command
            return real_run(command, **kwargs)
        if str(release / 'install.py') in command:
            stage = 'preflight' if '--validate-only' in command else 'install'
            # Keep the real production launch prefix. -B only prevents fixture bytecode;
            # the deployment launch is restricted to its real validate-only path.
            child = [command[0], '-B', *command[1:]]
            if '--validate-only' not in child:
                child.append('--validate-only')
            result = real_run(child, env=child_env, capture_output=True, text=True,
                              encoding='utf-8', timeout=30, check=False)
            children.append((stage,result))
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode, child, result.stdout, result.stderr)
            return result
        pytest.fail('No process except local Git and validate-only installer children is allowed.')

    def preserved():
        return ((root / 'device.json').read_bytes(), tuple(
            {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            for folder in (home, appdata, root / 'app')))

    monkeypatch.setattr(support,'run',run)
    monkeypatch.setattr(updater, 'snapshot', lambda *args,**kwargs: tmp_path / 'snapshot-not-created')
    monkeypatch.setattr(updater, 'restore', lambda *args: None)
    def controller(python,checkout,selected,command,action,*flags):
        assert selected==root and command=='service' and action in ('inspect','hold','stop','start','finish')
        effects.append('controller-intercepted-'+action)
        if action=='inspect':return {'status':'inspected','task_present':True,'task_enabled':True,'simulated':True}
        return {'status':'stopped' if action=='stop' else action,'empty_at_filetime':123,'simulated':True}
    monkeypatch.setattr(updater,'controller',controller)
    monkeypatch.setattr(updater, 'wait_health', lambda python, root, version,**kwargs: {'status': 'running', 'version': version,'simulated':True})
    before = preserved()
    failure = None
    try:
        updater.perform(root,'v0.1.7',skip_desktop=True)
    except subprocess.CalledProcessError as error:
        failure = error
    assert before == preserved(), 'Validate-only must preserve synthetic runtime/client settings.'
    assert not marker.exists(), 'The allowed untracked cached tomllib.py executed during ' + selected_launch
    assert failure is None and len(children)==2
    selected=[result for stage,result in children if stage==selected_launch]
    assert len(selected)==1 and selected[0].returncode==0
    for _,result in children:assert json.loads(result.stdout)['status']=='validated'
    plans=list((root/'state').glob('upgrade-client-plan-*.json'))
    assert len(plans)==1 and json.loads(plans[0].read_text())['files'], 'Refresh must exercise the locked TOML parser.'
    assert effects.count('provision-intercepted')==1
    assert effects.count('controller-intercepted-inspect')==1
    assert effects.index('controller-intercepted-inspect')<effects.index('controller-intercepted-hold')
