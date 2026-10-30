"""Bounded read-only coverage of current notes and persisted vector metadata.

No database repair, model loading, source writes or configuration creation.
Semantic retrieval quality remains a separate acceptance test.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from types import SimpleNamespace
import brain_settings as cfg

MAX_NOTES = 100_000
MAX_NOTE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 2_000_000_000
MAX_ROWS = 2_000_000
MODEL = 'FastEmbedEmbeddingProvider'
SCOPE = 'current file/entity SHA256, normalized indexed body and persisted vector coherence; retrieval quality is separate'


class IndexAttention(ValueError):
    """A fixed reason code, never a note/configuration/SQLite diagnostic dump."""


def _json(path, limit=1_000_000):
    if path.is_symlink() or os.path.isjunction(path): raise IndexAttention('linked_config')
    with path.open('rb') as stream: raw = stream.read(limit+1)
    if len(raw)>limit: raise IndexAttention('config_limit')
    value=json.loads(raw)
    if not isinstance(value,dict): raise IndexAttention('config_shape')
    return value


def configuration():
    value=_json(cfg.STATE/'config.json')
    try:cfg.validate_search_configuration(value)
    except ValueError:raise IndexAttention('unsupported_local_search_configuration') from None
    projects=value.get('projects')
    if not isinstance(projects,dict) or not isinstance(projects.get('brain'),dict):
        raise IndexAttention('project_config')
    if value.get('database_backend','sqlite')!='sqlite' or projects['brain'].get('mode','local')!='local':
        raise IndexAttention('unsupported_index_backend_or_routing')
    for path in (value.get('project_root'),projects['brain'].get('path')):
        if not isinstance(path,str) or not Path(path).is_absolute() or Path(path).resolve()!=cfg.VAULT:
            raise IndexAttention('project_config_path')
    if (value.get('disable_permalinks') is not True
            or value.get('ensure_frontmatter_on_sync') is not False
            or value.get('format_on_save') is not False or value.get('auto_update') is not False):
        raise IndexAttention('note_mutating_config_refused')
    return value


def _patterns():
    from basic_memory.ignore_utils import DEFAULT_IGNORE_PATTERNS
    patterns=set()
    for path in (cfg.STATE/'.bmignore',cfg.VAULT/'.gitignore'):
        if path.is_symlink() or os.path.isjunction(path): raise IndexAttention('linked_ignore')
        if not path.exists():
            if path.name=='.bmignore': patterns.update(DEFAULT_IGNORE_PATTERNS)
            continue
        with path.open('rb') as stream: raw=stream.read(1_000_001)
        if len(raw)>1_000_000: raise IndexAttention('ignore_limit')
        selected={line.strip() for line in raw.decode('utf-8').splitlines() if line.strip() and not line.strip().startswith('#')}
        patterns.update(selected or (DEFAULT_IGNORE_PATTERNS if path.name=='.bmignore' else ()))
    return patterns


def _offline(stat):
    # A cloud placeholder must not hydrate merely because status was requested.
    return bool(getattr(stat,'st_file_attributes',0)&(0x1000|0x40000|0x400000))


def inventory():
    from basic_memory.ignore_utils import should_ignore_path
    from basic_memory.index.filesystem import local_relative_path_is_filtered
    patterns=_patterns(); notes={}; total=0; errors=[]
    if not cfg.VAULT.is_dir(): raise IndexAttention('vault_unavailable')
    def walk_error(error): errors.append('unreadable_directory')
    for directory,dirs,files in os.walk(cfg.VAULT,followlinks=False,onerror=walk_error):
        parent=Path(directory)
        kept=[]
        for name in dirs:
            path=parent/name
            if name.startswith('.') or should_ignore_path(path,cfg.VAULT,patterns): continue
            if path.is_symlink() or os.path.isjunction(path): errors.append('linked_directory'); continue
            if _offline(path.stat()): errors.append('offline_directory'); continue
            kept.append(name)
        dirs[:]=kept
        for name in files:
            path=parent/name; relative=path.relative_to(cfg.VAULT).as_posix()
            if local_relative_path_is_filtered(relative) or should_ignore_path(path,cfg.VAULT,patterns): continue
            if PurePosixPath(relative).suffix.lower() not in ('.md','.markdown'): continue
            if path.is_symlink() or os.path.isjunction(path): errors.append('linked_note'); continue
            before=path.stat()
            if not stat.S_ISREG(before.st_mode): errors.append('non_regular_note'); continue
            if _offline(before): errors.append('offline_note'); continue
            if before.st_size>MAX_NOTE_BYTES: raise IndexAttention('note_size_limit')
            total+=before.st_size
            if total>MAX_TOTAL_BYTES or len(notes)>=MAX_NOTES: raise IndexAttention('inventory_limit')
            checksum=hashlib.sha256(); size=0; empty=True; pieces=[]
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(65536),b''):
                    size+=len(block)
                    if size>MAX_NOTE_BYTES: raise IndexAttention('note_size_limit')
                    checksum.update(block); empty=empty and not block.strip();pieces.append(block)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or size!=before.st_size:
                errors.append('source_changed')
            from basic_memory.file_utils import remove_frontmatter
            text=b''.join(pieces).decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            body=remove_frontmatter(text).replace('\x00','').strip()
            notes[relative]={'sha256':checksum.hexdigest(),'bytes':size,'empty':empty,
                             'body_sha256':hashlib.sha256(body.encode()).hexdigest()}
    if errors: raise IndexAttention(sorted(set(errors))[0])
    manifest=hashlib.sha256(json.dumps([(key,row['sha256'],row['bytes']) for key,row in sorted(notes.items())],ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
    return notes,manifest


def _path(value):
    if not isinstance(value,str) or not value or len(value)>32768 or '\\' in value or ':' in value:
        raise IndexAttention('invalid_indexed_path')
    path=PurePosixPath(value)
    if path.is_absolute() or any(part in ('','.','..') for part in value.split('/')):
        raise IndexAttention('invalid_indexed_path')
    return value


def _model(config):
    if config.get('semantic_embedding_provider','fastembed').strip().lower()!='fastembed': raise IndexAttention('unsupported_embedding_provider')
    model=config.get('semantic_embedding_model','bge-small-en-v1.5')
    dimensions=config.get('semantic_embedding_dimensions') or 384
    if not isinstance(model,str) or len(model)>200 or type(dimensions) is not int or not 1<=dimensions<=8192:
        raise IndexAttention('embedding_config')
    from basic_memory.repository.embedding_provider_factory import configured_embedding_provider_identity
    return configured_embedding_provider_identity(SimpleNamespace(semantic_embedding_provider='fastembed',
        semantic_embedding_model=model,semantic_embedding_dimensions=dimensions,
        semantic_embedding_document_prefix=config.get('semantic_embedding_document_prefix'),
        semantic_embedding_query_prefix=config.get('semantic_embedding_query_prefix')))


def _vectors(conn,project,entities,notes,model):
    from basic_memory.repository.semantic_chunking import build_vector_chunk_records,build_entity_fingerprint
    issues=0; total=0; zero=0; stale_sources=0
    for entity,info in entities.items():
        path=info['path']
        shape=conn.execute('SELECT count(*),coalesce(sum(coalesce(length(title),0)+coalesce(length(permalink),0)+coalesce(length(content_snippet),0)+coalesce(length(category),0)+coalesce(length(relation_type),0)),0) FROM search_index WHERE project_id=? AND entity_id=?',(project,entity)).fetchone()
        if shape[0]>16384 or shape[1]>8_000_000: raise IndexAttention('search_source_limit')
        rows=conn.execute('SELECT id,type,title,permalink,content_snippet,category,relation_type FROM search_index WHERE project_id=? AND entity_id=? ORDER BY type,id',(project,entity)).fetchall()
        primary=[row for row in rows if row['type']=='entity' and row['id']==entity]
        # Upstream search rows can preserve Windows CRLF or use translated LF.
        # Normalize only this body comparison; persisted chunk inputs below stay exact.
        if (len(primary)!=1 or hashlib.sha256((primary[0]['content_snippet'] or '').replace('\r\n','\n').replace('\r','\n').replace('\x00','').strip().encode()).hexdigest()!=notes[path]['body_sha256']
                or primary[0]['title']!=(info['title'] or '').replace('\x00','')
                or primary[0]['permalink']!=info['permalink']):
            stale_sources+=1;issues+=1
        expected=build_vector_chunk_records(SimpleNamespace(**dict(row)) for row in rows).records
        fingerprint=build_entity_fingerprint(expected)
        planned={row['chunk_key']:row['source_hash'] for row in expected}
        metadata=conn.execute('SELECT count(*),max(length(chunk_key)),max(length(source_hash)),max(length(entity_fingerprint)),max(length(embedding_model)),max(length(vector_index)),max(length(embedding_status)) FROM search_vector_chunks WHERE project_id=? AND entity_id=?',(project,entity)).fetchone()
        if metadata[0]>32768 or any((value or 0)>1000 for value in metadata[1:]): raise IndexAttention('chunk_metadata_limit')
        actual=conn.execute('SELECT c.chunk_key,c.source_hash,c.entity_fingerprint,c.embedding_model,c.vector_index,c.embedding_status,r.rowid AS vector_id,a.value00 AS vector_hash FROM search_vector_chunks c LEFT JOIN search_vector_embeddings_rowids r ON r.rowid=c.id LEFT JOIN search_vector_embeddings_auxiliary a ON a.rowid=c.id WHERE c.project_id=? AND c.entity_id=? LIMIT 32769',(project,entity)).fetchall()
        total+=len(actual)
        if total>MAX_ROWS: raise IndexAttention('vector_row_limit')
        if not actual:
            zero+=1
            if planned or not notes[path]['empty']: issues+=1
            continue
        if len(actual)!=len(planned) or len({row['chunk_key'] for row in actual})!=len(actual): issues+=1
        for row in actual:
            if (planned.get(row['chunk_key'])!=row['source_hash'] or row['entity_fingerprint']!=fingerprint
                    or row['embedding_model']!=model or row['vector_index']!='sqlite-vec'
                    or row['embedding_status']!='ready' or row['vector_id'] is None
                    or row['vector_hash']!=row['source_hash']): issues+=1
    return {'chunks':total,'invalid':issues,'zero_chunk_entities':zero,'stale_search_sources':stale_sources,
            'scope':'persisted derived search rows, chunk fingerprints and vector auxiliary hashes; raw-note semantic interpretation and retrieval quality are separate'}


def status():
    """No model calls, repair, source writes or DB creation; main DB opened ro."""
    base={'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
          'verification':SCOPE,'manifest_scope':'platform-local Basic Memory Markdown eligibility (.md/.markdown); non-Markdown entities outside this scope'}
    try:
        config=configuration(); notes,manifest=inventory()
        coverage={'eligible':len(notes),'manifest_sha256':manifest}
        path=cfg.STATE/'memory.db'
        if path.is_symlink() or os.path.isjunction(path): raise IndexAttention('linked_database')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1,isolation_level=None)) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
            rows=conn.execute("SELECT id,path,is_active FROM project WHERE name='brain'").fetchall()
            if len(rows)!=1 or rows[0]['is_active']!=1 or Path(rows[0]['path']).expanduser().resolve()!=cfg.VAULT:
                raise IndexAttention('database_project_mismatch')
            project=rows[0]['id']
            records=conn.execute('SELECT id,file_path,checksum,content_type,title,permalink FROM entity WHERE project_id=? LIMIT ?',(project,MAX_NOTES+1)).fetchall()
            if len(records)>MAX_NOTES: raise IndexAttention('entity_row_limit')
            indexed={}; entities={}; duplicates=0; mismatches=0; nonmd=0
            for row in records:
                relative=_path(row['file_path'])
                markdown=PurePosixPath(relative).suffix.lower() in ('.md','.markdown')
                if markdown!=(row['content_type']=='text/markdown'): mismatches+=1
                if not markdown: nonmd+=1; continue
                if relative in indexed: duplicates+=1
                indexed[relative]=row['checksum']
                if relative in notes: entities[row['id']]={'path':relative,'title':row['title'],'permalink':row['permalink']}
            coverage.update(indexed=len(indexed),missing=len(notes.keys()-indexed.keys()),extra=len(indexed.keys()-notes.keys()),
                stale=sum(indexed[key]!=notes[key]['sha256'] for key in notes.keys()&indexed.keys()),
                duplicates=duplicates,content_type_mismatches=mismatches,non_markdown_entities=nonmd)
            vectors=_vectors(conn,project,entities,notes,_model(config)) if config.get('semantic_search_enabled') is True else {'status':'disabled; not verified'}
        _,after=inventory()
        complete=not any(coverage[key] for key in ('missing','extra','stale','duplicates','content_type_mismatches'))
        stable=manifest==after
        verified=bool(notes) and complete and stable and vectors.get('invalid')==0
        return {**base,'status':'verified' if verified else 'needs review','coverage':coverage,'vectors':vectors,
                'inventory_stable':stable,'reason':'file_body_and_vector_structure_verified' if verified else 'coverage_or_vector_review_required',
                'guidance':'Use index retry --full after a stale checksum; search ranking and named-client retrieval require separate probes.'}
    except Exception as error:
        reason=str(error) if isinstance(error,IndexAttention) else 'local_index_unavailable'
        return {**base,'status':'needs attention','reason':reason,'error_type':type(error).__name__,
                'guidance':'Inspect local index/configuration; index status never repairs or resets the database.'}
