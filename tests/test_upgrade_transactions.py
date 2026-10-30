"""Sealed snapshot and recovery order. All paths/configs/processes are synthetic."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('transaction_update',SOURCE/'update.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)


@pytest.fixture
def installation(tmp_path,monkeypatch):
    root=tmp_path/'runtime';(root/'state').mkdir(parents=True);(root/'app').mkdir();(root/'backups').mkdir()
    vault=tmp_path/'vault';vault.mkdir();home=tmp_path/'home';home.mkdir()
    device={'vault':str(vault),'python':sys.executable,'release_root':str(tmp_path/'old-release'),'version':'0.1.7','device_id':'fixture','role':'capture','port':61234}
    (root/'device.json').write_bytes((json.dumps(device,indent=2)+'\n').encode())
    (root/'app/old.py').write_bytes(b'# old runtime\n')
    previous=Path(device['release_root']);previous.mkdir()
    (previous/'release-manifest.json').write_text(json.dumps({'version':'0.1.7','sha256':{'app/old.py':hashlib.sha256(b'# old runtime\n').hexdigest()}}))
    helper=updater.installer();support=helper.setup_helpers()
    monkeypatch.setattr(helper,'private_dir',lambda path:path.mkdir(parents=True,exist_ok=True))
    monkeypatch.setattr(helper,'private',lambda *args:None)
    monkeypatch.setattr(updater,'installer',lambda:helper)
    path=home/'config.json';path.write_bytes(b'{"owner":"before"}\n')
    after='{"owner":"after"}\n'
    planned_device={**device,'version':'0.1.8'}
    value={'format':1,'root':str(root),'python':sys.executable,'refresh':True,
           'device_before_sha256':support.digest((root/'device.json').read_bytes()),
           'device_after_sha256':support.digest((json.dumps(planned_device,indent=2)+'\n').encode()),
           'files':[{'client':'claude-code','path':str(path),'before_sha256':support.digest(path.read_bytes()),'after_sha256':support.digest(after.encode()),'after':after}]}
    return SimpleNamespace(root=root,vault=vault,home=home,device=device,plan=value,helper=helper,support=support,path=path,after=after,planned_device=planned_device)


def test_snapshot_is_exact_before_bytes_and_rejects_change_before_stop(installation):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    value=updater._snapshot(h.root,saved)
    assert value['previous']==h.device and (saved/'1').read_bytes()==h.path.read_bytes()
    assert 'state' not in {path.name for path in saved.iterdir()}
    h.path.write_text('{"owner":"concurrent"}')
    with pytest.raises(ValueError,match='old service is healthy'):updater.snapshot(h.root,sys.executable,h.plan)


def test_restore_uses_presealed_after_hash_even_without_write_receipt(installation):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    h.path.write_bytes(h.after.encode())
    (h.root/'device.json').write_bytes((json.dumps(h.planned_device,indent=2)+'\n').encode())
    (h.root/'app/old.py').unlink();(h.root/'app/new.py').write_bytes(b'# new runtime\n')
    result=updater.restore(h.root,saved)
    assert result['deferred_clients']==[] and h.path.read_bytes()==(saved/'1').read_bytes()
    assert (h.root/'app/old.py').is_file() and not (h.root/'app/new.py').exists()
    assert json.loads((h.root/'device.json').read_bytes())==h.device


def test_restore_preserves_concurrent_client_edit_and_refuses_foreign_device(installation):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    h.path.write_bytes(b'{"owner":"concurrent"}')
    result=updater.restore(h.root,saved)
    assert result['deferred_clients'][0]['path']==str(h.path) and h.path.read_bytes()==b'{"owner":"concurrent"}'
    (h.root/'device.json').write_bytes(b'{"owner":"changed-device"}')
    with pytest.raises(ValueError,match='Device changed outside'):updater.restore(h.root,saved)


def test_restore_absent_before_deletes_only_exact_after_bytes(installation):
    h=installation;h.path.unlink();h.plan['files'][0]['before_sha256']=None
    saved=updater.snapshot(h.root,sys.executable,h.plan)
    h.path.write_bytes(b'owner concurrent file')
    assert updater.restore(h.root,saved)['deferred_clients'] and h.path.exists()
    h.path.write_bytes(h.after.encode())
    assert not updater.restore(h.root,saved)['deferred_clients'] and not h.path.exists()


def test_corrupt_saved_app_refuses_before_live_bytes_change(installation):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    (saved/'app/old.py').write_bytes(b'# altered backup')
    before=(h.root/'app/old.py').read_bytes()
    with pytest.raises(ValueError,match='snapshot changed'):updater.restore(h.root,saved)
    assert (h.root/'app/old.py').read_bytes()==before


@pytest.mark.parametrize('switch',[False,True])
def test_rollback_stop_runs_outside_parent_work_locks_and_uses_new_controller(installation,monkeypatch,switch):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    held=[];events=[]
    class Locks:
        def __enter__(self):held.append(True)
        def __exit__(self,*args):held.clear()
    monkeypatch.setattr(updater,'_work_locks',lambda *args:Locks())
    def controller(python,checkout,root,command,action,*flags):
        events.append(action)
        assert str(checkout)=='new-reviewed-checkout' and python==sys.executable
        if action=='stop':assert not held
        if action=='install':assert held
        return {'status':action}
    monkeypatch.setattr(updater,'controller',controller)
    monkeypatch.setattr(updater,'wait_health',lambda *args,**kwargs:{'status':'running','version':'0.1.7'})
    result=updater._restart_previous(h.root,saved,sys.executable,'new-reviewed-checkout','a'*32,restore_bytes=switch)
    assert events==(['stop'] if switch else [])+['install','start','finish']
    assert result['health']['version']=='0.1.7'


def test_controller_carries_canonical_device_environment_and_isolated_source(installation,monkeypatch):
    h=installation;monkeypatch.setenv('BRAIN_PORT','9999');monkeypatch.setenv('BASIC_MEMORY_PROJECT','foreign')
    seen=[]
    def run(command,**kwargs):
        seen.append(command)
        assert command[1:4]==['-I','-B','-X'] and '-c' in command
        assert kwargs['env']['BRAIN_SYSTEM_ROOT']==str(h.root) and kwargs['env']['BRAIN_PORT']=='61234'
        assert 'BASIC_MEMORY_PROJECT' not in kwargs['env']
        return SimpleNamespace(returncode=0,stdout='{"status":"stopped"}')
    support=h.helper.setup_helpers();monkeypatch.setattr(support,'run',run)
    monkeypatch.setattr(h.helper,'setup_helpers',lambda:support)
    assert updater.controller(sys.executable,SOURCE,h.root,'service','stop')['status']=='stopped'


def test_unsupported_release_refused_before_provision_and_stop(installation,monkeypatch):
    h=installation;(h.root/'releases/v0.1.6').mkdir(parents=True)
    monkeypatch.setattr(updater,'__file__',str(h.root/'releases/v0.1.6/update.py'))
    monkeypatch.setattr(updater,'verify',lambda *args,**kwargs:{'version':'0.1.6'})
    monkeypatch.setattr(updater,'controller',lambda *args:pytest.fail('unsupported release controlled service'))
    with pytest.raises(ValueError,match='does not support safe'):updater.perform(h.root,'v0.1.6')


def test_packaged_host_refused_before_releases_or_state_change(installation,monkeypatch):
    h=installation
    monkeypatch.setattr(updater,'__file__',str(h.root/'releases/v0.1.8/update.py'))
    support=h.helper.setup_helpers()
    monkeypatch.setattr(support,'host_context',lambda:(_ for _ in ()).throw(ValueError('packaged')))
    monkeypatch.setattr(h.helper,'setup_helpers',lambda:support)
    before={path.relative_to(h.root).as_posix():path.read_bytes() for path in h.root.rglob('*') if path.is_file()}
    with pytest.raises(ValueError,match='packaged'):updater.perform(h.root,'v0.1.8')
    assert before=={path.relative_to(h.root).as_posix():path.read_bytes() for path in h.root.rglob('*') if path.is_file()}
