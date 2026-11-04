"""Three shared wiki reads are bounded at the real file-read boundary."""
import json
from pathlib import Path
from filelock import FileLock,Timeout
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import make_legacy

LIMIT=2_000_000
POLICY_LIMIT=131072


def writer_held():
    lock=FileLock(str(cfg.STATE/'writer.lock'),timeout=0)
    try:lock.acquire();return False
    except Timeout:return True
    finally:lock.release()


def observe(monkeypatch,target,*,grow_to=None):
    real_open=Path.open;reads=[];growth=[];models=[]
    class Stream:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def __getattr__(self,name):return getattr(self.stream,name)
        def read(self,size=-1):
            reads.append({'requested':size,'under_actual_writer':writer_held()})
            return self.stream.read(size)
    def opened(path,mode='r',*args,**kwargs):
        if path==target and mode in ('r','rb'):
            if grow_to is not None and not growth:
                before=path.stat().st_size
                with real_open(path,'ab') as output:output.write(b' '*(grow_to-before))
                growth.append({'before':before,'after':path.stat().st_size})
            return Stream(real_open(path,mode,*args,**kwargs))
        return real_open(path,mode,*args,**kwargs)
    def no_model(*args,**kwargs):models.append('unexpected subprocess');raise AssertionError('No model or subprocess is allowed in the observed wiki call.')
    monkeypatch.setattr(Path,'open',opened);monkeypatch.setattr(w.subprocess,'run',no_model)
    return reads,growth,models


def protected():
    notes={p.relative_to(s.VAULT).as_posix():s.digest(p.read_bytes()) for p in s.VAULT.rglob('*.md')}
    artifacts={p.relative_to(cfg.STATE).as_posix():s.digest(p.read_bytes()) for p in (cfg.STATE/'wiki/batches').rglob('*') if p.is_file()}
    with w.db() as conn:
        sources=[dict(row) for row in conn.execute('SELECT * FROM sources ORDER BY path')]
        batches=[dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY id')]
    return {'notes':notes,'private_batch_artifacts':artifacts,'sources_sha256':s.digest(json.dumps(sources,sort_keys=True).encode()),'batches_sha256':s.digest(json.dumps(batches,sort_keys=True).encode())}


def record(tmp_path,reads,growth,models,result):
    value={'actual_reads':reads,'actual_growth':growth,'model_calls':models,'result':result,'writer_released_after':not writer_held()}
    (tmp_path/'r160-read-observation.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    return value


@pytest.mark.parametrize('growing',[True,False],ids=['growth-after-stat','exact-2mb-control'])
def test_public_status_inventory_read_stays_bounded_and_preserves_original(brain,monkeypatch,tmp_path,growing):
    p=s.VAULT/'01-Projects/Original.md';p.parent.mkdir();prefix=b'# R160 private original\nHarmless original evidence.\n';raw=prefix if growing else prefix+b'x'*(LIMIT-len(prefix));p.write_bytes(raw)
    with monkeypatch.context() as patch:
        reads,growth,models=observe(patch,p,grow_to=LIMIT+1 if growing else None)
        result=w.status()
    observation=record(tmp_path,reads,growth,models,{'eligible_sources':result['eligible_sources'],'pending_source_parts':result['pending_source_parts']})
    expected=raw+b' '*(LIMIT+1-len(raw)) if growing else raw
    assert p.read_bytes()==expected and not models and not writer_held(),observation
    assert result['eligible_sources']==(0 if growing else 1),observation
    assert (result['pending_source_parts']==0) if growing else (result['pending_source_parts']>0),observation
    assert 'R160 private original' not in json.dumps(result),observation
    assert reads and all(row['requested']==LIMIT+1 and not row['under_actual_writer'] for row in reads),observation


@pytest.mark.parametrize('growing',[True,False],ids=['growth-after-stat','exact-128k-control'])
def test_public_run_policy_read_is_bounded_and_classified_without_model(brain,monkeypatch,tmp_path,growing):
    p=s.VAULT/w.SOURCE_POLICY;p.parent.mkdir(parents=True)
    raw=b'# R160 private policy\n```json\n{"version":1,"exclude_paths":[],"exclude_prefixes":[]}\n```\n'
    if not growing:raw+=b' '*(POLICY_LIMIT-len(raw))
    p.write_bytes(raw)
    with monkeypatch.context() as patch:
        reads,growth,models=observe(patch,p,grow_to=POLICY_LIMIT+1 if growing else None)
        result=w.run(1)
    observation=record(tmp_path,reads,growth,models,{'status':result['status'],'reason':result.get('reason'),'policy_valid':result['source_policy']['valid']})
    expected=raw+b' '*(POLICY_LIMIT+1-len(raw)) if growing else raw
    assert p.read_bytes()==expected and not models and not writer_held(),observation
    assert result['status']==('attention' if growing else 'idle'),observation
    assert result['source_policy']['valid'] is (not growing),observation
    assert 'R160 private policy' not in json.dumps(result),observation
    assert reads and all(row['requested']==POLICY_LIMIT+1 and not row['under_actual_writer'] for row in reads),observation


@pytest.mark.parametrize('oversize',[True,False],ids=['existing-2mb-plus-one','exact-2mb-idempotent-control'])
def test_legitimate_legacy_existing_receipt_is_bounded_under_real_writer(brain,monkeypatch,tmp_path,oversize):
    source,packet,result=fixture(brain);make_legacy(packet,w.apply(packet,result))
    first=w.migrate_legacy_receipts();assert len(first['receipts_created'])==1
    target=s.VAULT/first['receipts_created'][0]['path'];raw=target.read_bytes()
    padded=raw+b' '*(LIMIT+(1 if oversize else 0)-len(raw));target.write_bytes(padded)
    before=protected();error=None;report=None
    with monkeypatch.context() as patch:
        reads,growth,models=observe(patch,target)
        try:report=w.migrate_legacy_receipts()
        except (ValueError,w.WikiIntegrityError) as exc:error=exc
    observation=record(tmp_path,reads,growth,models,{'exception':type(error).__name__ if error else None,'field':getattr(error,'field',None),'page':getattr(error,'page',None),'verified_parts':report.get('verified_parts') if report else None})
    assert protected()==before and target.read_bytes()==padded and not models and not writer_held(),observation
    if oversize:
        assert isinstance(error,w.WikiIntegrityError) and error.field=='content' and error.page==target.relative_to(s.VAULT).as_posix(),observation
        assert 'R160 private' not in str(error),observation
    else:assert error is None and report['verified_parts']==1 and report['receipts_created']==[],observation
    assert reads and all(row['requested']==LIMIT+1 and row['under_actual_writer'] for row in reads),observation
