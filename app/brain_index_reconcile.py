"""Repair bounded local search drift from current Markdown, without a rebuild."""
import asyncio
from bisect import bisect_right
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from filelock import FileLock, Timeout

import brain_index as index
import brain_settings as cfg

BATCH_FILES = 32
VECTOR_PASSES = 3
REPAIR_SECONDS = 240
RECEIPT = 'index-reconcile-status.json'


def _batch(values, cursors, kind):
    """Keep persistent defects from monopolizing subsequent passes."""
    if not values:
        cursors.pop(kind, None)
        return ()
    ordered = sorted((hashlib.sha256(str(value).encode('utf-8', 'surrogatepass')).hexdigest(), value)
                     for value in values)
    previous = cursors.get(kind)
    start = bisect_right(ordered, previous, key=lambda item: item[0]) if isinstance(previous, str) else 0
    start %= len(ordered)
    selected = ordered[start:start+BATCH_FILES]
    cursors[kind] = selected[-1][0]
    return tuple(value for _, value in selected)


def _permitted(relative):
    # Recheck paths after the inventory. Missing parents are valid for a deleted
    # note, but linked directories and offline placeholders are never followed.
    candidate = cfg.VAULT / index._path(relative)
    for path in (candidate, *candidate.parents):
        if path == cfg.VAULT:
            break
        if path.is_symlink() or path.is_junction():
            return False
        try:
            if index._offline(path.lstat()):
                return False
        except (FileNotFoundError, NotADirectoryError):
            continue
    return candidate.resolve().is_relative_to(cfg.VAULT)


async def _repair(plan, counts, cursors):
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.ignore_utils import should_ignore_path
    from basic_memory.index.local_dependencies import build_local_index_project_dependencies
    from basic_memory.index.local_project import LocalProjectIndexDeletePathVerifier
    from basic_memory.indexing.project_index_maintenance import (
        ProjectIndexDeleteBatch, RepositoryProjectIndexMaintenanceStore,
        RepositoryProjectIndexMovedEntitySearchRefresher,
    )
    from basic_memory.repository import ProjectRepository

    config = ConfigManager().config
    if not config.is_locally_syncable('brain', str(cfg.VAULT)):
        raise index.IndexAttention('project_config_path')
    database = config.database_path
    if database.is_symlink() or database.is_junction() or database.resolve() != cfg.STATE / 'memory.db':
        raise index.IndexAttention('database_path_mismatch')
    _, sessions = await db.get_or_create_db(db_path=database, db_type=db.DatabaseType.FILESYSTEM)
    try:
        async with db.scoped_session(sessions) as session:
            project = await ProjectRepository().get_by_name(session, 'brain')
        if (project is None or not project.is_active
                or not config.is_locally_syncable('brain', project.path)
                or Path(project.path).resolve() != cfg.VAULT):
            raise index.IndexAttention('database_project_mismatch')
        runtime = await build_local_index_project_dependencies(project)

        removed = tuple(path for path in _batch(plan.removed, cursors, 'removed') if _permitted(path))
        vector_ids = set(plan.vectors)
        if removed:
            # The upstream store re-probes absence and compares accepted note
            # lineage in its transaction. It skips unsynchronized DB content.
            store = RepositoryProjectIndexMaintenanceStore(
                runtime.session_maker, runtime.project_id,
                external_vector_cleaner=runtime.external_vector_cleaner,
                delete_path_verifier=LocalProjectIndexDeletePathVerifier(runtime.file_service),
            )
            deleted = await store.apply_project_index_delete_batch(ProjectIndexDeleteBatch(1, removed))
            counts['removed'] = deleted.deleted_entities
            refresher = RepositoryProjectIndexMovedEntitySearchRefresher(
                runtime.session_maker, runtime.entity_repository, runtime.search_service,
            )
            await refresher.refresh_moved_entities(tuple(deleted.relation_cleanup_entity_ids))
            vector_ids.update(deleted.relation_cleanup_entity_ids)

        patterns = index._patterns()
        for path in _batch(plan.files, cursors, 'files'):
            try:
                if not _permitted(path) or should_ignore_path(cfg.VAULT / path, cfg.VAULT, patterns):
                    continue
                # This pinned indexer also refreshes FTS for unchanged file SHA.
                result = await runtime.file_indexer.index_file(path, source='local-index-reconcile')
                counts['refreshed'] += 1
                vector_ids.add(result.entity_id)
            except Exception:
                counts['file_failures'] += 1

        selected_vectors = list(_batch(vector_ids, cursors, 'vectors'))
        if config.semantic_search_enabled and selected_vectors:
            for _ in range(VECTOR_PASSES):
                result = await runtime.search_service.sync_entity_vectors_batch(selected_vectors)
                counts['vector_passes'] += 1
                counts['vector_failures'] = result.entities_failed
                if not result.entities_deferred or not result.embedding_jobs_total:
                    break
    finally:
        await db.shutdown_db()


async def _bounded_repair(plan, counts, cursors):
    async with asyncio.timeout(REPAIR_SECONDS):
        await _repair(plan, counts, cursors)


def _previous_cursors():
    try:
        cursors = index._json(cfg.STATE / RECEIPT, limit=8192).get('cursors')
    except (OSError, ValueError, RecursionError):
        return {}
    return {kind: cursors[kind] for kind in ('removed', 'files', 'vectors')
            if isinstance(cursors.get(kind), str)} if isinstance(cursors, dict) else {}


def _save(report):
    target = cfg.STATE / RECEIPT
    if target.is_symlink() or target.is_junction():
        raise index.IndexAttention('linked_index_reconcile_status')
    temporary = target.with_name('.index-reconcile-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(report, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    try:
        lock = FileLock(str(cfg.STATE / 'index-bootstrap.lock'), timeout=0)
        with lock:
            cfg.write_admission()
            started = time.monotonic()
            plan = index.RepairPlan()
            current = index.status(repair=plan)
            cursors = _previous_cursors()
            counts = dict(refreshed=0, removed=0, file_failures=0, vector_passes=0, vector_failures=0)
            error = None
            if current.get('inventory_stable') and current['coverage']['eligible'] and (plan.files or plan.removed or plan.vectors):
                try:
                    cfg.require_local_search_runtime()
                    os.environ['LOGFIRE_IGNORE_NO_CONFIG'] = '1'
                    from loguru import logger
                    logger.disable('basic_memory')
                    asyncio.run(_bounded_repair(plan, counts, cursors))
                except Exception as exc:
                    # Only controlled classifications reach the service receipt.
                    error = type(exc).__name__
                current = index.status()
            _save({
                'status': current['status'], 'checked_at': current['checked_at'],
                'reason': current['reason'], 'counts': counts, 'repair_error_type': error,
                'cursors': {} if current['status'] == 'verified' else cursors,
                'elapsed_seconds': round(time.monotonic() - started, 2),
            })
    except Timeout:
        return  # The initial/manual indexer owns the lock.
