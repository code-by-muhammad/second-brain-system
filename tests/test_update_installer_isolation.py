"""Cached installer imports use a real synthetic venv; deployment effects are intercepted."""
import contextlib
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
from types import SimpleNamespace
import venv

import filelock
import pytest
import tomlkit


SOURCE = Path(os.environ.get('R149_SOURCE', Path(__file__).resolve().parents[1])).resolve()


def test_public_update_preflight_and_owned_deploy_do_not_import_untracked_checkout_module(tmp_path, monkeypatch):
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
    previous=root/'old-release';(previous/'app').mkdir(parents=True)
    (previous/'app/original.py').write_bytes((root/'app/original.py').read_bytes())
    required = ['install.py', 'update.py', 'pyproject.toml', 'uv.lock']
    for name in required:
        (previous/name).write_bytes(b'# synthetic predecessor evidence\n')
    (root / 'device.json').write_text(json.dumps({'vault': str(vault), 'role': 'capture',
        'python': sys.executable, 'release_root': str(previous), 'version': '0.1.6',
        'device_id':'synthetic-isolation','service_name':'SecondBrain-Test-'+uuid.uuid4().hex}), encoding='utf-8')
    release = root / 'releases/v0.1.7'
    release.mkdir(parents=True)
    names = [*required, '.gitignore', 'setup_runtime.py', 'docs/AI-PROTOCOL.md',
             'app/brain_release.py', 'app/brain_connection.py']
    for name in names:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        source = (SOURCE / name).read_bytes()
        if name == 'install.py':
            source, replaced = re.subn(rb"(?m)^VERSION='[\d.]+'$", b"VERSION='0.1.7'", source)
            assert replaced == 1
        target.write_bytes(source)
    (release / 'app/brain_cli.py').write_bytes(b'# service calls are intercepted, never executed\n')
    names.append('app/brain_cli.py')
    git_env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    git_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='',
                   GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')

    def git(checkout, *arguments):
        return real_run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
            '-c', 'core.autocrlf=false', '-C', str(checkout), *arguments], env=git_env,
            capture_output=True, check=True, timeout=30)

    def tagged(checkout, version, names, capabilities=()):
        hashes = {name: hashlib.sha256((checkout / name).read_bytes()).hexdigest() for name in names}
        (checkout / 'release-manifest.json').write_text(json.dumps({'version': version,
            'repository': 'code-by-muhammad/second-brain-system', 'capabilities': capabilities,
            'sha256': hashes}), encoding='utf-8', newline='\n')
        git(checkout, 'init', '--initial-branch=main')
        git(checkout, 'config', 'user.name', 'Synthetic Installer Isolation')
        git(checkout, 'config', 'user.email', 'installer-isolation@example.invalid')
        git(checkout, 'config', 'commit.gpgsign', 'false')
        git(checkout, 'remote', 'add', 'origin', updater.REMOTE)
        git(checkout, 'add', '--all')
        git(checkout, 'commit', '-m', 'Synthetic tagged installer')
        git(checkout, '-c', 'tag.gpgsign=false', 'tag', 'v' + version)

    tagged(previous, '0.1.6', [*required, 'app/original.py'])
    tagged(release, '0.1.7', names, ['sealed-client-refresh-v1', 'owned-service-control-v1'])
    environment = release / '.venv'
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
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
    monkeypatch.setattr(install,'VERSION','0.1.7')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls: home))
    monkeypatch.setattr(support,'host_context',lambda:None)
    monkeypatch.setattr(support,'service_python',lambda selected: None)
    def provision(*args):effects.append('provision-intercepted');return python
    monkeypatch.setattr(support,'provision',provision)
    def deploy(selected,selected_python,selected_vault,*,client_value,source_sha256,sealed,release_tools):
        assert selected==root and selected_python==python and selected_vault==vault
        support.validate_plan(client_value,root,python)
        # The in-process deploy entry point uses the already sealed plan, not
        # a parser import from the checkout. Real deploy/locks are exercised by
        # the upgrade-transaction fixture; this test isolates import provenance.
        effects.append('owned-deploy-intercepted')
        monkeypatch.setattr(install,'REPO',release)
        before=json.loads((root/'device.json').read_text())
        install.atomic(root/'device.json',json.dumps(install.device_settings(before,vault,python),indent=2)+'\n')
        return {'status':'installed','simulated':True}
    monkeypatch.setattr(install,'deploy',deploy)

    def run(command, **kwargs):
        if Path(command[0]).name.lower() in ('git', 'git.exe'):
            assert 'clone' not in command
            return real_run(command, **kwargs)
        if str(release / 'install.py') in command:
            assert '--validate-only' in command, 'Existing-runtime deployment must not use a public installer child.'
            stage = 'preflight'
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
    monkeypatch.setattr(updater, 'restore', lambda *args: None)
    registration={'kind':sys.platform,'definition_sha256':'a'*64,'enabled':True,'interpreter':sys.executable}
    if sys.platform=='win32':registration.update(xml='<Task/>',owner_sid='S-1-5-21-101-102-103-1001')
    transaction=root/'state/service-transaction.json'
    def controller(python,checkout,selected,command,action,*flags):
        assert selected==root and command=='service' and action in ('inspect','hold','stop','install','start','finish')
        effects.append('controller-intercepted-'+action)
        if action=='inspect':return {'status':'inspected','task_present':True,'task_enabled':True,'simulated':True,'registration':dict(registration)}
        if action=='hold':
            saved=Path(flags[flags.index('--snapshot')+1])
            transaction.write_text(json.dumps({'hold_id':flags[flags.index('--hold-id')+1],'root':str(root),
                'snapshot':str(saved),'snapshot_sha256':hashlib.sha256((saved/'snapshot.json').read_bytes()).hexdigest(),
                'stage':'prepared'}))
        elif action=='stop':
            transaction.write_text(json.dumps({**json.loads(transaction.read_bytes()),'stage':'stopped'}))
        elif action=='finish':
            transaction.unlink()
            return {'status':'transaction released','hold_id':flags[flags.index('--hold-id')+1]}
        return {'status':'stopped' if action=='stop' else action,'empty_at_filetime':123,'simulated':True}
    monkeypatch.setattr(updater,'controller',controller)
    monkeypatch.setattr(updater, 'wait_health', lambda python, root, version,**kwargs: {'status': 'running', 'version': version,'simulated':True})
    before = preserved()
    failure = None
    try:
        updater.perform(root,'v0.1.7',skip_desktop=True)
    except subprocess.CalledProcessError as error:
        failure = error
    after=preserved()
    snapshots=list((root/'backups').glob('upgrade-*/snapshot.json'))
    assert len(snapshots)==1
    sealed=json.loads(snapshots[0].read_bytes())
    assert sealed['format']==3 and sealed['registration']==registration
    assert (root/'state/previous-release.json').read_bytes()==(snapshots[0].parent/'previous-binding.after').read_bytes()
    assert before[1:]==after[1:], 'The isolated preflight/deploy control must preserve owner clients/app.'
    assert not marker.exists(), 'The allowed untracked cached tomllib.py executed.'
    assert failure is None and len(children)==1
    assert children[0][1].returncode==0
    for _,result in children:assert json.loads(result.stdout)['status']=='validated'
    plans=list((root/'state').glob('upgrade-client-plan-*.json'))
    assert len(plans)==1 and json.loads(plans[0].read_text())['files'], 'Refresh must exercise the locked TOML parser.'
    assert hashlib.sha256(after[0]).hexdigest()==json.loads(plans[0].read_text())['device_after_sha256']
    assert effects.count('provision-intercepted')==1
    assert effects.count('controller-intercepted-inspect')==1
    assert effects.index('controller-intercepted-inspect')<effects.index('controller-intercepted-hold')
    assert effects.index('controller-intercepted-stop')<effects.index('owned-deploy-intercepted')<effects.index('controller-intercepted-install')
