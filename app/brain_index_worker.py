"""The pinned Basic Memory reindex, with its quadratic vector cleanup replaced by upstream 0.24's joined, atomic one.

Run only as: <python> -I -B <root>/app/brain_index_worker.py reindex -p brain [--full [--search]]
with one line, start, on standard input. Nothing is imported from Basic Memory, and nothing is created, before it.

Exit 2: unsupported arguments. Exit 75: the launcher never sent the start line. Exit 78: this Python environment is
not the reviewed build, named on standard error as "index worker: guard_refused:<reason>"; nothing was indexed.
This file imports no brain_* module: -I removes its own folder from the import path.
"""
import os
import sys

ARGUMENTS=(['reindex','-p','brain'],['reindex','-p','brain','--full'],['reindex','-p','brain','--full','--search'])
REVIEWED_VERSION='0.23.2'
# sha256 of inspect.getsource(SQLiteVecIndex.delete_orphans) in basic-memory 0.23.2.
REVIEWED_SHA256='e4200587c6af4991695b6334d29c63d4fe41bff242c85424b983a3a0d2b60056'
GUARD_EXIT=78
UNRELEASED_EXIT=75
RELEASE=b'start\n'
RELEASE_SECONDS=30
REASONS=('linked_worker_script','basic_memory_missing','basic_memory_version_not_reviewed','basic_memory_import_failed',
         'vector_cleanup_missing','vector_cleanup_unreadable','vector_cleanup_not_reviewed')
# Global, as in 0.23.2 and 0.24.0: a vector with no chunk row at all belongs to no project.
ORPHANS=('DELETE FROM search_vector_embeddings WHERE rowid IN ('
         'SELECT rowid FROM search_vector_embeddings EXCEPT SELECT id FROM search_vector_chunks)')
# Project-scoped: a vector whose chunk row is stale. 0.23.2 read the vector's hash from inside the subquery, which
# SQLite ran again for every vector, each time over every chunk of the project. This is upstream 0.24.0's join.
STALE=('DELETE FROM search_vector_embeddings WHERE rowid IN ('
       'SELECT chunks.id FROM search_vector_chunks AS chunks '
       'JOIN search_vector_embeddings AS vectors ON vectors.rowid = chunks.id '
       'WHERE chunks.project_id = :project_id AND NOT ('
       "chunks.vector_index = 'sqlite-vec' "
       'AND chunks.embedding_model = :embedding_identity '
       'AND vectors.source_hash = chunks.source_hash '
       "AND chunks.embedding_status = 'ready'))")


class Refused(RuntimeError): pass


def install():
    """Replace the reviewed method, or raise Refused. Distribution metadata is read before any upstream import."""
    import hashlib
    import inspect
    from importlib import metadata
    try:found=metadata.version('basic-memory')
    except metadata.PackageNotFoundError:raise Refused('basic_memory_missing') from None
    if found!=REVIEWED_VERSION:raise Refused('basic_memory_version_not_reviewed')
    try:
        from sqlalchemy import text
        from basic_memory import db
        from basic_memory.repository import sqlite_vec_index as module
    except ImportError:raise Refused('basic_memory_import_failed') from None
    try:original=module.SQLiteVecIndex.__dict__['delete_orphans']
    except (AttributeError,KeyError):raise Refused('vector_cleanup_missing') from None
    try:source=inspect.getsource(original)
    except (OSError,TypeError):raise Refused('vector_cleanup_unreadable') from None
    if hashlib.sha256(source.encode()).hexdigest()!=REVIEWED_SHA256:raise Refused('vector_cleanup_not_reviewed')
    async def delete_orphans(self,_live_keys):
        # Same signature and scope as 0.23.2. Two statements in one transaction: each DELETE decides and removes
        # under the write lock, so a vector another writer has just refreshed is never removed from an earlier read.
        # The scoped session commits once when it is left and rolls both back on an error; there is no commit here.
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            await session.execute(text(ORPHANS))
            await session.execute(text(STALE),{'project_id':self.scope.project_id,
                                               'embedding_identity':self.scope.embedding_identity})
    module.SQLiteVecIndex.delete_orphans=delete_orphans


def released():
    """True once the launcher sends the start line. End of input means the launcher ended first."""
    import threading
    got=[]
    def read():
        try:got.append(sys.stdin.buffer.readline(len(RELEASE)+1))
        except (AttributeError,OSError,ValueError):pass
    reader=threading.Thread(target=read,daemon=True);reader.start();reader.join(RELEASE_SECONDS)
    return got==[RELEASE]


def main():
    if sys.argv[1:] not in ARGUMENTS:
        print('index worker: unsupported arguments',file=sys.stderr,flush=True);return 2
    here=os.path.abspath(__file__)
    if os.path.islink(here) or os.path.isjunction(here):
        print('index worker: guard_refused:linked_worker_script',file=sys.stderr,flush=True);return GUARD_EXIT
    if not released():
        print('index worker: not_released',file=sys.stderr,flush=True)
        os._exit(UNRELEASED_EXIT)  # The reader thread may still hold stdin; interpreter shutdown must not wait on it.
    try:
        install()
        try:from basic_memory.cli.main import app
        except ImportError:raise Refused('basic_memory_import_failed') from None
    except Refused as error:
        print('index worker: guard_refused:'+str(error),file=sys.stderr,flush=True);return GUARD_EXIT
    sys.argv[0]='bm'
    return app()


if __name__=='__main__':sys.exit(main())
