from contextlib import closing
import json
import subprocess
from types import SimpleNamespace
import pytest
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_index_bootstrap as bootstrap
from test_index_coverage import config,database,add_note


EMBEDDING=b'  done Full-text search index rebuilt\n  Building vector embeddings (incremental sync)...\n'


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
        log.write(EMBEDDING);log.flush()
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
        log.write(EMBEDDING);log.flush()
        tick()
        clock[0]+=6
        tick()
        pytest.fail('A stalled index must be cancelled.')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert result['status']=='verified' and result['process_exit'] is None and 'error_type' not in result


def test_text_index_phase_is_not_ended_by_the_count_rule_and_the_count_clock_starts_with_embedding(brain,monkeypatch):
    """Measured: a full rebuild of the text index changes none of the counted tables for many minutes while it works."""
    ready()
    (cfg.VAULT/'01-Projects/Unindexed.md').write_text('Synthetic unindexed note.',encoding='utf-8')
    clock=[100.];reached=[]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    def run(command,log,overall,tick,cleanup):
        log.write(b'  Rebuilding full-text search index (full project index)...\n');log.flush()
        tick();clock[0]+=1000;tick();reached.append('text phase survived')
        # The marker arrives after more output than one read takes and split across two reads; the tick that
        # sees it is 30 seconds after the one before, and still starts the count clock instead of ending the run.
        log.write(b'x'*(1048576-10)+b'  Building vector ');log.flush();clock[0]+=30;tick()  # One read ends inside the marker.
        log.write(b'embeddings (full rebuild)...\n');log.flush();clock[0]+=30;tick();reached.append('marker found')
        clock[0]+=4;tick();reached.append('within the stall budget')
        clock[0]+=2;tick()
        pytest.fail('A stalled embedding phase must be cancelled.')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(retry=True,full=True,overall_seconds=5000,stall_seconds=5)
    assert reached==['text phase survived','marker found','within the stall budget']
    assert result['status']=='needs attention' and result['error_type']=='IndexStalled'


def test_text_index_phase_has_its_own_budget_and_an_earlier_pass_does_not_lend_its_marker(brain,monkeypatch):
    ready()
    (cfg.VAULT/'01-Projects/Unindexed.md').write_text('Synthetic unindexed note.',encoding='utf-8')
    clock=[100.];passes=[]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(bootstrap,'TEXT_PHASE_SECONDS',50)
    counts=iter(range(1,10000))
    monkeypatch.setattr(bootstrap,'_counts',lambda deadline=None:{'entity':next(counts),'search_vector_embeddings_rowids':5})
    def run(command,log,overall,tick,cleanup):
        passes.append(len(passes)+1)
        if len(passes)==1:
            # The first pass reaches embedding and changes rows, so another pass follows.
            log.write(EMBEDDING);log.flush();tick();tick()
            return SimpleNamespace(returncode=0)
        # The second writes nothing: the marker still in the log belongs to the first pass.
        tick();clock[0]+=49;tick();passes.append('inside the text budget')
        clock[0]+=2;tick()
        pytest.fail('A text phase past its budget must be cancelled.')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(retry=True,overall_seconds=5000,stall_seconds=5)
    assert passes==[1,2,'inside the text budget']
    assert result['status']=='needs attention' and result['error_type']=='IndexStalled'


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


def test_text_phase_budget_leaves_time_to_read_coverage_inside_a_short_overall_budget(brain,monkeypatch):
    """A complete index whose program never leaves the text phase was verified in v0.2.10 and still is."""
    ready()
    clock=[100.]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    def run(command,log,overall,tick,cleanup):
        for _ in range(80):
            tick();clock[0]+=1
        pytest.fail('A text phase past its budget must be cancelled.')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    # Reading coverage takes time too: twelve seconds here, which fitted after the stall budget in v0.2.10.
    actual=bootstrap.index.status
    def slow(deadline=None):
        clock[0]+=12;return actual(deadline=deadline)
    monkeypatch.setattr(bootstrap.index,'status',slow)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert result['status']=='verified' and result['elapsed_seconds']==17
    # With room to spare the phase gets a quarter of the run, not just the stall budget.
    clock[0]=100.
    assert bootstrap.main(overall_seconds=200,stall_seconds=5)['elapsed_seconds']==50+12



def test_marker_behind_a_long_log_is_found_in_one_tick_so_a_progressing_pass_is_not_judged_as_text_phase(brain,monkeypatch):
    ready()
    clock=[100.];counts=iter(range(1,100000));ticks=[]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(bootstrap,'_counts',lambda deadline=None:{'entity':next(counts),'search_vector_embeddings_rowids':5})
    def run(command,log,overall,tick,cleanup):
        log.write(b'y'*(8*1048576)+EMBEDDING);log.flush()
        for _ in range(12):
            tick();clock[0]+=1;ticks.append(clock[0])  # Rows change at every tick: nothing may end this pass.
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert len(ticks)==12 and result['status']=='verified' and result['process_exit']==0
