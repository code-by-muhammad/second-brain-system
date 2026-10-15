"""Exact history retention, streaming bounds and replay of compiler Log rotation."""
import copy
import json
import builtins
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic,note_snapshot
from test_wiki_remediation import explicit,fake_model


def test_legacy_oversize_log_rotates_exactly_once_without_repeated_model_work(brain,monkeypatch):
    _,_,_,result=existing_topic(brain)
    log=s.VAULT/w.BASE/'Log.md'
    raw=b'\xef\xbb\xbf# Retain complete history\r\n'+('Multibyte \u20ac history.\r\n'*110000).encode()
    assert len(raw)>2_000_000
    log.write_bytes(raw);packet=w.prepare();calls=fake_model(monkeypatch,explicit(packet,result))
    original=log.read_bytes();report=w.run()
    assert report['status']=='complete' and len(calls)==1
    archive=s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md')
    assert archive.read_bytes()==original and log.stat().st_size<=1_000_000
    assert archive.relative_to(s.VAULT).as_posix()[:-3] in log.read_text(encoding='utf-8')
    assert w.run()['status']=='idle' and len(calls)==1
    assert not w.lint()['issues']



def setup_log(brain,size=1_000_001):
    source,packet,result=fixture(brain)
    log=s.VAULT/w.BASE/'Log.md';log.parent.mkdir(parents=True,exist_ok=True)
    raw=b'# Entire old history\r\n'+b'x'*(size-len(b'# Entire old history\r\n'))
    log.write_bytes(raw)
    return source,packet,explicit(packet,result),log,raw


