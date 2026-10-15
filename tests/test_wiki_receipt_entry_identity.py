"""Malformed optional entries never create proof or interrupt valid migration."""
import copy,json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture

def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}

def compile_fixture(brain,kind):
    source,packet,result=fixture(brain)
    if kind=='skipped':
        row=packet['sources'][0]
        result={'pages':[],'dispositions':[{'source':row['path'],'part':row['part'],'status':'skipped','reason':'This synthetic administrative source was reviewed without a reusable topic.','quote':'A prototype was demonstrated.'}]}
    w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    return source,packet,folder

def change_key(entry,shape):
    if shape=='scalar':return 42
    entry=copy.deepcopy(entry)
    if shape=='missing':entry.pop('job_key')
    elif shape!='valid':entry['job_key']={'list':[],'dict':{},'null':None,'number':42,'bool':True,'unknown':'01-Projects/Unknown.md::part1'}[shape]
    return entry

@pytest.mark.parametrize('shape',['list','dict','null','number','bool','unknown','missing','scalar','valid'])
@pytest.mark.parametrize('kind',['published','skipped'])
@pytest.mark.parametrize('dry_run',[True,False])
def test_complete_journal_entries_require_identity_or_independent_claim_evidence(brain,shape,kind,dry_run):
    source,packet,folder=compile_fixture(brain,kind);original=source.read_bytes()
    journal=folder/'journal.json';saved=json.loads(journal.read_text(encoding='utf-8'))
    assert saved['state']=='complete'
    saved['entries']=[change_key(saved['entries'][0],shape)]
    journal.write_text(json.dumps(saved),encoding='utf-8')
    # Remove synthetic portable receipts so migration must prove this journal's
    # entry or use actual preserved claims, not another already-valid receipt.
    for path in (s.VAULT/w.BASE/'Receipts').glob('*.md'):path.unlink()
    notes,private=snapshot(s.VAULT),snapshot(folder)
    report=w.migrate_legacy_receipts(dry_run=dry_run,requeue_unverified=True)
    proven=kind=='published' or shape=='valid'
    assert report['verified_parts']==int(proven)
    assert len(report['unverified_parts'])==int(not proven)
    assert report['requeued_parts']==int(not dry_run and not proven)
    assert len(report['receipts_created'])==int(not dry_run and proven)
    assert snapshot(folder)==private and source.read_bytes()==original
    assert all(snapshot(s.VAULT)[name]==raw for name,raw in notes.items())
    if dry_run or not proven:assert snapshot(s.VAULT)==notes
    if not dry_run and proven:
        assert w._hydrate_receipts(w.inventory())['verified_parts']==1
        assert not w.migrate_legacy_receipts()['receipts_created']
    elif not dry_run:
        assert w._hydrate_receipts(w.inventory())['verified_parts']==0
        assert w.status()['pending_source_parts']==1

@pytest.mark.parametrize('kind',['published','skipped'])
@pytest.mark.parametrize('shape',['list','dict'])
def test_invalid_optional_receipt_coexists_with_valid_real_proof(brain,kind,shape):
    source,packet,folder=compile_fixture(brain,kind)
    valid=s.VAULT/w.BASE/'Receipts'/f"{packet['batch_id']}.md"
    metadata=w.meta(valid.read_text(encoding='utf-8'));payload=metadata['wiki_receipt']
    payload['parts'][0]=change_key(payload['parts'][0],shape)
    metadata['receipt_sha256']=w._json_hash(payload)
    invalid=valid.with_name('invalid-optional.md');invalid.write_bytes(s.note_text(metadata,'# Synthetic optional malformed receipt').encode())
    notes,private=snapshot(s.VAULT),snapshot(folder)
    result=w._hydrate_receipts(w.inventory())
    assert result['verified_parts']==1 and len(result['issues'])==1
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private

@pytest.mark.parametrize('damage',['source-change','claim-tamper'])
def test_malformed_journal_entry_cannot_rescue_unproved_migration(brain,damage):
    source,packet,folder=compile_fixture(brain,'published')
    saved=json.loads((folder/'journal.json').read_text(encoding='utf-8'))
    saved['entries'][0]['job_key']=[];w._atomic_json(folder/'journal.json',saved)
    for path in (s.VAULT/w.BASE/'Receipts').glob('*.md'):path.unlink()
    if damage=='source-change':source.write_bytes(source.read_bytes()+b'\nAn owner changed current source evidence.\n')
    else:
        topic=s.VAULT/w.BASE/'Projects/example.md';text=topic.read_text(encoding='utf-8');metadata=w.meta(text)
        metadata['wiki_claims'][0]['quote']='A quotation not present in any original source.'
        topic.write_bytes(s.note_text(metadata,'# Owner edited evidence').encode())
    notes,private=snapshot(s.VAULT),snapshot(folder)
    report=w.migrate_legacy_receipts(requeue_unverified=True)
    assert report['verified_parts']==0 and not report['receipts_created']
    assert w._hydrate_receipts(w.inventory())['verified_parts']==0
    assert snapshot(s.VAULT)==notes and snapshot(folder)==private
