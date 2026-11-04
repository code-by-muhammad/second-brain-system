"""Maintenance status survives a verified successful Git cleanup notification.

The public maintenance CLI and shipping Git sync/dispatcher run against a local
bare repository. Only the timing notification is injected after a real successful
commit; this is not a claim to reproduce native Windows cleanup latency. The
registered callback is invoked while the actual sync owns its caller locks.
"""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys

from filelock import FileLock, Timeout
import pytest

import brain_git as g
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
from test_sync_fsmonitor_policy import isolated_git, _raw, _init


LOG_NOTICE='Operational log rotation is deferred: active logs are preserved and may continue growing.'


def _repository(brain,tmp_path,monkeypatch,hooks,push_failure=False):
    vault,state=brain
    monkeypatch.setattr(cfg,'ROLE','capture')
    remote=tmp_path/'remote.git'
    _init(remote,hooks,bare=True)
    _init(vault,hooks)
    source=vault/'01-Projects/Synthetic.md';source.parent.mkdir()
    source.write_bytes(b'# Synthetic source\nOriginal local evidence.\n')
    _raw(vault,'add','--','01-Projects/Synthetic.md')
    _raw(vault,'commit','-m','synthetic baseline')
    _raw(vault,'remote','add','origin',str(remote))
    _raw(vault,'push','-u','origin','HEAD')
    before=_raw(vault,'rev-parse','HEAD').strip()
    source.write_bytes(b'# Synthetic source\nChanged local evidence for real sync.\n')
    if push_failure:
        # Fetch still uses the valid local bare repo. Actual Git push fails
        # against this absent fixture-only push target; no transport is faked.
        _raw(vault,'remote','set-url','--push','origin',str(tmp_path/'absent-push-target.git'))
    return vault,state,source,remote,before


def _observe_commit_callback(monkeypatch,state,count):
    shipping_run_git=g._run_git;observed=[];commits=[]
    def delegate(command,**kwargs):
        result=shipping_run_git(command,**kwargs)
        if list(command)[4:5]==['commit']:
            assert result.returncode==0
            commits.append(result.returncode)
            reporter=g._CLEANUP_REPORTER.get()
            assert callable(reporter),'Public maintenance must register its actual callback.'
            for _ in range(count):
                for name in ('maintenance.lock','writer.lock','git-sync.lock'):
                    with pytest.raises(Timeout):
                        with FileLock(str(state/name),timeout=0):pass
                reporter()
                current=json.loads((state/'maintenance-status.json').read_text(encoding='utf-8'))
                assert current['status']=='needs attention'
                assert current['stage']=='Git cleanup pending'
                assert current['git'].startswith('needs attention: Git process cleanup')
                observed.append(current)
        return result
    monkeypatch.setattr(g,'_run_git',delegate)
    return commits,observed


def _public_cli(monkeypatch):
    output=io.StringIO();script=str(Path(maintenance.__file__).resolve())
    with monkeypatch.context() as context:
        context.setattr(sys,'argv',[script])
        with contextlib.redirect_stdout(output):runpy.run_path(script,run_name='__main__')
    return json.loads(output.getvalue().strip())


def _assert_finished_return_disk_and_health(result,state,status,git):
    persisted=json.loads((state/'maintenance-status.json').read_text(encoding='utf-8'))
    assert result==persisted
    assert result['stage']=='finished' and result['git']==git
    assert result['status']==status
    public=maintenance.health_status(state/'maintenance-status.json')
    assert public['stage']=='finished' and public['git']==git and public['status']==status
    assert g._CLEANUP_REPORTER.get() is None,'The real context manager must reset after the pass.'


def test_real_sync_without_cleanup_notice_still_completes(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,source,remote,before=_repository(brain,tmp_path,monkeypatch,isolated_git)
    original=source.read_bytes();commits,notices=_observe_commit_callback(monkeypatch,state,0)
    result=_public_cli(monkeypatch)
    _assert_finished_return_disk_and_health(result,state,'completed','synced')
    assert commits==[0] and notices==[] and 'error' not in result
    after=_raw(vault,'rev-parse','HEAD').strip();assert after!=before
    assert _raw(remote,'rev-parse','refs/heads/main').strip()==after
    assert _raw(remote,'show','refs/heads/main:01-Projects/Synthetic.md')==original==source.read_bytes()


@pytest.mark.parametrize('notifications',[1,2],ids=['single-notice','repeated-notice'])
def test_verified_sync_restores_status_after_cleanup_notice(brain,tmp_path,monkeypatch,isolated_git,notifications):
    vault,state,source,remote,before=_repository(brain,tmp_path,monkeypatch,isolated_git)
    original=source.read_bytes();commits,notices=_observe_commit_callback(monkeypatch,state,notifications)
    result=_public_cli(monkeypatch)
    assert commits==[0] and len(notices)==notifications
    assert result['git']=='synced' and 'error' not in result
    after=_raw(vault,'rev-parse','HEAD').strip();assert after!=before
    assert _raw(remote,'rev-parse','refs/heads/main').strip()==after
    assert _raw(remote,'show','refs/heads/main:01-Projects/Synthetic.md')==original==source.read_bytes()
    _assert_finished_return_disk_and_health(result,state,'completed','synced')


def test_cleanup_success_preserves_prior_operational_log_attention(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,source,remote,before=_repository(brain,tmp_path,monkeypatch,isolated_git)
    log=state.parent/'logs/service-maintenance.log';raw=b'x'*5_000_001;log.write_bytes(raw)
    commits,notices=_observe_commit_callback(monkeypatch,state,2)
    result=_public_cli(monkeypatch)
    assert commits==[0] and len(notices)==2
    _assert_finished_return_disk_and_health(result,state,'needs attention','synced')
    assert result['error']==LOG_NOTICE and all(row['error']==LOG_NOTICE for row in notices)
    assert log.read_bytes()==raw and not list(log.parent.glob('*.gz'))
    assert _raw(remote,'show','refs/heads/main:01-Projects/Synthetic.md')==source.read_bytes()


def test_real_push_error_after_cleanup_notice_keeps_attention(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,source,remote,before=_repository(brain,tmp_path,monkeypatch,isolated_git,push_failure=True)
    original=source.read_bytes();commits,notices=_observe_commit_callback(monkeypatch,state,1)
    result=_public_cli(monkeypatch)
    assert commits==[0] and len(notices)==1
    assert result['git']=='needs attention: Git push failed (exit 128); local changes preserved. Inspect repository status.'
    _assert_finished_return_disk_and_health(result,state,'needs attention',result['git'])
    assert 'error' not in result and source.read_bytes()==original
    assert _raw(vault,'rev-parse','HEAD').strip()!=before
    assert _raw(remote,'rev-parse','refs/heads/main').strip()==before
    assert _raw(vault,'status','--porcelain')==b''
