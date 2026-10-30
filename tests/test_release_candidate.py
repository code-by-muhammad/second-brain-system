"""Qualified-platform refusals and staged release consistency, no services."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

SOURCE=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location('candidate_'+name,SOURCE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


@pytest.mark.parametrize('platform',['darwin','linux'])
@pytest.mark.parametrize('action',['update','recover','direct-perform','direct-recover'])
def test_unqualified_update_refuses_before_helpers_state_or_recovery_reads(tmp_path,monkeypatch,platform,action):
    updater=module('update');root=tmp_path/'absent-runtime'
    monkeypatch.setattr(updater.sys,'platform',platform)
    monkeypatch.setattr(updater,'installer',lambda:pytest.fail('Platform refusal must precede helper loading.'))
    monkeypatch.setattr(updater,'_recovery_binding',lambda *args:pytest.fail('Platform refusal must precede recovery reads.'))
    with pytest.raises(ValueError,match='qualified for Windows only'):
        if action=='direct-perform':updater.perform(root,'v0.1.8')
        elif action=='direct-recover':updater.recover(root,'a'*32)
        else:
            flags=['--version','v0.1.8'] if action=='update' else ['--recover','--hold-id','a'*32]
            monkeypatch.setattr(sys,'argv',['update.py','--root',str(root),*flags]);updater.main()
    assert not root.exists()


@pytest.mark.parametrize('platform',['darwin','linux'])
@pytest.mark.parametrize('plan',[False,True])
def test_unqualified_setup_refuses_before_helpers_or_any_plan_write(tmp_path,monkeypatch,platform,plan):
    installer=module('install');root=tmp_path/'absent-runtime';vault=tmp_path/'vault';vault.mkdir()
    monkeypatch.setattr(installer.sys,'platform',platform)
    monkeypatch.setattr(installer,'setup_helpers',lambda:pytest.fail('Platform refusal must precede helper loading.'))
    flags=['--configure-clients','--validate-only','--plan-out',str(tmp_path/'plan.json')] if plan else []
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),*flags])
    with pytest.raises(ValueError,match='qualified for Windows only'):installer.main()
    assert not root.exists() and not (tmp_path/'plan.json').exists()


@pytest.mark.parametrize('platform',['win32','darwin','linux'])
def test_old_configure_updater_is_refused_even_at_read_only_preflight(tmp_path,monkeypatch,platform):
    installer=module('install');root=tmp_path/'runtime';root.mkdir();vault=tmp_path/'vault';vault.mkdir()
    monkeypatch.setattr(installer.sys,'platform',platform)
    device=root/'device.json';device.write_text(json.dumps({'device_id':'fixture','vault':str(vault),'version':'0.1.7'}));before=device.read_bytes()
    support=installer.setup_helpers();monkeypatch.setattr(support,'host_context',lambda:None)
    monkeypatch.setattr(support,'provision',lambda *args:pytest.fail('Legacy preflight reached provisioning.'))
    monkeypatch.setattr(installer,'setup_helpers',lambda:support)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',sys.executable,'--configure-clients','--validate-only'])
    with pytest.raises(ValueError,match='legacy configure-client preflight'):installer.main()
    assert device.read_bytes()==before and list(root.iterdir())==[device]


def test_wrong_forward_checkout_refuses_before_helpers_or_state(tmp_path,monkeypatch):
    updater=module('update');root=tmp_path/'absent-runtime'
    monkeypatch.setattr(updater,'installer',lambda:pytest.fail('Wrong checkout loaded helpers.'))
    monkeypatch.setattr(sys,'argv',['update.py','--root',str(root),'--version','v0.1.8'])
    with pytest.raises(ValueError,match='verified forward updater'):updater.main()
    with pytest.raises(ValueError,match='verified forward updater'):updater.perform(root,'v0.1.8')
    assert not root.exists()


def test_recovery_wrong_running_checkout_refuses_before_sibling_helpers(tmp_path,monkeypatch):
    updater=module('update');root=tmp_path/'runtime';(root/'state').mkdir(parents=True)
    saved=root/'backups/snapshot';saved.mkdir(parents=True);hold='b'*32
    value={'controller_checkout':str(root/'releases/v0.1.8'),'controller_identity':{'tag':'v0.1.8'}}
    raw=json.dumps(value).encode();(saved/'snapshot.json').write_bytes(raw)
    transaction={'root':str(root),'hold_id':hold,'snapshot':str(saved),'snapshot_sha256':hashlib.sha256(raw).hexdigest()}
    path=root/'state/service-transaction.json';path.write_text(json.dumps(transaction));before=path.read_bytes()
    monkeypatch.setattr(updater,'installer',lambda:pytest.fail('Wrong recovery checkout loaded sibling helpers.'))
    with pytest.raises(ValueError,match='original verified forward controller'):updater.recover(root,hold)
    assert path.read_bytes()==before and (saved/'snapshot.json').read_bytes()==raw


@pytest.mark.parametrize('platform',['darwin','linux'])
def test_posix_read_only_planning_remains_no_write_without_platform_acceptance(tmp_path,monkeypatch,platform):
    installer=module('install');root=tmp_path/'absent-runtime';vault=tmp_path/'vault';vault.mkdir()
    monkeypatch.setattr(installer.sys,'platform',platform)
    monkeypatch.setattr(sys,'argv',['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',sys.executable,'--refresh-clients','--client','codex','--validate-only'])
    home=Path.home();before={path.relative_to(home).as_posix():path.read_bytes() for path in home.rglob('*') if path.is_file()}
    installer.main()
    assert not root.exists() and before=={path.relative_to(home).as_posix():path.read_bytes() for path in home.rglob('*') if path.is_file()}


@pytest.fixture
def staged(tmp_path,monkeypatch):
    repo=tmp_path/'staged-software';repo.mkdir();builder=module('build_release');monkeypatch.setattr(builder,'ROOT',repo)
    payload={'install.py':b"VERSION='0.1.8'\n",'app/brain_settings.py':b"VERSION='0.1.8'\n",
             'pyproject.toml':b'[project]\nversion="0.1.8"\n',
             'uv.lock':b'[[package]]\nname="shared-second-brain"\nversion="0.1.8"\n'}
    for name in ('update.py','setup_runtime.py','app/brain_service.py','app/brain_process.py','app/brain_windows_process.py','check_dependency_audit.py','security-exceptions.json','SECURITY.md'):payload[name]=b'# synthetic release builder contract\n'
    for name,data in payload.items():
        target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    def stage():subprocess.run(['git','-C',str(repo),'add','--all'],check=True,capture_output=True)
    stage();return builder,repo,payload,stage


def test_manifest_capabilities_bind_all_required_staged_bytes(staged):
    builder,repo,payload,_=staged
    # Unstaged changes cannot change the version/hash of the staged snapshot.
    (repo/'install.py').write_bytes(b"VERSION='9.9.9'\n")
    builder.main();value=json.loads((repo/'release-manifest.json').read_text())
    assert value['version']=='0.1.8' and value['qualified_service_platforms']==['win32']
    assert set(value['capabilities'])=={'sealed-client-refresh-v1','owned-service-control-v1'}
    assert value['sha256']=={name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}


@pytest.mark.parametrize('name',['install.py','app/brain_settings.py','uv.lock','pyproject.toml'])
def test_builder_refuses_mismatched_staged_versions(staged,name):
    builder,repo,_,stage=staged
    path=repo/name;path.write_bytes(path.read_bytes().replace(b'0.1.8',b'0.1.7'));stage()
    with pytest.raises(ValueError,match='versions must agree'):builder.main()
    assert not (repo/'release-manifest.json').exists()


@pytest.mark.parametrize('name',['setup_runtime.py','check_dependency_audit.py','security-exceptions.json','SECURITY.md'])
def test_builder_refuses_missing_staged_fixed_helper(staged,name):
    builder,repo,_,stage=staged
    (repo/name).unlink();stage()
    with pytest.raises(ValueError,match='required setup'):builder.main()
    assert not (repo/'release-manifest.json').exists()
