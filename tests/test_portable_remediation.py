"""Real installer entry points and rollback fixtures; never owner clients/services."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest
from filelock import FileLock, Timeout


def load(name):
    spec=importlib.util.spec_from_file_location('portable_'+name,Path(__file__).resolve().parents[1]/(name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def portable(tmp_path,monkeypatch):
    home=tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setenv('APPDATA',str(home/'AppData/Roaming'))
    root=tmp_path/'runtime'; vault=tmp_path/'vault'; vault.mkdir()
    install=load('install');updater=load('update')
    support=install.setup_helpers()
    monkeypatch.setattr(support,'desktop_busy',lambda:False)
    monkeypatch.setattr(install,'setup_helpers',lambda:support)
    return install,updater,root,vault,home


def main_args(monkeypatch,module,root,vault,*extra,python=None):
    monkeypatch.setattr(module.sys,'argv',['install.py','--root',str(root),'--vault',str(vault),
                                         '--reuse-python',str(python or sys.executable),*extra])


def test_main_rejects_vault_move_without_mutation(portable,monkeypatch,tmp_path):
    install,_,root,vault,_=portable
    main_args(monkeypatch,install,root,vault); install.main()
    before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    other=tmp_path/'another-vault'; other.mkdir()
    main_args(monkeypatch,install,root,other)
    with pytest.raises(ValueError,match='Changing --vault'): install.main()
    assert before=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_stale_index_path_rejected_before_any_new_device(portable):
    install,_,root,vault,_=portable
    (root/'state').mkdir(parents=True)
    config=root/'state/config.json'
    config.write_text(json.dumps({'projects':{'brain':{'path':str(vault/'old')}}}))
    with pytest.raises(ValueError,match='index targets another vault'):
        install.deploy(root,Path(sys.executable),vault)
    assert not (root/'device.json').exists()


def test_validate_only_is_read_only_for_fresh_install(portable,monkeypatch):
    install,_,root,vault,home=portable
    main_args(monkeypatch,install,root,vault,'--configure-clients','--validate-only')
    install.main()
    assert not root.exists() and list(home.iterdir())==[]


@pytest.mark.parametrize('filename,content',[
    ('.claude.json','{"mcpServers":null}'),
    ('.codex/config.toml','model = [invalid'),
    ('.claude/settings.json','{"hooks":{"Stop":null}}'),
    ('.codex/AGENTS.md','<!-- BEGIN SECOND-BRAIN -->\nowner text'),
])
def test_bad_client_preflight_changes_nothing(portable,monkeypatch,filename,content):
    install,_,root,vault,home=portable
    invalid=home/filename; invalid.parent.mkdir(parents=True,exist_ok=True); invalid.write_text(content)
    main_args(monkeypatch,install,root,vault,'--configure-clients','--include-hooks')
    with pytest.raises((ValueError,TypeError)): install.main()
    assert not root.exists() and invalid.read_text()==content
    assert [p for p in home.rglob('*') if p.is_file()]==[invalid]


@pytest.mark.skipif(os.name=='nt',reason='POSIX venv symlink and mode semantics')
def test_real_main_keeps_venv_interpreter_and_venv_only_import(portable,monkeypatch,tmp_path):
    install,_,root,vault,_=portable
    environment=tmp_path/'isolated-venv'
    venv.EnvBuilder(with_pip=False,symlinks=True).create(environment)
    python=environment/'bin/python'
    assert python.is_symlink()
    purelib=Path(subprocess.check_output([str(python),'-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
    (purelib/'second_brain_venv_marker.py').write_text('VALUE="venv-only"\n')
    main_args(monkeypatch,install,root,vault,'--configure-clients',python=python)
    install.main()
    recorded=Path(json.loads((root/'device.json').read_text())['python'])
    assert recorded==python.absolute() and recorded!=python.resolve()
    probe=subprocess.run([str(recorded),'-c','import second_brain_venv_marker; print(second_brain_venv_marker.VALUE)'],capture_output=True,text=True)
    assert probe.returncode==0 and probe.stdout.strip()=='venv-only'
    base=subprocess.run([str(python.resolve()),'-c','import second_brain_venv_marker'],capture_output=True,text=True)
    assert base.returncode!=0
    claude=json.loads((portable[-1]/'.claude.json').read_text())
    assert claude['mcpServers']['second-brain']['command']==str(python)


@pytest.mark.skipif(os.name=='nt',reason='POSIX file permission bits')
def test_atomic_keeps_mode_and_first_byte_private(portable,monkeypatch,tmp_path):
    install,_,_,_,_=portable
    target=tmp_path/'config.json'; target.write_text('old'); target.chmod(0o600)
    real_replace=install.os.replace
    real_fdopen=install.os.fdopen
    observed=[]; before_write=[]
    def fdopen(fd,*args,**kwargs):
        before_write.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd,*args,**kwargs)
    def replace(source,destination):
        observed.append((stat.S_IMODE(Path(source).stat().st_mode),Path(source).read_text()))
        real_replace(source,destination)
    monkeypatch.setattr(install.os,'replace',replace)
    monkeypatch.setattr(install.os,'fdopen',fdopen)
    previous=os.umask(0o022)
    try:
        install.atomic(target,'new-private')
        install.atomic(tmp_path/'new-token.json','new-token')
    finally: os.umask(previous)
    assert observed==[(0o600,'new-private'),(0o600,'new-token')]
    assert before_write==[0o600,0o600]
    assert stat.S_IMODE(target.stat().st_mode)==0o600
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(os.name!='nt',reason='Windows DACL setup ordering')
def test_windows_acl_applied_to_empty_temp_before_sensitive_bytes(portable,monkeypatch,tmp_path):
    install,_,_,_,_=portable
    real_private=install.private; seen=[]
    def private(path,directory=False):
        if not directory:
            seen.append(Path(path).read_bytes())
        return real_private(path,directory)
    monkeypatch.setattr(install,'private',private)
    install.atomic(tmp_path/'token.json','private-token')
    assert seen==[b'']
    result=subprocess.run(['icacls.exe',str(tmp_path/'token.json')],capture_output=True,text=True,check=True)
    assert '(I)' not in result.stdout


@pytest.mark.skipif(os.name=='nt',reason='POSIX directory permission bits')
def test_state_and_backups_private_before_token_write(portable,monkeypatch):
    install,_,root,vault,_=portable
    original=install.atomic; checked=[]
    def atomic(path,text):
        if Path(path).name in ('client-access.json','access.json'):
            checked.append(stat.S_IMODE((root/'state').stat().st_mode))
        return original(path,text)
    monkeypatch.setattr(install,'atomic',atomic)
    install.deploy(root,Path(sys.executable),vault)
    assert checked==[0o700,0o700]
    assert stat.S_IMODE((root/'backups').stat().st_mode)==0o700
    assert stat.S_IMODE((root/'state/client-access.json').stat().st_mode)==0o600


def prepare_update(portable,monkeypatch):
    from upgrade_fixture import prepare
    return prepare(portable,monkeypatch)


def test_update_preflight_failure_leaves_old_service_and_bytes(portable,monkeypatch):
    h=prepare_update(portable,monkeypatch)
    invalid=h.home/'.claude/settings.json';invalid.parent.mkdir(parents=True)
    invalid.write_text('{"runtime":'+json.dumps(str(h.root))+', invalid')
    original=(h.root/'device.json').read_bytes()
    with pytest.raises(ValueError):h.updater.perform(h.root,'v0.1.6')
    assert (h.root/'device.json').read_bytes()==original
    assert not any(row.get('action')=='stop' for row in h.events)
    assert h.running


def test_failed_real_install_switch_restores_exact_clients_and_app(portable,monkeypatch):
    h=prepare_update(portable,monkeypatch)
    owner=h.home/'.claude.json'
    original_owner=(json.dumps({'owner':'keep','mcpServers':{'second-brain':{'command':'old-python','args':[str(h.root/'app/brain_client.py')],'env':{'OWNER':'fixture'}}}})+'\r\n').encode()
    owner.write_bytes(original_owner)
    old_device=(h.root/'device.json').read_bytes()
    old_app={p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}
    token=(h.root/'state/client-access.json').read_bytes();h.fail_health=True
    with pytest.raises(RuntimeError,match='previous runtime health verified') as failure:h.updater.perform(h.root,'v0.1.6')
    assert str(failure.value.__cause__)=='new readiness failed'
    assert (h.root/'device.json').read_bytes()==old_device
    assert {p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}==old_app
    assert owner.read_bytes()==original_owner
    assert not (h.home/'.codex/config.toml').exists() and not (h.home/'.claude/settings.json').exists()
    assert (h.root/'state/client-access.json').read_bytes()==token
    assert h.health==['0.1.6','0.1.0']
    control=[row for row in h.events if row['kind']=='controller']
    assert all(row['checkout']==str(h.release) for row in control)
    assert [row['action'] for row in control]==['inspect','hold','stop','start','stop','install','start','finish']


def test_busy_writer_refusal_cancels_hold_without_runtime_switch(portable,monkeypatch):
    h=prepare_update(portable,monkeypatch)
    original=(h.root/'device.json').read_bytes()
    with FileLock(str(h.root/'state/wiki-worker.lock')):
        with pytest.raises(RuntimeError,match='deferred before runtime switch'):h.updater.perform(h.root,'v0.1.6')
    assert h.running and not h.last_end and (h.root/'device.json').read_bytes()==original
    assert not (h.root/'state/service-transaction.json').exists()
    assert not any(row.get('action') in ('start','install') for row in h.events)


def test_concurrent_updater_is_rejected(portable):
    _,updater,root,_,_=portable
    with updater.update_lock(root):
        with pytest.raises(RuntimeError,match='already running'):
            with updater.update_lock(root):pytest.fail('lock admitted a competing updater')


def test_migration_flags_reject_capture_device_and_missing_parent_flag(portable,monkeypatch):
    h=prepare_update(portable,monkeypatch)
    with pytest.raises(PermissionError,match='designated maintainer'):h.updater.perform(h.root,'v0.1.6',migrate_wiki=True)
    monkeypatch.setattr(sys,'argv',['update.py','--root',str(h.root),'--version','v0.1.6','--requeue-unverified'])
    with pytest.raises(SystemExit) as error:h.updater.main()
    assert error.value.code==2


@pytest.mark.parametrize('fail_relink',[False,True])
def test_migration_runs_under_worker_lock_and_does_not_rollback_notes_or_ledger(portable,monkeypatch,fail_relink):
    h=prepare_update(portable,monkeypatch)
    device=json.loads((h.root/'device.json').read_text());device['role']='maintainer'
    h.install.atomic(h.root/'device.json',json.dumps(device)+'\n')
    original=(h.root/'device.json').read_bytes();token=(h.root/'state/client-access.json').read_bytes()
    marker=h.vault/'verified-migration-receipt.md';ledger=h.root/'state/migration-test-ledger.json';actions=[]
    def wiki(action,flags):
        actions.append(action)
        with pytest.raises(Timeout):
            with FileLock(str(h.root/'state/wiki-worker.lock'),timeout=0):pass
        with FileLock(str(h.root/'state/writer.lock'),timeout=0):pass
        with FileLock(str(h.root/'state/maintenance.lock'),timeout=0):pass
        env=h.updater.cli_environment(h.root)
        assert env['BRAIN_SYSTEM_ROOT']==str(h.root) and env['BRAIN_VAULT']==str(h.vault)
        if action=='migrate':
            assert flags==('--apply','--requeue-unverified')
            marker.write_text('verified derived receipt');ledger.write_text('persistent local migration state')
            return {'dry_run':False,'verified_parts':3,'unverified_parts':[{'path':'source.md'}],'requeued_parts':1,
                    'complete_journals_verified':1,'rejected_batches':[],'receipts_created':[{'path':marker.name,'sha256':'synthetic-proof-hash'}]}
        if action=='relink':
            if fail_relink:raise RuntimeError('synthetic relink failure')
            return {'source_records_updated':2,'receipts':[]}
        pytest.fail('Updater attempted a model/compiler action: '+action)
    h.on_wiki=wiki
    if fail_relink:
        with pytest.raises(RuntimeError,match='previous runtime health verified') as failure:
            h.updater.perform(h.root,'v0.1.6',migrate_wiki=True,requeue_unverified=True)
        assert str(failure.value.__cause__)=='synthetic relink failure'
        assert (h.root/'device.json').read_bytes()==original
    else:
        result=h.updater.perform(h.root,'v0.1.6',migrate_wiki=True,requeue_unverified=True)
        assert result['wiki_migration']['verified_parts']==3 and result['wiki_migration']['unverified_parts']==1
        receipt=json.loads(Path(result['wiki_migration']['receipt']).read_text())
        assert receipt['summary']['source_records_updated']==2
    assert marker.read_text()=='verified derived receipt' and ledger.read_text()=='persistent local migration state'
    assert (h.root/'state/client-access.json').read_bytes()==token and actions==['migrate','relink']
    with FileLock(str(h.root/'state/wiki-worker.lock'),timeout=0):pass


def test_unverified_partial_stop_preserves_recovery_hold_without_restarting(portable,monkeypatch):
    h=prepare_update(portable,monkeypatch);original=(h.root/'device.json').read_bytes();h.partial_stop=True
    with pytest.raises(RuntimeError,match='synthetic partial native stop'):h.updater.perform(h.root,'v0.1.6')
    assert not h.running and (h.root/'device.json').read_bytes()==original and h.health==[]
    assert (h.root/'state/service-transaction.json').exists()
    assert not any(row.get('action') in ('start','install') for row in h.events)
    receipt=json.loads((h.root/'state/upgrade-status.json').read_text())
    assert receipt['status']=='recovery required' and receipt['pending_work']=='preserved'
