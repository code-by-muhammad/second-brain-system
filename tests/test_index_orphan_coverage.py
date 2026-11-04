"""Reject orphan derived rows using actual pinned FTS5/chunk DDL, all data synthetic."""
from contextlib import closing
import json
from types import SimpleNamespace

import pytest
import brain_index as index
import brain_settings as cfg
from test_index_coverage import config,database,add_note


def pinned_database():
    from basic_memory.models.search import CREATE_SEARCH_INDEX,CREATE_SQLITE_SEARCH_VECTOR_CHUNKS
    conn=database();add_note(conn,1,'Source.md')
    columns='entity_id,id,type,title,permalink,content_snippet,category,relation_type,project_id'
    search=conn.execute('SELECT '+columns+' FROM search_index').fetchall()
    chunks=conn.execute('SELECT * FROM search_vector_chunks').fetchall()
    conn.executescript('DROP TABLE search_index;DROP TABLE search_vector_chunks;CREATE UNIQUE INDEX synthetic_entity_id ON entity(id);')
    conn.executescript('CREATE TABLE IF NOT EXISTS observation(id INTEGER PRIMARY KEY,project_id INTEGER);CREATE TABLE IF NOT EXISTS relation(id INTEGER PRIMARY KEY,project_id INTEGER);')
    conn.execute(str(CREATE_SEARCH_INDEX));conn.execute(str(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS))
    conn.executemany('INSERT INTO search_index('+columns+') VALUES('+','.join('?'*9)+')',search)
    conn.executemany('INSERT INTO search_vector_chunks(id,entity_id,project_id,chunk_key,chunk_text,source_hash,entity_fingerprint,embedding_model,vector_index,embedding_status) VALUES('+','.join('?'*10)+')',chunks)
    conn.commit();return conn


def add_orphans(conn,*,project=1,search=True,vector=True):
    if search:
        conn.execute("INSERT INTO search_index(entity_id,id,type,title,content_snippet,project_id,file_path) VALUES(900,900,'entity','Synthetic deleted note','Synthetic stale derived content',?,'Deleted.md')",(project,))
    if vector:
        conn.execute("INSERT INTO search_vector_chunks(id,entity_id,project_id,chunk_key,chunk_text,source_hash,entity_fingerprint,embedding_model,vector_index,embedding_status) VALUES(9000,900,?,'synthetic-old','synthetic',?,?,?,'sqlite-vec','ready')",(project,'0'*64,'0'*64,'synthetic-old-model'))
    conn.commit()


def counts(conn):
    return tuple(conn.execute('SELECT count(*) FROM '+table+' AS child WHERE child.project_id=1 AND NOT EXISTS (SELECT 1 FROM entity AS parent WHERE parent.project_id=child.project_id AND parent.id=child.entity_id)').fetchone()[0]
                 for table in ('search_index','search_vector_chunks'))


@pytest.mark.parametrize('kind',['search','vector','both','cross-project-parent','other-project','existing-out-of-scope'])
def test_status_rejects_orphan_rows_without_crossing_project_or_eligibility_scope(brain,kind):
    config()
    with closing(pinned_database()) as conn:
        assert index.status()['status']=='verified'
        if kind in ('cross-project-parent','existing-out-of-scope'):
            conn.execute('INSERT INTO entity VALUES(900,?,?,?,?,?,NULL)',
                (2 if kind=='cross-project-parent' else 1,'Outside.pdf','synthetic','application/pdf','Outside'))
        add_orphans(conn,project=2 if kind=='other-project' else 1,search=kind!='vector',vector=kind!='search')
        actual=counts(conn)
    before=(cfg.STATE/'memory.db').read_bytes()
    report=index.status()
    assert (cfg.STATE/'memory.db').read_bytes()==before
    expected=(0,0) if kind in ('other-project','existing-out-of-scope') else ((1,0) if kind=='search' else (0,1) if kind=='vector' else (1,1))
    assert actual==expected
    assert (report['coverage']['orphan_search_rows'],report['coverage']['orphan_vector_chunks'])==expected
    assert report['status']==('needs review' if any(expected) else 'verified')
    print(json.dumps({'case':kind,'observed_status':report['status'],'actual_orphan_search_rows':actual[0],
        'actual_orphan_vector_chunks':actual[1],'status_was_read_only':True}))


