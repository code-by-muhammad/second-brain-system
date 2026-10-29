from contextlib import closing
import json
import subprocess
from types import SimpleNamespace
import pytest
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_index_bootstrap as bootstrap
from test_index_coverage import config,database,add_note


def ready():
    config()
    with closing(database()) as connection: add_note(connection,1,'01-Projects/Source.md')


def test_retry_preserves_prior_receipt_and_uses_full_flag(brain,monkeypatch):
    ready()
    prior=b'{"status":"needs attention","reason":"synthetic"}'
    (cfg.STATE/'index-bootstrap-status.json').write_bytes(prior)
    seen=[]
    def run(command,*args):
        seen.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(retry=True,full=True,overall_seconds=20,stall_seconds=5)
    assert result['status']=='verified' and seen[0][-1]=='--full'
    assert (cfg.STATE/'index-bootstrap-history'/result['history_record']).read_bytes()==prior


def test_active_retry_preserves_status_bytes(brain):
    ready()
    path=cfg.STATE/'index-bootstrap-status.json'
    path.write_bytes(b'{"status":"indexing"}')
    with FileLock(str(cfg.STATE/'index-bootstrap.lock')):
        with pytest.raises(Timeout): bootstrap.main(retry=True)
    assert path.read_bytes()==b'{"status":"indexing"}'
    assert not (cfg.STATE/'index-bootstrap-history').exists()


def test_supervisor_never_launches_against_a_held_bootstrap(brain):
    import brain_supervisor as supervisor
    path=cfg.STATE/'index-bootstrap-status.json'; path.write_bytes(b'{"status":"indexing"}')
    with FileLock(str(cfg.STATE/'index-bootstrap.lock')):
        assert supervisor._bootstrap_admitted() is False
    assert path.read_bytes()==b'{"status":"indexing"}'
    assert supervisor._bootstrap_admitted() is False
    assert json.loads(path.read_text())['reason']=='interrupted_bootstrap'


def test_supervisor_rechecks_terminal_state_under_lock(brain):
    import brain_supervisor as supervisor
    path=cfg.STATE/'index-bootstrap-status.json'; path.write_bytes(b'{"status":"verified"}')
    assert supervisor._bootstrap_admitted() is False
    assert path.read_bytes()==b'{"status":"verified"}'


@pytest.mark.parametrize('failure',[subprocess.TimeoutExpired('synthetic',1),OSError('synthetic private data')])
def test_failures_are_terminal_without_raw_diagnostics(brain,monkeypatch,failure):
    ready()
    def run(*args): raise failure
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main()
    assert result['status']=='needs attention' and result['error_type']==type(failure).__name__
    assert 'synthetic' not in json.dumps(result)
    assert result['stage']=='indexing local notes'


def test_stall_uses_monotonic_db_progress(brain,monkeypatch):
    ready()
    clock=[100.]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    def run(command,log,overall,tick,cleanup):
        tick()
        clock[0]+=6
        tick()
        pytest.fail('A stalled index must be cancelled.')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert result['status']=='needs attention' and result['error_type']=='IndexStalled'
    assert result['elapsed_seconds']==6


def test_missing_vector_tables_do_not_hide_entity_progress(brain,monkeypatch):
    ready()
    with closing(__import__('sqlite3').connect(cfg.STATE/'memory.db')) as connection:
        connection.execute('DROP TABLE search_vector_embeddings_rowids');connection.commit()
    counts=bootstrap._counts()
    assert counts['entity']==1 and counts['search_vector_embeddings_rowids'] is None


def test_basic_memory_environment_cannot_override_source_safety(brain,monkeypatch):
    monkeypatch.setenv('BASIC_MEMORY_ENSURE_FRONTMATTER_ON_SYNC','true')
    monkeypatch.setenv('BASIC_MEMORY_FORMAT_ON_SAVE','true')
    monkeypatch.setenv('BASIC_MEMORY_PROJECT_ROOT','C:/outside')
    monkeypatch.setenv('BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER','openai')
    child=cfg.environment()
    assert child['BASIC_MEMORY_ENSURE_FRONTMATTER_ON_SYNC']=='false'
    assert child['BASIC_MEMORY_FORMAT_ON_SAVE']=='false'
    assert 'BASIC_MEMORY_PROJECT_ROOT' not in child and 'BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER' not in child


def test_retry_rotates_prior_log_before_process_and_clears_cleanup_notice(brain,monkeypatch):
    ready()
    old=cfg.ROOT/'logs/index-bootstrap.log';old.write_bytes(b'synthetic previous diagnostics')
    def run(command,log,overall,tick,cleanup):
        assert old.stat().st_size==0
        assert old.with_name('index-bootstrap.previous.log').read_bytes()==b'synthetic previous diagnostics'
        cleanup()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main()
    assert result['status']=='verified' and 'cleanup' not in result


def test_log_and_history_budgets_preserve_actionable_failure(brain,monkeypatch):
    ready()
    monkeypatch.setattr(bootstrap,'LOG_LIMIT',100)
    def run(command,log,*args):
        log.write(b'x'*101);log.flush()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    assert bootstrap.main()['reason']=='private_log_budget_exceeded'
    path=cfg.STATE/'index-bootstrap-status.json';before=path.read_bytes()
    history=cfg.STATE/'index-bootstrap-history';history.mkdir()
    for number in range(256):(history/(str(number)+'.json')).write_bytes(b'{}')
    with pytest.raises(ValueError,match='status_history_count_limit'):bootstrap.main(retry=True)
    assert path.read_bytes()==before


def test_unsafe_source_mutation_config_refused_before_model(brain,monkeypatch):
    ready()
    value=config(); value['ensure_frontmatter_on_sync']=True
    (cfg.STATE/'config.json').write_text(json.dumps(value),encoding='utf-8')
    def refused(*args): pytest.fail('Unsafe config must not start indexing.')
    monkeypatch.setattr(bootstrap,'_run_reindex',refused)
    result=bootstrap.main()
    assert result['reason']=='note_mutating_config_refused'


@pytest.mark.parametrize('overall,stall',[(0,1),(3,3),(86401,1),(10,0)])
def test_budgets_cannot_be_unbounded(brain,overall,stall):
    with pytest.raises(ValueError): bootstrap.main(overall_seconds=overall,stall_seconds=stall)
