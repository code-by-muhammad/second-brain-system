"""Portable public maintenance contracts for independently appended service logs.

Only synthetic fixture logs/state/vault are used. The CLI is the real public
--no-sync path; the final control uses the existing injectable Git-sync seam.
"""
import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import runpy
import sys
import threading

import brain_git as git_module
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


NOTICE='Operational log rotation is deferred: active logs are preserved and may continue growing.'


def _public_cli(monkeypatch,no_sync=True):
    output=io.StringIO()
    arguments=[str(Path(maintenance.__file__).resolve())]
    if no_sync:arguments.append('--no-sync')
    with monkeypatch.context() as context:
        context.setattr(sys,'argv',arguments)
        with contextlib.redirect_stdout(output):
            runpy.run_path(arguments[0],run_name='__main__')
    return json.loads(output.getvalue().strip())


def test_public_cli_preserves_confirmed_append_during_oversize_log_handling(brain,monkeypatch):
    vault,state=brain;monkeypatch.setattr(cfg,'ROLE','capture')
    logs=state.parent/'logs';active=logs/'service-server.log'
    original=b'harmless operational line\n'*220000
    assert len(original)>5_000_000
    active.write_bytes(original)
    marker=b'R144-CONFIRMED-CONCURRENT-APPEND\n'
    ready=threading.Event();appended=threading.Event();failures=[];observation={}
    original_read=Path.read_bytes;original_write_text=Path.write_text
    def schedule(trigger):
        if not ready.is_set():
            observation['trigger']=trigger;ready.set()
        assert appended.wait(5) and not failures,'The synthetic concurrent append did not complete.'
    with active.open('ab',buffering=0) as writer:
        def append_once():
            try:
                assert ready.wait(5),'No public maintenance coordination point was reached.'
                written=writer.write(marker);os.fsync(writer.fileno())
                assert written==len(marker)
                observation['appended_bytes']=written
                observation['size_after_append']=active.stat().st_size
            except BaseException as error:failures.append(type(error).__name__+': '+str(error))
            finally:appended.set()
        def observed_read(path):
            raw=original_read(path)
            if path==active and not ready.is_set():schedule('actual-log-read-EOF')
            return raw
        def observed_status_write(path,data,*args,**kwargs):
            if path==state/'maintenance-status.tmp' and json.loads(data).get('stage')=='finished':
                # A safe deferral need not read the active log. Still force one
                # real append while public maintenance holds its caller locks.
                schedule('public-final-status-write')
            return original_write_text(path,data,*args,**kwargs)
        thread=threading.Thread(target=append_once,daemon=True);thread.start()
        try:
            with monkeypatch.context() as context:
                context.setattr(Path,'read_bytes',observed_read)
                context.setattr(Path,'write_text',observed_status_write)
                status=_public_cli(monkeypatch)
        finally:thread.join(6)
        assert not thread.is_alive() and ready.is_set() and appended.is_set() and not failures
    archives=list(logs.glob('service-server.log.*.gz'))
    payloads=[active.read_bytes()]
    for archive in archives:
        with gzip.open(archive,'rb') as stream:payloads.append(stream.read())
    assert observation['appended_bytes']==len(marker)
    assert observation['size_after_append']==len(original)+len(marker)
    # The old public CLI reaches the real EOF seam, then loses this confirmed
    # marker from both actual gzip output and the truncated active log.
    assert sum(payload.count(marker) for payload in payloads)==1,'Confirmed concurrent append was lost.'
    assert active.read_bytes()==original+marker and archives==[]
    assert status['status']=='needs attention' and status['error']==NOTICE and status['git']=='not requested'


def test_oversize_logs_and_existing_archives_survive_repeated_attention_passes(brain,monkeypatch):
    vault,state=brain;monkeypatch.setattr(cfg,'ROLE','capture')
    logs=state.parent/'logs';active=logs/'service-bootstrap.log'
    original=b'private synthetic log marker\n'+b'x'*5_000_001;active.write_bytes(original)
    archive=logs/'service-bootstrap.log.historical.gz'
    archive_bytes=gzip.compress(b'previous synthetic operational history\n',mtime=0);archive.write_bytes(archive_bytes)
    before_names={path.name for path in logs.iterdir()}
    for _ in range(2):
        status=_public_cli(monkeypatch)
        assert status['status']=='needs attention' and status['error']==NOTICE
        assert status['git']=='not requested' and status['stage']=='finished'
        assert 'completed_private_artifacts_removed' in status
        assert active.read_bytes()==original and archive.read_bytes()==archive_bytes
        assert {path.name for path in logs.iterdir()}==before_names,'Deferral must not duplicate archives each pass.'
    public=maintenance.health_status(state/'maintenance-status.json')
    assert public['status']=='needs attention' and public['error']==NOTICE
    assert 'private synthetic log marker' not in json.dumps(public)


def test_small_and_exact_threshold_logs_remain_unchanged_and_complete_normally(brain,monkeypatch):
    vault,state=brain;monkeypatch.setattr(cfg,'ROLE','capture')
    logs=state.parent/'logs'
    payloads={'service-maintenance.log':b'small synthetic diagnostic\n',
              'service-supervisor.log':b'e'*5_000_000}
    for name,raw in payloads.items():(logs/name).write_bytes(raw)
    status=_public_cli(monkeypatch)
    assert status['status']=='completed' and status['git']=='not requested' and status['stage']=='finished'
    assert 'error' not in status
    assert {path.name:path.read_bytes() for path in logs.iterdir()}==payloads
    assert maintenance.health_status(state/'maintenance-status.json')['status']=='completed'


def test_log_attention_keeps_daily_linking_cleanup_and_git_sync_running(brain,monkeypatch):
    vault,state=brain
    templates=vault/'Templates';templates.mkdir()
    (templates/'daily-note.md').write_text('# Synthetic daily\n\n## Work Log\n',encoding='utf-8')
    capture=store.capture('r144-synthetic-daily','Synthetic log preservation milestone',
                          'Harmless synthetic result.','tests','Synthetic local fixture')
    capture_path=vault/capture['path'];capture_before=capture_path.read_bytes()
    active=state.parent/'logs/service-maintenance.log';raw=b'q'*5_000_001;active.write_bytes(raw)
    calls=[];cleanup_calls=[];actual_cleanup=wiki.cleanup
    def sync_fixture():
        calls.append('synthetic sync seam')
        assert active.read_bytes()==raw
        return 'synced'
    def observed_cleanup(*args,**kwargs):
        value=actual_cleanup(*args,**kwargs);cleanup_calls.append(value);return value
    monkeypatch.setattr(git_module,'sync',sync_fixture)
    monkeypatch.setattr(wiki,'cleanup',observed_cleanup)
    status=_public_cli(monkeypatch,no_sync=False)
    assert calls==['synthetic sync seam'] and len(cleanup_calls)==1
    assert status['git']=='synced' and status['captures_linked']==1
    assert status['status']=='needs attention' and status['error']==NOTICE and status['stage']=='finished'
    assert status['completed_private_artifacts_removed']==cleanup_calls[0]
    daily=list((vault/'05-Daily').glob('*.md'));assert len(daily)==1
    assert daily[0].read_text('utf-8').count('<!-- brain-capture:r144-synthetic-daily -->')==1
    assert capture_path.read_bytes()==capture_before and active.read_bytes()==raw
    assert not list((state.parent/'logs').glob('*.gz'))