def test_disabled_semantic_index_does_not_require_vector_table(brain):
    selected=config();selected['semantic_search_enabled']=False
    (cfg.STATE/'config.json').write_text(json.dumps(selected))
    with closing(pinned_database()) as conn:
        conn.execute('DROP TABLE search_vector_chunks');conn.commit()
    before=(cfg.STATE/'memory.db').read_bytes();report=index.status()
    assert report['status']=='needs review' and report['vectors']['status']=='disabled; not verified'
    assert report['coverage']['orphan_vector_chunks'] is None and report['coverage']['orphan_search_rows']==0
    assert (cfg.STATE/'memory.db').read_bytes()==before


def test_null_fts_parent_is_counted_as_structurally_abnormal(brain):
    config()
    with closing(pinned_database()) as conn:
        conn.execute("INSERT INTO search_index(entity_id,id,type,project_id) VALUES(NULL,900,'entity',1)");conn.commit()
    before=(cfg.STATE/'memory.db').read_bytes();report=index.status()
    assert report['status']=='needs review' and report['coverage']['orphan_search_rows']==1
    assert (cfg.STATE/'memory.db').read_bytes()==before


def rebuild_coherent_vectors(conn):
    from basic_memory.repository.semantic_chunking import build_vector_chunk_records,build_entity_fingerprint
    columns=('id','type','title','permalink','content_snippet','category','relation_type')
    rows=[SimpleNamespace(**dict(zip(columns,row))) for row in conn.execute('SELECT '+','.join(columns)+' FROM search_index WHERE project_id=1 AND entity_id=1 ORDER BY type,id')]
    chunks=build_vector_chunk_records(rows).records;fingerprint=build_entity_fingerprint(chunks)
    conn.executescript('DELETE FROM search_vector_chunks;DELETE FROM search_vector_embeddings_rowids;DELETE FROM search_vector_embeddings_auxiliary;')
    for offset,chunk in enumerate(chunks):
        ident=1000+offset
        conn.execute('INSERT INTO search_vector_chunks(id,entity_id,project_id,chunk_key,chunk_text,source_hash,entity_fingerprint,embedding_model,vector_index,embedding_status) VALUES(?,1,1,?,?,?,?,?,?,?)',
            (ident,chunk['chunk_key'],chunk['chunk_text'],chunk['source_hash'],fingerprint,'FastEmbedEmbeddingProvider:bge-small-en-v1.5:384','sqlite-vec','ready'))
        conn.execute('INSERT INTO search_vector_embeddings_rowids(rowid) VALUES(?)',(ident,))
        conn.execute('INSERT INTO search_vector_embeddings_auxiliary VALUES(?,?)',(ident,chunk['source_hash']))
    conn.commit()


@pytest.mark.parametrize('kind',['observation','relation'])
@pytest.mark.parametrize('backing_project',[None,1,2])
def test_child_search_backing_rows_are_project_scoped_even_with_coherent_vectors(brain,kind,backing_project):
    config()
    with closing(pinned_database()) as conn:
        conn.execute('INSERT INTO search_index(entity_id,id,type,title,content_snippet,project_id) VALUES(1,900,?,?,?,1)',
            (kind,'Synthetic child','Synthetic child content'))
        if backing_project is not None:
            conn.execute('INSERT INTO '+kind+'(id,project_id) VALUES(900,?)',(backing_project,))
        rebuild_coherent_vectors(conn)
    before=(cfg.STATE/'memory.db').read_bytes();report=index.status()
    assert report['vectors']['invalid']==0
    assert report['coverage']['orphan_search_rows']==(0 if backing_project==1 else 1)
    assert report['status']==('verified' if backing_project==1 else 'needs review')
    assert (cfg.STATE/'memory.db').read_bytes()==before
