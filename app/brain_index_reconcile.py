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
# Cancelling the repair cannot stop model work already handed to a thread, and the process would wait for it
# while holding the index lock. Past this grace the process ends itself; SQLite rolls an open write back.
EXIT_GRACE_SECONDS = 60
# One pass is two coverage scans, one inventory and the repair. The scans carry their own limit; this is
# the sum, after which the process ends itself whatever it is waiting on, so its locks cannot stay held.
PASS_SECONDS = 3 * index.MAX_STATUS_SECONDS + REPAIR_SECONDS + EXIT_GRACE_SECONDS
RECEIPT_GRACE_SECONDS = 5
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


async def _repair(plan, counts, cursors, eligible, manifest):
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
        followups = []
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
            # Notes that linked to a removed one need their search rows refreshed. They are refreshed as files
            # below, so each one passes the same screening, and only if this scan found it eligible: a source
            # in a folder that is ignored now, linked, offline or over the size limit is never opened.
            related = sorted(set(deleted.relation_cleanup_entity_ids))
            if related:
                async with db.scoped_session(runtime.session_maker) as session:
                    sources = await runtime.entity_repository.find_by_ids(session, related)
                followups = [entity.file_path for entity in sources
                             if isinstance(entity.file_path, str) and entity.file_path in eligible]
            vector_ids.update(deleted.relation_cleanup_entity_ids)

        patterns = index._patterns()
        for path in dict.fromkeys((*_batch(plan.files, cursors, 'files'), *followups)):
            try:
                if not _permitted(path) or should_ignore_path(cfg.VAULT / path, cfg.VAULT, patterns):
                    continue
                # This pinned indexer also refreshes FTS for unchanged file SHA.
                result = await runtime.file_indexer.index_file(path, source='local-index-reconcile')
                counts['refreshed'] += 1
                vector_ids.add(result.entity_id)
            except Exception:
                counts['file_failures'] += 1

        # Notes that changed while this ran are left to the next pass: no vectors are built from a scan
        # that no longer describes the folder.
        if index.inventory(_scan_limit())[1] != manifest:
            counts['inventory_changed'] = 1
            return
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


async def _bounded_repair(plan, counts, cursors, eligible, manifest):
    async with asyncio.timeout(REPAIR_SECONDS):
        await _repair(plan, counts, cursors, eligible, manifest)


def _scan_limit():
    """A check for one folder scan: it raises once the scan has run as long as a coverage scan may."""
    deadline = time.monotonic() + index.MAX_STATUS_SECONDS

    def check():
        if time.monotonic() >= deadline:
            raise index.IndexAttention('reconcile_scan_time_limit')
    return check


def _deadline_exit():
    # Reached only when a pass is still running past everything it may take: a scan that does not return, or a
    # repair past its budget and grace. The receipt says so, the process ends at once, and the operating
    # system releases the locks it held.
    # The exit itself depends on nothing: a second timer ends the process a few seconds later even if writing
    # the receipt never returns. The receipt is best effort.
    import threading
    last = threading.Timer(RECEIPT_GRACE_SECONDS, os._exit, (75,))
    last.daemon = True
    last.start()
    try:
        _save({'status': 'needs review', 'checked_at': None, 'reason': 'repair_deadline', 'counts': {},
               'repair_error_type': 'RepairDeadline', 'cursors': _previous_cursors(),
               'elapsed_seconds': PASS_SECONDS})
    finally:
        os._exit(75)


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
        # The index lock keeps the initial and manual indexer out. The maintenance lock keeps out the pass that
        # links milestones and synchronizes with Git, the managed changes to the notes; a pass that is running
        # owns it, and this repair waits for its next turn.
        lock = FileLock(str(cfg.STATE / 'index-bootstrap.lock'), timeout=0)
        with lock, FileLock(str(cfg.STATE / 'maintenance.lock'), timeout=0):
            cfg.write_admission()
            started = time.monotonic()
            # Armed before the first scan and for the whole pass: nothing in it may keep the locks for longer.
            import threading
            watchdog = threading.Timer(PASS_SECONDS, _deadline_exit)
            watchdog.daemon = True
            watchdog.start()
            try:
                plan = index.RepairPlan()
                current = index.status(repair=plan)
                cursors = _previous_cursors()
                counts = dict(refreshed=0, removed=0, file_failures=0, vector_passes=0, vector_failures=0, inventory_changed=0)
                error = None
                # An empty inventory is never repaired: a notes folder that is unavailable looks the same, and
                # removing every entry on that evidence would cost a whole rebuild.
                if current.get('inventory_stable') and current['coverage']['eligible'] and (plan.files or plan.removed or plan.vectors):
                    try:
                        cfg.require_local_search_runtime()
                        os.environ['LOGFIRE_IGNORE_NO_CONFIG'] = '1'
                        from loguru import logger
                        logger.disable('basic_memory')
                        eligible, manifest = index.inventory(_scan_limit())
                        # The plan was made from one scan; it is only acted on while the folder still matches it.
                        if manifest != current['coverage'].get('manifest_sha256'):
                            counts['inventory_changed'] = 1
                        else:
                            asyncio.run(_bounded_repair(plan, counts, cursors, frozenset(eligible), manifest))
                    except (Exception, SystemExit) as exc:
                        # Only the kind of failure reaches the receipt. The upstream configuration loader ends with
                        # SystemExit and a message that can quote the configuration; that message is not passed on.
                        error = type(exc).__name__
                    current = index.status()
                # The receipt is written while the watchdog is still armed: storage that stalls here ends the
                # process like anything else in the pass.
                _save({
                    'status': current['status'], 'checked_at': current['checked_at'],
                    'reason': current['reason'], 'counts': counts, 'repair_error_type': error,
                    'cursors': {} if current['status'] == 'verified' else cursors,
                    'elapsed_seconds': round(time.monotonic() - started, 2),
                })
            finally:
                watchdog.cancel()
    except Timeout:
        return  # The initial/manual indexer owns the lock.
