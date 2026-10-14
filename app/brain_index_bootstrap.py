"""Portable first-index completion. No source rewrites or Git operations."""
import json, os, sqlite3, subprocess
from filelock import FileLock
import brain_settings as cfg
os.environ.update(cfg.environment())

def main():
    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=1):
        path=cfg.STATE/'index-bootstrap-status.json'
        path.write_text(json.dumps({'status':'indexing'}),encoding='utf-8')
        binary=cfg.PYTHON.parent/('bm.exe' if os.name=='nt' else 'bm')
        with (cfg.ROOT/'logs/index-bootstrap.log').open('ab') as log:
            result=subprocess.run([str(binary),'reindex','-p','brain'],env=cfg.environment(),stdout=log,stderr=log,timeout=7200,**cfg.child_options())
        with sqlite3.connect(cfg.STATE/'memory.db') as conn:
            counts={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('entity','search_vector_chunks','search_vector_embeddings_rowids')}
        good=result.returncode==0 and counts['entity']>0 and counts['search_vector_chunks']==counts['search_vector_embeddings_rowids']
        path.write_text(json.dumps({'status':'verified' if good else 'needs attention','counts':counts,'verification':'process exit and chunk/vector row counts; retrieval quality tested separately'}),encoding='utf-8')

if __name__=='__main__': main()
