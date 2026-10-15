"""Unused model context must refresh; actual partial publication must recover."""
import copy
from datetime import date
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit,fake_model


def pending_topic(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    topic=s.VAULT/w.BASE/'Projects/example.md'
    another=source.with_name('Another.md'); another.write_bytes(source.read_bytes())
    packet=w.prepare(); result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(s.VAULT).as_posix()
    return source,another,topic,packet,result


def saved_status(ident):
    with w.db() as conn: return dict(conn.execute('SELECT * FROM batches WHERE id=?',(ident,)).fetchone())


def assert_no_strikes():
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0]==0


@pytest.mark.parametrize('repair',[False,True],ids=['malformed-now','repaired-before-retry'])
def test_unused_pending_snapshot_cannot_bypass_preflight_or_requested_fresh_repair(brain,monkeypatch,repair):
    source,another,topic,packet,result=pending_topic(brain)
    originals={path:path.read_bytes() for path in (source,another)}
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; old_input=(folder/'input.json').read_bytes()
    info=w.meta(topic.read_text()); info['wiki_claims'][0]['source_revision']['date']=date(2026,10,1)
    topic.write_bytes(s.note_text(info,'# Manual unquoted date.').encode())
    if repair:
        info=w.meta(topic.read_text()); info['wiki_claims'][0]['source_revision']['date']='2026-10-01'
        s._write(topic,s.note_text(info,'# Owner evidence-based repair.'),'owner_integrity_repair',s.digest(topic.read_bytes()))
        w.reset_retries()
    current=topic.read_bytes(); calls=fake_model(monkeypatch,explicit(packet,result))
    report=w.run()
    assert saved_status(packet['batch_id'])['status']=='superseded'
    assert (folder/'input.json').read_bytes()==old_input
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert_no_strikes()
    if repair:
        assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']
        supplied=json.loads(calls.inputs[0].split('INPUT_JSON:\n',1)[1])
        assert supplied['batch_id']!=packet['batch_id']
        assert next(page['sha256'] for page in supplied['catalog'] if page['path']==topic.relative_to(s.VAULT).as_posix())==s.digest(current)
    else:
        assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==0
        assert topic.read_bytes()==current and w.run()['status']=='attention'


def test_unchanged_saved_output_finishes_without_another_model_call(brain,monkeypatch):
    _,_,_,packet,result=pending_topic(brain)
    output=cfg.STATE/'wiki/batches'/packet['batch_id']/'output.json'; output.write_text(json.dumps(explicit(packet,result)),encoding='utf-8')
    calls=fake_model(monkeypatch,result)
    report=w.run()
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and len(calls)==0
    assert not report['lint']['issues'] and w.status()['pending_source_parts']==0


def test_stale_saved_output_is_preserved_but_only_fresh_context_spends_a_call(brain,monkeypatch):
    _,_,topic,packet,result=pending_topic(brain)
    output=cfg.STATE/'wiki/batches'/packet['batch_id']/'output.json'; output.write_text(json.dumps(explicit(packet,result)),encoding='utf-8'); saved=output.read_bytes()
    info=w.meta(topic.read_text()); topic.write_bytes(s.note_text(info,'# Owner updated topic explanation.').encode()); current=s.digest(topic.read_bytes())
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
    assert saved_status(packet['batch_id'])['status']=='superseded' and output.read_bytes()==saved
    supplied=json.loads(calls.inputs[0].split('INPUT_JSON:\n',1)[1])
    assert next(page['sha256'] for page in supplied['catalog'] if page['path']==topic.relative_to(s.VAULT).as_posix())==current
    assert_no_strikes()


def test_stale_unused_publication_plan_is_retired_and_saved_history_preserved(brain,monkeypatch):
    _,_,topic,packet,result=pending_topic(brain); calls=fake_model(monkeypatch,explicit(packet,result)); original=s._write
    def stop(*args,**kwargs): raise OSError('Injected interruption before the first changed note')
    monkeypatch.setattr(s,'_write',stop)
    with pytest.raises(OSError): w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; plan=folder/'publication.json'; saved=plan.read_bytes()
    info=w.meta(topic.read_text()); topic.write_bytes(s.note_text(info,'# Valid owner repair after unused planning.').encode())
    current=s.digest(topic.read_bytes()); monkeypatch.setattr(s,'_write',original); w.reset_retries()
    report=w.run()
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==2
    assert saved_status(packet['batch_id'])['status']=='superseded' and plan.read_bytes()==saved
    supplied=json.loads(calls.inputs[-1].split('INPUT_JSON:\n',1)[1])
    assert next(page['sha256'] for page in supplied['catalog'] if page['path']==topic.relative_to(s.VAULT).as_posix())==current
    assert_no_strikes()


def test_actual_topic_replace_before_journal_recovers_old_plan_without_new_call(brain,monkeypatch):
    _,_,_,packet,result=pending_topic(brain); calls=fake_model(monkeypatch,explicit(packet,result)); original=s._write
    def crash_after_replace(path,text,kind,*args,**kwargs):
        receipt=original(path,text,kind,*args,**kwargs)
        if kind=='wiki_compile': raise OSError('Injected replace-before-journal crash')
        return receipt
    monkeypatch.setattr(s,'_write',crash_after_replace)
    with pytest.raises(OSError): w.run()
    monkeypatch.setattr(s,'_write',original)
    report=w.run()
    assert report['status']=='complete' and report['batch_id']==packet['batch_id'] and len(calls)==1
    assert not report['lint']['issues'] and w.status()['pending_source_parts']==0


@pytest.mark.parametrize('change',['added','deleted'])
def test_full_catalog_addition_or_deletion_refreshes_unused_context_before_model(brain,monkeypatch,change):
    source,another,topic,packet,result=pending_topic(brain); originals={path:path.read_bytes() for path in (source,another)}
    added=s.VAULT/w.BASE/'Concepts/owner-added.md'
    if change=='added':
        added.parent.mkdir(); added.write_bytes(s.note_text({'wiki_kind':'Concepts','title':'Owner topic','wiki_claims':[],'related':[],'source_hashes':{}},'# Owner topic.').encode())
    else: topic.unlink()
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
    supplied=json.loads(calls.inputs[0].split('INPUT_JSON:\n',1)[1]); catalog={page['path'] for page in supplied['catalog']}
    if change=='added': assert added.relative_to(s.VAULT).as_posix() in catalog
    else: assert topic.relative_to(s.VAULT).as_posix() not in catalog
    assert saved_status(packet['batch_id'])['status']=='superseded' and all(path.read_bytes()==raw for path,raw in originals.items())
    assert not report['lint']['issues']; assert_no_strikes()


def test_legacy_partial_catalog_uses_its_known_current_page_snapshot(brain,monkeypatch):
    _,_,topic,packet,result=pending_topic(brain)
    packet['version']=1
    for page in packet['catalog']: page.pop('sha256')
    w._atomic_json(cfg.STATE/'wiki/batches'/packet['batch_id']/'input.json',packet)
    info=w.meta(topic.read_text()); topic.write_bytes(s.note_text(info,'# Owner updated a legacy known snapshot.').encode())
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and report['batch_id']!=packet['batch_id']
    assert saved_status(packet['batch_id'])['status']=='superseded' and not report['lint']['issues']
