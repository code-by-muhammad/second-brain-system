"""Preserve direct-editor bytes and control derived auxiliary diagnostics."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


def test_existing_topic_editor_save_during_evidence_render_is_never_overwritten(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain); packet=w.prepare(); original=w._EvidenceHistory._current; edited=[]
    def owner_save(history,path):
        if not edited:
            raw=topic.read_bytes()+b'\nOwner direct editor revision during evidence lookup.\n'; topic.write_bytes(raw); edited.append(raw)
        return original(history,path)
    monkeypatch.setattr(w._EvidenceHistory,'_current',owner_save)
    with pytest.raises(w.WikiTransientError): w.apply(packet,result)
    assert topic.read_bytes()==edited[0]


def test_new_topic_editor_creation_during_evidence_render_is_never_overwritten(brain,monkeypatch):
    _,packet,result=fixture(brain); target=s.VAULT/w.BASE/'Projects/example.md'; original=w._EvidenceHistory._current; edited=[]
    def owner_save(history,path):
        if not edited:
            target.parent.mkdir(parents=True,exist_ok=True); raw=b'# Owner created this target during planning.\n';target.write_bytes(raw);edited.append(raw)
        return original(history,path)
    monkeypatch.setattr(w._EvidenceHistory,'_current',owner_save)
    with pytest.raises(w.WikiTransientError): w.apply(packet,result)
    assert target.read_bytes()==edited[0]


@pytest.mark.parametrize('location',['source-record','log'])
def test_non_utf8_derived_auxiliary_stops_with_controlled_attention_and_no_strikes(brain,monkeypatch,location):
    _,another,_,result=existing_topic(brain); rel=another.relative_to(s.VAULT).as_posix()
    target=s.VAULT/w.BASE/('Sources/'+s.digest(rel.encode())[:16]+'.md' if location=='source-record' else 'Log.md')
    target.write_bytes('# Synthetic owner auxiliary note; preserve encoding.\n'.encode('utf-16'))
    before=note_snapshot();packet=w.prepare();calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError) as caught:w.run()
    assert caught.value.page==target.relative_to(s.VAULT).as_posix() and caught.value.field=='encoding'
    report=w.run();assert report['status']=='attention' and report['consecutive_failures']==0 and len(calls)==1 and note_snapshot()==before
    assert w.status()['worker']['integrity_page']==target.relative_to(s.VAULT).as_posix()
    with w.db() as conn: assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0


def test_non_utf8_source_record_relink_refuses_without_note_mutation(brain):
    source,packet,result=fixture(brain);w.apply(packet,result)
    target=s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode())[:16]+'.md')
    target.write_bytes('# Preserve unreadable owner record.\n'.encode('utf-16'));before=note_snapshot()
    with pytest.raises(w.WikiIntegrityError) as caught:w.rebuild_source_links()
    assert caught.value.page==target.relative_to(s.VAULT).as_posix() and note_snapshot()==before


@pytest.mark.parametrize('source',['C:/Synthetic/private.md','01-Projects/'+'x'*10000+'.md','01-Projects/ghp_'+'syntheticmarker'*4+'.md'])
def test_malformed_origin_diagnostic_never_echoes_unvalidated_source(brain,source):
    _,_,topic,_=existing_topic(brain);info=w.meta(topic.read_text(encoding='utf-8'));info['wiki_claims'][0].update(source=source,source_revision=None)
    topic.write_bytes(s.note_text(info,'# Preserve invalid private metadata.').encode());before=note_snapshot()
    issues=[x for x in w.lint()['issues'] if x.get('claim_index')==0]
    assert issues and all(source not in json.dumps(x) for x in issues)
    assert all(set(x)<= {'page','issue','field','claim_index'} for x in issues) and note_snapshot()==before


@pytest.mark.parametrize('kind',['source-record','log','index'])
def test_auxiliary_editor_save_after_consumed_snapshot_is_not_blessed_by_plan(brain,monkeypatch,kind):
    _,another,_,result=existing_topic(brain);packet=w.prepare();relative=another.relative_to(s.VAULT).as_posix()
    target=s.VAULT/w.BASE/('Sources/'+s.digest(relative.encode())[:16]+'.md' if kind=='source-record' else 'Log.md' if kind=='log' else 'Index.md')
    if kind=='source-record':target.write_bytes(s.note_text({'sources':[relative],'latest_source_part':0},'# Owner record before planning.').encode())
    original=w._plan_write;edited=[]
    def owner_save(writes,path,*args,**kwargs):
        if path==target and not edited:
            raw=target.read_bytes()+b'\nOwner appended after the compiler consumed its before image.\n';target.write_bytes(raw);edited.append(raw)
        return original(writes,path,*args,**kwargs)
    monkeypatch.setattr(w,'_plan_write',owner_save)
    with pytest.raises(w.WikiTransientError):w.apply(packet,result)
    assert target.read_bytes()==edited[0]



def test_saved_topic_plan_cannot_bless_a_tampered_late_before_image(brain,monkeypatch):
    _,_,topic,result=existing_topic(brain);packet=w.prepare();calls=fake_model(monkeypatch,explicit(packet,result));write=s._write
    monkeypatch.setattr(s,'_write',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('Stop before the first write')))
    with pytest.raises(OSError):w.run()
    monkeypatch.setattr(s,'_write',write);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];plan=json.loads((folder/'publication.json').read_text(encoding='utf-8'))
    owner=topic.read_bytes()+b'\nOwner change must not be blessed by a late before image.\n';topic.write_bytes(owner)
    for item in plan['writes']:
        if item['path']==topic.relative_to(s.VAULT).as_posix():item['before']=s.digest(owner)
    w._atomic_json(folder/'publication.json',plan)
    assert not w._saved_publication_started(packet,folder/'publication.json')
    with pytest.raises(w.WikiOutputError):w.apply(packet,explicit(packet,result))
    assert topic.read_bytes()==owner and len(calls)==1



def test_existing_target_deleted_after_prepare_keeps_transient_cas_classification(brain):
    _,_,topic,result=existing_topic(brain);packet=w.prepare();topic.unlink();before=note_snapshot()
    with pytest.raises(w.WikiTransientError):w.apply(packet,result)
    assert note_snapshot()==before and not topic.exists()
