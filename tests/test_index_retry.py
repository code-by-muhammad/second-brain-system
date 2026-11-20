from contextlib import closing
import json
import os
import sqlite3
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
    def run(command,*args,**launch):
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
        assert supervisor._bootstrap_admitted() is None
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
    def run(*args,**launch): raise failure
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,deadline,tick,cleanup,**launch):
        seen.append(command[7:])
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


@pytest.mark.parametrize('retry,full',[(False,False),(True,False),(True,True)],ids=['initial','manual','full'])
def test_fast_first_shard_counts_as_durable_progress(brain,monkeypatch,retry,full):
    from basic_memory.repository.semantic_vector_sync import OVERSIZED_ENTITY_VECTOR_SHARD_SIZE

    config()
    shard_size=OVERSIZED_ENTITY_VECTOR_SHARD_SIZE
    db_path=cfg.STATE/'memory.db'
    with closing(database()) as connection:
        target=add_note(connection,1,'01-Projects/Source.md','word ' * 100_000)
        source=target.read_bytes()
        rows=connection.execute('SELECT * FROM search_vector_chunks ORDER BY id').fetchall()
        pending=rows if full else rows[1:]
        assert 2*shard_size<len(pending)<=3*shard_size
        retained=rows[shard_size][0] if full else rows[0][0]
        connection.execute('DELETE FROM search_vector_chunks WHERE id>?',(retained,))
        connection.execute('DELETE FROM search_vector_embeddings_rowids WHERE rowid>?',(retained,))
        connection.execute('DELETE FROM search_vector_embeddings_auxiliary WHERE rowid>?',(retained,))
        connection.commit()
    assert bootstrap.index.status()['vectors']['invalid']==1

    commands=[]
    def run(command,log,deadline,tick,cleanup,**launch):
        start=len(commands)*shard_size
        commands.append(command[7:])
        with closing(sqlite3.connect(db_path)) as connection:
            if '--full' in command:
                connection.execute('DELETE FROM search_vector_chunks')
                connection.execute('DELETE FROM search_vector_embeddings_rowids')
                connection.execute('DELETE FROM search_vector_embeddings_auxiliary')
            for row in pending[start:start+shard_size]:
                connection.execute('INSERT INTO search_vector_chunks VALUES(?,?,?,?,?,?,?,?,?,?)',row)
                connection.execute('INSERT INTO search_vector_embeddings_rowids(rowid) VALUES(?)',(row[0],))
                connection.execute('INSERT INTO search_vector_embeddings_auxiliary VALUES(?,?)',(row[0],row[5]))
            connection.commit()
        # The child can commit and exit before two progress samples are taken.
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(retry=retry,full=full,overall_seconds=20,stall_seconds=5)
    assert result['status']=='verified'
    assert commands==[['--full'] if full else [],[],[]]
    assert target.read_bytes()==source


@pytest.mark.parametrize('full',[False,True],ids=['plain','full'])
def test_retry_stops_when_more_passes_do_not_help(brain,monkeypatch,full):
    ready()
    seen=[]
    _moving_runner(seen,monkeypatch,embedding=False,observed=not full)
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:_coverage('needs review',invalid=4))
    result=bootstrap.main(full=full,overall_seconds=20,stall_seconds=5)
    assert seen==[['--full'] if full else [],[]] and result['status']=='needs review'


def test_search_rows_lost_to_a_lock_are_rebuilt_once_then_embedded(brain,monkeypatch):
    ready()
    seen=[];lost=_coverage('needs review',invalid=2,zero_chunk_entities=1,stale_search_sources=1)
    reports=iter([lost,_coverage('needs review',invalid=1,zero_chunk_entities=1),_coverage('verified')])
    def run(command,log,deadline,tick,cleanup,**launch):
        seen.append(command[7:])
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,*args,**launch):
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
    def refused(*args,**launch): pytest.fail('Unsafe config must not start indexing.')
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
    def run(command,log,deadline,tick,cleanup,**launch):
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
    def run(command,log,deadline,tick,cleanup,**launch):
        log.write(b'y'*(8*1048576)+EMBEDDING);log.flush()
        for _ in range(12):
            tick();clock[0]+=1;ticks.append(clock[0])  # Rows change at every tick: nothing may end this pass.
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert len(ticks)==12 and result['status']=='verified' and result['process_exit']==0


# ---- committed progress that changes no count -------------------------------------------------------------------

def _edit_in_place(tag):
    """What embedding a shard of an already-counted note does: new hashes, the same rows."""
    with closing(sqlite3.connect(cfg.STATE/'memory.db')) as connection:
        connection.execute('UPDATE search_vector_chunks SET source_hash=?',(tag,))
        connection.execute('UPDATE search_vector_embeddings_auxiliary SET value00=?',(tag,))
        connection.commit()


