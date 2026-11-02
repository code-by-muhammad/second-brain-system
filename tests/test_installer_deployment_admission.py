"""A sealed settings plan must never authorize an existing runtime deployment."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize('version', ['0.1.7', '0.1.8'])
@pytest.mark.parametrize('options', [[], ['--download-model'], ['--install-service']])
def test_public_client_plan_cannot_redeploy_existing_runtime(tmp_path, monkeypatch, version, options):
    source = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('admission_installer', source / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root, vault = tmp_path / 'runtime', tmp_path / 'vault'
    vault.mkdir()
    root.mkdir()
    device = {'vault': str(vault), 'python': sys.executable,
              'device_id': 'synthetic-interrupted-or-live', 'version': version}
    (root / 'device.json').write_text(json.dumps(device), encoding='utf-8')
    plan = {'format': 1, 'root': str(root), 'python': sys.executable,
            'refresh': True, 'files': []}
    raw = json.dumps(plan).encode()
    path = tmp_path / 'settings-plan.json'
    path.write_bytes(raw)
    support = installer.setup_helpers()
    monkeypatch.setattr(support, 'host_context', lambda: None)
    monkeypatch.setattr(installer, 'setup_helpers', lambda: support)
    def forbidden(*args, **kwargs):
        pytest.fail('A settings plan reached runtime mutation without an updater.')
    monkeypatch.setattr(installer, 'deploy', forbidden)
    monkeypatch.setattr(support, 'provision', forbidden)
    monkeypatch.setattr(support, 'service_python', forbidden)
    monkeypatch.setattr(sys, 'argv', ['install.py', '--root', str(root), '--vault', str(vault),
        '--reuse-python', sys.executable, '--refresh-clients', '--client-plan', str(path),
        '--plan-sha256', hashlib.sha256(raw).hexdigest(), *options])
    before = (root / 'device.json').read_bytes()
    with pytest.raises(ValueError, match='approved update.py'):
        installer.main()
    assert (root / 'device.json').read_bytes() == before
