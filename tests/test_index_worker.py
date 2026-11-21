"""The index worker: its reviewed pin, its replacement cleanup, its refusals and its start gate. All data is synthetic."""
import asyncio
import hashlib
import inspect
import os
from importlib import metadata
from pathlib import Path
import random
import signal
import sqlite3
import subprocess
import sys
import time
import pytest
import brain_index_bootstrap as bootstrap
import brain_index_worker as worker
import brain_process as processes

WORKER=Path(worker.__file__)
ORIGINAL_SQL=("DELETE FROM search_vector_embeddings WHERE rowid IN (SELECT id FROM search_vector_chunks "
              "WHERE project_id = :project_id AND NOT (vector_index = 'sqlite-vec' AND embedding_model = :embedding_identity "
              "AND search_vector_embeddings.source_hash = search_vector_chunks.source_hash AND embedding_status = 'ready'))")
PARAMS={'project_id':1,'embedding_identity':'selected'}


def _adapter_class():
    from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex
    return SQLiteVecIndex


@pytest.fixture
def patched(monkeypatch):
    """The worker's method on the real class for one test; the reviewed original is put back afterwards."""
    adapter=_adapter_class();original=adapter.__dict__['delete_orphans']
    monkeypatch.setattr(adapter,'delete_orphans',original)
    worker.install()
    assert adapter.__dict__['delete_orphans'] is not original
    return original


def test_locked_dependency_is_the_reviewed_version_and_method():
    assert metadata.version('basic-memory')==worker.REVIEWED_VERSION
    source=inspect.getsource(_adapter_class().__dict__['delete_orphans'])
    assert hashlib.sha256(source.encode()).hexdigest()==worker.REVIEWED_SHA256
    # The statement that was replaced is the one this method still carries.
    assert 'search_vector_embeddings.source_hash' in source and 'await session.commit()' in source


def test_worker_bootstrap_and_process_matcher_agree_on_the_contract():
    assert bootstrap.REVIEWED_BASIC_MEMORY==worker.REVIEWED_VERSION
    assert (bootstrap.GUARD_EXIT,bootstrap.UNRELEASED_EXIT,bootstrap.RELEASE)==(worker.GUARD_EXIT,worker.UNRELEASED_EXIT,worker.RELEASE)==(78,75,b'start\n')
    assert bootstrap.GUARD_REASONS==worker.REASONS and len(worker.REASONS)==7
    assert bootstrap.GUARD_MARKER==b'index worker: guard_refused:'
    assert processes.INDEX_ARGUMENTS==worker.ARGUMENTS and processes.INDEX_WORKER==WORKER.name
    # The launcher's window always ends before the worker stops waiting.
    assert bootstrap.GRANT_SECONDS<worker.RELEASE_SECONDS==bootstrap.RELEASE_SECONDS


def test_worker_imports_no_application_module():
    """-I removes the script's folder from the import path, so a brain_* import could never succeed."""
    import ast
    names=set()
    for node in ast.walk(ast.parse(WORKER.read_text(encoding='utf-8'))):
        if isinstance(node,ast.Import):names.update(alias.name for alias in node.names)
        if isinstance(node,ast.ImportFrom):names.add(node.module or '')
    assert not [name for name in names if name.startswith('brain_')]


# ---- the replacement cleanup, through the shipped engine --------------------------------------------------------

def _connect(path):
    import sqlite_vec
    connection=sqlite3.connect(path)
    connection.enable_load_extension(True);sqlite_vec.load(connection);connection.enable_load_extension(False)
    return connection


def _schema(connection):
    connection.execute('PRAGMA journal_mode=WAL')
    connection.executescript("""
        CREATE TABLE search_vector_chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
          chunk_key TEXT NOT NULL, source_hash TEXT NOT NULL, embedding_model TEXT NOT NULL,
          vector_index TEXT NOT NULL, embedding_status TEXT NOT NULL);
        CREATE INDEX idx_search_vector_chunks_project_entity ON search_vector_chunks (project_id, entity_id);
        CREATE VIRTUAL TABLE search_vector_embeddings USING vec0(embedding float[4], +source_hash text);
    """)


