"""A capped oversized policy prefix cannot be labelled as a full-file SHA."""
import json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_shared_read_bounds import observe,writer_held

@pytest.mark.parametrize('route',['run','status'])
def test_public_oversized_grown_policy_has_no_misleading_prefix_sha(brain,monkeypatch,tmp_path,route):
    policy=s.VAULT/w.SOURCE_POLICY;policy.parent.mkdir(parents=True)
    raw=b'# R160 private policy prefix\n```json\n{"version":1,"exclude_paths":[],"exclude_prefixes":[]}\n```\n'
    policy.write_bytes(raw)
    marker=cfg.STATE/'original-state.json';marker.write_text('{"untouched":true}\n',encoding='utf-8')
    original_state={p.relative_to(cfg.STATE).as_posix():p.read_bytes() for p in cfg.STATE.rglob('*') if p.is_file()}
    with monkeypatch.context() as patch:
        reads,growth,models=observe(patch,policy,grow_to=131074)
        result=w.run(1) if route=='run' else w.status()
    expected=raw+b' '*(131074-len(raw));actual=policy.read_bytes()
    observed={'route':route,'actual_bytes':len(actual),'actual_full_sha256':s.digest(actual),'capped_prefix_sha256':s.digest(actual[:131073]),'reported_sha256':result['source_policy']['sha256'],'status':result['status'],'valid':result['source_policy']['valid'],'read_requests':reads,'growth':growth,'models':models,'writer_released':not writer_held()}
    (tmp_path/'r160-policy-sha-observation.json').write_text(json.dumps(observed,indent=2)+'\n',encoding='utf-8')
    assert actual==expected and actual[:131073]!=actual
    assert original_state=={p.relative_to(cfg.STATE).as_posix():p.read_bytes() for p in cfg.STATE.rglob('*') if p.is_file() and p.suffix!='.lock'}
    assert result['status']=='attention' and result['source_policy']['valid'] is False
    assert 'policy exceeds 128 KiB' in result['reason']
    assert reads and all(row['requested']==131073 and not row['under_actual_writer'] for row in reads)
    assert growth==[{'before':len(raw),'after':131074}] and not models and not writer_held()
    assert 'R160 private policy prefix' not in json.dumps(result)
    assert observed['actual_full_sha256']!=observed['capped_prefix_sha256']
    assert result['source_policy']['sha256'] is None,observed
