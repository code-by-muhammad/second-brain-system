"""A count match is not file or vector coverage; all data is synthetic."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest
import brain_settings as cfg
import brain_index_bootstrap as bootstrap


def config():
    value = dict(projects={'brain': {'path': str(cfg.VAULT), 'mode': 'local'}}, project_root=str(cfg.VAULT),
                 semantic_search_enabled=True, semantic_embedding_provider='fastembed',
                 semantic_embedding_model='bge-small-en-v1.5', semantic_embedding_dimensions=384,
                 disable_permalinks=True, ensure_frontmatter_on_sync=False, format_on_save=False, auto_update=False)
    (cfg.STATE / 'config.json').write_text(json.dumps(value), encoding='utf-8')
    return value


def database():
    connection = sqlite3.connect(cfg.STATE / 'memory.db')
    connection.executescript('''
      CREATE TABLE project(id INTEGER, name TEXT, path TEXT, is_active INTEGER);
      CREATE TABLE entity(id INTEGER, project_id INTEGER, file_path TEXT, checksum TEXT, content_type TEXT,title TEXT,permalink TEXT);
      CREATE TABLE search_index(entity_id INTEGER,id INTEGER,type TEXT,title TEXT,permalink TEXT,content_snippet TEXT,category TEXT,relation_type TEXT,project_id INTEGER);
      CREATE TABLE search_vector_chunks(id INTEGER,entity_id INTEGER,project_id INTEGER,chunk_key TEXT,chunk_text TEXT,source_hash TEXT,entity_fingerprint TEXT,embedding_model TEXT,vector_index TEXT,embedding_status TEXT);
      CREATE TABLE search_vector_embeddings_rowids(rowid INTEGER PRIMARY KEY,id,chunk_id INTEGER,chunk_offset INTEGER);
      CREATE TABLE search_vector_embeddings_auxiliary(rowid INTEGER PRIMARY KEY,value00);
    ''')
    connection.execute('INSERT INTO project VALUES(1,?,?,1)', ('brain', str(cfg.VAULT)))
    return connection


def add_note(connection, ident, path, content='Synthetic note.'):
    from basic_memory.repository.semantic_chunking import build_vector_chunk_records, build_entity_fingerprint
    target = cfg.VAULT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    connection.execute('INSERT INTO entity VALUES(?,1,?,?,?,?,NULL)', (ident, path, hashlib.sha256(target.read_bytes()).hexdigest(), 'text/markdown',path))
    row = SimpleNamespace(id=ident, type='entity', title=path, permalink=None, content_snippet=content, category=None, relation_type=None)
    connection.execute('INSERT INTO search_index VALUES(?,?,?,?,?,?,?,?,1)', (ident, ident, 'entity', path, None, content, None, None))
    chunks = build_vector_chunk_records([row]).records
    fingerprint = build_entity_fingerprint(chunks)
    for offset, chunk in enumerate(chunks):
        key = ident*1000+offset
        connection.execute('INSERT INTO search_vector_chunks VALUES(?,?,1,?,?,?,?,?,?,?)',
            (key, ident, chunk['chunk_key'], chunk['chunk_text'], chunk['source_hash'], fingerprint,
             'FastEmbedEmbeddingProvider:bge-small-en-v1.5:384', 'sqlite-vec', 'ready'))
        connection.execute('INSERT INTO search_vector_embeddings_rowids(rowid) VALUES(?)', (key,))
        connection.execute('INSERT INTO search_vector_embeddings_auxiliary VALUES(?,?)', (key, chunk['source_hash']))
    connection.commit()
    return target


def test_partial_index_cannot_be_verified_by_bootstrap(brain, monkeypatch):
    config()
    with closing(database()) as connection:
        add_note(connection, 1, '01-Projects/Starter.md')
    (cfg.VAULT / '01-Projects/Imported.md').write_text('Missing imported source.', encoding='utf-8')
    monkeypatch.setattr(bootstrap, '_run_reindex', lambda *a, **k: SimpleNamespace(returncode=0))
    result = bootstrap.main()
    assert result['status'] != 'verified'


def test_complete_coverage_and_current_hash(brain):
    import brain_index as index
    config()
    with closing(database()) as connection:
        note = add_note(connection, 1, '01-Projects/Source.md')
    before = (cfg.STATE / 'memory.db').read_bytes()
    first = index.status()
    assert first['status'] == 'verified' and first['coverage']['missing'] == 0
    original = note.stat()
    assert len(b'Changed source!') == original.st_size
    note.write_bytes(b'Changed source!')
    os.utime(note, ns=(original.st_atime_ns, original.st_mtime_ns))
    second = index.status()
    assert second['status'] != 'verified' and second['coverage']['stale'] == 1
    assert (cfg.STATE / 'memory.db').read_bytes() == before


@pytest.mark.parametrize('field,bad', [('backend','postgres'),('backend',None),('backend','SQLite'),
                                    ('mode','cloud'),('mode',None),('path','.'),('project_root','.')])
def test_unsupported_database_routing_or_relative_paths_refuse_before_reindex(brain,monkeypatch,field,bad):
    import brain_index as index
    value=config()
    with closing(database()) as connection: add_note(connection,1,'Source.md')
    before=(cfg.STATE/'memory.db').read_bytes()
    if field=='backend': value['database_backend']=bad
    elif field in ('mode','path'): value['projects']['brain'][field]=bad
    else: value[field]=bad
    (cfg.STATE/'config.json').write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.chdir(cfg.VAULT)
    def unexpected(*args,**kwargs): pytest.fail('Unsupported configuration must not launch reindex')
    monkeypatch.setattr(bootstrap,'_run_reindex',unexpected)
    result=index.status()
    assert result['status']=='needs attention'
    expected='project_config_path' if field in ('path','project_root') else 'unsupported_index_backend_or_routing'
    assert result['reason']==expected
    assert bootstrap.main()['status']!='verified'
    assert (cfg.STATE/'memory.db').read_bytes()==before


def test_absent_backend_and_mode_use_pinned_local_sqlite_defaults(brain):
    import brain_index as index
    value=config();value['projects']['brain'].pop('mode')
    (cfg.STATE/'config.json').write_text(json.dumps(value),encoding='utf-8')
    with closing(database()) as connection: add_note(connection,1,'Source.md')
    before=(cfg.STATE/'memory.db').read_bytes()
    assert index.status()['status']=='verified'
    assert (cfg.STATE/'memory.db').read_bytes()==before


@pytest.mark.parametrize('mutation', ['missing', 'wrong-vector-hash', 'wrong-vector-id', 'pending', 'wrong-model', 'fingerprint', 'no-chunks', 'foreign-project'])
def test_invalid_current_vector_or_project_refused(brain, mutation):
    import brain_index as index
    config()
    with closing(database()) as connection:
        note = add_note(connection, 1, 'Source.md')
        statements = {
            'missing': 'DELETE FROM entity', 'wrong-vector-hash': "UPDATE search_vector_embeddings_auxiliary SET value00='stale'",
            'wrong-vector-id': 'UPDATE search_vector_embeddings_rowids SET rowid=999',
            'pending': "UPDATE search_vector_chunks SET embedding_status='pending'",
            'wrong-model': "UPDATE search_vector_chunks SET embedding_model='old'",
            'fingerprint': "UPDATE search_vector_chunks SET entity_fingerprint='old'",
            'no-chunks': 'DELETE FROM search_vector_chunks',
            'foreign-project': "UPDATE project SET path='C:/unrelated-vault'",
        }
        connection.execute(statements[mutation]); connection.commit()
    result=index.status()
    if mutation=='foreign-project':
        assert result['status']=='needs attention' and result['reason']=='database_project_mismatch'
    elif mutation=='missing':
        assert result['status']=='needs review' and result['coverage']['missing']==1
    else:
        assert result['status']=='needs review' and result['vectors']['invalid']>0


def test_current_entity_checksum_cannot_hide_stale_search_body(brain):
    import brain_index as index
    config()
    with closing(database()) as connection:
        note=add_note(connection,1,'Source.md')
        note.write_text('Current source with new evidence.',encoding='utf-8')
        connection.execute('UPDATE entity SET checksum=?',(hashlib.sha256(note.read_bytes()).hexdigest(),));connection.commit()
    result=index.status()
    assert result['status']=='needs review' and result['coverage']['stale']==0
    assert result['vectors']['stale_search_sources']==1


@pytest.mark.parametrize('raw,stored',[
    (b'# Source\n\nPayload.\n','# Source\r\n\r\nPayload.\r\n'),
    (b'# Source\r\n\r\nPayload.\r\n','# Source\n\nPayload.\n'),
    (b'# Source\r\rPayload.\r','# Source\r\rPayload.\r'),
    (b'---\r\ntitle: Source\r\n---\r\n# Source\r\n\r\nPayload.\r\n','# Source\r\n\r\nPayload.\r\n'),
    (b'\xef\xbb\xbf---\r\ntitle: Source\r\n---\r\n# Source\r\n\r\nPayload.\r\n','# Source\r\n\r\nPayload.\r\n'),
],ids=['lf-file-crlf-row','crlf-file-lf-row','cr-body-no-frontmatter','crlf-yaml','bom-crlf-yaml'])
def test_current_body_accepts_only_equivalent_newlines_and_keeps_chunk_inputs(brain,monkeypatch,raw,stored):
    import brain_index as index
    import basic_memory.repository.semantic_chunking as chunking
    config()
    with closing(database()) as connection:
        note=add_note(connection,1,'Source.md',stored)
        note.write_bytes(raw)
        connection.execute('UPDATE entity SET checksum=?',(hashlib.sha256(raw).hexdigest(),));connection.commit()
    real_builder=chunking.build_vector_chunk_records;observed=[]
    def unchanged_rows(rows):
        values=list(rows);observed.extend(row.content_snippet for row in values if row.type=='entity')
        return real_builder(values)
    monkeypatch.setattr(chunking,'build_vector_chunk_records',unchanged_rows)
    result=index.status()
    assert result['status']=='verified' and result['coverage']['stale']==0
    assert result['vectors']['stale_search_sources']==result['vectors']['invalid']==0
    assert observed==[stored] and note.read_bytes()==raw


@pytest.mark.parametrize('stored',['# Source\r\n\r\nDifferent payload.\r\n','# Source \r\n\r\nPayload.\r\n','# Source\r\n\r\nPay load.\r\n'])
def test_newline_tolerance_does_not_hide_body_or_internal_whitespace_changes(brain,stored):
    import brain_index as index
    config();raw=b'# Source\r\n\r\nPayload.\r\n'
    with closing(database()) as connection:
        note=add_note(connection,1,'Source.md',stored);note.write_bytes(raw)
        connection.execute('UPDATE entity SET checksum=?',(hashlib.sha256(raw).hexdigest(),));connection.commit()
    result=index.status()
    assert result['status']=='needs review' and result['coverage']['stale']==0
    assert result['vectors']['stale_search_sources']==1 and note.read_bytes()==raw


def test_newline_only_file_change_still_requires_current_raw_entity_hash(brain):
    import brain_index as index
    config();raw=b'# Source\n\nPayload.\n'
    with closing(database()) as connection:
        note=add_note(connection,1,'Source.md',raw.decode());note.write_bytes(raw)
        connection.execute('UPDATE entity SET checksum=?',(hashlib.sha256(raw).hexdigest(),));connection.commit()
    note.write_bytes(raw.replace(b'\n',b'\r\n'))
    result=index.status()
    assert result['status']=='needs review' and result['coverage']['stale']==1
    assert result['vectors']['stale_search_sources']==0


def test_unsupported_frontmatter_split_fails_closed_without_normalization_guess(brain):
    import brain_index as index
    config();raw=b'+++\ntitle="Source"\n+++\nBody.'
    with closing(database()) as connection:
        note=add_note(connection,1,'Source.md','Body.');note.write_bytes(raw)
        connection.execute('UPDATE entity SET checksum=?',(hashlib.sha256(raw).hexdigest(),));connection.commit()
    result=index.status()
    assert result['status']=='needs review' and result['vectors']['stale_search_sources']==1
    assert note.read_bytes()==raw


def test_missing_or_corrupt_db_never_created_or_repaired(brain):
    import brain_index as index
    config()
    path = cfg.STATE / 'memory.db'
    assert index.status()['status'] == 'needs attention' and not path.exists()
    path.write_bytes(b'synthetic invalid database')
    assert index.status()['status'] == 'needs attention'
    assert path.read_bytes() == b'synthetic invalid database'


def test_empty_ignore_uses_upstream_defaults_and_hidden_files_excluded(brain):
    import brain_index as index
    config()
    with closing(database()) as connection: add_note(connection, 1, 'Source.md')
    (cfg.STATE / '.bmignore').write_bytes(b'')
    (cfg.VAULT / '.hidden.md').write_text('Not eligible.', encoding='utf-8')
    assert index.status()['coverage']['eligible'] == 1
