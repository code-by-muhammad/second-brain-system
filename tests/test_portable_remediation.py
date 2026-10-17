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
    return load('install'),load('update'),root,vault,home


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
    main_args(monkeypatch,install,root,vault,'--configure-clients')
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


def prepare_update(portable):
    install,updater,root,vault,home=portable
    install.deploy(root,Path(sys.executable),vault)
    device=json.loads((root/'device.json').read_text()); device.update(version='0.1.0',release_root=str(root/'old-release'))
    install.atomic(root/'device.json',json.dumps(device)+'\n')
    release=root/'releases/v0.1.6'; release.mkdir(parents=True)
    # The command dispatcher uses the real installer, intercepting OS lifecycle only.
    for path in ('install.py','docs/AI-PROTOCOL.md'):
        target=release/path; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((install.REPO/path).read_bytes())
    (release/'app').mkdir()
    (release/'app/brain_cli.py').write_text('# new app\n')
    return device,release


def test_update_preflight_failure_leaves_old_service_and_bytes(portable,monkeypatch):
    install,updater,root,vault,home=portable
    prepare_update(portable)
    invalid=home/'.claude/settings.json'; invalid.parent.mkdir(parents=True); invalid.write_text('{bad json')
    original=(root/'device.json').read_bytes(); calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if '--validate-only' in args:
            # Use the real selected Python and installer with a synthetic HOME.
            monkeypatch.setattr(install,'REPO',root/'releases/v0.1.6')
            main_args(monkeypatch,install,root,vault,'--configure-clients','--validate-only')
            install.main()
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(updater.subprocess,'run',run)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.6'})
    with pytest.raises(ValueError): updater.perform(root,'v0.1.6')
    assert (root/'device.json').read_bytes()==original
    assert not any(c[-2:]==['service','stop'] for c in calls)


def test_failed_real_install_switch_restores_exact_clients_and_app(portable,monkeypatch):
    install,updater,root,vault,home=portable
    device,release=prepare_update(portable)
    owner=home/'.claude.json'; owner.write_bytes(b'{"owner":"keep"}\r\n')
    old_device=(root/'device.json').read_bytes()
    old_app={p.name:p.read_bytes() for p in (root/'app').glob('*.py')}
    token=(root/'state/client-access.json').read_bytes()
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if str(release/'install.py') in args:
            monkeypatch.setattr(install,'REPO',release)
            extra=['--configure-clients']+(['--validate-only'] if '--validate-only' in args else [])
            main_args(monkeypatch,install,root,vault,*extra)
            install.main()
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(updater.subprocess,'run',run)
    # Avoid recursively mocking the Windows ACL helper through subprocess.run.
    if os.name=='nt': monkeypatch.setattr(install,'private',lambda *args:None)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.6'})
    health=[]
    def wait(python,path,version):
        health.append(version)
        if version=='0.1.6': raise RuntimeError('new readiness failed')
        return {'status':'running','version':version}
    monkeypatch.setattr(updater,'wait_health',wait)
    with pytest.raises(RuntimeError,match='new readiness failed'): updater.perform(root,'v0.1.6')
    assert (root/'device.json').read_bytes()==old_device
    assert {p.name:p.read_bytes() for p in (root/'app').glob('*.py')}==old_app
    assert owner.read_bytes()==b'{"owner":"keep"}\r\n'
    assert not (home/'.codex/config.toml').exists()
    assert not (home/'.claude/settings.json').exists()
    assert (root/'state/client-access.json').read_bytes()==token
    assert health==['0.1.6','0.1.0']
    assert not any('old-release' in str(c) and 'install.py' in str(c) for c in calls)
    stops=[i for i,c in enumerate(calls) if c[-2:]==['service','stop']]
    registration=next(i for i,c in enumerate(calls) if c[-2:]==['service','install'])
    assert len(stops)==2 and stops[-1]<registration


def test_busy_worker_prevents_service_stop(portable,monkeypatch):
    _,updater,root,_,_=portable
    prepare_update(portable)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.6'})
    calls=[]
    monkeypatch.setattr(updater.subprocess,'run',lambda args,**kw:(calls.append(args) or SimpleNamespace(returncode=0,stdout='')))
    with FileLock(str(root/'state/wiki-worker.lock')):
        with pytest.raises(Timeout): updater.perform(root,'v0.1.6')
    assert not any(c[-2:]==['service','stop'] for c in calls)


def test_concurrent_updater_is_rejected(portable):
    _,updater,root,_,_=portable
    with updater.update_lock(root):
        with pytest.raises(RuntimeError,match='already running'):
            with updater.update_lock(root): pytest.fail('lock admitted a competing updater')


def test_migration_flags_reject_capture_device_and_missing_parent_flag(portable,monkeypatch):
    _,updater,root,_,_=portable
    prepare_update(portable)
    with pytest.raises(PermissionError,match='designated maintainer'):
        updater.perform(root,'v0.1.6',migrate_wiki=True)
    monkeypatch.setattr(updater.sys,'argv',['update.py','--root',str(root),'--version','v0.1.6','--requeue-unverified'])
    with pytest.raises(SystemExit) as error: updater.main()
    assert error.value.code==2