@pytest.mark.parametrize('position',['spool','archive','topic','log'])
def test_rotation_replay_survives_actual_replacement_crash_without_new_model(brain,monkeypatch,position):
    source,packet,result,log,raw=setup_log(brain);original=source.read_bytes();calls=fake_model(monkeypatch,result)
    stream_write=w._stream_publish;normal_write=s._write;plan=w._publication_plan;failed=False
    def crash_stream(write,*args):
        nonlocal failed
        receipt=stream_write(write,*args)
        if not failed and write['kind']==('wiki_log_archive' if position=='archive' else 'wiki_log') and position in ('archive','log'):
            failed=True;raise OSError('Synthetic crash after atomic replacement, before journal')
        return receipt
    def crash_normal(path,text,kind,*args):
        nonlocal failed
        receipt=normal_write(path,text,kind,*args)
        if not failed and kind=='wiki_compile' and position=='topic':
            failed=True;raise OSError('Synthetic crash after topic replacement, before journal')
        return receipt
    def crash_plan(*args):
        nonlocal failed
        value=plan(*args)
        if not failed and position=='spool':failed=True;raise OSError('Synthetic crash after durable spool, before plan')
        return value
    monkeypatch.setattr(w,'_stream_publish',crash_stream);monkeypatch.setattr(s,'_write',crash_normal);monkeypatch.setattr(w,'_publication_plan',crash_plan)
    with pytest.raises(OSError):w.run()
    assert len(calls)==1 and source.read_bytes()==original
    monkeypatch.setattr(w,'_stream_publish',stream_write);monkeypatch.setattr(s,'_write',normal_write);monkeypatch.setattr(w,'_publication_plan',plan)
    assert w.run()['status']=='complete' and len(calls)==1
    assert (s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md')).read_bytes()==raw
    assert log.read_text().count('wiki-batch:'+packet['batch_id'])==1
    assert w.status()['worker']['consecutive_failures']==0


@pytest.mark.parametrize('size',[100,999000,1_000_000,2_000_001])
def test_rotation_threshold_keeps_normal_plans_v2_and_rotated_plans_v3(brain,size):
    _,packet,result,log,raw=setup_log(brain,size);w.apply(packet,result)
    plan=json.loads((cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').read_text())
    rotated=size>=1_000_000
    assert plan['version']==(3 if rotated else 2)
    assert ('log_rotation' in plan)==rotated
    if rotated:assert (s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md')).read_bytes()==raw
    else:assert log.read_bytes().startswith(raw.replace(b'\r\n',b'\n'))


def test_legacy_log_streaming_has_bounded_reads_and_no_read_bytes(brain,monkeypatch):
    _,packet,result,log,_=setup_log(brain,2_000_001)
    raw=b'\xef\xbb\xbf'+b'a'*(65536-4)+'\u20ac'.encode()+b'\r\n'+b'z'*2_100_000
    log.write_bytes(raw);calls=fake_model(monkeypatch,result)
    original_open=type(log).open;original_read=type(log).read_bytes;requests=[]
    class BoundedReader:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,size=-1):
            requests.append(size);assert 0<size<=65536
            return self.stream.read(size)
        def __getattr__(self,key):return getattr(self.stream,key)
    def checked_open(path,mode='r',*args,**kwargs):
        stream=original_open(path,mode,*args,**kwargs)
        return BoundedReader(stream) if mode=='rb' and (path==log or path.suffix=='.bin' or path.parent.name=='Log-Archives') else stream
    def checked_read(path):
        assert path!=log and path.suffix!='.bin' and path.parent.name!='Log-Archives'
        return original_read(path)
    monkeypatch.setattr(type(log),'open',checked_open);monkeypatch.setattr(type(log),'read_bytes',checked_read)
    assert w.run()['status']=='complete' and len(calls)==1
    assert requests and max(requests)==65536
    monkeypatch.setattr(type(log),'read_bytes',original_read);monkeypatch.setattr(type(log),'open',original_open)
    assert (s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md')).read_bytes()==raw


@pytest.mark.parametrize('corruption',['missing','bytes','descriptor-size','descriptor-path','replacement','v2-archive'])
def test_rotation_saved_spool_and_descriptor_tampering_stop_without_fresh_calls(brain,monkeypatch,corruption):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);original=s._write
    def stop(path,text,kind,*args):
        if kind=='wiki_compile':raise OSError('Stop after archive, before semantic publication')
        return original(path,text,kind,*args)
    monkeypatch.setattr(s,'_write',stop)
    with pytest.raises(OSError):w.run()
    monkeypatch.setattr(s,'_write',original)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];publication=folder/'publication.json';plan=json.loads(publication.read_text());spool=folder/plan['log_rotation']['spool']
    if corruption=='missing':spool.unlink()
    elif corruption=='bytes':spool.write_bytes(b'Altered private recovery history')
    elif corruption=='descriptor-size':plan['log_rotation']['bytes']+=1
    elif corruption=='descriptor-path':plan['log_rotation']['path']=w.BASE+'Projects/owner.md'
    elif corruption=='replacement':plan['writes'][-1]['text']+='\nChanged archive link';plan['writes'][-1]['after']=s.digest(plan['writes'][-1]['text'].encode())
    else:plan['version']=2
    w._atomic_json(publication,plan);before=note_snapshot();w.reset_retries()
    try:w.run()
    except (w.WikiIntegrityError,w.WikiOutputError):pass
    assert len(calls)==1 and note_snapshot()==before and log.read_bytes()==raw
    if corruption in ('missing','bytes','descriptor-size','descriptor-path'):
        assert w.status()['worker']['status']=='attention'


def test_archive_collision_is_preserved_and_never_overwritten(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);archive=s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md');archive.parent.mkdir();archive.write_bytes(b'Owner collision, preserve it.')
    before=note_snapshot();calls=fake_model(monkeypatch,result)
    with pytest.raises(w.WikiIntegrityError):w.run()
    assert w.status()['worker']['consecutive_failures']==0 and note_snapshot()==before
    assert archive.read_bytes()==b'Owner collision, preserve it.' and len(calls)==1


def test_existing_exact_archive_is_reused(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);archive=s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md');archive.parent.mkdir();archive.write_bytes(raw)
    calls=fake_model(monkeypatch,result);assert w.run()['status']=='complete' and len(calls)==1
    assert archive.read_bytes()==raw


def test_invalid_utf8_log_blocks_before_model_and_preserves_all_bytes(brain,monkeypatch):
    _,packet,result,log,_=setup_log(brain);log.write_bytes(b'# history\n\xff');before=note_snapshot();calls=fake_model(monkeypatch,result)
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='encoding'
    assert len(calls)==0 and note_snapshot()==before


def test_unused_log_owner_edit_replans_from_saved_output_without_another_call(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);original=s._write
    def stop(path,text,kind,*args):
        if kind=='wiki_compile':raise OSError('Stop after archive only')
        return original(path,text,kind,*args)
    monkeypatch.setattr(s,'_write',stop)
    with pytest.raises(OSError):w.run()
    monkeypatch.setattr(s,'_write',original);updated=raw+b'\nOwner additional history.\n';log.write_bytes(updated)
    assert w.run()['status']=='complete' and len(calls)==1
    assert (s.VAULT/w.BASE/'Log-Archives'/(s.digest(updated)+'.md')).read_bytes()==updated
    assert list((cfg.STATE/'wiki/batches'/packet['batch_id']).glob('publication-obsolete-*.json'))


def test_owner_edit_during_log_streaming_is_preserved(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);stream=w._stream_file;changed=False
    def edit_after_read(path,*args,**kwargs):
        nonlocal changed
        value=stream(path,*args,**kwargs)
        if path==log and kwargs.get('sink') is not None and not changed:
            changed=True;log.write_bytes(raw+b'\nOwner concurrent edit.\n')
        return value
    monkeypatch.setattr(w,'_stream_file',edit_after_read);calls=fake_model(monkeypatch,result)
    report=w.run()
    assert report['status']=='retryable' and report['consecutive_failures']==0 and report['model_called'] is False
    assert len(calls)==0 and log.read_bytes()==raw+b'\nOwner concurrent edit.\n'


def test_archive_only_publication_cannot_override_new_owner_policy(brain,monkeypatch):
    source,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);publish=w._stream_publish
    def exclude_after_archive(write,*args):
        receipt=publish(write,*args)
        if write['kind']=='wiki_log_archive':
            policy=s.VAULT/w.SOURCE_POLICY;policy.parent.mkdir(parents=True,exist_ok=True)
            policy.write_text('```json\n'+json.dumps({'version':1,'exclude_paths':[source.relative_to(s.VAULT).as_posix()],'exclude_prefixes':[]})+'\n```\n',encoding='utf-8')
        return receipt
    monkeypatch.setattr(w,'_stream_publish',exclude_after_archive)
    assert w.run()['status']=='policy_changed' and len(calls)==1
    assert not (s.VAULT/w.BASE/'Projects/example.md').exists() and log.read_bytes()==raw
    assert not w._saved_publication_started(packet,cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json')
    assert w.run()['status']=='idle' and len(calls)==1


def test_completed_rotation_spool_cleanup_never_deletes_pending_spools(brain,monkeypatch):
    _,packet,result,_,_=setup_log(brain);calls=fake_model(monkeypatch,result);w.run();folder=cfg.STATE/'wiki/batches'/packet['batch_id'];spools=list(folder.glob('log-history-*.bin'));assert spools
        # Independently named pending recovery input is not a cleanup target.
    pending=cfg.STATE/'wiki/batches'/('a'*24);pending.mkdir();preserved=pending/('log-history-'+'b'*64+'.bin');preserved.write_bytes(b'Pending history')
    with w.db() as conn:conn.execute('UPDATE batches SET updated=? WHERE id=?',('2000-01-01T00:00:00+00:00',packet['batch_id']))
    w.cleanup();assert all(not path.exists() for path in spools) and preserved.read_bytes()==b'Pending history'



@pytest.mark.parametrize('ancestor',['batch','batches','wiki'])
def test_recovery_spool_rejects_actual_junction_or_symlink_ancestry_before_external_read(brain,monkeypatch,ancestor):
    import os,subprocess
    _,packet,result,_,_=setup_log(brain);w.apply(packet,result)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];plan=json.loads((folder/'publication.json').read_text())
    linked={'batch':folder,'batches':folder.parent,'wiki':folder.parent.parent}[ancestor]
    outside=cfg.STATE.parent.parent/('outside-spool-'+ancestor)
    assert linked.resolve().is_relative_to(cfg.STATE.resolve()) and outside.resolve().is_relative_to(cfg.STATE.parent.parent.resolve())
    linked.rename(outside)
    if os.name=='nt':
        proc=subprocess.run(['cmd','/d','/c','mklink','/J',str(linked),str(outside)],capture_output=True,**cfg.child_options())
        assert proc.returncode==0
    else:linked.symlink_to(outside,target_is_directory=True)
    external=outside/plan['log_rotation']['spool'] if ancestor=='batch' else outside/packet['batch_id']/plan['log_rotation']['spool'] if ancestor=='batches' else outside/'batches'/packet['batch_id']/plan['log_rotation']['spool']
    raw=external.read_bytes();stream=w._stream_file
    def forbid_external(path,*args,**kwargs):
        assert not path.resolve().is_relative_to(outside.resolve()),'An external spool was opened before ancestry validation.'
        return stream(path,*args,**kwargs)
    monkeypatch.setattr(w,'_stream_file',forbid_external)
    with pytest.raises(w.WikiIntegrityError) as error:w._archive_payload(plan,packet)
    assert error.value.field=='archive' and external.read_bytes()==raw


def test_actively_growing_log_stops_at_initial_size_without_model_or_spool_publication(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);original=type(log).open;requests=[];grew=False
    class GrowingReader:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,size=-1):
            nonlocal grew
            requests.append(size);value=self.stream.read(size)
            if not grew:
                grew=True
                with original(log,'ab') as writer:writer.write(b'Owner growing history.')
            return value
        def __getattr__(self,key):return getattr(self.stream,key)
    def checked(path,mode='r',*args,**kwargs):
        stream=original(path,mode,*args,**kwargs)
        return GrowingReader(stream) if path==log and mode=='rb' else stream
    monkeypatch.setattr(type(log),'open',checked)
    report=w.run()
    assert report['status']=='retryable' and report['consecutive_failures']==0 and report['model_called'] is False
    assert len(calls)==0 and sum(requests)<=len(raw)+1 and max(requests)<=65536
    assert not list((cfg.STATE/'wiki/batches'/packet['batch_id']).glob('log-history-*.bin'))
    monkeypatch.setattr(type(log),'open',original)
    assert log.read_bytes()==raw+b'Owner growing history.'


