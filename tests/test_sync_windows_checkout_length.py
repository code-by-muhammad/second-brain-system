"""Actual Git-for-Windows checkout boundaries in disposable repositories."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git, _raw, _init

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Git-for-Windows UTF-16 checkout boundary.')
INCOMING = b'# Harmless incoming Windows checkout fixture\n'
CAPTURE = '00-Inbox/AI-Captures/Local.md'
CAPTURE_RAW = b'# Synthetic local capture remains owned and preserved\n'


def units(path):
    return len(str(path).encode('utf-16-le'))//2


def _input(repo, *args, data):
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '--literal-pathspecs', '-C', str(repo), *args], input=data, capture_output=True, timeout=10, check=True)
    return result.stdout.strip()


def repo(brain, tmp_path, hooks, setting):
    vault, state = brain
    remote = tmp_path/'remote.git'
    _init(remote, hooks, bare=True)
    _init(vault, hooks)
    if setting != 'unset': _raw(vault, 'config', '--local', 'core.longpaths', setting)
    baseline = vault/'01-Projects/Ordinary.md'
    baseline.parent.mkdir()
    baseline.write_bytes(b'# Baseline\n')
    _raw(vault, 'add', '--', '01-Projects/Ordinary.md')
    _raw(vault, 'commit', '-m', 'synthetic baseline')
    _raw(vault, 'remote', 'add', 'origin', str(remote))
    _raw(vault, 'push', '-u', 'origin', 'HEAD')
    capture = vault/CAPTURE
    capture.parent.mkdir(parents=True)
    capture.write_bytes(CAPTURE_RAW)
    _raw(vault, 'add', '--', CAPTURE)
    _raw(vault, 'commit', '-m', 'synthetic explicitly committed local capture')
    return vault, state, remote


def incoming(vault, remote, relative):
    tree = {}
    for name, raw in [('01-Projects/Ordinary.md', b'# Baseline\n'), (relative, INCOMING)]:
        blob = _input(vault, 'hash-object', '-w', '--stdin', data=raw)
        current = tree
        parts = name.split('/')
        for part in parts[:-1]: current = current.setdefault(part, {})
        current[parts[-1]] = blob
    def build(node):
        rows = []
        for name, value in sorted(node.items()):
            kind = b'040000 tree ' if isinstance(value, dict) else b'100644 blob '
            oid = build(value) if isinstance(value, dict) else value
            rows.append(kind+oid+b'\t'+name.encode('utf-8')+b'\0')
        return _input(vault, 'mktree', '-z', data=b''.join(rows))
    parent = _raw(remote, 'rev-parse', 'refs/heads/main').strip()
    oid = _input(vault, 'commit-tree', build(tree).decode('ascii'), '-p', parent.decode('ascii'), data=b'Synthetic platform boundary incoming tree\n')
    _raw(vault, 'push', 'origin', oid.decode('ascii')+':refs/heads/main')
    return oid


def relative_for(vault, kind, target_units, emoji=False):
    folder = vault/'01-Projects'
    if kind == 'file':
        suffix = '.md'
        fill = target_units-units(folder)-1-units(suffix)-(2 if emoji else 0)
        name = 'x'*fill+('\U0001f680' if emoji else '')+suffix
        relative = '01-Projects/'+name
        assert units(vault/relative) == target_units
    else:
        fill = target_units-units(folder)-1-(2 if emoji else 0)
        name = 'd'*fill+('\U0001f680' if emoji else '')
        relative = '01-Projects/'+name+'/N.md'
        assert units((vault/relative).parent) == target_units
    assert fill > 0 and len(name.encode('utf-8')) <= 255
    return relative


def snapshot(vault, remote):
    return {'head': _raw(vault, 'rev-parse', 'HEAD').decode().strip(),
            'remote': _raw(remote, 'rev-parse', 'refs/heads/main').decode().strip(),
            'index': _raw(vault, 'ls-files', '--stage', '-z').decode(),
            'status': _raw(vault, 'status', '--porcelain', '-z').decode(),
            'local_capture_sha256': hashlib.sha256((vault/CAPTURE).read_bytes()).hexdigest(),
            'config_sha256': hashlib.sha256((vault/'.git/config').read_bytes()).hexdigest()}


CASES = [
    ('file', 259, 'unset', False, False, False),
    ('file', 260, 'unset', False, False, True),
    ('file', 260, 'false', False, False, True),
    ('file', 280, 'true', False, False, False),
    ('directory', 247, 'false', False, False, False),
    ('directory', 248, 'false', False, False, True),
    ('directory', 268, 'true', False, False, False),
    ('file', 260, 'false', True, False, True),
    ('file', 259, 'false', True, False, False),
    ('directory', 248, 'false', True, False, True),
    ('directory', 248, 'false', False, True, False),
]


@pytest.mark.parametrize('kind,target_units,setting,emoji,existing,paused', CASES,
    ids=['file259-default','file260-default','file260-false','file280-true','directory247-false','directory248-false','directory268-true','emoji-file260-false','emoji-file259-false','emoji-directory248-false','existing-directory248-false'])
def test_public_sync_observes_real_device_checkout_limits(brain, tmp_path, monkeypatch, isolated_git, kind, target_units, setting, emoji, existing, paused):
    vault, state, remote = repo(brain, tmp_path, isolated_git, setting)
    relative = relative_for(vault, kind, target_units, emoji)
    if existing: (vault/relative).parent.mkdir()
    oid = incoming(vault, remote, relative)
    before = snapshot(vault, remote)
    commands = []
    original = g._run_git
    def observe(command, **kwargs):
        commands.append(list(command))
        return original(command, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(g, '_run_git', observe)
        try:
            result, error = g.sync(), None
        except g.GitSyncError as exc:
            result, error = None, str(exc)
    after = snapshot(vault, remote)
    observation = {'kind': kind, 'relative': relative, 'file_utf16_units': units(vault/relative), 'file_python_length': len(str(vault/relative)), 'directory_utf16_units': units((vault/relative).parent), 'setting': setting, 'existing_directory': existing, 'result': result, 'error': error, 'before': before, 'after': after, 'commands': commands, 'git_version': _raw(vault, '--version').decode().strip()}
    (tmp_path/'r166-checkout-observation.json').write_text(json.dumps(observation, indent=2)+'\n', encoding='utf-8')
    assert (vault/CAPTURE).read_bytes() == CAPTURE_RAW and before['config_sha256'] == after['config_sha256'], observation
    if paused:
        assert error is None and result.startswith('paused: Incoming path exceeds this Windows device'), observation
        assert json.dumps(relative, ensure_ascii=True) in result, observation
        assert before == after and not (vault/relative).exists(), observation
        assert not any(command[4] in ('merge','merge-tree','push','add','commit','reset') for command in commands), observation
    else:
        assert error is None and result == 'synced', observation
        assert after['head'] == after['remote'] and after['status'] == '', observation
        assert (vault/relative).read_bytes() == INCOMING, observation
        assert _raw(remote, 'show', 'refs/heads/main:'+CAPTURE) == CAPTURE_RAW