def _chunks(connection,rows):
    connection.executemany('INSERT INTO search_vector_chunks(id,entity_id,chunk_key,project_id,source_hash,embedding_model,'
                           'vector_index,embedding_status) VALUES (?,1,?,?,?,?,?,?)',
                           ((ident,str(ident),project,source,model,backend,status) for ident,project,source,model,backend,status in rows))


def _vectors(connection,rows):
    connection.executemany("INSERT INTO search_vector_embeddings(rowid,embedding,source_hash) VALUES (?,'[0,1,0,0]',?)",rows)


def _left(connection):
    return connection.execute('SELECT rowid,source_hash FROM search_vector_embeddings ORDER BY rowid').fetchall()


async def _adapter(path):
    from basic_memory import db
    from basic_memory.repository.semantic_vector_index import VectorIndexScope
    from sqlalchemy.ext.asyncio import async_sessionmaker
    engine=db._create_sqlite_engine('sqlite+aiosqlite:///'+Path(path).as_posix(),db.DatabaseType.FILESYSTEM)
    adapter=_adapter_class()(async_sessionmaker(engine,expire_on_commit=False),VectorIndexScope('test',1,'selected',4))
    await adapter.initialize()
    return engine,adapter


def test_cleanup_keeps_a_vector_another_writer_refreshed_and_commits_once(tmp_path,patched):
    from sqlalchemy import event
    path=tmp_path/'refresh.db'
    writer=_connect(path);_schema(writer)
    _chunks(writer,[(1,1,'old','selected','sqlite-vec','pending'),(2,1,'h','selected','sqlite-vec','ready')])
    _vectors(writer,[(1,'old'),(2,'h'),(99,'gone')]);writer.commit()
    state={'done':False,'commits':0,'deletes':0}
    async def scenario():
        engine,adapter=await _adapter(path)
        @event.listens_for(engine.sync_engine,'before_cursor_execute')
        def before(connection,cursor,statement,*rest):
            if 'DELETE FROM search_vector_embeddings' not in statement:return
            state['deletes']+=1
            if state['done']:return
            # Another writer finishes chunk 1 after this session opened and before its first write.
            writer.execute("UPDATE search_vector_chunks SET source_hash='fresh',embedding_status='ready' WHERE id=1")
            writer.execute('DELETE FROM search_vector_embeddings WHERE rowid=1');_vectors(writer,[(1,'fresh')])
            writer.commit();state['done']=True
        @event.listens_for(engine.sync_engine,'commit')
        def committed(connection):state['commits']+=1
        try:await adapter.delete_orphans(())
        finally:await engine.dispose()
    asyncio.run(scenario())
    try:
        # The refreshed vector survives, the ownerless one is gone, and both statements were one transaction.
        assert state=={'done':True,'commits':1,'deletes':2}
        assert _left(writer)==[(1,'fresh'),(2,'h')]
    finally:writer.close()


def test_cleanup_failure_rolls_back_both_deletes(tmp_path,patched):
    from sqlalchemy import event
    path=tmp_path/'rollback.db'
    writer=_connect(path);_schema(writer)
    _chunks(writer,[(1,1,'h','selected','sqlite-vec','ready')]);_vectors(writer,[(1,'h'),(99,'gone')]);writer.commit()
    async def scenario():
        engine,adapter=await _adapter(path)
        @event.listens_for(engine.sync_engine,'before_cursor_execute')
        def before(connection,cursor,statement,*rest):
            if 'JOIN search_vector_embeddings AS vectors' in statement:raise RuntimeError('synthetic failure in the second delete')
        try:
            with pytest.raises(RuntimeError,match='synthetic failure'):await adapter.delete_orphans(())
        finally:await engine.dispose()
    asyncio.run(scenario())
    try:assert [row[0] for row in _left(writer)]==[1,99]  # The first delete was not kept.
    finally:writer.close()


