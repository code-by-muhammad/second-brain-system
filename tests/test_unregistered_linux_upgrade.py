"""Held updater routing with a simulated native user-space service."""
import json
from pathlib import Path
import sys

import pytest

from test_portable_remediation import portable
from upgrade_fixture import prepare

pytestmark=pytest.mark.skipif(sys.platform!='linux',reason='Linux user-space registration.')


def _unregistered():
    return {'status':'inspected','task_present':False,'task_enabled':None,'registration':None,
            'native':{'backend':'user-space','file_present':False,'enabled':None,'loaded':False,
                      'running':False,'quiescent':True,'main_pid':None}}


def test_predicate_rejects_every_other_platform_and_a_stopped_service(portable,monkeypatch):
    updater=portable[1]
    native=_unregistered()['native']
    assert updater._unregistered_user_service(_unregistered())
    for platform in ('win32','darwin'):
        monkeypatch.setattr(updater.sys,'platform',platform)
        assert not updater._unregistered_user_service(_unregistered())
    monkeypatch.setattr(updater.sys,'platform','linux')
    stopped={**_unregistered(),'task_enabled':False,'native':{**native,'file_present':True,'enabled':False}}
    assert not updater._unregistered_user_service(stopped)
    assert not updater._unregistered_user_service({**_unregistered(),'native':{'file_present':False}})


@pytest.fixture
def unregistered(portable,monkeypatch):
    h=prepare(portable,monkeypatch,previous_version='0.2.15',target_version='0.2.16')
    h.running=False
    (h.root/'state/install-transaction.json').write_text('{"stage":"complete"}')
    original=h.updater.controller
    def controller(python,checkout,root,command,action=None,*flags):
        if action=='inspect':
            h.events.append({'kind':'controller','action':action})
            return _unregistered()
        if action=='plan-registration':
            h.events.append({'kind':'controller','action':action})
            return {'status':'registration planned','artifacts':[]}
        return original(python,checkout,root,command,action,*flags)
    monkeypatch.setattr(h.updater,'controller',controller)
    return h


@pytest.mark.parametrize('no_service',[False,True])
def test_unregistered_update_is_held_and_uses_common_finish(unregistered,no_service):
    h=unregistered;before=(h.root/'device.json').read_bytes()
    result=h.updater.perform(h.root,'v0.2.16',no_service=no_service,clients=[])
    assert result['status']=='updated' and result['first_user_service'] is not no_service
    assert result['service_policy']==('absent' if no_service else 'first')
    assert result['client_overwrites_after_health']==[]
    assert json.loads((h.root/'device.json').read_text())['version']=='0.2.16'
    actions=['inspect','hold','stop','check-stopped','finish'] if no_service else [
        'inspect','plan-registration','hold','stop','check-stopped','install','start','finish']
    assert [row['action'] for row in h.events if row['kind']=='controller']==actions
    seal=json.loads((Path(result['snapshot'])/'snapshot.json').read_bytes())
    assert seal['registration']=={'kind':'linux','backend':'user-space','present':False}
    assert not (h.root/'state/service-transaction.json').exists()
    assert (h.root/'bin'/'second-brain-mcp').is_file() and before!=(h.root/'device.json').read_bytes()


def test_registered_no_service_refuses_before_provision(portable,monkeypatch):
    h=prepare(portable,monkeypatch,previous_version='0.2.15',target_version='0.2.16')
    before=(h.root/'device.json').read_bytes()
    with pytest.raises(ValueError,match='verified unregistered'):
        h.updater.perform(h.root,'v0.2.16',no_service=True)
    assert all(row['kind']!='provision' and row.get('action')!='hold' for row in h.events)
    assert (h.root/'device.json').read_bytes()==before


@pytest.mark.parametrize('backend,enabled',[('user-space',False),('systemd',None)])
def test_stopped_registered_and_absent_systemd_refuse_before_provision(unregistered,monkeypatch,backend,enabled):
    h=unregistered
    before=(h.root/'device.json').read_bytes()
    def controller(python,checkout,root,command,action=None,*flags):
        assert action=='inspect'
        native={**_unregistered()['native'],'backend':backend,'enabled':enabled,'file_present':enabled is False}
        return {**_unregistered(),'task_enabled':enabled,'native':native}
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(ValueError,match='absent or explicitly stopped'):
        h.updater.perform(h.root,'v0.2.16')
    assert not h.events
    assert (h.root/'device.json').read_bytes()==before


