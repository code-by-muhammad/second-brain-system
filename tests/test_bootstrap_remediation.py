import json
import sqlite3
import subprocess
import pytest
import brain_index_bootstrap as bootstrap
import brain_settings as cfg
from test_index_coverage import config

@pytest.fixture(autouse=True)
def source_safe_bootstrap_fixture(brain):
    config()
    (cfg.VAULT/'Fixture.md').write_text('Synthetic fixture.',encoding='utf-8')

def test_missing_binary_persists_a_terminal_failure(brain,monkeypatch):
    monkeypatch.setattr(cfg,'PYTHON',cfg.ROOT/'missing-venv/bin/python')
    result=bootstrap.main()
    assert result['status']=='needs attention' and result['error_type']=='FileNotFoundError'
    assert json.loads((cfg.STATE/'index-bootstrap-status.json').read_text())==result
    assert not (cfg.STATE/'memory.db').exists()

@pytest.mark.parametrize('failure',[subprocess.TimeoutExpired('synthetic bm',7200),OSError('synthetic private path')])
def test_process_failures_leave_terminal_state_without_raw_diagnostics(brain,monkeypatch,failure):
    def fail(*args,**kwargs): raise failure
    monkeypatch.setattr(bootstrap,'_run_reindex',fail)
    result=bootstrap.main()
    assert result['status']=='needs attention' and result['error_type']==type(failure).__name__
    assert 'synthetic' not in json.dumps(result)

def test_missing_tables_leave_terminal_state(brain,monkeypatch):
    with sqlite3.connect(cfg.STATE/'memory.db') as conn: conn.execute('CREATE TABLE unrelated (id INTEGER)')
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k: subprocess.CompletedProcess(a,0))
    result=bootstrap.main()
    assert result['status']=='needs attention' and result['current_index']['error_type']=='OperationalError'

@pytest.mark.parametrize('exit_code,chunks,rows,expected',[(0,3,3,'needs attention'),(1,3,3,'needs attention'),(0,3,2,'needs attention')])
def test_legacy_counts_without_current_coverage_never_verify(brain,monkeypatch,exit_code,chunks,rows,expected):
    with sqlite3.connect(cfg.STATE/'memory.db') as conn:
        for table,count in [('entity',1),('search_vector_chunks',chunks),('search_vector_embeddings_rowids',rows)]:
            conn.execute('CREATE TABLE '+table+' (id INTEGER)')
            conn.executemany('INSERT INTO '+table+' VALUES (?)',[(n,) for n in range(count)])
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k: subprocess.CompletedProcess(a,exit_code))
    result=bootstrap.main()
    assert result['status']==expected and result['process_exit']==exit_code
