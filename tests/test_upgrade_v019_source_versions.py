"""Version-dependent updater/metadata controls; OS and controller execution are fake.

The real planner, snapshot, deploy and restore functions run on synthetic bytes.
These tests do not execute v0.1.7/v0.1.8/v0.2.2 app code or prove native E2E.
The fixture uses one shared Python placeholder. Checkout/metadata selection is
checked here; actual old/new interpreter separation needs the native receipts.
"""
import json
import hashlib
import os
from pathlib import Path
import sys

import pytest

import brain_process as processes
import brain_service as service
import brain_settings as cfg
from test_portable_remediation import portable
from upgrade_fixture import prepare


def binding_path(h):
    return h.root/'state/previous-release.json'


def install_prior_binding(h):
    before=(json.dumps({'python':str(h.root/'older/.venv/Scripts/python.exe'),
        'version':'0.1.6','release_root':str(h.root/'older'),
        'controller_python':str(h.root/'older-controller/.venv/Scripts/python.exe')},indent=2)+'\n').encode()
    h.install.atomic(binding_path(h),before)
    return before


def assert_new_controller(h,expected_finish):
    events=[row for row in h.events if row['kind']=='controller']
    assert events and all(row['checkout']==str(h.release) for row in events)
    finish=[row for row in events if row['action']=='finish']
    assert len(finish)==1
    flags=finish[0]['flags']
    assert flags[flags.index('--expected-version')+1]==expected_finish
    return events


@pytest.mark.parametrize('previous_version,target_version',[('0.1.7','0.2.0'),('0.1.8','0.2.0'),('0.2.2','0.2.3'),('0.1.7','0.2.4'),('0.2.3','0.2.4'),('0.1.7','0.2.5'),('0.2.4','0.2.5')])
def test_current_forward_uses_new_controller_and_replaces_existing_binding(portable,monkeypatch,previous_version,target_version):
    h=prepare(portable,monkeypatch,previous_version=previous_version,target_version=target_version)
    old_binding=install_prior_binding(h)
    result=h.updater.perform(h.root,'v'+target_version)
    assert result['status']=='updated' and result['version']==target_version
    assert json.loads((h.root/'device.json').read_bytes())['version']==target_version
    current=json.loads(binding_path(h).read_bytes())
    assert binding_path(h).read_bytes()!=old_binding
    assert current['version']==previous_version and current['python']==h.device['python']
    assert current['controller_python']==sys.executable and current['snapshot']==result['snapshot']
    assert h.health==[target_version] and not (h.root/'state/service-transaction.json').exists()
    assert [row['action'] for row in assert_new_controller(h,target_version)]==[
        'inspect','hold','stop','install','start','finish']


@pytest.mark.parametrize('previous_version,target_version',[('0.1.7','0.2.0'),('0.1.8','0.2.0'),('0.2.2','0.2.3'),('0.1.7','0.2.4'),('0.2.3','0.2.4'),('0.1.7','0.2.5'),('0.2.4','0.2.5')])
def test_current_automatic_rollback_restores_prior_binding_and_exact_runtime(portable,monkeypatch,previous_version,target_version):
    h=prepare(portable,monkeypatch,previous_version=previous_version,target_version=target_version)
    old_binding=install_prior_binding(h)
    before_device=(h.root/'device.json').read_bytes()
    before_app={p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}
    h.fail_health=True
    with pytest.raises(RuntimeError,match='previous runtime health verified'):
        h.updater.perform(h.root,'v'+target_version)
    assert (h.root/'device.json').read_bytes()==before_device
    assert {p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}==before_app
    assert binding_path(h).read_bytes()==old_binding
    assert h.health==[target_version,previous_version]
    assert not (h.root/'state/service-transaction.json').exists()
    assert [row['action'] for row in assert_new_controller(h,previous_version)]==[
        'inspect','hold','stop','install','start','stop','install','start','finish']