@pytest.mark.parametrize('fail_relink',[False,True])
def test_migration_runs_under_worker_lock_and_does_not_rollback_notes_or_ledger(portable,monkeypatch,capsys,fail_relink):
    install,updater,root,vault,_=portable
    _,release=prepare_update(portable)
    device=json.loads((root/'device.json').read_text()); device['role']='maintainer'
    install.atomic(root/'device.json',json.dumps(device)+'\n')
    original=(root/'device.json').read_bytes()
    token=(root/'state/client-access.json').read_bytes()
    marker=vault/'verified-migration-receipt.md'; ledger=root/'state/migration-test-ledger.json'
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if str(release/'install.py') in args:
            monkeypatch.setattr(install,'REPO',release)
            main_args(monkeypatch,install,root,vault,'--configure-clients',
                      *(['--validate-only'] if '--validate-only' in args else []))
            install.main()
        if 'wiki' in args:
            # The migration must exclude the compiler, while releasing the locks
            # its own CLI needs. No run/model command is permitted in this path.
            with pytest.raises(Timeout):
                with FileLock(str(root/'state/wiki-worker.lock'),timeout=0): pass
            with FileLock(str(root/'state/writer.lock'),timeout=0): pass
            with FileLock(str(root/'state/maintenance.lock'),timeout=0): pass
            assert kwargs['env']['BRAIN_SYSTEM_ROOT']==str(root)
            assert kwargs['env']['BRAIN_STATE']==str(root/'state')
            assert kwargs['env']['BRAIN_VAULT']==str(vault)
            action=args[args.index('wiki')+1]
            if action=='migrate':
                assert args[-2:]==['--apply','--requeue-unverified']
                marker.write_text('verified derived receipt')
                ledger.write_text('persistent local migration state')
                result={'dry_run':False,'verified_parts':3,'unverified_parts':[{'path':'source.md'}],
                        'requeued_parts':1,'complete_journals_verified':1,'rejected_batches':[],
                        'receipts_created':[{'path':marker.name,'sha256':'synthetic-proof-hash'}]}
            elif action=='relink':
                if fail_relink: raise RuntimeError('synthetic relink failure')
                result={'source_records_updated':2,'receipts':[]}
            else: pytest.fail('Updater attempted a model/compiler command: '+action)
            return SimpleNamespace(returncode=0,stdout=json.dumps(result))
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(updater.subprocess,'run',run)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.6'})
    monkeypatch.setattr(updater,'wait_health',lambda python,path,version:{'status':'running','version':version})
    if fail_relink:
        with pytest.raises(RuntimeError,match='synthetic relink failure'):
            updater.perform(root,'v0.1.6',migrate_wiki=True,requeue_unverified=True)
        assert (root/'device.json').read_bytes()==original
    else:
        updater.perform(root,'v0.1.6',migrate_wiki=True,requeue_unverified=True)
        result=json.loads(capsys.readouterr().out.splitlines()[-1])
        assert result['wiki_migration']['verified_parts']==3
        assert result['wiki_migration']['unverified_parts']==1
        receipt=json.loads(Path(result['wiki_migration']['receipt']).read_text())
        assert receipt['summary']['source_records_updated']==2
    assert marker.read_text()=='verified derived receipt'
    assert ledger.read_text()=='persistent local migration state'
    assert (root/'state/client-access.json').read_bytes()==token
    with FileLock(str(root/'state/wiki-worker.lock'),timeout=0): pass
    assert [c[c.index('wiki')+1] for c in calls if 'wiki' in c]==['migrate','relink']


def test_partial_old_service_stop_failure_recovers_previous_service(portable,monkeypatch):
    _,updater,root,_,_=portable
    device,_=prepare_update(portable)
    original=(root/'device.json').read_bytes()
    native={'running':True,'partial_failure_raised':False}
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if args[-2:]==['service','stop']:
            native['running']=False
            if not native['partial_failure_raised']:
                native['partial_failure_raised']=True
                raise subprocess.CalledProcessError(1,args,stderr='native job stopped, remaining PID cleanup failed')
        elif args[-2:]==['service','start']:
            native['running']=True
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(updater.subprocess,'run',run)
    monkeypatch.setattr(updater,'verify',lambda path,**kwargs:{'version':'0.1.6'})
    health=[]
    def wait(python,path,version):
        assert native['running']
        health.append(version)
        return {'status':'running','version':version}
    monkeypatch.setattr(updater,'wait_health',wait)
    with pytest.raises(subprocess.CalledProcessError): updater.perform(root,'v0.1.6')
    assert native['running'], 'A partially stopped old service must be recovered before reporting update failure.'
    assert (root/'device.json').read_bytes()==original
    assert health==['0.1.0']
    assert any(c[-2:]==['service','install'] and c[0]==device['python'] for c in calls)
