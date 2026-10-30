"""Actual local Windows rename with canonical/tamper/exclusion controls."""
import copy,json,os
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_saved_catalog_identity import brain
from test_wiki import fixture
from test_wiki_model_context_preflight import fake_skip_model
OUT=Path(__file__).resolve().parent
def snapshot(root):return {p.relative_to(root).as_posix():s.digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
def attempt():
    try:return w.run(limit=1),None
    except Exception as error:return None,type(error).__name__
def record(name,value):pass

@pytest.mark.parametrize('mode',['case-only-rename','different-name-control','unchanged-control','tampered-part-control','credential-exclusion-control'])
def test_saved_source_inventory_absence_is_distinct_from_tampered_present_part(brain,monkeypatch,mode):
    source,packet,result=fixture(brain);raw=source.read_bytes();old_path=source;folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    if mode in ('case-only-rename','different-name-control'):
        target=source.with_name('example.md' if mode=='case-only-rename' else 'Renamed.md')
        transient=source.with_name('RenameTemporary.md');source.rename(transient);transient.rename(target);source=target
    elif mode=='tampered-part-control':
        changed=copy.deepcopy(packet);changed['sources'][0].update(part=2,parts=2,job_key=packet['sources'][0]['path']+'::part2');w._atomic_json(folder/'input.json',changed)
    elif mode=='credential-exclusion-control':
        source.write_bytes(raw+b'\n'+('ghp_'+'SyntheticMarkerAlphaBetaGammaDelta').encode()+b'\n')
    old_hash=w._current_hash(old_path);inventory=w.inventory();names=[p.name for p in source.parent.iterdir()]
    if mode=='case-only-rename':
        assert source.name in names and 'Example.md' not in names
        if os.name=='nt':assert old_path.exists() and old_hash==packet['sources'][0]['sha256']
    before=snapshot(folder);notes=snapshot(s.VAULT);calls=fake_skip_model(monkeypatch)
    report,error=attempt();first_state=w._read_worker_state();first_calls=len(calls)
    retry=None;retry_error=None;retry_state=None
    if mode=='case-only-rename':
        w.reset_retries();retry,retry_error=attempt();retry_state=w._read_worker_state()
    with w.db() as conn:old_status=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    record('path-'+mode,{'mode':mode,'actual_os':os.name,'actual_directory_names':names,'old_spelling_exists':old_path.exists(),'old_spelling_raw_hash_matches':old_hash==packet['sources'][0]['sha256'],'canonical_inventory_paths':[r['path'] for r in inventory],'report':report,'exception':error,'first_worker':first_state,'first_model_calls':first_calls,'retry_report':retry,'retry_exception':retry_error,'retry_worker':retry_state,'total_model_calls':len(calls),'old_batch_status':old_status,'private_batch_preserved':snapshot(folder)==before,'vault_preserved':snapshot(s.VAULT)==notes,'source_bytes_preserved':source.read_bytes()==raw if mode!='credential-exclusion-control' else source.read_bytes()==raw+b'\n'+('ghp_'+'SyntheticMarkerAlphaBetaGammaDelta').encode()+b'\n'})
    if mode=='tampered-part-control':
        assert error=='WikiIntegrityError' and first_state.get('integrity_field')=='recovery' and first_state.get('attention_required') and not calls
        assert snapshot(folder)==before and snapshot(s.VAULT)==notes
    elif mode=='credential-exclusion-control':
        assert error is None and report['status']=='idle' and not calls and source.read_bytes()!=raw
    else:
        assert error is None and report is not None and report['status']=='complete' and first_calls==1
        assert source.read_bytes()==raw
        if mode in ('case-only-rename','different-name-control'):assert old_status=='stale' and snapshot(folder)==before

from test_wiki_saved_plan_repair import partial_index_conflict

def test_temporary_inventory_omission_retires_unused_selection_without_corruption(brain,monkeypatch):
    source,packet,_=fixture(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    private=snapshot(folder); raw=source.read_bytes(); inventory=w.inventory; seen=[]
    def omit_once(*args,**kwargs):
        seen.append(1)
        return [] if len(seen)==1 else inventory(*args,**kwargs)
    monkeypatch.setattr(w,'inventory',omit_once);calls=fake_skip_model(monkeypatch)
    report=w.run(limit=1)
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
    assert snapshot(folder)==private and source.read_bytes()==raw
    assert not w._read_worker_state()['attention_required']

def test_case_rename_does_not_release_actual_partial_owner_conflict(brain,monkeypatch):
    source,packet,calls,index,before,owner,plan=partial_index_conflict(brain,monkeypatch)
    original=source; target=source.with_name('example.md'); temporary=source.with_name('RenameTemporary.md')
    source.rename(temporary); temporary.rename(target)
    notes=snapshot(s.VAULT);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];private=snapshot(folder)
    with w.db() as conn: prior_status=conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]
    for _ in range(2):
        w.reset_retries();attempt()
        assert len(calls)==1 and snapshot(s.VAULT)==notes and snapshot(folder)==private
        with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==prior_status
    target.rename(temporary);temporary.rename(original)
    if before is None: index.unlink()
    else: index.write_bytes(before)
    w.reset_retries();assert w.run()['status']=='complete' and len(calls)==1
