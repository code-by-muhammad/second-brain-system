"""Daily aggregation isolation, portable source hashes and safe display contracts."""
import builtins
import json
from datetime import datetime
from pathlib import Path

import pytest
import brain_git
import brain_maintenance as m
import brain_settings as cfg
import brain_store as s
import brain_wiki as w


def template(vault):
    path=vault/'Templates/daily-note.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('# Synthetic daily\n\n## Work Log\n',encoding='utf-8')
    return path


def capture(event='daily-event',title='Synthetic milestone',source='synthetic-test'):
    return s.capture(event,title,'Synthetic verified outcome.',source,'private test fixture')


def daily_for(receipt):
    raw=s.safe_path(receipt['path']).read_text(encoding='utf-8')
    when=datetime.fromisoformat(w.meta(raw)['captured_at'])
    return s.VAULT/'05-Daily'/(when.strftime('%Y-%m-%d-%A')+'.md')


def git_recorder(monkeypatch):
    calls=[]
    monkeypatch.setattr(brain_git,'sync',lambda *args,**kwargs:calls.append('sync') or 'synced')
    return calls


@pytest.mark.parametrize('fault',['missing-template','invalid-template','invalid-daily','write-error'])
def test_daily_input_failure_flags_capture_and_still_reaches_git(brain,monkeypatch,fault):
    vault,state=brain; receipt=capture(); source=s.safe_path(receipt['path']); before=source.read_bytes()
    target=daily_for(receipt)
    if fault=='invalid-template': template(vault).write_bytes(b'\xffinvalid synthetic template')
    elif fault=='invalid-daily':
        template(vault); target.parent.mkdir(); target.write_bytes(b'\xffinvalid synthetic daily')
    elif fault=='write-error':
        template(vault); original=s._write
        def fail_daily(path,text,kind,*args,**kwargs):
            if kind=='daily_capture_link': raise OSError('Synthetic daily write failure')
            return original(path,text,kind,*args,**kwargs)
        monkeypatch.setattr(s,'_write',fail_daily)
    daily_before=target.read_bytes() if target.exists() else None; calls=git_recorder(monkeypatch)
    for _ in range(2):
        result=m.maintenance()
        assert result['review'] and result['captures_linked']==0
        assert result['completed_private_artifacts_removed']==0
        assert result['review'][0]['path']==receipt['path']
        assert 'daily note or template' in result['review'][0]['reasons'][0]
    assert calls==['sync','sync']
    assert source.read_bytes()==before
    assert (target.read_bytes() if target.exists() else None)==daily_before
    assert json.loads((state/'maintenance-status.json').read_text(encoding='utf-8'))['stage']=='finished'


def test_healthy_capture_links_once_and_still_syncs(brain,monkeypatch):
    vault,_=brain; template(vault); receipt=capture(); calls=git_recorder(monkeypatch)
    assert m.maintenance()['captures_linked']==1
    assert m.maintenance()['captures_linked']==0
    assert calls==['sync','sync']
    assert daily_for(receipt).read_text(encoding='utf-8').count('<!-- brain-capture:daily-event -->')==1


def test_capture_device_does_not_require_daily_template_to_sync(brain,monkeypatch):
    capture(); monkeypatch.setattr(cfg,'ROLE','capture'); calls=git_recorder(monkeypatch)
    assert m.maintenance()['captures_linked']==0 and calls==['sync']


def derived_source(brain,raw,recorded):
    vault,_=brain; relative='01-Projects/Source.md'; source=vault/relative
    source.parent.mkdir(exist_ok=True); source.write_bytes(raw)
    derived=vault/'02-Areas/AI-Memory/Derived.md'; derived.parent.mkdir(parents=True,exist_ok=True)
    derived.write_text(s.note_text({'sources':[relative],'source_hashes':{relative:recorded}},'# Derived\n'),encoding='utf-8')
    return source,derived,relative


