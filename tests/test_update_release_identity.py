"""Public updater cache provenance; all Git data and process effects are synthetic."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SOURCE = Path(os.environ.get('R148_SOURCE', Path(__file__).resolve().parents[1])).resolve()
REMOTE = 'https://github.com/code-by-muhammad/second-brain-system.git'
VERSION = 'v0.1.7'


class ReachedDependencyPreparation(RuntimeError):
    pass


@pytest.fixture
def release_case(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('identity_update', SOURCE / 'update.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    real_run = subprocess.run
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='file')
    root = tmp_path / 'synthetic runtime'
    root.mkdir()
    (root / 'device.json').write_text(json.dumps({'vault': str(tmp_path / 'synthetic vault'),
        'python': 'synthetic-old-python', 'release_root': 'synthetic-old-release',
        'version': '0.1.6', 'role': 'capture'}), encoding='utf-8')
    release = root / 'releases' / VERSION
    effects = []
    git_calls = []
    clone_source = None

    def git(repo, *args):
        result = real_run(['git', '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false', '-C', str(repo), *args],
            env=environment, capture_output=True, check=True, timeout=30)
        return result.stdout

    def manifest(repo):
        hashes = {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in repo.rglob('*') if p.is_file() and '.git' not in p.relative_to(repo).parts
                  and p.name != 'release-manifest.json'}
        (repo / 'release-manifest.json').write_text(json.dumps({'version': '0.1.7',
            'repository': 'code-by-muhammad/second-brain-system',
            'capabilities':['sealed-client-refresh-v1','owned-service-control-v1'],'sha256': hashes}),
            encoding='utf-8', newline='\n')

    def make(repo, origin=REMOTE, annotated=False):
        repo.mkdir(parents=True)
        for name in ('install.py', 'setup_runtime.py', 'update.py', 'pyproject.toml', 'uv.lock', 'app/brain_cli.py'):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# synthetic release bytes; never executed\n')
        (repo / '.gitignore').write_bytes(b'.venv/\n')
        manifest(repo)
        git(repo, 'init', '--initial-branch=main')
        git(repo, 'config', 'user.name', 'Synthetic Release Identity')
        git(repo, 'config', 'user.email', 'release-identity@example.invalid')
        git(repo, 'config', 'commit.gpgsign', 'false')
        git(repo, 'config', 'tag.gpgsign', 'false')
        git(repo, 'remote', 'add', 'origin', origin)
        git(repo, 'add', '--all')
        git(repo, 'commit', '-m', 'Synthetic approved release')
        git(repo, 'tag', *(['-a', '-m', 'Synthetic approved tag'] if annotated else []), VERSION)
        return git(repo, 'rev-parse', 'HEAD').decode().strip()

    def run(command, **kwargs):
        nonlocal clone_source
        if Path(command[0]).name.lower() in ('git', 'git.exe'):
            if 'clone' in command:
                assert clone_source is not None and command[-2] == REMOTE
                effects.append('clone')
                # The expected HTTPS clone is intercepted with a local fixture clone.
                result = real_run(['git', '-c', 'core.hooksPath=' + os.devnull,
                    '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false',
                    'clone', '--branch', VERSION, '--', str(clone_source), str(release)],
                    env=environment, capture_output=True, check=True, timeout=30)
                git(release, 'remote', 'set-url', 'origin', REMOTE)
                return result
            git_calls.append(command)
            child = kwargs['env']
            assert child['GIT_ALLOW_PROTOCOL'] == '' and child['GIT_OPTIONAL_LOCKS'] == '0'
            assert child['GIT_NO_REPLACE_OBJECTS'] == '1' and child['GIT_CONFIG_NOSYSTEM'] == '1'
            assert 'GIT_DIR' not in child and 'GIT_WORK_TREE' not in child
            assert 'core.fsmonitor=false' in command and kwargs['timeout'] <= 30
            return real_run(command, **kwargs)
        effects.append('uv' if command[0] == 'uv' else 'unexpected-child')
        if command[0] == 'uv':
            raise ReachedDependencyPreparation('Synthetic dependency preparation intercepted.')
        raise AssertionError('No cached Python, service, client or network child may run.')

    def invoke(seed=None):
        nonlocal clone_source
        clone_source = seed
        monkeypatch.setattr(updater.subprocess, 'run', run)
        helper=updater.installer();support=helper.setup_helpers()
        def provision(*args):
            effects.append('uv')
            raise ReachedDependencyPreparation('Synthetic dependency preparation intercepted.')
        monkeypatch.setattr(support,'provision',provision)
        monkeypatch.setattr(support,'run',run)
        monkeypatch.setattr(helper,'setup_helpers',lambda:support)
        monkeypatch.setattr(updater,'installer',lambda:helper)
        monkeypatch.setattr(updater,'__file__',str(release/'update.py'))
        monkeypatch.setattr(updater, 'update_lock', lambda _: contextlib.nullcontext())
        monkeypatch.setattr(sys, 'argv', ['update.py', '--root', str(root), '--version', VERSION])
        monkeypatch.setenv('GIT_DIR', str(tmp_path / 'unrelated git directory'))
        monkeypatch.setenv('GIT_WORK_TREE', str(tmp_path / 'unrelated worktree'))
        updater.main()

    return updater, root, release, effects, git_calls, git, manifest, make, invoke


@pytest.mark.parametrize('kind', ['wrong-head', 'missing-tag', 'missing-git', 'wrong-origin',
                                  'payload-and-manifest', 'manifest-only', 'staged-index'])
def test_public_update_rejects_self_consistent_wrong_cache(release_case, kind):
    updater, root, release, effects, _, git, manifest, make, invoke = release_case
    make(release)
    if kind == 'wrong-head':
        (release / 'app/brain_cli.py').write_bytes(b'# later main build, same version\n')
        manifest(release)
        git(release, 'add', '--all')
        git(release, 'commit', '-m', 'Synthetic later main build')
    elif kind == 'missing-tag':
        git(release, 'tag', '-d', VERSION)
    elif kind == 'missing-git':
        # Preserve the metadata as evidence instead of deleting a checkout.
        (release / '.git').rename(release / 'saved-git-metadata')
    elif kind == 'wrong-origin':
        git(release, 'remote', 'set-url', 'origin', 'https://github.com/other/other.git')
    elif kind == 'payload-and-manifest':
        (release / 'app/brain_cli.py').write_bytes(b'# uncommitted generated release\n')
        manifest(release)
    elif kind == 'manifest-only':
        path = release / 'release-manifest.json'
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        path = release / 'app/brain_cli.py'
        original = path.read_bytes()
        path.write_bytes(b'# staged bytes differ from both HEAD and worktree\n')
        git(release, 'add', '--', 'app/brain_cli.py')
        path.write_bytes(original)
    # BEFORE's documented standalone manifest check accepts every fixture.
    assert updater.verify(release)['version'] == '0.1.7'
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='[Cc]ached release'):
        invoke()
    assert effects == []
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('origin', [REMOTE, 'git@github.com:code-by-muhammad/second-brain-system.git',
                                   'ssh://git@github.com/code-by-muhammad/second-brain-system.git'],
                         ids=['https', 'scp-ssh', 'ssh-url'])
@pytest.mark.parametrize('annotated', [False, True], ids=['lightweight', 'annotated'])
def test_public_update_reuses_valid_local_tag(release_case, origin, annotated):
    _, _, release, effects, _, git, _, make, invoke = release_case
    make(release, origin, annotated)
    # Unrelated local files and the ignored native environment are not release payload.
    (release / '.venv').mkdir()
    (release / '.venv/local-marker').write_bytes(b'local environment, not shipped')
    (release / 'owner-validation.txt').write_bytes(b'local report, not shipped')
    git(release, 'config', 'core.fsmonitor', 'unexpected-fsmonitor-command')
    with pytest.raises(ReachedDependencyPreparation):
        invoke()
    assert effects == ['uv']


@pytest.mark.parametrize('annotated', [False, True], ids=['lightweight', 'annotated'])
def test_public_update_verifies_fresh_clone_before_dependencies(release_case, tmp_path, annotated):
    _, _, _, effects, _, _, _, make, invoke = release_case
    seed = tmp_path / 'local approved source'
    make(seed, annotated=annotated)
    with pytest.raises(ReachedDependencyPreparation):
        invoke(seed)
    assert effects == ['clone', 'uv']