def test_concurrent_immutable_spool_creation_preserves_collision(brain,monkeypatch):
    _,packet,_,log,raw=setup_log(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];link=w.os.link;created=[]
    def race(source,target,*args,**kwargs):
        if target.parent==folder and target.name.startswith('log-history-'):
            target.write_bytes(b'Concurrent private immutable owner bytes');created.append(target)
        return link(source,target,*args,**kwargs)
    monkeypatch.setattr(w.os,'link',race)
    with pytest.raises(w.WikiIntegrityError):w._log_snapshot(folder)
    assert len(created)==1 and created[0].read_bytes()==b'Concurrent private immutable owner bytes' and log.read_bytes()==raw


def test_multiple_rotations_retain_exact_linked_history_chain(brain):
    source,packet,result,log,raw=setup_log(brain)
    for index in range(3):
        if index:
            source.write_bytes(source.read_bytes()+b'\nAdditional dated source revision.\n')
            packet=w.prepare();result=explicit(packet,result)
            retained=log.read_bytes();raw=retained+b'\n'+b'x'*1_000_000;log.write_bytes(raw)
        w.apply(packet,result)
        archive=s.VAULT/w.BASE/'Log-Archives'/(s.digest(raw)+'.md')
        assert archive.read_bytes()==raw
        assert archive.relative_to(s.VAULT).as_posix()[:-3] in log.read_text()
    assert len(list((s.VAULT/w.BASE/'Log-Archives').glob('*.md')))==3


