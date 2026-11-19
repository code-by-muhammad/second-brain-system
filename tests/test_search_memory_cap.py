"""Index memory knobs stay small. No model is loaded."""
import json

import brain_settings as cfg


def test_existing_config_is_lowered_without_dropping_other_keys(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'config.json').write_text(json.dumps({
        'semantic_embedding_provider': 'fastembed',
        'semantic_embedding_threads': 4,
        'semantic_embedding_batch_size': 8,
        'reranker_enabled': False,
        'default_project': 'brain',
    }), encoding='utf-8')
    assert cfg.clamp_search_memory(state) is True
    data = json.loads((state / 'config.json').read_text(encoding='utf-8'))
    assert data['semantic_embedding_threads'] == 1
    assert data['semantic_embedding_batch_size'] == 1
    assert data['semantic_embedding_parallel'] == 1
    assert data['index_batch_size'] == 8
    assert data['index_parse_max_concurrent'] == 1
    assert data['default_project'] == 'brain'
    assert data['reranker_enabled'] is False
    assert cfg.clamp_search_memory(state) is False


def test_child_environment_caps_numeric_libraries():
    env = cfg.environment()
    assert env['OMP_NUM_THREADS'] == '1'
    assert env['MKL_NUM_THREADS'] == '1'
    assert env['TOKENIZERS_PARALLELISM'] == 'false'