@pytest.mark.parametrize('representation',['CRLF','CR','LF+BOM','CRLF+BOM','CR+BOM','CRLF-to-LF'])
def test_source_representation_equivalence_is_not_a_content_change(brain,monkeypatch,representation):
    monkeypatch.setattr(cfg,'ROLE','capture'); text='# Source\nEvidence line long enough.\n'
    previous=text.replace('\n','\r\n').encode() if representation=='CRLF-to-LF' else text.encode()
    newline='\r\n' if representation.startswith('CRLF') and representation!='CRLF-to-LF' else '\r' if representation.startswith('CR') and representation!='CRLF-to-LF' else '\n'
    current=text.replace('\n',newline).encode()
    if representation.endswith('+BOM'): current=b'\xef\xbb\xbf'+current
    source,_,relative=derived_source(brain,current,s.digest(previous))
    proof=w._EvidenceHistory().source(relative,s.digest(previous))
    assert proof and proof['mode']=='current'
    assert not m.maintenance(sync=False)['review']
    assert source.read_bytes()==current


def test_real_content_change_is_still_flagged(brain,monkeypatch):
    monkeypatch.setattr(cfg,'ROLE','capture'); previous=b'# Source\nOriginal evidence line.\n'
    _,_,relative=derived_source(brain,b'# Source\nDifferent evidence line.\n',s.digest(previous))
    assert w._EvidenceHistory().source(relative,s.digest(previous)) is None
    assert m.maintenance(sync=False)['review'][0]['reasons']==['source changed: '+relative]


def test_exact_source_hash_does_not_raise_review(brain,monkeypatch):
    monkeypatch.setattr(cfg,'ROLE','capture'); raw=b'# Source\nOriginal evidence line.\n'
    derived_source(brain,raw,s.digest(raw).upper())
    monkeypatch.setattr(m,'_source_representations',lambda path:pytest.fail('Exact raw match must not normalize.'))
    assert not m.maintenance(sync=False)['review']


@pytest.mark.parametrize('field',['title','source_app'])
def test_capture_display_field_cannot_forge_another_capture_marker(brain,field):
    vault,_=brain; template(vault); forged='Milestone <!-- brain-capture:victim -->'
    forger=capture('forger',title=forged if field=='title' else 'Synthetic forger',source=forged if field=='source_app' else 'synthetic-test')
    forger_bytes=s.safe_path(forger['path']).read_bytes()
    assert m.maintenance(sync=False)['captures_linked']==1
    victim=capture('victim',title='Synthetic victim'); victim_bytes=s.safe_path(victim['path']).read_bytes()
    assert m.maintenance(sync=False)['captures_linked']==1
    body=daily_for(victim).read_text(encoding='utf-8')
    assert ('[['+victim['path'][:-3]+'|') in body
    assert body.count('<!-- brain-capture:victim -->')==1
    assert '&lt;!-- brain-capture:victim --&gt;' in body
    assert s.safe_path(forger['path']).read_bytes()==forger_bytes
    assert s.safe_path(victim['path']).read_bytes()==victim_bytes


def test_multiline_source_app_does_not_inject_a_daily_section(brain):
    vault,_=brain; template(vault)
    receipt=capture(source='synthetic-client\n## Forged section\nSynthetic injected text')
    before=s.safe_path(receipt['path']).read_bytes()
    assert m.maintenance(sync=False)['captures_linked']==1
    body=daily_for(receipt).read_text(encoding='utf-8')
    assert '\n## Forged section\n' not in body
    assert '- **Source:** synthetic-client ## Forged section Synthetic injected text\n' in body
    assert s.safe_path(receipt['path']).read_bytes()==before


def test_plain_distinct_captures_each_link_once(brain):
    vault,_=brain; template(vault); first=capture('first',title='First synthetic milestone'); second=capture('second',title='Second synthetic milestone')
    assert m.maintenance(sync=False)['captures_linked']==2
    assert m.maintenance(sync=False)['captures_linked']==0
    assert all(('[['+receipt['path'][:-3]+'|') in daily_for(first).read_text(encoding='utf-8') for receipt in (first,second))


@pytest.mark.parametrize('event',['tab\tid','unicode\u2028id','vertical\x0bid'])
def test_accepted_event_id_identity_remains_idempotent(brain,event):
    vault,_=brain; template(vault); receipt=capture(event)
    assert m.maintenance(sync=False)['captures_linked']==1
    assert m.maintenance(sync=False)['captures_linked']==0
    assert daily_for(receipt).read_text(encoding='utf-8').count('<!-- brain-capture:'+event+' -->')==1