def test_owner_edit_at_active_log_replace_keeps_partial_repair_safe(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);publish=w._stream_publish
    def owner_edit(write,*args):
        if write['kind']=='wiki_log':log.write_bytes(raw+b'\nOwner edit before replace.\n')
        return publish(write,*args)
    monkeypatch.setattr(w,'_stream_publish',owner_edit)
    with pytest.raises(w.WikiTransientError):w.run()
    assert len(calls)==1 and log.read_bytes()==raw+b'\nOwner edit before replace.\n'
    assert w._saved_publication_started(packet,cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json')
    # Actual semantic progress does not authorize overwriting a new owner Log.
    monkeypatch.setattr(w,'_stream_publish',publish)
    with pytest.raises(w.WikiTransientError):w.run()
    assert len(calls)==1 and log.read_bytes()==raw+b'\nOwner edit before replace.\n'



def test_completed_directory_alias_cannot_delete_pending_sibling_spools(brain):
    import os,subprocess
    _,packet,result,_,_=setup_log(brain);w.apply(packet,result);folder=cfg.STATE/'wiki/batches'/packet['batch_id']
    with w.db() as conn:conn.execute('UPDATE batches SET updated=? WHERE id=?',('2000-01-01T00:00:00+00:00',packet['batch_id']))
    sibling=folder.parent/('c'*24);folder.rename(sibling)
    preserved={path.name:path.read_bytes() for path in sibling.iterdir() if path.is_file()}
    if os.name=='nt':
        proc=subprocess.run(['cmd','/d','/c','mklink','/J',str(folder),str(sibling)],capture_output=True,**cfg.child_options());assert proc.returncode==0
    else:folder.symlink_to(sibling,target_is_directory=True)
    with pytest.raises(w.WikiIntegrityError):w.cleanup()
    assert all((sibling/name).read_bytes()==raw for name,raw in preserved.items())


@pytest.mark.parametrize('corruption',['spool','plan'])
def test_stale_actual_partial_with_bad_recovery_input_never_quietly_idles(brain,monkeypatch,corruption):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);publish=w._stream_publish
    def owner_edit(write,*args):
        if write['kind']=='wiki_log':log.write_bytes(raw+b'\nOwner current history.\n')
        return publish(write,*args)
    monkeypatch.setattr(w,'_stream_publish',owner_edit)
    with pytest.raises(w.WikiTransientError):w.run()
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];publication=folder/'publication.json';plan=json.loads(publication.read_text())
    assert (s.VAULT/w.BASE/'Receipts'/(packet['batch_id']+'.md')).exists()
    if corruption=='spool':(folder/plan['log_rotation']['spool']).unlink()
    else:plan['writes'][-1]['after']='0'*64;w._atomic_json(publication,plan)
    before=note_snapshot();monkeypatch.setattr(w,'_stream_publish',publish)
    with pytest.raises(w.WikiIntegrityError):w.run()
    report=w.status()['worker'];assert report['status']=='attention' and report['integrity_field'] in ('archive','recovery')
    assert len(calls)==1 and note_snapshot()==before


