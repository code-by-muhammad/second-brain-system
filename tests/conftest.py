import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid

# Establish the boundary before any product import can create local state.
_real_home = Path.home()
_base = Path(os.environ.get('SB_TEST_SANDBOX', ('C:/SB-Test' if os.name == 'nt' else tempfile.gettempdir()) + '/sb-tests-' + uuid.uuid4().hex))
os.environ['SB_TEST_SANDBOX'] = str(_base)
from isolation_guard import install, validate_sandbox
_base = validate_sandbox(_base, _real_home, Path(__file__).resolve().parents[1])
_base.mkdir(parents=True, exist_ok=True)
os.environ['SB_TEST_SANDBOX'] = str(_base)
for key in list(os.environ):
    if key.startswith(('BRAIN_', 'BASIC_MEMORY_', 'GIT_')) or (key.startswith('R') and key.endswith('_SOURCE')):
        os.environ.pop(key)


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port != 8765
    return port


def _environment(base):
    home = base / 'home'
    values = dict(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / 'AppData/Roaming'),
                  LOCALAPPDATA=str(home / 'AppData/Local'), XDG_CONFIG_HOME=str(home / '.config'),
                  BRAIN_SYSTEM_ROOT=str(base / 'runtime'), BRAIN_STATE=str(base / 'runtime/state'),
                  BRAIN_VAULT=str(base / 'vault'), BASIC_MEMORY_CONFIG_DIR=str(base / 'runtime/state'),
                  FASTEMBED_CACHE_PATH=str(base / 'models'), HF_HOME=str(base / 'hf'),
                  GNUPGHOME=str(home / '.gnupg'),XDG_CACHE_HOME=str(home / '.cache'),
                  TEMP=str(home / 'tmp'),TMP=str(home / 'tmp'),TMPDIR=str(home / 'tmp'),
                  BRAIN_PORT=str(_port()), HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                  GIT_ALLOW_PROTOCOL='file', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(base / 'gitconfig'),
                  GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', PYTHONDONTWRITEBYTECODE='1')
    for name in ('HOME', 'APPDATA', 'LOCALAPPDATA', 'BRAIN_STATE', 'BRAIN_VAULT','TEMP','GNUPGHOME'):
        Path(values[name]).mkdir(parents=True, exist_ok=True)
    return values


os.environ.update(_environment(_base / 'session'))
tempfile.tempdir=os.environ['TEMP']
install()
import pytest
import brain_settings as cfg
import brain_store as store
import brain_service as service


def pytest_configure(config):
    if not config.option.basetemp:
        raise pytest.UsageError('Use --basetemp beneath the explicit SB_TEST_SANDBOX.')
    if config.option.basetemp:
        target = Path(config.option.basetemp).resolve()
        if target == _base or not target.is_relative_to(_base):
            raise pytest.UsageError('--basetemp must be a child of SB_TEST_SANDBOX, never the sandbox root.')


@pytest.fixture(autouse=True)
def isolated_host(tmp_path_factory, monkeypatch):
    before = dict(os.environ)
    values = _environment(tmp_path_factory.mktemp('host'))
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    home = Path(values['HOME'])
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(tempfile,'tempdir',values['TEMP'])
    root, state, vault = (Path(values[key]) for key in ('BRAIN_SYSTEM_ROOT', 'BRAIN_STATE', 'BRAIN_VAULT'))
    (root / 'logs').mkdir()
    for module in (cfg, store):
        for key, value in (('ROOT', root), ('STATE', state), ('VAULT', vault)):
            monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(cfg, 'PORT', int(values['BRAIN_PORT']))
    monkeypatch.setattr(cfg, 'PYTHON', Path(sys.executable))
    identity = uuid.uuid4().hex
    monkeypatch.setattr(service, 'TASK', 'SecondBrain-Test-' + identity)
    monkeypatch.setattr(service, 'LABEL', 'local.second-brain.test.' + identity)
    monkeypatch.setattr(service, 'LINUX_UNIT', 'second-brain-test-' + identity + '.service')
    server = sys.modules.get('brain_server')
    if server is not None:
        monkeypatch.setattr(server, 'ROOT', root)
    yield home
    # Product imports may update os.environ outside monkeypatch.
    os.environ.clear()
    os.environ.update(before)

@pytest.fixture
def brain(tmp_path,monkeypatch):
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; state=root/'state'
    state.mkdir(parents=True); vault.mkdir(); (root/'logs').mkdir()
    for module in (cfg,store):
        monkeypatch.setattr(module,'ROOT',root); monkeypatch.setattr(module,'STATE',state); monkeypatch.setattr(module,'VAULT',vault)
    monkeypatch.setattr(cfg,'ROLE','maintainer'); monkeypatch.setattr(cfg,'DEVICE_ID','test-host')
    return vault,state
