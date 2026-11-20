"""Memory limits apply at the pinned loader without rewriting current config."""
import json
import os
import subprocess
import sys

import pytest

import brain_settings as cfg


def load_config(env, *, save=False):
    program = '''
import json, sys
from basic_memory.config import ConfigManager
manager = ConfigManager()
config = manager.load_config()
if sys.argv[1] == 'save':
    manager.save_config(config)
print(json.dumps(dict((key, getattr(config, key)) for key in sys.argv[2:])))
'''
    child = subprocess.run([sys.executable, '-I', '-B', '-c', program, 'save' if save else 'read',
                            *cfg.SEARCH_MEMORY_LIMITS], capture_output=True, check=True,
                           text=True, env=env, timeout=30)
    return json.loads(child.stdout)


def test_current_config_load_uses_caps_and_preserves_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, 'STATE', tmp_path)
    monkeypatch.setenv('BASIC_MEMORY_INDEX_BATCH_SIZE', '2048')
    monkeypatch.setenv('BASIC_MEMORY_MCP_PROJECT', 'foreign')
    path = tmp_path / 'config.json'
    original = b'{ "index_batch_size": 3, "semantic_embedding_parallel": 12, "index_batch_max_bytes": true, "index_parse_max_concurrent": "invalid" }\n'
    path.write_bytes(original)
    stale = tmp_path / 'config.json.tmp'
    stale.write_bytes(b'stale temporary file')
    env = cfg.environment()
    assert 'BASIC_MEMORY_MCP_PROJECT' not in env
    expected = {**cfg.SEARCH_MEMORY_LIMITS, 'index_batch_size': 3}
    assert load_config(env) == expected
    assert path.read_bytes() == original
    assert stale.read_bytes() == b'stale temporary file'


def test_legacy_config_migrates_as_in_pinned_loader_with_only_caps_added(monkeypatch, tmp_path):
    original = json.dumps({'projects': {'brain': str(cfg.VAULT)}, 'default_project': 'brain',
                           'index_batch_size': 3, 'semantic_embedding_parallel': 4}).encode()
    snapshots = []
    for mode in ('plain', 'capped'):
        state = tmp_path / mode
        state.mkdir()
        monkeypatch.setattr(cfg, 'STATE', state)
        path = state / 'config.json'
        path.write_bytes(original)
        env = cfg.environment()
        if mode == 'plain':
            for key in cfg.SEARCH_MEMORY_LIMITS:
                env.pop('BASIC_MEMORY_' + key.upper())
        load_config(env)
        assert path.with_suffix('.json.bak').read_bytes() == original
        snapshots.append(json.loads(path.read_bytes()))
    plain, capped = snapshots
    assert capped == {**plain, **cfg.SEARCH_MEMORY_LIMITS, 'index_batch_size': 3}


def test_explicit_upstream_save_can_persist_effective_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, 'STATE', tmp_path)
    path = tmp_path / 'config.json'
    path.write_text('{"index_batch_size": 500}', encoding='utf-8')
    effective = load_config(cfg.environment(), save=True)
    saved = json.loads(path.read_bytes())
    assert effective == cfg.SEARCH_MEMORY_LIMITS
    assert {key: saved[key] for key in effective} == effective


def test_failed_deploy_keeps_existing_config_bytes():
    import install

    config = cfg.STATE / 'config.json'
    original = b'{"index_batch_size": 300, "default_project": "brain"}\r\n'
    config.write_bytes(original)
    with pytest.raises(ValueError, match='Prepared application differs'):
        install.deploy(cfg.ROOT, sys.executable, cfg.VAULT,
                       source_sha256={'app/brain_settings.py': '0' * 64})
    assert config.read_bytes() == original
    assert not (cfg.ROOT / 'device.json').exists()


@pytest.mark.parametrize(('contents', 'message'), [
    (b'x' * 1_000_001, 'size bound'),
    (b'{', 'valid local JSON'),
    (b'{"semantic_embedding_provider": "remote"}', 'Only local FastEmbed'),
    (b'{"reranker_enabled": true}', 'Only local FastEmbed'),
])
def test_invalid_or_unsupported_config_refuses_before_startup(monkeypatch, tmp_path, contents, message):
    monkeypatch.setattr(cfg, 'STATE', tmp_path)
    path = tmp_path / 'config.json'
    path.write_bytes(contents)
    with pytest.raises(ValueError, match=message):
        cfg.environment()
    assert path.read_bytes() == contents


@pytest.mark.skipif(os.name != 'posix', reason='requires native POSIX symlink creation')
def test_linked_config_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, 'STATE', tmp_path)
    other = tmp_path / 'elsewhere.json'
    other.write_bytes(b'{}')
    (tmp_path / 'config.json').symlink_to(other)
    with pytest.raises(ValueError, match='Linked search configuration'):
        cfg.environment()
    assert other.read_bytes() == b'{}'


def test_child_environment_caps_numeric_libraries():
    env = cfg.environment()
    assert env['OMP_NUM_THREADS'] == '1'
    assert env['MKL_NUM_THREADS'] == '1'
    assert env['TOKENIZERS_PARALLELISM'] == 'false'