@pytest.mark.parametrize('rename_number',[1,2])
def test_interrupted_app_rename_restores_original_binding_and_launcher(unregistered,monkeypatch,rename_number):
    h=unregistered;app=h.root/'app';launcher=h.root/'bin/second-brain-mcp'
    before=(h.root/'device.json').read_bytes()
    original={p.name:p.read_bytes() for p in app.iterdir()}
    launcher.write_bytes(b'previous launcher\n');launcher.chmod(0o751)
    observed=[];replace=h.install.os.replace
    def interrupted(source,target,*args,**kwargs):
        source=Path(source);target=Path(target)
        if (source==app and target.name=='displaced-app') or (target==app and source.parent.name.startswith('staged-')):
            observed.append(source)
            if len(observed)==rename_number:raise RuntimeError('Interrupted app publication')
        return replace(source,target,*args,**kwargs)
    monkeypatch.setattr(h.install.os,'replace',interrupted)
    with pytest.raises(RuntimeError,match='service absence verified'):
        h.updater.perform(h.root,'v0.2.16',no_service=True)
    assert (h.root/'device.json').read_bytes()==before
    assert {p.name:p.read_bytes() for p in app.iterdir()}==original
    assert launcher.read_bytes()==b'previous launcher\n' and launcher.stat().st_mode&0o777==0o751
    assert not (h.root/'state/service-transaction.json').exists()


def test_first_registration_health_failure_withdraws_before_restoring(unregistered):
    h=unregistered;h.fail_health=True
    before=(h.root/'device.json').read_bytes()
    launcher=h.root/'bin/second-brain-mcp';launcher.unlink()
    with pytest.raises(RuntimeError,match='service absence verified'):
        h.updater.perform(h.root,'v0.2.16')
    assert (h.root/'device.json').read_bytes()==before and not launcher.exists() and not h.running
    assert [row['action'] for row in h.events if row['kind']=='controller']==[
        'inspect','plan-registration','hold','stop','check-stopped','install','start',
        'stop','check-stopped','withdraw-first','finish']
    assert not (h.root/'state/service-transaction.json').exists()


@pytest.mark.parametrize('partial',['unit','activation'])
def test_partial_owned_registration_can_be_stopped_and_withdrawn(unregistered,monkeypatch,partial):
    import brain_service as service
    from filelock import FileLock,Timeout
    from types import SimpleNamespace

    h=unregistered
    directory=h.home/'.config/second-brain';directory.mkdir(parents=True)
    reg=SimpleNamespace(path=directory/'second-brain.service',enabled_path=directory/'enabled',
                        boot_path=directory/'boot-start',home=h.home)
    native={'unit':(reg.path,(b'candidate native unit\n',0o644)),
            'activation':(reg.enabled_path,(b'false\n',0o644))}
    path,after=native[partial];path.write_bytes(after[0]);path.chmod(after[1])
    hold='a'*32;saved=h.root/'backups/test-partial';saved.mkdir()
    records=h.updater._seal_artifacts(saved,[(kind,target,(None,None),image,'native')
                                            for kind,(target,image) in native.items()])
    snapshot={'root':str(h.root),'registration':{'kind':'linux','backend':'user-space','present':False},
              'deployment':{'hold_id':hold,'service_policy':'first','target_app_sha256':{},'artifacts':records}}
    value={'hold_id':hold,'root':str(h.root),'snapshot':str(saved),'stage':'stopped','empty_at_filetime':123}
    monkeypatch.setattr(service.cfg,'ROOT',h.root)
    monkeypatch.setattr(service,'_held_snapshot',lambda observed:snapshot)
    locks=[]
    def prove(registration):
        assert registration is reg
        try:
            with FileLock(str(h.root/'state/writer.lock'),timeout=0):locks.append(False)
        except Timeout:locks.append(True)
    monkeypatch.setattr(service,'prove_user_space_quiescence',prove)
    stopped=service._stop_first_user_space(reg,value,2)
    assert stopped['status']=='stopped' and locks==[False,True] and path.read_bytes()==after[0]
    service._publish_native_images(reg,value,snapshot,restoring=True)
    assert not reg.path.exists() and not reg.enabled_path.exists()