@pytest.mark.parametrize('previous_version,target_version',[('0.1.7','0.2.0'),('0.1.8','0.2.0'),('0.2.2','0.2.3'),('0.1.7','0.2.4'),('0.2.3','0.2.4'),('0.1.7','0.2.5'),('0.2.4','0.2.5')])
def test_current_explicit_uuid_recovery_uses_snapshot_controller_and_restores_sealed_binding(portable,monkeypatch,previous_version,target_version):
    h=prepare(portable,monkeypatch,previous_version=previous_version,target_version=target_version)
    old_binding=install_prior_binding(h)
    before_device=(h.root/'device.json').read_bytes()
    before_app={p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}
    h.fail_health=True
    actual=h.updater.controller;stops=[]
    def controlled_failure(python,checkout,root,command,action=None,*flags):
        if action=='stop':
            stops.append(True)
            if len(stops)==2:raise RuntimeError('synthetic automatic rollback stop refusal')
        return actual(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controlled_failure)
    with pytest.raises(RuntimeError,match='recovery is incomplete'):
        h.updater.perform(h.root,'v'+target_version)
    transaction=json.loads((h.root/'state/service-transaction.json').read_bytes())
    snapshot=json.loads((Path(transaction['snapshot'])/'snapshot.json').read_bytes())
    assert snapshot['controller_checkout']==str(h.release)
    assert snapshot['controller_identity']['tag']=='v'+target_version
    assert snapshot['previous']['version']==previous_version
    forward_binding=binding_path(h).read_bytes()
    assert forward_binding!=old_binding
    # Format3 seals exact original metadata and restores it before releasing the hold.
    assert snapshot['format']==3 and snapshot['previous_binding']['before_sha256']==hashlib.sha256(old_binding).hexdigest()
    monkeypatch.setattr(h.updater,'controller',actual)
    with h.updater.update_lock(h.root):
        recovered=h.updater.recover(h.root,transaction['hold_id'])
    assert recovered['status']=='recovered'
    assert (h.root/'device.json').read_bytes()==before_device
    assert {p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}==before_app
    assert binding_path(h).read_bytes()==old_binding
    assert not (h.root/'state/service-transaction.json').exists()
    assert h.health==[target_version,previous_version]
    assert_new_controller(h,previous_version)


@pytest.mark.parametrize('version,expected',[('0.1.7','legacy-unavailable'),('0.1.8',None)])
def test_only_pre_heartbeat_versions_receive_legacy_exemption(brain,monkeypatch,version,expected):
    monkeypatch.setattr(cfg,'DEVICE',{'version':version})
    assert not (cfg.STATE/'service-heartbeat.json').exists()
    assert service._heartbeat({'supervisor_pid':101,'supervisor_birth':200},300,True)==expected


def test_v018_heartbeat_must_match_fresh_supervisor_identity(brain,monkeypatch):
    monkeypatch.setattr(cfg,'DEVICE',{'version':'0.1.8'})
    identity={'supervisor_pid':101,'supervisor_birth':200}
    path=cfg.STATE/'service-heartbeat.json'
    value={'status':'ready','pid':101,'birth':200,'written_filetime':300}
    path.write_text(json.dumps(value))
    assert service._heartbeat(identity,300,True)=='verified'
    for changed in ({'pid':102},{'birth':201},{'written_filetime':299}):
        path.write_text(json.dumps({**value,**changed}))
        assert service._heartbeat(identity,300,True) is None


@pytest.mark.skipif(os.name!='nt',reason='Windows launcher names and retained release metadata')
def test_launchers_admits_old_v018_and_new_v019_controller_python(brain,monkeypatch):
    base=cfg.ROOT/'host-python'
    old=cfg.ROOT/'releases/v0.1.8/.venv/Scripts/python.exe'
    current=cfg.ROOT/'releases/v0.1.9/.venv/Scripts/python.exe'
    unrelated=cfg.ROOT/'unrelated/.venv/Scripts/python.exe'
    for executable in (old,current):
        executable.parent.mkdir(parents=True)
        (executable.parent.parent/'pyvenv.cfg').write_text('home = '+str(base)+'\n')
    monkeypatch.setattr(cfg,'PYTHON',old)
    (cfg.STATE/'previous-release.json').write_text(json.dumps({'python':str(old),'controller_python':str(current)}))
    launchers=processes._launchers()
    for executable in (old,current):
        for name in ('python.exe','pythonw.exe'):
            assert launchers[processes._path(executable.with_name(name))]==processes._path(base/name)
    assert processes._path(unrelated) not in launchers
