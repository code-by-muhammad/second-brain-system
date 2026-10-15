"""Portable first-index completion. No source rewrites or Git operations."""
import json, os, sqlite3, subprocess
from datetime import datetime, timezone
from filelock import FileLock
import brain_settings as cfg
os.environ.update(cfg.environment())

def main():
    cfg.STATE.mkdir(parents=True,exist_ok=True)
    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=1):
        path=cfg.STATE/'index-bootstrap-status.json'
        def save(status):
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(status),encoding='utf-8')
            os.replace(temp,path)
            return status
        save({'status':'indexing','started_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
        stage='launching reindex'
        try:
            binary=cfg.PYTHON.parent/('bm.exe' if os.name=='nt' else 'bm')
            (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True)
            with (cfg.ROOT/'logs/index-bootstrap.log').open('ab') as log:
                result=subprocess.run([str(binary),'reindex','-p','brain'],env=cfg.environment(),stdout=log,stderr=log,timeout=7200,**cfg.child_options())
            stage='verifying local index'
            with sqlite3.connect((cfg.STATE/'memory.db').resolve().as_uri()+'?mode=ro',uri=True) as conn:
                counts={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('entity','search_vector_chunks','search_vector_embeddings_rowids')}
            good=result.returncode==0 and counts['entity']>0 and counts['search_vector_chunks']==counts['search_vector_embeddings_rowids']
            return save({'status':'verified' if good else 'needs attention','counts':counts,'process_exit':result.returncode,
                         'verification':'process exit and chunk/vector row counts; retrieval quality tested separately',
                         'finished_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
        except Exception as exc:
            # This terminal state stops the supervisor's automatic bootstrap loop.
            # Keep raw subprocess/source diagnostics only in the private log.
            return save({'status':'needs attention','stage':stage,'error_type':type(exc).__name__,
                         'error':'Initial indexing failed; inspect the private bootstrap log and repair before retrying.',
                         'finished_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})

if __name__=='__main__': main()
