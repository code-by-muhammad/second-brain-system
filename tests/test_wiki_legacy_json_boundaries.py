"""Synthetic dry-run migration must report damaged artifacts without note edits."""
from pathlib import Path
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture

@pytest.mark.parametrize('shape',['journal-scalar','journal-list','journal-deep','input-source-scalar','input-text-scalar','input-deep','output-deep','valid-control','missing-input-control','malformed-output-control'])
def test_legacy_dry_run_reports_unreadable_artifacts_without_uncontrolled_exception(brain,shape):
    source,packet,result=fixture(brain);w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];deep=b'['*100_000+b'0'+b']'*100_000
    if shape=='journal-scalar':(folder/'journal.json').write_bytes(b'42')
    elif shape=='journal-list':(folder/'journal.json').write_bytes(b'[]')
    elif shape=='journal-deep':(folder/'journal.json').write_bytes(deep)
    elif shape in ('input-source-scalar','input-text-scalar'):
        value=json.loads((folder/'input.json').read_text())
        if shape=='input-source-scalar':value['sources'][0]=42
        else:value['sources'][0]['text']=42
        (folder/'input.json').write_text(json.dumps(value),encoding='utf-8')
    elif shape=='input-deep':(folder/'input.json').write_bytes(deep)
    elif shape=='output-deep':(folder/'output.json').write_bytes(deep)
    elif shape=='missing-input-control':(folder/'input.json').unlink()
    elif shape=='malformed-output-control':(folder/'output.json').write_bytes(b'{invalid synthetic json')
    snapshot=lambda root:{p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    private,notes=snapshot(folder),snapshot(s.VAULT);report=None;error=None
    try:report=w.migrate_legacy_receipts(dry_run=True)
    except Exception as exc:error=type(exc).__name__
    observation={'shape':shape,'exception':error,'report':report,'private_preserved':snapshot(folder)==private,'notes_preserved':snapshot(s.VAULT)==notes}
    assert snapshot(folder)==private and snapshot(s.VAULT)==notes
    assert error is None and isinstance(report,dict)
    if shape.startswith('journal-'):assert any(row['batch_id']==packet['batch_id'] for row in report['rejected_batches'])

from test_wiki_remediation import make_legacy

def snap(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}

@pytest.mark.parametrize('artifact',['input-scalar','input-row','input-text','input-deep','output-deep','output-invalid'])
@pytest.mark.parametrize('dry_run',[True,False])
def test_unreadable_legacy_artifact_fallback_requires_real_preserved_claim(brain,artifact,dry_run):
    source,packet,result=fixture(brain)
    folder=make_legacy(packet,w.apply(packet,result)); original=source.read_bytes()
    if artifact=='input-scalar': (folder/'input.json').write_bytes(b'42')
    elif artifact in ('input-row','input-text'):
        changed=json.loads((folder/'input.json').read_text())
        if artifact=='input-row': changed['sources'][0]=42
        else: changed['sources'][0]['text']=42
        w._atomic_json(folder/'input.json',changed)
    else:
        target=folder/('input.json' if artifact.startswith('input') else 'output.json')
        target.write_bytes(b'['*100000+b'0'+b']'*100000 if artifact.endswith('deep') else b'{bad')
    private=snap(folder); notes=snap(s.VAULT)
    report=w.migrate_legacy_receipts(dry_run=dry_run)
    assert report['verified_parts']==1 and not report['unverified_parts']
    assert snap(folder)==private and source.read_bytes()==original
    if dry_run: assert snap(s.VAULT)==notes and not report['receipts_created']
    else:
        assert len(report['receipts_created'])==1
        assert all(snap(s.VAULT)[key]==value for key,value in notes.items())
        assert not w.migrate_legacy_receipts()['receipts_created']

@pytest.mark.parametrize('shape',['scalar','list','deep'])
def test_invalid_legacy_journal_real_migration_preserves_all_artifacts(brain,shape):
    _,packet,result=fixture(brain);folder=make_legacy(packet,w.apply(packet,result))
    (folder/'journal.json').write_bytes({'scalar':b'42','list':b'[]','deep':b'['*100000+b'0'+b']'*100000}[shape])
    private,notes=snap(folder),snap(s.VAULT)
    report=w.migrate_legacy_receipts()
    assert len(report['rejected_batches'])==1 and not report['receipts_created']
    assert snap(folder)==private and snap(s.VAULT)==notes

def test_unreadable_legacy_input_does_not_fabricate_missing_claim_evidence(brain):
    _,packet,result=fixture(brain);folder=make_legacy(packet,w.apply(packet,result))
    (folder/'input.json').write_bytes(b'42')
    topic=s.VAULT/w.BASE/'Projects/example.md'
    text=topic.read_text(encoding='utf-8'); metadata=w.frontmatter(text,strict=True)
    metadata['wiki_claims'][0]['quote']='This quotation is absent from the original evidence.'
    topic.write_bytes(s.note_text(metadata,text.split('---',2)[2]).encode())
    notes,private=snap(s.VAULT),snap(folder)
    report=w.migrate_legacy_receipts()
    assert report['verified_parts']==0 and len(report['unverified_parts'])==1 and not report['receipts_created']
    assert snap(s.VAULT)==notes and snap(folder)==private

@pytest.mark.parametrize('artifact',['journal.json','input.json','output.json'])
def test_fatal_memory_error_is_not_reclassified_as_unreadable_legacy_data(brain,monkeypatch,artifact):
    _,packet,result=fixture(brain);folder=make_legacy(packet,w.apply(packet,result))
    (folder/'output.json').write_text(json.dumps(result),encoding='utf-8')
    target=folder/artifact; original=Path.read_bytes if artifact=='journal.json' else Path.read_text
    method='read_bytes' if artifact=='journal.json' else 'read_text'
    def fail(path,*args,**kwargs):
        if path==target: raise MemoryError('synthetic fatal allocation')
        return original(path,*args,**kwargs)
    notes,private=snap(s.VAULT),snap(folder)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path,method,fail)
        with pytest.raises(MemoryError): w.migrate_legacy_receipts()
    assert snap(s.VAULT)==notes and snap(folder)==private
