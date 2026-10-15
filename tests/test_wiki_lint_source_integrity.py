"""Lint and publication must agree on existing metadata and unavailable sources."""
import copy
from datetime import date
import json
from pathlib import Path

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_history import git_fixture
from test_wiki_prepare_integrity import existing_topic, note_snapshot
from test_wiki_remediation import explicit, fake_model


@pytest.mark.parametrize('kind',['title-date','title-int','title-null','claim-date','claim-nan','claim-recursive','claim-bytes'])
def test_lint_reports_each_worker_blocking_field_and_keeps_valid_claim_checks(brain,kind):
    source,_,topic,_=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8')); claim=info['wiki_claims'][0]
    valid=copy.deepcopy(claim); valid['quote']='This exact quote does not appear in the source.'
    info['wiki_claims'].append(valid)
    if kind.startswith('title-'): info['title']={'title-date':date(2026,10,1),'title-int':123,'title-null':None}[kind]
    else: claim['reviewed']={'claim-date':date(2026,10,1),'claim-nan':float('nan'),'claim-recursive':claim,'claim-bytes':{b'key':'value'}}[kind]
    topic.write_bytes(s.note_text(info,'# Preserve malformed owner metadata and valid sibling.').encode()); before=note_snapshot()
    report=w.lint(); relevant=[x for x in report['issues'] if x['page']==topic.relative_to(s.VAULT).as_posix()]
    assert any(x['issue']==('malformed title metadata' if kind.startswith('title-') else 'malformed claim metadata') and x.get('field')==('title' if kind.startswith('title-') else 'wiki_claims') for x in relevant)
    assert any(x.get('claim_index')==1 and x['issue']=='evidence quote missing' for x in relevant)
    assert note_snapshot()==before
    assert all('reviewed' not in json.dumps(x) for x in relevant)


@pytest.mark.parametrize('field',['title','claim-extra','claim-text','claim-quote','claim-source'])
def test_yaml_lone_surrogate_is_controlled_before_model_and_lint_names_field(brain,monkeypatch,field):
    _,_,topic,result=existing_topic(brain); info=w.meta(topic.read_text(encoding='utf-8'))
    if field=='title': info['title']='Example \ud800'
    else: info['wiki_claims'][0][{'claim-extra':'reviewed','claim-text':'text','claim-quote':'quote','claim-source':'source'}[field]]='Unsupported \ud800 text with sufficient length.'
    topic.write_bytes(s.note_text(info,'# YAML escape remains preserved.').encode()); before=note_snapshot(); calls=fake_model(monkeypatch,result)
    report=w.run()
    assert report['status']=='attention' and report['reason']=='WikiIntegrityError' and report['consecutive_failures']==0
    assert report.get('integrity_field')==('title' if field=='title' else 'wiki_claims')
    assert len(calls)==0 and note_snapshot()==before
    assert w.run()['status']=='attention' and len(calls)==0
    assert any(x.get('field')==('title' if field=='title' else 'wiki_claims') for x in w.lint()['issues'])


def source_revision_case(brain,tmp_path,kind,with_history=True):
    vault,_=brain; git=git_fixture(tmp_path,vault) if with_history else None
    source,packet,result=fixture(brain); w.apply(packet,result); original=source.read_bytes()
    if git: git('add','--','.'); git('commit','-m','Exact UTF-8 source and anchored claims')
    altered={'utf16':b'\xff\xfe'+original.decode().encode('utf-16-le'),
             'cp1252':original+b'\nOwner annotation caf\xe9.\n',
             'oversize':original+b'x'*2_000_001}[kind]
    source.write_bytes(altered)
    if git: git('add','--','.'); git('commit','-m','Owner source encoding or size change')
    another=source.with_name('Another.md'); another.write_bytes(original)
    packet=w.prepare(); result=copy.deepcopy(result); result['pages'][0]['claims'][0]['source']=another.relative_to(vault).as_posix()
    return source,another,packet,explicit(packet,result)


@pytest.mark.parametrize('kind',['utf16','cp1252','oversize'])
def test_unavailable_current_source_uses_exact_git_origin_without_unbounded_read(brain,tmp_path,monkeypatch,kind):
    source,another,packet,result=source_revision_case(brain,tmp_path,kind); before=note_snapshot(); reads=[]
    real=Path.read_bytes
    def read(path):
        if path==source: reads.append(path)
        return real(path)
    monkeypatch.setattr(Path,'read_bytes',read); calls=fake_model(monkeypatch,result)
    report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']
    assert report['lint']['historical_evidence_count']==1
    assert 'raw_sha256' not in json.dumps(report['lint'])
    assert len(reads)==(0 if kind=='oversize' else 1)
    assert real(source)==before[source.relative_to(s.VAULT).as_posix()] and real(another)==before[another.relative_to(s.VAULT).as_posix()]
    claim=w.meta((s.VAULT/w.BASE/'Projects/example.md').read_text(encoding='utf-8'))['wiki_claims'][0]
    assert claim['source_revision']['sha256']!=s.digest(real(source))


