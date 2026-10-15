"""Install an explicit release from the owner's private repository; no tracking of main."""
import argparse, contextlib, hashlib, importlib.util, json, os, re, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

REMOTE='https://github.com/code-by-muhammad/second-brain-system.git'

def installer():
    spec=importlib.util.spec_from_file_location('_brain_update_installer',Path(__file__).with_name('install.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

@contextlib.contextmanager
def update_lock(root):
    helpers=installer(); helpers.private_dir(root/'state')
    from filelock import FileLock, Timeout
    lock=FileLock(str(root/'state/update.lock'),timeout=1,mode=0o600)
    try:
        try: lock.acquire()
        except Timeout as exc: raise RuntimeError('Another release update is already running.') from exc
        yield
    finally: lock.release()  # OS lock is also released after process failure.

def snapshot(root,python):
    """Snapshot runtime code and client settings only; never live state or notes."""
    helpers=installer(); helpers.private_dir(root/'backups')
    target=Path(tempfile.mkdtemp(prefix='upgrade-',dir=root/'backups'))
    helpers.private_dir(target)
    installed=root/'app'
    if installed.exists(): shutil.copytree(installed,target/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    paths=[root/'device.json']+[path for path,_ in helpers.client_plan(root,python)]
    records=[]
    for i,path in enumerate(paths):
        entry={'path':str(path),'present':path.exists()}
        if entry['present']:
            entry['mode']=stat.S_IMODE(path.stat().st_mode)
            helpers.atomic(target/str(i),path.read_bytes())
        records.append(entry)
    helpers.atomic(target/'snapshot.json',json.dumps(records,indent=2)+'\n')
    return target

def restore(root,target):
    helpers=installer()
    saved=target/'app'
    if saved.exists():
        # The path is fixed beneath the explicit runtime, never the vault or state.
        app=root/'app'
        if app.is_symlink() or not app.resolve().is_relative_to(root.resolve()) or app.resolve()==root.resolve():
            raise ValueError('Unsafe runtime app restore path.')
        if app.exists(): shutil.rmtree(app)
        shutil.copytree(saved,app)
    for i,entry in enumerate(json.loads((target/'snapshot.json').read_text(encoding='utf-8'))):
        path=Path(entry['path'])
        if entry['present']:
            helpers.atomic(path,(target/str(i)).read_bytes())
            if os.name!='nt': os.chmod(path,entry['mode'])
        else: path.unlink(missing_ok=True)

def verify(checkout):
    manifest=json.loads((checkout/'release-manifest.json').read_text(encoding='utf-8'))
    required={'install.py','update.py','pyproject.toml','uv.lock'} | {p.relative_to(checkout).as_posix() for p in (checkout/'app').glob('*.py')}
    if not required <= set(manifest['sha256']): raise ValueError('Release manifest omits executable or dependency files.')
    for relative,expected in manifest['sha256'].items():
        target=(checkout/relative).resolve()
        if not target.is_relative_to(checkout.resolve()): raise ValueError('Unsafe manifest path.')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('Release integrity mismatch: '+relative)
    return manifest

def cli_environment():
    """Match the UTF-8 JSON pipe decoder, including when using a rollback CLI."""
    env=os.environ.copy()
    env['PYTHONIOENCODING']='utf-8'
    return env

def wait_health(python,root,version,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result=subprocess.run([str(python),str(root/'app/brain_cli.py'),'health'],env=cli_environment(),capture_output=True,text=True,encoding='utf-8',timeout=30)
            if result.returncode==0:
                data=json.loads(result.stdout)
                if data.get('status')=='running' and data.get('version')==version: return data
        except (ValueError,subprocess.TimeoutExpired): pass
        time.sleep(2)
    raise RuntimeError('MCP did not report the expected release before the startup deadline.')

def wiki_command(python,root,vault,action,*flags):
    """Explicit local receipt maintenance; no compiler/model call is made here."""
    env=cli_environment()
    env.update(BRAIN_SYSTEM_ROOT=str(root),BRAIN_STATE=str(root/'state'),BRAIN_VAULT=str(vault))
    result=subprocess.run([str(python),str(root/'app/brain_cli.py'),'wiki',action,*flags],
                          env=env,capture_output=True,text=True,encoding='utf-8',timeout=600,check=True)
    receipt=json.loads(result.stdout)
    if not isinstance(receipt,dict): raise ValueError('Invalid wiki '+action+' receipt.')
    return receipt

def apply_wiki_migration(python,root,vault,requeue_unverified=False):
    flags=['--apply']+(['--requeue-unverified'] if requeue_unverified else [])
    migration=wiki_command(python,root,vault,'migrate',*flags)
    if migration.get('dry_run') is not False: raise ValueError('Wiki migration did not return an applied receipt.')
    relink=wiki_command(python,root,vault,'relink')
    result={'status':'applied','verified_parts':migration['verified_parts'],
            'unverified_parts':len(migration['unverified_parts']),'requeued_parts':migration['requeued_parts'],
            'complete_journals_verified':migration['complete_journals_verified'],
            'rejected_batches':len(migration['rejected_batches']),
            'source_records_updated':relink['source_records_updated']}
    path=root/'state/upgrade-wiki-migration.json'
    installer().atomic(path,json.dumps({'summary':result,'receipts_created':migration['receipts_created'],
                                       'source_link_receipts':relink['receipts']},indent=2)+'\n')
    return {**result,'receipt':str(path)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True); parser.add_argument('--version',required=True)
    parser.add_argument('--migrate-wiki',action='store_true',help='Maintainer only: verify legacy wiki receipts and repair source links during this upgrade.')
    parser.add_argument('--requeue-unverified',action='store_true',help='With --migrate-wiki, keep unproved legacy source parts pending.')
    args=parser.parse_args(); root=Path(args.root).expanduser().resolve()
    if not re.fullmatch(r'v\d+\.\d+\.\d+',args.version): parser.error('Select an explicit version such as v0.1.0.')
    if args.requeue_unverified and not args.migrate_wiki: parser.error('--requeue-unverified requires --migrate-wiki.')
    with update_lock(root): perform(root,args.version,args.migrate_wiki,args.requeue_unverified)

def perform(root,version,migrate_wiki=False,requeue_unverified=False):
    if requeue_unverified and not migrate_wiki: raise ValueError('--requeue-unverified requires --migrate-wiki.')
    device=json.loads((root/'device.json').read_text(encoding='utf-8'))
    if migrate_wiki and device.get('role')!='maintainer': raise PermissionError('--migrate-wiki requires the designated maintainer device.')
    release=root/'releases'/version
    if not release.exists(): subprocess.run(['git','clone','--depth','1','--branch',version,'--',REMOTE,str(release)],check=True)
    info=verify(release)
    if info['version']!=version.removeprefix('v'): raise ValueError('Release version does not match requested tag.')
    subprocess.run(['uv','sync','--locked','--no-dev','--project',str(release),'--python','3.12'],check=True)
    python=release/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    # Bad owner config must fail while the old service is still healthy, and the
    # replacement installer must agree before any runtime or client switch.
    subprocess.run([str(python),str(release/'install.py'),'--root',str(root),'--vault',device['vault'],
                    '--reuse-python',str(python),'--configure-clients','--validate-only'],check=True)
    previous={'release_root':device['release_root'],'python':device['python'],'version':device['version']}
    from filelock import FileLock
    # The order matches the worker/maintenance paths; fail before stopping if busy.
    with FileLock(str(root/'state/wiki-worker.lock'),timeout=1):
        stopped=False; migration=None
        try:
            with FileLock(str(root/'state/maintenance.lock'),timeout=1), FileLock(str(root/'state/writer.lock'),timeout=1):
                saved=snapshot(root,python)
                # Native stop can succeed before child cleanup reports failure;
                # once attempted, recovery must restore/start the former service.
                stopped=True
                subprocess.run([device['python'],str(root/'app/brain_cli.py'),'service','stop'],check=True)
                installer().atomic(root/'state/previous-release.json',json.dumps({**previous,'snapshot':str(saved)},indent=2)+'\n')
                subprocess.run([str(python),str(release/'install.py'),'--root',str(root),'--vault',device['vault'],'--reuse-python',str(python),'--configure-clients','--install-service'],check=True)
            if migrate_wiki: migration=apply_wiki_migration(python,root,device['vault'],requeue_unverified)
            subprocess.run([str(python),str(root/'app/brain_cli.py'),'service','start'],check=True)
            health=wait_health(python,root,info['version'])
        except Exception:
            if not stopped: raise
            # Stop first, then restore bytes and registration. Executing the old
            # installer would reintroduce its symlink bug or reparse broken config.
            subprocess.run([str(python),str(root/'app/brain_cli.py'),'service','stop'],check=False)
            with FileLock(str(root/'state/maintenance.lock'),timeout=30), FileLock(str(root/'state/writer.lock'),timeout=30):
                restore(root,saved)
                subprocess.run([previous['python'],str(root/'app/brain_cli.py'),'service','install'],check=True)
            subprocess.run([previous['python'],str(root/'app/brain_cli.py'),'service','start'],check=True)
            wait_health(previous['python'],root,previous['version'])
            raise
    print(json.dumps({'status':'updated','version':info['version'],'previous':previous,'health':health,'wiki_migration':migration}))

if __name__=='__main__': main()
