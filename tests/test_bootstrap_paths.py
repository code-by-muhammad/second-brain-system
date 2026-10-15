import os
import sqlite3
from types import SimpleNamespace
import pytest
import brain_settings as cfg
import brain_index_bootstrap as bootstrap


@pytest.mark.parametrize('suffix', ['#review', '?review'])
def test_bootstrap_readonly_uri_encodes_runtime_path(brain, monkeypatch, suffix):
    if os.name == 'nt' and '?' in suffix:
        pytest.skip('A question mark is not a valid Windows directory character.')
    state = cfg.STATE.parent / ('state' + suffix)
    state.mkdir()
    monkeypatch.setattr(cfg, 'STATE', state)
    connection = sqlite3.connect(state / 'memory.db')
    try:
        for table in ('entity', 'search_vector_chunks', 'search_vector_embeddings_rowids'):
            connection.execute('CREATE TABLE ' + table + '(id INTEGER)')
            connection.execute('INSERT INTO ' + table + ' VALUES(1)')
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    result = bootstrap.main()
    assert result['status'] == 'verified'
    assert result['counts'] == {table: 1 for table in ('entity', 'search_vector_chunks', 'search_vector_embeddings_rowids')}
