"""A sealed updater plan preserves device edits across deployment staging."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('sealed_device_installer', source / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    support = installer.setup_helpers()
    # Keep the real deploy/atomic/read logic; all runtime and source paths are
    # synthetic and no service/client/model/native ACL process is launched.
    monkeypatch.setattr(installer, 'setup_helpers', lambda: support)
    monkeypatch.setattr(installer, 'private', lambda *args, **kwargs: None)
    def private_dir(path):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(installer, 'private_dir', private_dir)
    root, vault, repo = tmp_path / 'runtime', tmp_path / 'vault', tmp_path / 'source'
    root.mkdir(); vault.mkdir(); (repo / 'app').mkdir(parents=True)
    (root / 'app').mkdir(); (root / 'state').mkdir()
    (root / 'app/probe.py').write_bytes(b'original payload\n')
    payload = b'verified candidate payload\n'
    (repo / 'app/probe.py').write_bytes(payload)
    monkeypatch.setattr(installer, 'REPO', repo)
    old = {'vault': str(vault), 'python': sys.executable, 'device_id': 'synthetic',
           'port': 8765, 'version': '0.1.7', 'role': 'capture',
           'release_root': str(tmp_path / 'previous-source')}
    before = (json.dumps(old, indent=2) + '\n').encode()
    (root / 'device.json').write_bytes(before)
    token = 'synthetic-fixture-only-token'
    (root / 'state/client-access.json').write_text(json.dumps({'token': token}), encoding='utf-8')
    (root / 'state/access.json').write_text(json.dumps({'token_sha256': hashlib.sha256(token.encode()).hexdigest()}), encoding='utf-8')
    plan = {'format': 1, 'root': str(root.resolve()), 'python': sys.executable,
            'files': [], 'device_before_sha256': hashlib.sha256(before).hexdigest()}
    after = (json.dumps(installer.device_settings(old, vault, Path(sys.executable)), indent=2) + '\n').encode()
    plan['device_after_sha256'] = hashlib.sha256(after).hexdigest()
    edited = (json.dumps({**old, 'port': 9876, 'owner_concurrent_edit': 'preserve'}, indent=2) + '\n').encode()
    def deploy():
        return installer.deploy(root, Path(sys.executable), vault, client_value=plan,
            source_sha256={'app/probe.py': hashlib.sha256(payload).hexdigest()})
    return installer, support, root, private_dir, before, after, edited, payload, deploy


def test_unchanged_sealed_device_deploys_expected_bytes(deployment):
    _, _, root, _, _, after, _, payload, deploy = deployment
    result = deploy()
    assert result['status'] == 'installed'
    assert (root / 'device.json').read_bytes() == after
    assert (root / 'app/probe.py').read_bytes() == payload


def test_device_edit_after_sealed_preflight_is_preserved_before_app_switch(deployment, monkeypatch):
    installer, support, root, private_dir, _, _, edited, _, deploy = deployment
    injected = []
    def edit_during_privacy_setup(path):
        private_dir(path)
        if path == root / 'state' and not injected:
            (root / 'device.json').write_bytes(edited)
            injected.append(True)
    monkeypatch.setattr(installer, 'private_dir', edit_during_privacy_setup)
    with pytest.raises(support.ConcurrentWriteError):
        deploy()
    assert injected == [True]
    assert (root / 'device.json').read_bytes() == edited
    assert (root / 'app/probe.py').read_bytes() == b'original payload\n'


def test_device_edit_during_atomic_staging_is_preserved_before_app_switch(deployment, monkeypatch):
    installer, support, root, _, _, _, edited, _, deploy = deployment
    original_atomic, original_read = installer.atomic, support.read
    active = []; reads = []
    def observed_read(path):
        if active and Path(path) == root / 'device.json':
            reads.append(True)
            if len(reads) == 2:
                (root / 'device.json').write_bytes(edited)
        return original_read(path)
    def atomic(path, text, **kwargs):
        if Path(path) != root / 'device.json':
            return original_atomic(path, text, **kwargs)
        active.append(True)
        try:
            return original_atomic(path, text, **kwargs)
        finally:
            active.pop()
    monkeypatch.setattr(support, 'read', observed_read)
    monkeypatch.setattr(installer, 'atomic', atomic)
    with pytest.raises(support.ConcurrentWriteError):
        deploy()
    assert len(reads) == 2
    assert (root / 'device.json').read_bytes() == edited
    assert (root / 'app/probe.py').read_bytes() == b'original payload\n'

@pytest.fixture
def fresh_install(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('fresh_create_installer', source / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    support = installer.setup_helpers()
    monkeypatch.setattr(installer, 'setup_helpers', lambda: support)
    monkeypatch.setattr(installer, '_qualified_platform', lambda: None)
    monkeypatch.setattr(support, 'host_context', lambda: None)
    monkeypatch.setattr(installer, 'private', lambda *args, **kwargs: None)
    monkeypatch.setattr(installer, 'private_dir', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(installer.secrets, 'token_urlsafe', lambda size: 'synthetic-planned-token')
    root, vault, repo = tmp_path / 'runtime', tmp_path / 'vault', tmp_path / 'source'
    vault.mkdir(); (repo / 'app').mkdir(parents=True)
    (repo / 'app/probe.py').write_bytes(b'synthetic exact source only\n')
    for name in ('install.py', 'setup_runtime.py', 'pyproject.toml', 'uv.lock', 'release-manifest.json'):
        (repo / name).write_bytes(b'synthetic retained source identity only\n')
    monkeypatch.setattr(installer, 'REPO', repo)
    monkeypatch.setattr(sys, 'argv', ['install.py', '--root', str(root), '--vault', str(vault),
                                     '--reuse-python', sys.executable])
    # Actual public main owns the install journal and deploy call. Model,
    # service and client integrations are all unselected; no child is launched.
    return installer, support, root, installer.main


def test_fresh_absent_artifacts_match_completed_journal(fresh_install):
    _, _, root, run = fresh_install
    run()
    record = json.loads((root / 'state/install-transaction.json').read_bytes())
    assert record['stage'] == 'complete'
    for relative, expected in record['artifact_sha256'].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
    token = json.loads((root / 'state/client-access.json').read_bytes())['token']
    assert token == 'synthetic-planned-token'
    assert json.loads((root / 'state/access.json').read_bytes())['token_sha256'] == hashlib.sha256(token.encode()).hexdigest()
    assert json.loads((root / 'state/config.json').read_bytes())['semantic_embedding_provider'] == 'fastembed'


@pytest.mark.parametrize('relative', ['state/client-access.json', 'state/access.json',
                                     'state/config.json', 'state/.bmignore'])
def test_fresh_late_created_artifact_is_preserved(fresh_install, monkeypatch, relative):
    installer, support, root, run = fresh_install
    original_atomic = installer.atomic
    owner = b'synthetic-owner-created-bytes-must-survive\n'
    created = []
    def atomic(path, text, **kwargs):
        if Path(path) == root / relative and not created:
            assert not Path(path).exists()
            Path(path).write_bytes(owner)
            created.append(True)
        return original_atomic(path, text, **kwargs)
    monkeypatch.setattr(installer, 'atomic', atomic)
    with pytest.raises(support.ConcurrentWriteError):
        run()
    assert created == [True] and (root / relative).read_bytes() == owner
    record = json.loads((root / 'state/install-transaction.json').read_bytes())
    assert record['stage'] == 'deploying'
