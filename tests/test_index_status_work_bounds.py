"""Real FTS5 work scaling and refusal before explosive chunk materialization."""
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

import brain_index as index


def search_database():
    from basic_memory.models.search import CREATE_SEARCH_INDEX
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute(str(CREATE_SEARCH_INDEX))
    conn.executescript('''
      CREATE TABLE search_vector_chunks(id INTEGER,project_id INTEGER,entity_id INTEGER,chunk_key TEXT,source_hash TEXT,entity_fingerprint TEXT,embedding_model TEXT,vector_index TEXT,embedding_status TEXT);
      CREATE INDEX vector_entity ON search_vector_chunks(project_id,entity_id);
      CREATE TABLE search_vector_embeddings_rowids(rowid INTEGER PRIMARY KEY);
      CREATE TABLE search_vector_embeddings_auxiliary(rowid INTEGER PRIMARY KEY,value00 TEXT);
    ''')
    return conn


def measured_work(count):
    with search_database() as conn:
        conn.executemany('INSERT INTO search_index(id,entity_id,project_id,type,title,content_snippet) VALUES(?,?,1,\'entity\',\'Note\',\'x\')',((i,i) for i in range(count)))
        conn.executemany('INSERT INTO search_index(id,entity_id,project_id,type,title,content_snippet) VALUES(?,?,2,\'entity\',\'Other\',\'filler\')',((i,i) for i in range(count)))
        steps=[0]
        def progress():steps[0]+=100;return 0
        conn.set_progress_handler(progress,100)
        entities={i:{'path':str(i),'title':'Note','permalink':None} for i in range(count)}
        notes={str(i):{'body_sha256':hashlib.sha256(b'x').hexdigest(),'empty':False} for i in range(count)}
        result=index._vectors(conn,1,entities,notes,'synthetic-not-a-model-call')
        assert result['invalid']==count and result['stale_search_sources']==0
        return steps[0]


def test_actual_fts_routing_is_linear_instead_of_one_full_scan_per_entity():
    work=[measured_work(size) for size in (250,500,1000)]
    assert work[1]<work[0]*2.4 and work[2]<work[1]*2.4,work
    assert work[2]<1_000_000,work


@pytest.mark.parametrize('count',[40_000,499_000])
def test_short_bullet_expansion_refuses_before_upstream_record_allocation(monkeypatch,count):
    import basic_memory.repository.semantic_chunking as chunks
    row={'id':1,'type':'entity','title':'Note','permalink':None,
         'content_snippet':'- x\n'*count,'category':None,'relation_type':None}
    def forbidden(*args,**kwargs):pytest.fail('Oversized bullet expansion reached record allocation')
    monkeypatch.setattr(chunks,'build_vector_chunk_records',forbidden)
    assert index._planned_chunks([row],32768,lambda:None) is None


def test_planning_matches_pinned_upstream_hashes_for_mixed_boundaries():
    from basic_memory.repository.semantic_chunking import build_vector_chunk_records
    samples=['', 'ordinary words '*2000, '# Header\n\nBody\n- one\n- two',
             '\n'.join('# H'+str(i) for i in range(1000)), '- \n- x\v- y\u2028- z',
             ' spaces\r\n\r\n# Header\n* bullet\n\ntext']
    for content in samples:
        rows=[{'id':i,'type':kind,'title':'Source','permalink':'source',
               'content_snippet':content,'category':None,'relation_type':None}
              for i,kind in enumerate(('entity','observation','relation'))]
        expected={row['chunk_key']:row['source_hash'] for row in build_vector_chunk_records(SimpleNamespace(**row) for row in rows).records}
        assert index._planned_chunks(rows,len(expected),lambda:None)==expected


def test_conflicting_duplicate_rows_do_not_verify_by_arbitrary_last_wins_order():
    row={'id':1,'type':'entity','title':'Source','permalink':None,
         'content_snippet':'First','category':None,'relation_type':None}
    assert index._planned_chunks([row,{**row,'content_snippet':'Other'}],2,lambda:None) is None


def test_status_deadline_returns_attention_without_creating_database(brain,monkeypatch):
    from brain_settings import STATE
    monkeypatch.setattr(index,'MAX_STATUS_SECONDS',0)
    assert index.status()['reason']=='status_time_limit'
    assert not (STATE/'memory.db').exists()


def test_upstream_source_line_work_refuses_before_splitter(monkeypatch):
    import basic_memory.repository.semantic_chunking as chunks
    monkeypatch.setattr(index,'MAX_CHUNK_SOURCE_BREAKS',100)
    row={'id':1,'type':'entity','title':None,'permalink':None,
         'content_snippet':'# H\n'*101,'category':None,'relation_type':None}
    monkeypatch.setattr(chunks,'build_vector_chunk_records',lambda *args:pytest.fail('Line budget reached splitter'))
    with pytest.raises(index.IndexAttention,match='chunk_source_work_limit'):
        index._planned_chunks([row],32768,lambda:None)