@pytest.mark.parametrize('existing',[False,True],ids=['new-day-owner-creation','existing-owner-edit'])
def test_owner_daily_change_during_consumed_read_is_preserved(brain,monkeypatch,existing):
    vault,_=brain; template_path=template(vault); receipt=capture(); daily=daily_for(receipt)
    before_capture=s.safe_path(receipt['path']).read_bytes()
    if existing:
        daily.parent.mkdir(); daily.write_bytes(b'# Original owner daily\n\n## Work Log\n')
    owner=b'# Concurrent owner content; preserve exactly.\r\n'; read=m._read_derived; mutations=[]
    def consumed_read(path):
        result=read(path)
        if path==(daily if existing else template_path):
            daily.parent.mkdir(exist_ok=True); daily.write_bytes(owner); mutations.append('owner edit')
        return result
    monkeypatch.setattr(m,'_read_derived',consumed_read); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert mutations==['owner edit'] and daily.read_bytes()==owner
    assert result['captures_linked']==0 and result['review'] and calls==['sync']
    assert s.safe_path(receipt['path']).read_bytes()==before_capture


def test_existing_daily_late_owner_change_is_rejected_by_write_cas(brain,monkeypatch):
    vault,_=brain; template(vault); receipt=capture(); daily=daily_for(receipt); daily.parent.mkdir()
    before=b'# Original owner daily\r\n\r\n## Work Log\r\n'; daily.write_bytes(before)
    owner=b'# Later owner content; preserve exactly.\r\n'; write=s._write; checks=[]
    def concurrent_write(path,text,kind,*args,**kwargs):
        if kind=='daily_capture_link':
            checks.append(kwargs.get('expected_sha256')); daily.write_bytes(owner)
        return write(path,text,kind,*args,**kwargs)
    monkeypatch.setattr(s,'_write',concurrent_write); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert checks==[s.digest(before)] and daily.read_bytes()==owner
    assert result['captures_linked']==0 and result['review'] and calls==['sync']


@pytest.mark.parametrize('legacy',['inline-title','inline-source','standalone-no-link','standalone-wrong-link'])
def test_historical_forged_marker_does_not_suppress_genuine_link(brain,legacy):
    vault,_=brain; template(vault); victim=capture('victim'); target=daily_for(victim); target.parent.mkdir()
    marker='<!-- brain-capture:victim -->'
    forged={'inline-title':'### 10:00 — Milestone '+marker,
            'inline-source':'- **Source:** Client '+marker,
            'standalone-no-link':marker,
            'standalone-wrong-link':marker+'\n- **What / Why / How / Result / Next:** [[00-Inbox/AI-Captures/unrelated|Saved milestone and evidence]]'}[legacy]
    old='# Existing owner daily\n\n## Work Log\n'+forged+'\n'; target.write_text(old,encoding='utf-8')
    before=s.safe_path(victim['path']).read_bytes()
    assert m.maintenance(sync=False)['captures_linked']==1
    assert old.rstrip() in target.read_text(encoding='utf-8')
    assert ('[['+victim['path'][:-3]+'|') in target.read_text(encoding='utf-8')
    assert m.maintenance(sync=False)['captures_linked']==0
    assert s.safe_path(victim['path']).read_bytes()==before


