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
import time
import tempfile
from types import SimpleNamespace
import brain_settings as cfg
from brain_walk import walk,WalkLimit

MAX_NOTES = 100_000
MAX_NOTE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 2_000_000_000
MAX_ROWS = 2_000_000
MAX_STATUS_SECONDS = 120
MAX_CHUNK_SOURCE_BREAKS = 262_144
MODEL = 'FastEmbedEmbeddingProvider'
SCOPE = 'current file/entity SHA256, normalized indexed body and persisted vector coherence; retrieval quality is separate'
HISTORY_PATTERN='/02-Areas/AI-Memory/Wiki/Log-Archives/'+'[0-9a-f]'*64+'.md'

def _history_path(relative):
    return bool(re.fullmatch(r'02-Areas/AI-Memory/Wiki/Log-Archives/[a-f0-9]{64}\.md',relative,
                            re.IGNORECASE if os.name=='nt' else 0))

def _scope_bytes(path):
    if any(item.is_symlink() or os.path.isjunction(item) for item in (path,*path.parents)):
        raise IndexAttention('linked_ignore')
    if not path.exists():return None
    with path.open('rb') as stream:raw=stream.read(1_000_001)
    if len(raw)>1_000_000:raise IndexAttention('ignore_limit')
    try:raw.decode('utf-8')
    except UnicodeError:raise IndexAttention('ignore_encoding_invalid') from None
    return raw