def _in_place_runner(seen,monkeypatch,reports,change=True):
    monkeypatch.setattr(bootstrap.index,'inventory',lambda check:({'01-Projects/Source.md':{}},'synthetic-manifest'))
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:next(reports))
    monkeypatch.setattr(bootstrap.time,'sleep',lambda seconds:None)
    def run(command,log,deadline,tick,cleanup,**launch):
        seen.append(command[7:])
        if change:_edit_in_place('edit-%d'%len(seen))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)


def test_a_shard_embedded_in_place_counts_as_progress_although_no_count_moves(brain,monkeypatch):
    ready()
    seen=[];reports=iter([_coverage('needs review',invalid=1),_coverage('needs review',invalid=1),_coverage('verified')])
    _in_place_runner(seen,monkeypatch,reports)
    before=bootstrap._counts()
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert bootstrap._counts()==before
    assert seen==[[],[],[]] and result['passes']==3 and result['status']=='verified' and result['process_exit']==0


@pytest.mark.parametrize('failures',[1,3])
def test_a_committed_state_that_cannot_be_read_after_a_pass_is_unknown_not_a_miss(brain,monkeypatch,failures):
    ready()
    seen=[];reports=iter([_coverage('needs review',invalid=1),_coverage('needs review',invalid=1),_coverage('verified')])
    _in_place_runner(seen,monkeypatch,reports)
    read=bootstrap._ready;left=[failures]
    def flaky(deadline=None):
        if seen and left[0]:
            left[0]-=1;raise sqlite3.OperationalError('synthetic transient read failure')
        return read(deadline)
    monkeypatch.setattr(bootstrap,'_ready',flaky)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert left==[0] and seen==[[],[],[]] and result['status']=='verified'


def test_unreadable_state_earns_two_follow_ups_per_run_and_no_more(brain,monkeypatch):
    ready()
    seen=[]
    _in_place_runner(seen,monkeypatch,iter(lambda:_coverage('needs review',invalid=4),None))
    def unreadable(deadline=None):raise sqlite3.OperationalError('synthetic read failure')
    monkeypatch.setattr(bootstrap,'_ready',unreadable)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert seen==[[],[],[]] and result['status']=='needs review'


def test_a_committed_state_seen_before_in_this_run_is_not_progress(brain,monkeypatch):
    ready()
    seen=[]
    _in_place_runner(seen,monkeypatch,iter(lambda:_coverage('needs review',invalid=4),None),change=False)
    flip=iter(['a','b','a','b','a','b','a','b'])
    monkeypatch.setattr(bootstrap,'_ready',lambda deadline=None:next(flip))
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    # Before the run: a. Pass 1 leaves b, which is new and earns a follow-up; pass 2 leaves a again, which is not.
    assert len(seen)==2 and result['status']=='needs review'


# ---- the in-pass watchdog, on a fake clock ----------------------------------------------------------------------

def _watched(monkeypatch,script,*,readable=lambda second:True,ticks=40,budget=8):
    """One pass ticking once a second from 0. script(second) runs before each tick and may commit; returns the
    seconds at which the committed state was read during the pass, and the receipt."""
    ready()
    (cfg.VAULT/'01-Projects/Unindexed.md').write_text('Synthetic unindexed note.',encoding='utf-8')  # Coverage cannot verify.
    clock=[100.];state={'mark':'m0','count':5,'inside':False};reads=[]
    monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(bootstrap.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(bootstrap,'_counts',lambda deadline=None:{'entity':1,'search_vector_embeddings_rowids':state['count']})
    def read(deadline=None):
        second=int(clock[0]-100)
        if state['inside']:reads.append(second)
        if not readable(second if state['inside'] else -1):raise sqlite3.OperationalError('synthetic read failure')
        return state['mark']
    monkeypatch.setattr(bootstrap,'_ready',read)
    def run(command,log,deadline,tick,cleanup,**launch):
        if 'stalled' in state:return SimpleNamespace(returncode=0)  # Only the first pass is the subject.
        log.write(EMBEDDING);log.flush();state.update(inside=True,stalled=None)
        try:
            for second in range(ticks):
                script(second,state);tick();clock[0]+=1
        except bootstrap.IndexStalled:
            state['stalled']=second;raise
        finally:state['inside']=False
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=2000,stall_seconds=budget)
    return reads,result,state['stalled']


def _productive(second,state):
    if second==1:state['count']=6       # Rows change early,
    if second==9:state['mark']='m1'     # then a commit that changes none of them, after the count budget has run out.


