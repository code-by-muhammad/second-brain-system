from datetime import datetime, timedelta, timezone
from test_wiki import fixture
import brain_wiki as w
import brain_settings as cfg

def test_cleanup_keeps_receipts_sources_and_pending_jobs(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    pending=cfg.STATE/'wiki/batches/pending'; pending.mkdir(); (pending/'input.json').write_text('synthetic pending input')
    old=(datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
    with w.db() as conn: conn.execute('UPDATE batches SET updated=? WHERE id=?',(old,packet['batch_id']))
    assert w.cleanup()>0
    assert source.exists() and (pending/'input.json').exists()
    assert (folder/'journal.json').exists(), 'Saved memory source references must remain readable after artifact cleanup.'
    with w.db() as conn: assert conn.execute('SELECT receipt FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
