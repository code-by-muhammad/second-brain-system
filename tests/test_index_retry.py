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
    # A second note that was never indexed: coverage cannot verify, so the stall stays a failure.
    (cfg.VAULT/'01-Projects/Unindexed.md').write_text('Synthetic unindexed note.',encoding='utf-8')
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


def test_stall_after_complete_work_is_verified_by_coverage(brain,monkeypatch):
    """Upstream ends a run with a slow orphan cleanup that changes no row; coverage decides."""
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
    assert result['status']=='verified' and result['process_exit'] is None and 'error_type' not in result


def _coverage(status,**vectors):
    return {'status':status,'inventory_stable':True,'coverage':{'manifest_sha256':'synthetic-manifest','missing':0,'stale':0},
            'vectors':{'invalid':0,'zero_chunk_entities':0,'stale_search_sources':0,'chunks':10,**vectors}}


def _moving_runner(seen,monkeypatch,embedding=True,observed=True):
    """Each child run changes row counts, as a real pass does; embedding says whether vectors are among them."""
    monkeypatch.setattr(bootstrap.index,'inventory',lambda check:({'01-Projects/Source.md':{}},'synthetic-manifest'))
    counts=iter(range(1,10000))
    def rows(deadline=None):
        value=next(counts)
        return {'entity':value,'search_vector_embeddings_rowids':value if embedding else 5}
    monkeypatch.setattr(bootstrap,'_counts',rows)
    def run(command,log,overall,tick,cleanup):
        seen.append(command[4:])
        if observed is True or (observed=='first' and len(seen)==1):tick();tick()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)


def test_retry_repeats_while_a_large_note_is_still_being_embedded(brain,monkeypatch):
    ready()
    seen=[];reports=iter([_coverage('needs review',invalid=5),_coverage('needs review',invalid=2),_coverage('verified')])
    _moving_runner(seen,monkeypatch)
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:next(reports))
    result=bootstrap.main(retry=True,full=True,overall_seconds=20,stall_seconds=5)
    assert seen==[['--full'],[],[]] and result['passes']==3 and result['status']=='verified'


def test_one_large_note_stays_one_defect_while_its_chunks_grow(brain,monkeypatch):
    """256 chunks per run: the defect count does not move, the chunk count does."""
    ready()
    seen=[];reports=iter([_coverage('needs review',invalid=1) for number in range(6)]+[_coverage('verified')])
    # After the first pass a child may finish between two samples: progress is measured, not only observed.
    _moving_runner(seen,monkeypatch,observed='first')
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:next(reports))
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert len(seen)==7 and result['status']=='verified'


def test_retry_stops_when_more_passes_do_not_help(brain,monkeypatch):
    ready()
    seen=[]
    _moving_runner(seen,monkeypatch,embedding=False)
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:_coverage('needs review',invalid=4))
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert seen==[[],[]] and result['status']=='needs review'  # Two passes without measured gain end the run.


def test_search_rows_lost_to_a_lock_are_rebuilt_once_then_embedded(brain,monkeypatch):
    ready()
    seen=[];lost=_coverage('needs review',invalid=2,zero_chunk_entities=1,stale_search_sources=1)
    reports=iter([lost,_coverage('needs review',invalid=1,zero_chunk_entities=1),_coverage('verified')])
    def run(command,log,overall,tick,cleanup):
        seen.append(command[4:])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    monkeypatch.setattr(bootstrap.index,'inventory',lambda check:({'01-Projects/Source.md':{}},'synthetic-manifest'))
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:next(reports))
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert seen==[[],['--full','--search'],[]] and result['status']=='verified'


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
    report=bootstrap.main(retry=True)
    assert report['status']=='needs attention' and report['reason']=='status_history_count_limit' and report['prior_status_preserved']
    assert json.loads((cfg.STATE/'index-bootstrap-attention.json').read_text())==report
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