def test_same_count_commit_on_the_expiry_tick_is_seen_by_a_read_made_in_that_tick(brain,monkeypatch):
    reads,result,stalled=_watched(monkeypatch,_productive,ticks=12)
    assert stalled is None and result['process_exit']==0 and result.get('error_type')!='IndexStalled'
    assert reads==[0,2,4,6,8,9,11]  # The read at 9 is the expiry read; the schedule moves on from it.


def test_idle_worker_is_stalled_one_budget_after_embedding_starts(brain,monkeypatch):
    reads,result,ended=_watched(monkeypatch,lambda second,state:None)
    assert result['status']=='needs attention' and result['error_type']=='IndexStalled' and result['process_exit'] is None
    assert reads==[0,2,4,6,8] and ended==8


def test_failed_reads_do_not_raise_the_read_rate_and_five_at_expiry_stall(brain,monkeypatch):
    reads,result,ended=_watched(monkeypatch,lambda second,state:None,readable=lambda second:second<0)
    assert result['error_type']=='IndexStalled'
    # Four scheduled attempts in the budget, then one per tick at expiry, five in all.
    assert reads==[0,2,4,6,8,9,10,11,12] and ended==12


def test_first_successful_read_of_a_pass_is_a_baseline_not_a_stall(brain,monkeypatch):
    """Nothing could be read before second 9. A worker that then commits at 9 and again at 20 is never stalled."""
    def script(second,state):
        _productive(second,state)
        if second==16:state['mark']='m2'
    reads,result,stalled=_watched(monkeypatch,script,readable=lambda second:second>=9,ticks=20)
    assert stalled is None and result['process_exit']==0 and result.get('error_type')!='IndexStalled'


def test_idle_worker_is_stalled_one_whole_budget_after_a_late_baseline(brain,monkeypatch):
    reads,result,ended=_watched(monkeypatch,lambda second,state:state.update(count=6) if second==1 else None,
                                readable=lambda second:second>=9)
    assert ended==17  # Baseline at 9, budget 8: a whole budget of observation, not the rest of the first one.


# ---- what the worker's exit status means ------------------------------------------------------------------------

def _no_database(monkeypatch):
    config();(cfg.VAULT/'Fixture.md').write_text('Synthetic fixture.',encoding='utf-8')
    assert not (cfg.STATE/'memory.db').exists()


@pytest.mark.parametrize('database_present',[False,True])
def test_guard_refusal_keeps_its_reason_after_the_tick_has_read_past_it(brain,monkeypatch,database_present):
    ready() if database_present else _no_database(monkeypatch)
    def run(command,log,deadline,tick,cleanup,**launch):
        # Other startup output first, more than one small read; then the tick consumes the log before the exit.
        log.write(b'synthetic startup noise\n'*300+b'index worker: guard_refused:vector_cleanup_not_reviewed\r\n');log.flush()
        tick();tick()
        return SimpleNamespace(returncode=78)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert (result['status'],result['reason'],result['guard_reason'],result['process_exit'])==(
        'needs attention','index_worker_guard_refused','vector_cleanup_not_reviewed',78)
    assert result['guidance']==bootstrap.GUARD_GUIDANCE and 'retry after repair' not in result['error']
    assert (cfg.STATE/'memory.db').exists() is database_present
    assert json.loads((cfg.STATE/'index-bootstrap-status.json').read_text())==result


def test_guard_refusal_without_a_readable_reason_says_so_and_an_earlier_pass_does_not_lend_one(brain,monkeypatch):
    ready()
    passes=[];reports=iter([_coverage('needs review',invalid=1)])
    monkeypatch.setattr(bootstrap.index,'inventory',lambda check:({'01-Projects/Source.md':{}},'synthetic-manifest'))
    monkeypatch.setattr(bootstrap.index,'status',lambda deadline=None:next(reports))
    def run(command,log,deadline,tick,cleanup,**launch):
        passes.append(1)
        if len(passes)==1:
            log.write(b'index worker: guard_refused:basic_memory_missing\n');log.flush();_edit_in_place('moved')
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=78)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert len(passes)==2 and result['reason']=='index_worker_guard_refused' and result['guard_reason']=='guard_reason_unavailable'


def test_linked_worker_refusal_points_at_the_application_files_not_the_environment(brain,monkeypatch):
    ready()
    def run(command,log,deadline,tick,cleanup,**launch):
        log.write(b'index worker: guard_refused:linked_worker_script\n');log.flush()
        return SimpleNamespace(returncode=78)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert result['guard_reason']=='linked_worker_script' and result['guidance']==bootstrap.LINKED_GUIDANCE


