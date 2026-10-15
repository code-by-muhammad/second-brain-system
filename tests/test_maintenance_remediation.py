import json
import yaml
import pytest
import brain_maintenance as maintenance
import brain_git
import brain_settings as cfg
import brain_store as store
from test_sync import pair, git

@pytest.mark.parametrize('metadata',[
    {'sources':'[[01-Projects/Source]]'}, {'sources':['[[01-Projects/Source]]']},
    {'sources':['https://example.invalid/source.md']}, {'sources':None},
    {'sources':'01-Projects/Source.md'}, {'sources':[None,{'path':'Source.md'}]},
    {'sources':['01-Projects/Source.md'],'source_hashes':['bad']},
    {'sources':['01-Projects/Source.md'],'source_hashes':{'01-Projects/Source.md':['bad']}},
    {'sources':['01-Projects/Source.md'],'source_hashes':None},
])
def test_optional_bad_metadata_is_contained_and_git_still_runs(brain,monkeypatch,metadata):
    vault,state=brain
    (vault/'01-Projects').mkdir(); (vault/'01-Projects/Source.md').write_text('Synthetic source.\n')
    folder=vault/'02-Areas/AI-Memory'; folder.mkdir(parents=True)
    path=folder/'Optional.md'; path.write_text('---\n'+yaml.safe_dump(metadata)+'---\n\nDerived note.\n')
    before=path.read_bytes(); calls=[]
    monkeypatch.setattr(brain_git,'sync',lambda: calls.append(True) or 'synced')
    result=maintenance.maintenance()
    assert calls==[True] and result['git']=='synced' and result['status']=='completed'
    assert path.read_bytes()==before
    assert json.loads((state/'maintenance-status.json').read_text())['git']=='synced'

@pytest.mark.parametrize('bad_fields',[{'event_id':42},{'event_id':['bad']},{'source_app':{'bad':'type'}},
                                        {'event_id':'break-->marker'},{'captured_at':{'bad':'date'}}])
def test_malformed_capture_does_not_block_valid_capture_or_sync(brain,monkeypatch,bad_fields):
    vault,state=brain
    (vault/'Templates').mkdir(); (vault/'Templates/daily-note.md').write_text('# Synthetic daily\n\n## Work Log\n')
    capture=store.capture('valid-capture','Synthetic milestone','Synthetic result.','test','synthetic fixture')
    meta={'captured_at':'2026-10-15T05:00:00+05:00','event_id':'bad-capture','source_app':'test',**bad_fields}
    path=vault/'00-Inbox/AI-Captures/malformed.md'
    path.write_text('---\n'+yaml.safe_dump(meta)+'---\n\n# Malformed capture\n'); before=path.read_bytes()
    calls=[]; monkeypatch.setattr(brain_git,'sync',lambda: calls.append(True) or 'synced')
    result=maintenance.maintenance()
    assert result['captures_linked']==1 and calls==[True]
    assert path.read_bytes()==before and any(r['path'].endswith('malformed.md') for r in result['review'])

def test_fatal_maintenance_error_replaces_stale_success(brain,monkeypatch):
    import brain_wiki
    vault,state=brain
    (state/'maintenance-status.json').write_text(json.dumps({'git':'synced','at':'old status'}))
    def fail(): raise RuntimeError('synthetic private marker must stay local')
    monkeypatch.setattr(brain_wiki,'cleanup',fail)
    with pytest.raises(RuntimeError): maintenance.maintenance()
    result=json.loads((state/'maintenance-status.json').read_text())
    assert result['status']=='needs attention' and result['git']=='not completed: maintenance aborted'
    assert result['at']!='old status' and 'synthetic private marker' not in json.dumps(result)

def test_git_error_does_not_leak_unknown_exception_text(brain,monkeypatch):
    def fail(): raise RuntimeError('synthetic private marker must stay local')
    monkeypatch.setattr(brain_git,'sync',fail)
    result=maintenance.maintenance()
    assert result['status']=='needs attention' and 'synthetic private marker' not in json.dumps(result)

def test_ownership_mismatch_can_pull_handoff_then_aggregate_next_cycle(brain,tmp_path,monkeypatch):
    a,b=pair(tmp_path)
    control=a/'02-Areas/AI-Memory/Wiki/Control.md'; control.parent.mkdir(parents=True)
    control.write_text('---\nmaintainer_device_id: previous-host\n---\n')
    (a/'Templates').mkdir(); (a/'Templates/daily-note.md').write_text('# Daily\n\n## Work Log\n')
    git(a,'add','.'); git(a,'commit','-m','previous owner'); git(a,'push','origin','HEAD')
    git(b,'pull','--ff-only')
    remote_control=b/'02-Areas/AI-Memory/Wiki/Control.md'
    remote_control.write_text('---\nmaintainer_device_id: test-host\n---\n')
    git(b,'add','.'); git(b,'commit','-m','explicit synthetic owner handoff'); git(b,'push','origin','HEAD')
    monkeypatch.setattr(store,'VAULT',a); monkeypatch.setattr(cfg,'VAULT',a)
    store.capture('handoff-capture','Synthetic event','Synthetic handoff result.','test','synthetic fixture')
    first=maintenance.maintenance()
    assert first['captures_linked']==0 and first['aggregation'].startswith('skipped: shared ownership')
    assert first['git']=='synced' and 'test-host' in control.read_text()
    assert not (a/'05-Daily').exists()
    second=maintenance.maintenance()
    assert second['captures_linked']==1 and second['git']=='synced'

def test_health_review_is_bounded_without_discarding_local_diagnostics(brain):
    vault,state=brain; path=state/'maintenance-status.json'
    original={'git':'synced','review':[{'path':f'02-Areas/AI-Memory/{i}.md','reasons':['synthetic issue']*20} for i in range(1000)]}
    path.write_text(json.dumps(original))
    result=maintenance.health_status(path)
    assert len(result['review'])==20 and result['review_count']==1000 and result['review_truncated'] is True
    assert result['review'][0]['reason_count']==20 and len(result['review'][0]['reasons'])==5
    assert result['review'][0]['reasons_truncated'] is True
    assert json.loads(path.read_text())==original

def test_bad_health_status_returns_controlled_diagnostic(brain):
    path=brain[1]/'maintenance-status.json'; path.write_text('not JSON')
    assert maintenance.health_status(path)['status']=='needs attention'