def test_owner_resolves_stale_partial_log_cas_then_saved_plan_finishes_once(brain,monkeypatch):
    _,packet,result,log,raw=setup_log(brain);calls=fake_model(monkeypatch,result);publish=w._stream_publish
    def owner_edit(write,*args):
        if write['kind']=='wiki_log':log.write_bytes(raw+b'\nOwner intentional new history.\n')
        return publish(write,*args)
    monkeypatch.setattr(w,'_stream_publish',owner_edit)
    with pytest.raises(w.WikiTransientError):w.run()
    assert w.status()['pending_source_parts']==0  # Verified receipt is already durable.
    monkeypatch.setattr(w,'_stream_publish',publish)
    # An owner can resolve by explicitly restoring the planned before image;
    # production never performs this automatic overwrite.
    log.write_bytes(raw);w.reset_retries()
    assert w.run()['status']=='complete' and len(calls)==1
    assert w.status()['worker']['status']=='complete'



@pytest.mark.parametrize('field',['packet','archive','recovery','serialization'])
def test_new_controlled_integrity_fields_survive_status_roundtrip(brain,field):
    w._atomic_json(cfg.STATE/'wiki-worker-status.json',{'status':'attention','attention_required':True,'reason':'WikiIntegrityError','integrity_page':w.BASE+'Index.md','integrity_field':field,'consecutive_failures':0,'retry_after':0})
    assert w.status()['worker']['integrity_field']==field