def test_cleanup_leaves_the_same_rows_as_the_reviewed_method_and_has_no_correlated_step(tmp_path,patched):
    generator=random.Random(7);chunks=[];vectors=[]
    for ident in range(1,1501):
        kind=generator.choice(['ok']*8+['hash','pending','model','index','sibling_ok','sibling_bad','novec'])
        project=2 if kind.startswith('sibling') else 1
        chunks.append((ident,project,'h%d'%ident,'other' if kind=='model' else 'selected',
                       'x' if kind=='index' else 'sqlite-vec','pending' if kind=='pending' else 'ready'))
        if kind!='novec':vectors.append((ident,'stale' if kind in ('hash','sibling_bad') else 'h%d'%ident))
    # Ownerless vectors belong to no project; the first statement removes them whatever project runs it.
    vectors+=[(ident,'gone') for ident in range(5000,5040)]
    left={}
    async def run(name):
        engine,adapter=await _adapter(tmp_path/(name+'.db'))
        try:await (patched(adapter,()) if name=='reviewed' else adapter.delete_orphans(()))
        finally:await engine.dispose()
    for name in ('reviewed','worker'):
        connection=_connect(tmp_path/(name+'.db'));_schema(connection);_chunks(connection,chunks);_vectors(connection,vectors);connection.commit()
        asyncio.run(run(name))
        left[name]=_left(connection)
        if name=='worker':
            for statement in (worker.ORPHANS,worker.STALE):
                plan=[row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+statement,PARAMS if ':project_id' in statement else {})]
                assert plan and not any('CORRELATED' in step for step in plan),plan
            reviewed_plan=[row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+ORIGINAL_SQL,PARAMS)]
            assert any('CORRELATED' in step for step in reviewed_plan)  # What made the reviewed statement quadratic.
        connection.close()
    assert left['reviewed']==left['worker'] and len(left['worker'])<len(vectors)
    kept={row[0] for row in left['worker']}
    assert not kept&set(range(5000,5040))
    # A stale vector of another project is not this project's to remove.
    assert any(ident in kept for ident,project,*_ in chunks if project==2 and (ident,'stale') in vectors)


# ---- refusals ---------------------------------------------------------------------------------------------------

def _refusal(monkeypatch,capsys,arguments=('reindex','-p','brain')):
    monkeypatch.setattr(worker,'released',lambda:True)
    monkeypatch.setattr(sys,'argv',[str(WORKER),*arguments])
    code=worker.main()
    return code,capsys.readouterr().err


def _restore_method(monkeypatch):
    adapter=_adapter_class();monkeypatch.setattr(adapter,'delete_orphans',adapter.__dict__['delete_orphans'])
    return adapter


def test_missing_distribution_is_refused_by_name(monkeypatch,capsys):
    def absent(name):raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata,'version',absent)
    assert _refusal(monkeypatch,capsys)==(78,'index worker: guard_refused:basic_memory_missing\n')


def test_another_version_is_refused_before_any_upstream_import(monkeypatch,capsys):
    monkeypatch.setattr(metadata,'version',lambda name:'0.24.0')
    import builtins
    real=builtins.__import__
    def refuse(name,*rest):
        if name.split('.')[0] in ('basic_memory','sqlalchemy'):pytest.fail('Nothing upstream is imported for an unreviewed version.')
        return real(name,*rest)
    monkeypatch.setattr(builtins,'__import__',refuse)
    assert _refusal(monkeypatch,capsys)==(78,'index worker: guard_refused:basic_memory_version_not_reviewed\n')


@pytest.mark.parametrize('module',['basic_memory.repository.sqlite_vec_index','basic_memory.db','sqlalchemy','basic_memory.cli.main','watchfiles'])
def test_unimportable_module_or_cli_dependency_is_refused_by_name(monkeypatch,capsys,module):
    _restore_method(monkeypatch)
    import builtins
    import basic_memory.cli.main as cli
    monkeypatch.setattr(cli,'app',lambda:pytest.fail('A refused worker must not index.'))
    real=builtins.__import__;parent,_,child=module.rpartition('.');blocked=[]
    def refuse(name,globals=None,locals=None,fromlist=(),level=0):
        # Only what the worker itself asks for: its two import statements name these modules directly.
        asked=sys._getframe(1).f_globals.get('__name__')==worker.__name__
        if asked and (name==module or (name==parent and child in (fromlist or ())) or (module=='watchfiles' and name=='basic_memory.cli.main')):
            blocked.append(name);raise ImportError('synthetic: '+module+' cannot be imported')
        return real(name,globals,locals,fromlist,level)
    monkeypatch.setattr(builtins,'__import__',refuse)
    assert _refusal(monkeypatch,capsys)==(78,'index worker: guard_refused:basic_memory_import_failed\n')
    assert blocked