def test_held_upgrade_preserves_conflicting_launcher_and_exact_recovery_uuid(unregistered,monkeypatch):
    h=unregistered;h.fail_health=True
    launcher=h.root/'bin/second-brain-mcp'
    before=launcher.read_bytes();original=h.updater.controller
    def controller(python,checkout,root,command,action=None,*flags):
        result=original(python,checkout,root,command,action,*flags)
        if action=='start':
            assert launcher.read_bytes()!=before or launcher.is_file()
            launcher.write_bytes(b'concurrent launcher\n')
        return result
    monkeypatch.setattr(h.updater,'controller',controller)
    with pytest.raises(RuntimeError,match='recovery is incomplete'):
        h.updater.perform(h.root,'v0.2.16')
    receipt=json.loads((h.root/'state/upgrade-status.json').read_bytes())
    active=json.loads((h.root/'state/service-transaction.json').read_bytes())
    assert receipt['hold_id']==active['hold_id']
    assert launcher.read_bytes()==b'concurrent launcher\n'


@pytest.mark.parametrize('selection,no_hooks',[('',False),('grok',True)])
def test_plain_wizard_forwards_no_service_and_exact_selection(unregistered,monkeypatch,selection,no_hooks):
    from types import SimpleNamespace
    import setup_device

    h=unregistered
    monkeypatch.setattr(setup_device,'REPO',h.release)
    namespace={'verify':lambda path:{'version':'0.2.16','sha256':{}}}
    monkeypatch.setattr(setup_device.runpy,'run_path',lambda path:namespace)
    monkeypatch.setattr(setup_device,'pending_install',lambda root,version:None)
    calls=[]
    def run(command,*,capture=False):
        arguments=list(map(str,command));calls.append(arguments)
        if arguments[3].endswith('/update.py'):
            device=dict(h.device,version='0.2.16')
            (h.root/'device.json').write_text(json.dumps(device))
        return SimpleNamespace(returncode=0,stdout='{"deferred": []}',stderr='')
    monkeypatch.setattr(setup_device,'run',run)
    argv=['setup_device.py','--root',str(h.root),'--clients',selection,'--no-service']
    if no_hooks:argv.append('--no-hooks')
    monkeypatch.setattr(setup_device.sys,'argv',argv)
    with pytest.raises(SystemExit) as result:setup_device.main()
    assert result.value.code==0
    upgrade=calls[0]
    assert upgrade[1:3]==['-I','-B'] and upgrade[upgrade.index('--clients')+1]==selection
    assert '--no-service' in upgrade and ('--no-hooks' in upgrade)==no_hooks
    if selection:assert '--configure-clients' in calls[1] and '--clients-only' in calls[1]
    assert '--no-service' in calls[-1] and calls[-1][3].endswith('/check_setup.py')


def test_mandatory_file_publication_refuses_swapped_parent(unregistered,monkeypatch):
    h=unregistered;helper=h.updater.release_helpers()
    directory=h.root/'bin';launcher=directory/'second-brain-mcp'
    original=helper.file_image(launcher)
    moved=h.root/'owner-moved-bin'
    foreign=h.root/'foreign-bin';foreign.mkdir()
    foreign_file=foreign/launcher.name;foreign_file.write_bytes(b'foreign owner bytes')
    same_parent=helper._same_parent
    def swap(path,parent):
        if not moved.exists():
            directory.rename(moved)
            directory.symlink_to(foreign,target_is_directory=True)
        return same_parent(path,parent)
    monkeypatch.setattr(helper,'_same_parent',swap)
    with pytest.raises((OSError,ValueError)):
        helper.write_image(launcher,(b'new candidate launcher',0o700),original)
    assert foreign_file.read_bytes()==b'foreign owner bytes'
    assert (moved/launcher.name).read_bytes()==original[0]
    assert sorted(item.name for item in moved.iterdir())==[launcher.name]