@pytest.mark.parametrize('error',[OSError('Synthetic bounded read failure'),w.WikiTransientError('Synthetic streaming source growth')])
def test_resumed_source_stream_failure_records_retry_without_calls_or_strikes(brain,monkeypatch,error):
    source,packet,result=fixture(brain);calls=fake_model(monkeypatch,explicit(packet,result));read=w._current_hash
    def fail(path):
        if path==source:raise error
        return read(path)
    monkeypatch.setattr(w,'_current_hash',fail)
    report=w.run();assert report['status']=='retryable' and report['consecutive_failures']==0 and report['model_called'] is False
    assert len(calls)==0 and w._read_worker_state()['status']=='retryable'



@pytest.mark.parametrize('location',['source-record','case-log','case-archives'])
def test_catalog_discovery_does_not_whole_file_read_auxiliary_or_case_history(brain,monkeypatch,location):
    _,_,_,log,_=setup_log(brain,100)
    if location=='source-record':target=s.VAULT/w.BASE/'Sources/owner.md';target.parent.mkdir();target.write_bytes(b'x'*2_000_001)
    elif location=='case-log':target=log.with_name('lOg.md');log.rename(target);target.write_bytes(b'x'*2_000_001)
    else:target=s.VAULT/w.BASE/'lOg-ArChIvEs'/'history.md';target.parent.mkdir();target.write_bytes(b'x'*2_000_001)
    original=type(target).read_bytes;opened=type(target).open;requests=[]
    def forbid(path):
        assert path!=target,'Catalog discovery must not perform whole-file reads of auxiliary/history files.'
        return original(path)
    class Measured:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,size=-1):requests.append(size);assert 0<size<=2_000_001;return self.stream.read(size)
        def __getattr__(self,key):return getattr(self.stream,key)
    def watch(path,mode='r',*args,**kwargs):
        stream=opened(path,mode,*args,**kwargs)
        return Measured(stream) if path==target and mode=='rb' else stream
    monkeypatch.setattr(type(target),'read_bytes',forbid);monkeypatch.setattr(type(target),'open',watch)
    assert w.pages()==[]
    assert requests==([2_000_001] if location=='source-record' else [])
    assert target.stat().st_size==2_000_001



def test_oversize_topic_stops_with_content_attention_before_model(brain,monkeypatch):
    _,packet,result=fixture(brain);topic=s.VAULT/w.BASE/'Projects/oversize.md';topic.parent.mkdir(parents=True)
    header=b'---\nwiki_kind: Projects\ntitle: Oversize\nwiki_claims: []\n---\n'
    raw=header+b'x'*(2_000_001-len(header));topic.write_bytes(raw);calls=fake_model(monkeypatch,explicit(packet,result))
    report=w.run();assert report['status']=='attention' and report['integrity_field']=='content' and len(calls)==0
    assert report['integrity_page']==topic.relative_to(s.VAULT).as_posix() and topic.read_bytes()==raw


def test_bounded_topic_discovery_preserves_original_bom_crlf_raw_sha(brain):
    vault,_=brain;topic=vault/w.BASE/'Projects/normal.md';topic.parent.mkdir(parents=True)
    raw=b'\xef\xbb\xbf---\r\nwiki_kind: Projects\r\ntitle: Normal\r\nwiki_claims: []\r\n---\r\n# Evidence\r\n'
    topic.write_bytes(raw);page=w._checked_pages()[0]
    assert page['sha256']==s.digest(raw) and page['meta']['title']=='Normal' and topic.read_bytes()==raw