def ensure_history_scope():
    """Explicit bootstrap/retry migration of derived index scope, never notes."""
    transaction=cfg.STATE/'install-transaction.json'
    if transaction.exists() and _json(transaction,64*1024*1024).get('stage')!='complete':
        raise IndexAttention('initial_install_incomplete')
    path=cfg.STATE/'.bmignore';before=_scope_bytes(path)
    lines=(before or b'').decode('utf-8').splitlines()
    if HISTORY_PATTERN in {line.strip() for line in lines}:
        return {'status':'unchanged','pattern':HISTORY_PATTERN,'before_sha256':hashlib.sha256(before).hexdigest()}
    from basic_memory.ignore_utils import DEFAULT_IGNORE_PATTERNS
    # An empty/comment-only file uses upstream defaults. Preserve that fallback
    # when adding the first active rule, in addition to every original byte.
    active=any(line.strip() and not line.strip().startswith('#') for line in lines)
    defaults='' if active else '\n'.join(sorted(DEFAULT_IGNORE_PATTERNS))+'\n'
    after=(before or b'')+(b'\n' if before and not before.endswith(b'\n') else b'')+(defaults+HISTORY_PATTERN+'\n').encode('utf-8')
    if len(after)>1_000_000:raise IndexAttention('ignore_limit')
    sha=hashlib.sha256(before).hexdigest() if before is not None else None
    backup=None
    if before is not None:
        folder=cfg.ROOT/'backups/bmignore'
        if any(item.is_symlink() or os.path.isjunction(item) for item in (folder,*folder.parents)):
            raise IndexAttention('linked_ignore_backup')
        folder.mkdir(parents=True,exist_ok=True);backup=folder/sha
        if backup.exists():
            if _scope_bytes(backup)!=before:raise IndexAttention('ignore_backup_integrity')
        else:
            descriptor,temporary=tempfile.mkstemp(prefix='.scope-backup-',dir=folder)
            try:
                with os.fdopen(descriptor,'wb') as output:output.write(before);output.flush();os.fsync(output.fileno())
                os.replace(temporary,backup)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
    descriptor,temporary=tempfile.mkstemp(prefix='.scope-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as output:output.write(after);output.flush();os.fsync(output.fileno())
        if _scope_bytes(path)!=before:raise IndexAttention('ignore_changed_during_migration')
        if before is None:
            if os.name=='nt':os.rename(temporary,path)
            else:os.link(temporary,path)
        else:os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return {'status':'updated','pattern':HISTORY_PATTERN,'before_sha256':sha,'after_sha256':hashlib.sha256(after).hexdigest(),
            'backup':str(backup) if backup else None,'scope':'generated compiler archive files only; wiki source-selection and original notes unchanged'}


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
        try:decoded=raw.decode('utf-8')
        except UnicodeError:raise IndexAttention('ignore_encoding_invalid') from None
        selected={line.strip() for line in decoded.splitlines() if line.strip() and not line.strip().startswith('#')}
        patterns.update(selected or (DEFAULT_IGNORE_PATTERNS if path.name=='.bmignore' else ()))
    return patterns


def _offline(stat):
    # A cloud placeholder must not hydrate merely because status was requested.
    return bool(getattr(stat,'st_file_attributes',0)&(0x1000|0x40000|0x400000))


def inventory(check=lambda:None,scope_counts=None):
    from basic_memory.ignore_utils import should_ignore_path
    from basic_memory.index.filesystem import local_relative_path_is_filtered
    patterns=_patterns(); notes={}; total=0; errors=[]
    if not cfg.VAULT.is_dir(): raise IndexAttention('vault_unavailable')
    def walk_error(error): errors.append('unreadable_directory')
    for directory,dirs,files in walk(cfg.VAULT,check=check,onerror=walk_error):
        check()
        parent=Path(directory)
        kept=[]
        for name in dirs:
            check()
            path=parent/name
            if name.startswith('.') or should_ignore_path(path,cfg.VAULT,patterns): continue
            if path.is_symlink() or os.path.isjunction(path): errors.append('linked_directory'); continue
            if _offline(path.stat()): errors.append('offline_directory'); continue
            kept.append(name)
        dirs[:]=kept
        for name in files:
            check()
            path=parent/name; relative=path.relative_to(cfg.VAULT).as_posix()
            if local_relative_path_is_filtered(relative) or should_ignore_path(path,cfg.VAULT,patterns):
                if scope_counts is not None and HISTORY_PATTERN in patterns and _history_path(relative):
                    scope_counts['generated_history_files']=scope_counts.get('generated_history_files',0)+1
                continue
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
                    check()
                    size+=len(block)
                    if size>MAX_NOTE_BYTES: raise IndexAttention('note_size_limit')
                    checksum.update(block); empty=empty and not block.strip();pieces.append(block)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or size!=before.st_size:
                errors.append('source_changed')
            from basic_memory.file_utils import remove_frontmatter
            try:text=b''.join(pieces).decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            except UnicodeError:raise IndexAttention('note_encoding_invalid') from None
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


def _search_rows(conn,project,entities,check):
    """Scan FTS5's UNINDEXED routing columns once; retrieve text by rowid."""
    grouped={};sizes={};total=0
    query=('SELECT rowid,entity_id,coalesce(length(title),0)+coalesce(length(permalink),0)+'
           'coalesce(length(content_snippet),0)+coalesce(length(category),0)+coalesce(length(relation_type),0) AS chars '
           'FROM search_index WHERE project_id=?')
    for row in conn.execute(query,(project,)):
        check()
        entity=row['entity_id']
        if entity not in entities:continue
        ids=grouped.setdefault(entity,[]);ids.append(row['rowid'])
        total+=1;sizes[entity]=sizes.get(entity,0)+row['chars']
        if len(ids)>16384 or sizes[entity]>8_000_000:raise IndexAttention('search_source_limit')
        if total>MAX_ROWS:raise IndexAttention('search_row_limit')
    return grouped


def _planned_chunks(rows,limit,check):
    from basic_memory.repository.semantic_chunking import build_vector_chunk_records,compose_row_source_text
    planned={};seen={};minimum=0
    selected=[]
    for row in rows:
        check();item=SimpleNamespace(**dict(row));key=(item.type,item.id)
        text=compose_row_source_text(item)
        if key in seen:
            if seen[key]!=text:return None
            continue
        seen[key]=text;selected.append(item)
        # This allocation-free lower bound covers short bullets before the
        # upstream splitter can expand a small source into hundreds of thousands
        # of dictionaries. Empty/whitespace-only bullets are not counted.
        minimum+=sum(1 for _ in re.finditer(r'(?m)^[\-*][ \t]+\S',text))
        if minimum>limit:return None
        breaks=0
        for _ in re.finditer(r'[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]',text):
            breaks+=1
            if breaks>MAX_CHUNK_SOURCE_BREAKS:raise IndexAttention('chunk_source_work_limit')
    for item in selected:
        check()
        for row in build_vector_chunk_records([item]).records:
            planned[row['chunk_key']]=row['source_hash']
            if len(planned)>limit:return None
    return planned


def _vectors(conn,project,entities,notes,model,check=lambda:None):
    from basic_memory.repository.semantic_chunking import build_entity_fingerprint
    issues=0; total=0; zero=0; stale_sources=0
    grouped=_search_rows(conn,project,entities,check)
    for entity,info in entities.items():
        check()
        path=info['path']
        metadata=conn.execute('SELECT count(*),max(length(chunk_key)),max(length(source_hash)),max(length(entity_fingerprint)),max(length(embedding_model)),max(length(vector_index)),max(length(embedding_status)) FROM search_vector_chunks WHERE project_id=? AND entity_id=?',(project,entity)).fetchone()
        if metadata[0]>32768 or any((value or 0)>1000 for value in metadata[1:]): raise IndexAttention('chunk_metadata_limit')
        total+=metadata[0]
        if total>MAX_ROWS:raise IndexAttention('vector_row_limit')
        rows=[]
        for ident in grouped.get(entity,[]):
            check()
            rows.append(conn.execute('SELECT id,type,title,permalink,content_snippet,category,relation_type FROM search_index WHERE rowid=?',(ident,)).fetchone())
        rows.sort(key=lambda row:(row['type'],row['id']))
        primary=[row for row in rows if row['type']=='entity' and row['id']==entity]
        # Upstream search rows can preserve Windows CRLF or use translated LF.
        # Normalize only this body comparison; persisted chunk inputs below stay exact.
        if (len(primary)!=1 or hashlib.sha256((primary[0]['content_snippet'] or '').replace('\r\n','\n').replace('\r','\n').replace('\x00','').strip().encode()).hexdigest()!=notes[path]['body_sha256']
                or primary[0]['title']!=(info['title'] or '').replace('\x00','')
                or primary[0]['permalink']!=info['permalink']):
            stale_sources+=1;issues+=1
        planned=_planned_chunks(rows,metadata[0],check)
        if planned is None or len(planned)!=metadata[0]:
            issues+=1
            if not metadata[0]:zero+=1
            continue
        fingerprint=build_entity_fingerprint({'chunk_key':key,'source_hash':sha} for key,sha in planned.items())
        actual=conn.execute('SELECT c.chunk_key,c.source_hash,c.entity_fingerprint,c.embedding_model,c.vector_index,c.embedding_status,r.rowid AS vector_id,a.value00 AS vector_hash FROM search_vector_chunks c LEFT JOIN search_vector_embeddings_rowids r ON r.rowid=c.id LEFT JOIN search_vector_embeddings_auxiliary a ON a.rowid=c.id WHERE c.project_id=? AND c.entity_id=? LIMIT 32769',(project,entity)).fetchall()
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


def status(*,deadline=None):
    """No model calls, repair, source writes or DB creation; main DB opened ro."""
    base={'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
          'verification':SCOPE,'manifest_scope':'platform-local Basic Memory Markdown eligibility (.md/.markdown); configured compiler-history archives and non-Markdown entities outside this scope; existing out-of-scope rows can remain searchable'}
    deadline=min(deadline if deadline is not None else float('inf'),time.monotonic()+MAX_STATUS_SECONDS)
    def check():
        if time.monotonic()>=deadline:raise IndexAttention('status_time_limit')
    try:
        check();config=configuration();scope_counts={};notes,manifest=inventory(check,scope_counts)
        history_scope=HISTORY_PATTERN in _patterns()
        coverage={'eligible':len(notes),'manifest_sha256':manifest}
        path=cfg.STATE/'memory.db'
        if path.is_symlink() or os.path.isjunction(path): raise IndexAttention('linked_database')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1,isolation_level=None)) as conn:
            conn.row_factory=sqlite3.Row
            conn.set_progress_handler(lambda:int(time.monotonic()>=deadline),10000)
            conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
            rows=conn.execute("SELECT id,path,is_active FROM project WHERE name='brain'").fetchall()
            if len(rows)!=1 or rows[0]['is_active']!=1 or Path(rows[0]['path']).expanduser().resolve()!=cfg.VAULT:
                raise IndexAttention('database_project_mismatch')
            project=rows[0]['id']
            records=conn.execute('SELECT id,file_path,checksum,content_type,title,permalink FROM entity WHERE project_id=? LIMIT ?',(project,MAX_NOTES+1)).fetchall()
            if len(records)>MAX_NOTES: raise IndexAttention('entity_row_limit')
            indexed={}; entities={}; duplicates=0; mismatches=0; nonmd=0;excluded_history=0
            for row in records:
                check()
                relative=_path(row['file_path'])
                markdown=PurePosixPath(relative).suffix.lower() in ('.md','.markdown')
                if markdown!=(row['content_type']=='text/markdown'): mismatches+=1
                if not markdown: nonmd+=1; continue
                if history_scope and _history_path(relative):excluded_history+=1;continue
                if relative in indexed: duplicates+=1
                indexed[relative]=row['checksum']
                if relative in notes: entities[row['id']]={'path':relative,'title':row['title'],'permalink':row['permalink']}
            coverage.update(indexed=len(indexed),missing=len(notes.keys()-indexed.keys()),extra=len(indexed.keys()-notes.keys()),
                stale=sum(indexed[key]!=notes[key]['sha256'] for key in notes.keys()&indexed.keys()),
                duplicates=duplicates,content_type_mismatches=mismatches,non_markdown_entities=nonmd,
                excluded_derived_history_entities=excluded_history,**scope_counts)
            check()
            coverage['orphan_search_rows']=conn.execute(
                'SELECT count(*) FROM search_index AS child WHERE child.project_id=? AND ('
                'NOT EXISTS (SELECT 1 FROM entity AS parent WHERE parent.project_id=child.project_id AND parent.id=child.entity_id) '
                "OR (child.type='observation' AND NOT EXISTS (SELECT 1 FROM observation AS parent WHERE parent.project_id=child.project_id AND parent.id=child.id)) "
                "OR (child.type='relation' AND NOT EXISTS (SELECT 1 FROM relation AS parent WHERE parent.project_id=child.project_id AND parent.id=child.id)))",
                (project,)).fetchone()[0]
            coverage['orphan_vector_chunks']=None
            if config.get('semantic_search_enabled',True) is True:
                check()
                coverage['orphan_vector_chunks']=conn.execute(
                    'SELECT count(*) FROM search_vector_chunks AS child WHERE child.project_id=? '
                    'AND NOT EXISTS (SELECT 1 FROM entity AS parent WHERE parent.project_id=child.project_id AND parent.id=child.entity_id)',
                    (project,)).fetchone()[0]
                vectors=_vectors(conn,project,entities,notes,_model(config),check)
            else:vectors={'status':'disabled; not verified'}
        _,after=inventory(check)
        complete=not any(coverage.get(key) for key in ('missing','extra','stale','duplicates','content_type_mismatches','orphan_search_rows','orphan_vector_chunks'))
        stable=manifest==after
        verified=bool(notes) and complete and stable and vectors.get('invalid')==0
        return {**base,'status':'verified' if verified else 'needs review','coverage':coverage,'vectors':vectors,
                'inventory_stable':stable,'reason':'file_body_and_vector_structure_verified' if verified else 'coverage_or_vector_review_required',
                'guidance':'Use index retry --full after a stale checksum; search ranking and named-client retrieval require separate probes.'}
    except Exception as error:
        reason='status_time_limit' if time.monotonic()>=deadline else (str(error) if isinstance(error,(IndexAttention,WalkLimit)) else 'local_index_unavailable')
        return {**base,'status':'needs attention','reason':reason,'error_type':type(error).__name__,
                'guidance':'Inspect local index/configuration; index status never repairs or resets the database.'}