@pytest.mark.skipif(os.name=='nt',reason='Symbolic links need a privilege on Windows.')
def test_linked_worker_script_is_refused_before_anything_is_started(brain,monkeypatch,tmp_path):
    ready()
    real=tmp_path/'elsewhere.py';real.write_text('raise SystemExit(0)\n',encoding='utf-8')
    (cfg.ROOT/'app').mkdir();(cfg.ROOT/'app'/'brain_index_worker.py').symlink_to(real)
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:pytest.fail('A linked worker must not be started.'))
    result=bootstrap.main()
    assert (result['status'],result['reason'],result['guidance'])==('needs attention','linked_worker_refused',bootstrap.LINKED_GUIDANCE)
    assert 'process_exit' not in result


def _restore_linked_worker(root):
    """The repair the receipt names, step by step, as "Index worker is a link" in docs/DEVICE-SETUP.md gives it."""
    import hashlib,shutil
    from pathlib import Path
    name='app/brain_index_worker.py'
    release=Path(json.loads((root/'device.json').read_text(encoding='utf-8'))['release_root'])
    listed=json.loads((release/'release-manifest.json').read_text(encoding='utf-8'))['sha256'][name]
    source=release/name;installed=root/name
    # 1. The release copy is a regular file with the listed SHA-256.
    assert not source.is_symlink() and source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()==listed
    # 2. Remove the link itself, never its target.
    assert installed.is_symlink();installed.unlink()
    # 3. Copy the release file into place.
    shutil.copyfile(source,installed)
    # 4. The installed file is a regular file with the same SHA-256.
    assert not installed.is_symlink() and hashlib.sha256(installed.read_bytes()).hexdigest()==listed


@pytest.mark.skipif(os.name=='nt',reason='Symbolic links need a privilege on Windows.')
def test_linked_worker_receipt_names_a_repair_that_works_and_not_the_setup_that_refuses(brain,monkeypatch,tmp_path):
    import hashlib,shutil
    from pathlib import Path
    import setup_device
    ready()
    source=Path(bootstrap.__file__).parent;release=tmp_path/'releases'/'v9.9.9';version='9.9.9'
    shutil.copytree(source,release/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(release/'app',cfg.ROOT/'app')
    (release/'release-manifest.json').write_text(json.dumps({'version':version,'sha256':{
        'app/'+path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (release/'app').glob('*.py')}}),encoding='utf-8')
    (cfg.ROOT/'device.json').write_text(json.dumps({'version':version,'release_root':str(release)}),encoding='utf-8')
    assert setup_device.runtime_matches_release(cfg.ROOT,release,version)
    # The fault: the installed worker is replaced by a link, here to a file with the very same bytes.
    elsewhere=tmp_path/'elsewhere.py';shutil.copyfile(release/'app'/'brain_index_worker.py',elsewhere)
    worker=cfg.ROOT/'app'/'brain_index_worker.py';worker.unlink();worker.symlink_to(elsewhere)
    started=[]
    def run(command,*args,**launch):
        started.append(command);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    refused=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert (refused['status'],refused['reason'],refused['guidance'])==('needs attention','linked_worker_refused',bootstrap.LINKED_GUIDANCE)
    assert started==[]
    # What the receipt used to say is not a repair: setup for the installed version sees the link and changes nothing.
    assert not setup_device.runtime_matches_release(cfg.ROOT,release,version)
    with pytest.raises(SystemExit,match='Re-running this same version does not replace the runtime'):
        setup_device.refuse_same_version_drift(version,release)
    guidance=refused['guidance']
    assert 'running setup for this version again' not in guidance and 'setup for this same version does not replace' in guidance
    for step in ('"'+bootstrap.LINKED_SECTION+'" in docs/DEVICE-SETUP.md','release_root in device.json','release-manifest.json',
                 'remove the link itself, never its target','copy that file to app/brain_index_worker.py','index retry'):
        assert step in guidance
    # The repair it does name: the runtime matches its release again, the link's target is untouched, and the same
    # retry now starts the installed worker and verifies.
    _restore_linked_worker(cfg.ROOT)
    assert setup_device.runtime_matches_release(cfg.ROOT,release,version) and elsewhere.is_file()
    result=bootstrap.main(retry=True,overall_seconds=20,stall_seconds=5)
    assert result['status']=='verified' and [command[3] for command in started]==[str(worker)]
    assert 'guidance' not in result


def test_unreleased_worker_is_recorded_before_any_database_read(brain,monkeypatch):
    _no_database(monkeypatch)
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:SimpleNamespace(returncode=75))
    result=bootstrap.main()
    assert (result['status'],result['reason'],result['process_exit'])==('needs attention','index_worker_not_released',75)
    assert 'guard_reason' not in result and not (cfg.STATE/'memory.db').exists()


