import brain_maintenance as m
import brain_store as s
import brain_settings as cfg

def test_compilation_log_without_frontmatter_does_not_block_maintenance(brain):
    vault,state=brain; folder=vault/'02-Areas/AI-Memory/Wiki'; folder.mkdir(parents=True)
    (folder/'Log.md').write_text('# Wiki compilation log\n\nSynthetic history.\n')
    assert m.maintenance(sync=False)['git']=='not requested'

def test_malformed_capture_preserved_and_flagged(brain):
    vault,state=brain; folder=vault/'00-Inbox/AI-Captures'; folder.mkdir(parents=True)
    path=folder/'legacy.md'; path.write_text('# Legacy capture without metadata\n')
    result=m.maintenance(sync=False)
    assert path.exists() and result['review'][0]['reasons']==['capture metadata missing; original preserved']

def test_daily_aggregation_is_once_and_maintainer_only(brain,monkeypatch):
    vault,state=brain; (vault/'Templates').mkdir(); (vault/'Templates/daily-note.md').write_text('# Synthetic daily\n\n## Work Log\n')
    s.capture('maintenance-test','Synthetic event','Synthetic evidence.','test','synthetic fixture')
    monkeypatch.setattr(cfg,'ROLE','capture')
    assert m.maintenance(sync=False)['captures_linked']==0
    monkeypatch.setattr(cfg,'ROLE','maintainer')
    assert m.maintenance(sync=False)['captures_linked']==1
    assert m.maintenance(sync=False)['captures_linked']==0
    assert next((vault/'05-Daily').glob('*.md')).read_text().count('<!-- brain-capture:')==1