@pytest.mark.parametrize('name',['bracket[legacy].md','anchor#legacy.md','block^legacy.md'])
def test_unsafe_renamed_capture_is_flagged_before_read_or_link(brain,monkeypatch,name):
    vault,_=brain; template(vault); receipt=capture(); old=s.safe_path(receipt['path']); renamed=old.with_name(name); old.rename(renamed); before=renamed.read_bytes()
    read_text=Path.read_text
    def no_read(path,*args,**kwargs):
        if path==renamed: pytest.fail('Unsafe legacy capture must be rejected before a read.')
        return read_text(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',no_read); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert result['captures_linked']==0 and calls==['sync']
    assert result['review'][0]['reasons']==['capture path cannot be safely linked; original preserved']
    assert renamed.read_bytes()==before and not (vault/'05-Daily').exists()


@pytest.mark.parametrize('blocked',['Templates/daily-note.md','05-Daily/'])
def test_daily_and_template_paths_are_checked_before_read(brain,monkeypatch,blocked):
    vault,_=brain; template(vault); receipt=capture(); original=s.safe_path
    def refuse(relative):
        if relative.startswith(blocked) if blocked.endswith('/') else relative==blocked:
            raise ValueError('Synthetic unsafe path refusal')
        return original(relative)
    monkeypatch.setattr(s,'safe_path',refuse); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert result['captures_linked']==0 and calls==['sync'] and result['review']
    assert not (vault/'05-Daily').exists() and original(receipt['path']).is_file()


def test_daily_failure_does_not_skip_other_capture_review_cleanup_or_git(brain,monkeypatch):
    vault,_=brain; template(vault); bad=capture('bad'); good=capture('good'); original=m._daily_link
    def fail_one(path,*args):
        if path==s.safe_path(bad['path']): raise ValueError('Synthetic daily path refusal')
        return original(path,*args)
    monkeypatch.setattr(m,'_daily_link',fail_one)
    derived=vault/'02-Areas/AI-Memory/Invalid.md'; derived.parent.mkdir(parents=True); derived.write_text('---\nsources: invalid\n---\n')
    cleanup=[]; monkeypatch.setattr(w,'cleanup',lambda:cleanup.append('cleanup') or 0); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert result['captures_linked']==1 and len(result['review'])==2
    assert cleanup==['cleanup'] and calls==['sync']
    assert ('[['+good['path'][:-3]+'|') in daily_for(good).read_text(encoding='utf-8')


@pytest.mark.parametrize('fatal',[RuntimeError,MemoryError])
def test_unexpected_daily_failure_is_not_swallowed(brain,monkeypatch,fatal):
    vault,_=brain; template(vault); capture(); calls=git_recorder(monkeypatch)
    def fail(*args): raise fatal('Synthetic unexpected failure')
    monkeypatch.setattr(m,'_daily_link',fail)
    with pytest.raises(fatal): m.maintenance()
    assert not calls


@pytest.mark.parametrize('raw',[b'',b'\xef\xbb\xbf',b'a\r\nb\rc\n',b'\xef\xbb\xbf\xef\xbb\xbfbody\r\n',b'x'*65535+b'\r\n'+('α🙂\r\n'*20000).encode()],ids=['empty','only-bom','mixed-eol','interior-bom','chunk-boundary-utf8-crlf'])
def test_streamed_variants_exactly_match_compiler_normalization(brain,raw):
    vault,_=brain; path=vault/'source.md'; path.write_bytes(raw)
    assert m._source_representations(path)|{s.digest(raw)}==set(w._source_bytes(raw)['variants'])


class Reader:
    def __init__(self,stream,requests,step=None,small=False): self.stream=stream; self.requests=requests; self.step=step; self.small=small
    def __enter__(self): return self
    def __exit__(self,*args): return self.stream.__exit__(*args)
    def fileno(self): return self.stream.fileno()
    def read(self,size=-1):
        assert 0<size<=65536
        self.requests.append(size); data=self.stream.read(min(size,1) if self.small else size)
        if self.step is not None:
            action,self.step=self.step,None; action()
        return data


def wrap_reads(monkeypatch,path,step=None,small=False):
    original=Path.open; requests=[]
    def wrapped(target,*args,**kwargs):
        stream=original(target,*args,**kwargs)
        return Reader(stream,requests,step,small) if target==path and args and args[0]=='rb' else stream
    monkeypatch.setattr(Path,'open',wrapped)
    return requests


def test_bom_utf8_and_crlf_split_across_single_byte_reads(brain,monkeypatch):
    vault,_=brain; path=vault/'source.md'; raw=b'\xef\xbb\xbf'+('α🙂\r\nβ\rγ\n').encode(); path.write_bytes(raw)
    requests=wrap_reads(monkeypatch,path,small=True)
    assert m._source_representations(path)|{s.digest(raw)}==set(w._source_bytes(raw)['variants'])
    assert requests and all(0<size<=65536 for size in requests)


def test_large_source_equivalence_never_uses_full_file_reads(brain,monkeypatch):
    monkeypatch.setattr(cfg,'ROLE','capture'); previous=('# Source\n'+('large synthetic evidence\n'*100000)).encode()
    current=previous.replace(b'\n',b'\r\n'); source,_,_=derived_source(brain,current,s.digest(previous))
    requests=wrap_reads(monkeypatch,source)
    for method in ('read_bytes','read_text'):
        original=getattr(Path,method)
        def refuse(path,*args,_original=original,**kwargs):
            if path==source: pytest.fail('Large source must use bounded binary streams.')
            return _original(path,*args,**kwargs)
        monkeypatch.setattr(Path,method,refuse)
    assert not m.maintenance(sync=False)['review']
    assert len(current)>2_000_000 and requests and max(requests)<=65536


@pytest.mark.parametrize('mutation',['growth','truncation'])
def test_streamed_equivalence_rejects_source_size_changes(brain,monkeypatch,mutation):
    vault,_=brain; path=vault/'source.md'; path.write_bytes(b'x'*70000)
    def change():
        with builtins.open(path,'ab' if mutation=='growth' else 'wb') as target:
            target.write(b'additional' if mutation=='growth' else b'short')
    wrap_reads(monkeypatch,path,step=change)
    with pytest.raises(ValueError,match='Source (?:changed|grew)'): m._source_representations(path)


def test_invalid_utf8_variant_is_flagged_and_original_preserved(brain,monkeypatch):
    monkeypatch.setattr(cfg,'ROLE','capture'); raw=b'\xffinvalid synthetic source'
    source,_,_=derived_source(brain,raw,s.digest(b'valid former source'))
    result=m.maintenance(sync=False)
    assert result['review'][0]['reasons']==['source metadata could not be validated; original preserved']
    assert source.read_bytes()==raw


@pytest.mark.parametrize('kind',['capture','template','daily','daily-at-cap'])
def test_oversized_daily_inputs_are_bounded_preserved_and_do_not_skip_git(brain,monkeypatch,kind):
    vault,_=brain; template_path=template(vault); receipt=capture(); capture_path=s.safe_path(receipt['path']); daily=daily_for(receipt)
    target=capture_path if kind=='capture' else template_path if kind=='template' else daily
    target.parent.mkdir(parents=True,exist_ok=True)
    raw=b'# Large synthetic owner content\n'+b'x'*(2_000_000-len(b'# Large synthetic owner content\n'))
    if kind!='daily-at-cap': raw+=b'x'
    target.write_bytes(raw); before_capture=capture_path.read_bytes(); original=Path.open; requests=[]
    class Bounded:
        def __init__(self,stream): self.stream=stream
        def __enter__(self): return self
        def __exit__(self,*args): return self.stream.__exit__(*args)
        def read(self,size=-1):
            assert size==2_000_001; requests.append(size); return self.stream.read(size)
    def wrapped(path,*args,**kwargs):
        stream=original(path,*args,**kwargs)
        return Bounded(stream) if path==target and args and args[0]=='rb' else stream
    monkeypatch.setattr(Path,'open',wrapped); calls=git_recorder(monkeypatch)
    result=m.maintenance()
    assert result['captures_linked']==0 and result['review'] and calls==['sync']
    assert requests==[2_000_001]
    monkeypatch.setattr(Path,'open',original)
    assert target.read_bytes()==raw and capture_path.read_bytes()==before_capture


@pytest.mark.parametrize('fatal',[RuntimeError,MemoryError])
def test_unexpected_normalizer_errors_are_not_swallowed(brain,monkeypatch,fatal):
    monkeypatch.setattr(cfg,'ROLE','capture'); derived_source(brain,b'new content',s.digest(b'old content'))
    def fail(*args): raise fatal('Synthetic unexpected failure')
    monkeypatch.setattr(m,'_source_representations',fail)
    with pytest.raises(fatal): m.maintenance(sync=False)