def test_missing_unreadable_or_changed_cleanup_is_refused_by_name(monkeypatch,capsys):
    adapter=_restore_method(monkeypatch);original=adapter.__dict__['delete_orphans']
    with monkeypatch.context() as scope:
        scope.delattr(adapter,'delete_orphans')
        assert _refusal(scope,capsys)==(78,'index worker: guard_refused:vector_cleanup_missing\n')
    with monkeypatch.context() as scope:
        def unreadable(value):raise OSError('synthetic: source is not available')
        scope.setattr(inspect,'getsource',unreadable)
        assert _refusal(scope,capsys)==(78,'index worker: guard_refused:vector_cleanup_unreadable\n')
    with monkeypatch.context() as scope:
        async def delete_orphans(self,_live_keys):return None
        scope.setattr(adapter,'delete_orphans',delete_orphans)
        assert _refusal(scope,capsys)==(78,'index worker: guard_refused:vector_cleanup_not_reviewed\n')
        assert adapter.__dict__['delete_orphans'] is delete_orphans  # Nothing was replaced by a refusal.
    assert adapter.__dict__['delete_orphans'] is original


def test_reviewed_build_is_patched_and_the_pinned_cli_is_called_as_bm(monkeypatch,capsys):
    adapter=_restore_method(monkeypatch);original=adapter.__dict__['delete_orphans']
    import basic_memory.cli.main as cli
    called=[]
    monkeypatch.setattr(cli,'app',lambda:called.append(list(sys.argv)) or 0)
    code,errors=_refusal(monkeypatch,capsys,('reindex','-p','brain','--full','--search'))
    assert (code,errors)==(0,'') and called==[['bm','reindex','-p','brain','--full','--search']]
    assert adapter.__dict__['delete_orphans'] is not original


def _run(arguments,*,script=WORKER,stdin=subprocess.DEVNULL,data=None,environment=None,timeout=60):
    options={'input':data} if data is not None else {'stdin':stdin}
    return subprocess.run([sys.executable,'-I','-B',str(script),*arguments],capture_output=True,timeout=timeout,
                          env=dict(os.environ if environment is None else environment),**options)


@pytest.mark.parametrize('arguments',[[],['reindex'],['reindex','-p','main'],['reindex','-p','brain','--search'],
    ['reindex','-p','brain','--full','--search','--extra'],['sync'],['reindex','-p','brain','--embeddings'],['--help']])
def test_unsupported_arguments_exit_2_before_the_gate_and_before_basic_memory(tmp_path,arguments):
    probe=tmp_path/'probe.py'
    probe.write_text('import runpy,sys\nsys.argv=[sys.argv[1]]+sys.argv[2:]\n'
                     'try:runpy.run_path(sys.argv[0],run_name="__main__")\n'
                     'except SystemExit as error:code=error.code\n'
                     'print("upstream" if any(name.split(".")[0] in ("basic_memory","sqlalchemy") for name in sys.modules) else "clean")\n'
                     'sys.exit(code)\n',encoding='utf-8')
    # Standard input holds the start line: it is the arguments, not the gate, that stop this.
    result=subprocess.run([sys.executable,'-I','-B',str(probe),str(WORKER),*arguments],capture_output=True,input=worker.RELEASE,timeout=60,env=dict(os.environ))
    assert result.returncode==2 and result.stdout.strip()==b'clean'
    assert b'unsupported arguments' in result.stderr


def _isolated(tmp_path):
    home=tmp_path/'home';state=tmp_path/'state';home.mkdir();state.mkdir()
    environment={**os.environ,'HOME':str(home),'USERPROFILE':str(home),'BASIC_MEMORY_CONFIG_DIR':str(state)}
    return environment,lambda:sorted(str(path) for folder in (home,state) for path in folder.rglob('*'))


