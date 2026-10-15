"""Decoded and structured metadata must obey the established credential guard."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


@pytest.mark.parametrize('extra',[{'password':'synthetic_mock_value_1234567890'},{'api_key':'synthetic_mock_value_1234567890'},'password\t=\tsynthetic_mock_value_1234567890'])
def test_decoded_structured_credential_context_stops_before_model_and_lint_is_actionable(brain,monkeypatch,extra):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8')); info['wiki_claims'][0]['owner_note']=extra
    topic.write_bytes(s.note_text(info,'# Synthetic context only.').encode()); before=note_snapshot()
    rows=[row for row in w.inventory() if row['path']==result['pages'][0]['claims'][0]['source']]
    calls=fake_model(monkeypatch,explicit({'sources':rows},result))
    try: report=w.run()
    except ValueError: report={}
    if calls: assert any('synthetic_mock_value_1234567890' in value for value in calls.inputs)
    assert len(calls)==0 and report['status']=='attention' and report['consecutive_failures']==0
    assert report['integrity_field']=='wiki_claims' and note_snapshot()==before
    lint=w.lint(); assert any(x.get('field')=='wiki_claims' and x.get('claim_index')==0 for x in lint['issues'])
    assert 'synthetic_mock_value_1234567890' not in json.dumps(lint) and 'synthetic_mock_value_1234567890' not in json.dumps(w.status()['worker'])


def test_saved_structured_context_is_screened_before_model(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); packet=w.prepare()
    packet['current_pages'][0]['meta']['wiki_claims'][0]['owner_note']={'password':'synthetic_mock_value_1234567890'}
    w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    before=note_snapshot(); calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='attention' and len(calls)==0 and report['consecutive_failures']==0
    assert note_snapshot()==before and report['integrity_page']==topic.relative_to(s.VAULT).as_posix()


def test_benign_structured_api_key_guidance_and_shared_alias_still_publish(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8'))
    shared={'api_key':'rotate via the provider console; never store tokens in notes'}
    info['wiki_claims'][0]['owner_note']={'instructions':shared,'same-guidance':shared}
    topic.write_bytes(s.note_text(info,'# Benign structured guidance.').encode()); packet=w.prepare()
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']


@pytest.mark.parametrize('kind',['cycle','deep','many-nodes'])
def test_malformed_or_excessive_structured_context_is_bounded_and_preserved(brain,monkeypatch,kind):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8'))
    if kind=='cycle':
        value={}; value['self']=value
    elif kind=='deep':
        value='A benign annotation'
        for _ in range(70): value={'next':value}
    else: value=[0]*20001
    info['wiki_claims'][0]['owner_note']=value
    topic.write_bytes(s.note_text(info,'# Preserve owner context requiring review.').encode()); before=note_snapshot()
    rows=[row for row in w.inventory() if row['path']==result['pages'][0]['claims'][0]['source']]
    calls=fake_model(monkeypatch,explicit({'sources':rows},result))
    if kind=='many-nodes':
        # Oversize claim context is intentionally omitted before model preflight;
        # selecting its existing topic still validates it before any note write.
        with pytest.raises(w.WikiIntegrityError): w.run()
        report=w.run(); assert len(calls)==1
    else:
        report=w.run(); assert len(calls)==0
    assert report['status']=='attention' and report['consecutive_failures']==0
    assert note_snapshot()==before and any(x.get('field')=='wiki_claims' for x in w.lint()['issues'])