@pytest.mark.parametrize('kind',['utf16','cp1252','oversize'])
def test_unavailable_current_without_actual_history_stops_with_integrity_no_repeats(brain,tmp_path,monkeypatch,kind):
    _,_,packet,result=source_revision_case(brain,tmp_path,kind,False); before=note_snapshot(); calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    assert caught.value.page==w.BASE+'Projects/example.md' and caught.value.claim_index==0
    report=w.run(); assert report['status']=='attention' and report['consecutive_failures']==0
    assert len(calls)==1 and note_snapshot()==before
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]==0
        assert conn.execute('SELECT attempts FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]==0
    assert w.lint()['issues']


def test_catalog_title_changed_during_model_is_controlled_without_writes_or_strikes(brain,monkeypatch):
    _,packet,result=fixture(brain)
    other=s.VAULT/w.BASE/'Concepts/other.md'; other.parent.mkdir(parents=True)
    info={'wiki_kind':'Concepts','title':'Unrelated catalog topic','wiki_claims':[]}; other.write_bytes(s.note_text(info,'# Preserve other topic.').encode())
    packet=w.prepare(); before=note_snapshot()
    def owner_edit():
        info['title']=123; other.write_bytes(s.note_text(info,'# Owner title edit during compilation.').encode())
    calls=fake_model(monkeypatch,explicit(packet,result),after=owner_edit)
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    assert caught.value.page==other.relative_to(s.VAULT).as_posix()
    report=w.run(); assert report['status']=='attention' and report['consecutive_failures']==0
    assert len(calls)==1 and not (s.VAULT/w.BASE/'Projects/example.md').exists()
    assert {p:v for p,v in note_snapshot().items() if p!=other.relative_to(s.VAULT).as_posix()}=={p:v for p,v in before.items() if p!=other.relative_to(s.VAULT).as_posix()}


@pytest.mark.parametrize('invalid',['not-a-part',True,-1,{}])
def test_manual_source_record_part_corruption_is_controlled_not_model_retry(brain,monkeypatch,invalid):
    source,packet,result=fixture(brain); w.apply(packet,result)
    record=s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode())[:16]+'.md')
    info=w.meta(record.read_text(encoding='utf-8')); info['latest_source_part']=invalid
    record.write_bytes(s.note_text(info,'# Preserve owner-corrupted source record.').encode())
    source.write_bytes(source.read_bytes()+b'\nNew owner evidence.'); packet=w.prepare(); before=note_snapshot()
    calls=fake_model(monkeypatch,explicit(packet,result))
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    assert caught.value.page==record.relative_to(s.VAULT).as_posix()
    assert w.run()['status']=='attention' and len(calls)==1 and note_snapshot()==before


@pytest.mark.parametrize('valid',[None,0,2])
def test_legacy_source_record_optional_part_values_still_publish(brain,tmp_path,monkeypatch,valid):
    git=git_fixture(tmp_path,s.VAULT)
    source,packet,result=fixture(brain); w.apply(packet,result)
    git('add','--','.'); git('commit','-m','Exact prior source revision')
    record=s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode())[:16]+'.md')
    info=w.meta(record.read_text(encoding='utf-8')); info['latest_source_part']=valid
    record.write_bytes(s.note_text(info,'# Preserved compatible source metadata.').encode())
    source.write_bytes(source.read_bytes()+b'\nNew owner evidence.'); packet=w.prepare()
    calls=fake_model(monkeypatch,explicit(packet,result)); report=w.run()
    assert report['status']=='complete' and len(calls)==1 and not report['lint']['issues']
    assert w.meta(record.read_text(encoding='utf-8'))['latest_source_part']==max(valid or 0,1)


def test_current_file_growth_after_stat_still_uses_bounded_read_and_rejects_oversize(brain,monkeypatch):
    source,_,_=fixture(brain); original=source.read_bytes(); original_sha=s.digest(original)
    real_open=Path.open; reads=[]
    class GrowingStream:
        def __init__(self,path): self.path=path
        def __enter__(self):
            # Growth occurs after the stat check, before opening the stream.
            self.path.write_bytes(original+b'x'*2_000_001)
            self.stream=real_open(self.path,'rb'); return self
        def read(self,size=-1): reads.append(size); return self.stream.read(size)
        def __exit__(self,*args): self.stream.close()
    def opened(path,*args,**kwargs):
        if path==source and args==('rb',): return GrowingStream(path)
        return real_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    history=w._EvidenceHistory()
    assert history._current(source.relative_to(s.VAULT).as_posix()) is None
    assert reads==[2_000_001] and history.commands==0
    assert history.source(source.relative_to(s.VAULT).as_posix(),original_sha,'A prototype was demonstrated.') is None