@pytest.mark.parametrize('line',[None,b'',b'start',b'START\n',b'start \n',b'go\n',b'start\r\n',b'\nstart\n'])
def test_without_the_exact_start_line_nothing_is_imported_or_created(tmp_path,line):
    environment,created=_isolated(tmp_path)
    started=time.monotonic()
    result=_run(['reindex','-p','brain'],environment=environment) if line is None else _run(['reindex','-p','brain'],data=line,environment=environment)
    assert result.returncode==75 and result.stderr.replace(b'\r\n',b'\n')==b'index worker: not_released\n' and result.stdout==b''
    assert created()==[] and time.monotonic()-started<20


def test_an_unreleased_worker_never_reaches_the_guard(monkeypatch,capsys):
    class Ended(BaseException):pass
    def ended(code):raise Ended(code)
    monkeypatch.setattr(worker,'released',lambda:False)
    monkeypatch.setattr(worker,'install',lambda:pytest.fail('The guard imports Basic Memory; it must not run.'))
    monkeypatch.setattr(worker.os,'_exit',ended)
    monkeypatch.setattr(sys,'argv',[str(WORKER),'reindex','-p','brain'])
    with pytest.raises(Ended) as caught:worker.main()
    assert caught.value.args==(75,) and capsys.readouterr().err=='index worker: not_released\n'


def test_start_line_passes_the_gate_and_a_changed_method_is_refused_in_a_real_process(tmp_path):
    """A copy whose reviewed digest is wrong: it is released, reaches the guard, and refuses with exit 78."""
    copy=tmp_path/'brain_index_worker.py'
    source=WORKER.read_text(encoding='utf-8')
    assert source.count(worker.REVIEWED_SHA256)==1
    copy.write_text(source.replace(worker.REVIEWED_SHA256,'0'*64),encoding='utf-8')
    environment,_=_isolated(tmp_path)
    result=_run(['reindex','-p','brain'],script=copy,data=worker.RELEASE,environment=environment,timeout=180)
    assert result.returncode==78
    assert b'index worker: guard_refused:vector_cleanup_not_reviewed' in result.stderr


@pytest.mark.skipif(os.name=='nt',reason='Symbolic links need a privilege on Windows; the junction check is the same statement.')
def test_linked_worker_script_is_refused_before_the_gate(tmp_path):
    link=tmp_path/'brain_index_worker.py';link.symlink_to(WORKER)
    result=_run(['reindex','-p','brain'],script=link,data=worker.RELEASE)
    assert result.returncode==78 and result.stderr==b'index worker: guard_refused:linked_worker_script\n'


@pytest.mark.skipif(os.name=='nt',reason='On Windows the Job, not the pipe, ends a worker whose launcher died.')
def test_worker_whose_launcher_is_killed_before_release_ends_having_done_nothing(tmp_path):
    environment,created=_isolated(tmp_path)
    log=tmp_path/'worker.log';pid_file=tmp_path/'worker.pid'
    launcher=tmp_path/'launcher.py'
    launcher.write_text('import subprocess,sys,time\n'
                        'log=open(sys.argv[2],"ab")\n'
                        'child=subprocess.Popen([sys.executable,"-I","-B",sys.argv[1],"reindex","-p","brain"],stdin=subprocess.PIPE,stdout=log,stderr=log,start_new_session=True)\n'
                        'open(sys.argv[3],"w").write(str(child.pid))\n'
                        'time.sleep(120)\n',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-I','-B',str(launcher),str(WORKER),str(log),str(pid_file)],env=environment)
    try:
        deadline=time.monotonic()+30
        while not (pid_file.exists() and pid_file.read_text()):
            assert process.poll() is None and time.monotonic()<deadline
            time.sleep(.02)
        pid=int(pid_file.read_text())
        time.sleep(.5)
        os.kill(pid,0)  # Still waiting for its start line.
        assert log.read_bytes()==b''
        process.kill();process.wait(timeout=10)
        while True:
            try:os.kill(pid,0)
            except ProcessLookupError:break
            assert time.monotonic()<deadline,'The worker outlived its launcher.'
            time.sleep(.02)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        if pid_file.exists() and pid_file.read_text():
            try:os.kill(int(pid_file.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
    assert log.read_bytes()==b'index worker: not_released\n' and created()==[]
