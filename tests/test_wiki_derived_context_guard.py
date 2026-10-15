"""Synthetic credential markers must never enter derived model context."""
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import existing_topic, note_snapshot
from test_wiki_remediation import explicit, fake_model


@pytest.mark.parametrize('location',['claim-extra','title','claim-text'])
def test_derived_credential_marker_stops_before_model_and_lint_reports_controlled_field(brain,monkeypatch,location):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8')); marker='ghp_'+('A'*40)
    if location=='title': info['title']='Owner review '+marker
    else: info['wiki_claims'][0]['owner_note' if location=='claim-extra' else 'text']=marker
    topic.write_bytes(s.note_text(info,'# Synthetic marker only, preserved for owner repair.').encode()); before=note_snapshot()
    calls=fake_model(monkeypatch,explicit({'sources':[row for row in w.inventory() if row['path']==result['pages'][0]['claims'][0]['source']]},result))
    try: report=w.run()
    except ValueError: report={}
    if calls: assert any(marker in value for value in calls.inputs)
    assert len(calls)==0
    assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==0
    assert report['integrity_field']==('title' if location=='title' else 'wiki_claims')
    assert w.run()['status']=='attention' and len(calls)==0 and note_snapshot()==before
    lint=w.lint(); assert any(x.get('field')==report['integrity_field'] for x in lint['issues'])
    assert marker not in json.dumps(lint) and marker not in json.dumps(w.status()['worker'])


def test_old_saved_pending_context_is_screened_before_model_without_source_blame(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); packet=w.prepare(); marker='ghp_'+('A'*40)
    # Model the older valid-JSON private packet's missing screening boundary;
    # its actual derived file hashes remain unchanged and therefore pass CAS.
    packet['current_pages'][0]['meta']['wiki_claims'][0]['owner_note']=marker
    w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    before=note_snapshot(); calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==0
    assert report['integrity_page']==topic.relative_to(s.VAULT).as_posix() and report['claim_index']==0
    assert note_snapshot()==before and marker not in json.dumps(w.status()['worker'])
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0


def test_normal_api_key_discussion_is_not_mistaken_for_a_credential(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8'))
    info['title']='Example Project API key rotation guidance'
    info['wiki_claims'][0]['owner_note']='Discuss API key rotation and keep credentials out of shared notes.'
    topic.write_bytes(s.note_text(info,'# Normal security guidance.').encode()); packet=w.prepare()
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']