def test_ordinary_worker_failure_keeps_its_exit_status_without_a_database(brain,monkeypatch):
    _no_database(monkeypatch)
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:SimpleNamespace(returncode=1))
    result=bootstrap.main()
    assert result['status']=='needs attention' and result['process_exit']==1 and result['reason']=='local_indexing_failed'


def _preflight(monkeypatch):
    ready()
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:pytest.fail('No worker is started for an unusable environment.'))
    monkeypatch.setattr(bootstrap.index,'ensure_history_scope',lambda:pytest.fail('Nothing upstream is used after a refusal.'))
    result=bootstrap.main()
    assert (result['status'],result['reason'],result['guidance'])==('needs attention','index_worker_guard_refused',bootstrap.GUARD_GUIDANCE)
    assert 'process_exit' not in result
    return result['guard_reason']


def test_preflight_names_a_missing_or_unreviewed_distribution(brain,monkeypatch):
    from importlib import metadata
    with monkeypatch.context() as scope:
        def absent(name):raise metadata.PackageNotFoundError(name)
        scope.setattr(metadata,'version',absent)
        assert _preflight(scope)=='basic_memory_missing'
    (cfg.STATE/'memory.db').unlink();(cfg.VAULT/'01-Projects/Source.md').unlink()
    with monkeypatch.context() as scope:
        scope.setattr(metadata,'version',lambda name:'0.24.0')
        assert _preflight(scope)=='basic_memory_version_not_reviewed'


@pytest.mark.parametrize('broken,missing',[('basic_memory.index.filesystem','watchfiles'),('basic_memory.repository.semantic_chunking','basic_memory.repository.semantic_chunking')])
def test_preflight_names_any_import_failure_whatever_module_is_missing(brain,monkeypatch,broken,missing):
    import importlib
    real=importlib.import_module
    def refuse(name,*rest):
        if name==broken:raise ModuleNotFoundError('synthetic: no module named '+missing,name=missing)
        return real(name,*rest)
    monkeypatch.setattr(importlib,'import_module',refuse)
    assert _preflight(monkeypatch)=='basic_memory_import_failed'


def test_import_failure_past_the_preflight_is_still_the_environment(brain,monkeypatch):
    ready()
    def run(*a,**k):raise ImportError('synthetic: a module vanished while running')
    monkeypatch.setattr(bootstrap,'_run_reindex',run)
    result=bootstrap.main()
    assert (result['reason'],result['guard_reason'])==('index_worker_guard_refused','runtime_import_failed')
    assert 'synthetic' not in json.dumps(result)


def test_preflight_list_is_exactly_what_the_index_module_imports_from_basic_memory():
    import ast,inspect
    found=set()
    for node in ast.walk(ast.parse(inspect.getsource(bootstrap.index))):
        if isinstance(node,ast.ImportFrom) and (node.module or '').split('.')[0]=='basic_memory':found.add(node.module)
        if isinstance(node,ast.Import):found.update(alias.name for alias in node.names if alias.name.split('.')[0]=='basic_memory')
    assert found==set(bootstrap.UPSTREAM) and len(bootstrap.UPSTREAM)==len(set(bootstrap.UPSTREAM))
    assert bootstrap._upstream() is None


@pytest.mark.parametrize('result,code',[
    ({'status':'needs attention','reason':'index_worker_guard_refused','guard_reason':'basic_memory_missing'},3),
    ({'status':'needs attention','reason':'index_worker_not_released','process_exit':75},None),
    ({'status':'deferred','reason':'launch_admission_busy'},None),
    ({'status':'needs attention','reason':'linked_worker_refused'},None),
    ({'status':'verified'},None),
])
def test_cli_exit_status_is_3_only_for_a_guard_refusal(monkeypatch,capsys,result,code):
    import sys
    import brain_cli
    monkeypatch.setattr(bootstrap,'main',lambda **options:dict(result))
    monkeypatch.setattr(sys,'argv',['brain_cli.py','index','retry'])
    if code is None:brain_cli.main()
    else:
        with pytest.raises(SystemExit) as ended:brain_cli.main()
        assert ended.value.code==code
    assert json.loads(capsys.readouterr().out)==result  # The report is printed first either way.


def test_receipt_names_both_progress_signals(brain,monkeypatch):
    ready()
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *a,**k:SimpleNamespace(returncode=0))
    result=bootstrap.main()
    assert 'committed vector manifest' in result['progress_signal'] and result['launch_generation'] is None
