"""Install an explicit approved software release; never track main or sync vault Git."""
import argparse, contextlib, hashlib, importlib.util, json, os, re, shutil, stat, secrets, subprocess, sys, tempfile, time
from pathlib import Path

REMOTE='https://github.com/code-by-muhammad/second-brain-system.git'

def _qualified_platform():
    if sys.platform!='win32':
        raise ValueError('v0.1.8 update/recovery is qualified for Windows only. Keep the accepted v0.1.7 on macOS/Linux; no update changes made.')

def _forward_binding(root,version):
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version or ''):raise ValueError('Select an explicit approved release tag.')
    release=(Path(root)/'releases'/version).resolve()
    if Path(__file__).resolve().parent!=release:
        raise ValueError('Run the verified forward updater from exactly '+str(release/'update.py')+' using the retained installed Python outside the target venv.')
    return release

def installer():
    import types
    path=Path(__file__).resolve().with_name('install.py')
    module=types.ModuleType('_brain_update_installer');module.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module

@contextlib.contextmanager
def update_lock(root):
    helpers=installer();helpers.setup_helpers().runtime_paths(root);helpers.private_dir(root/'state')
    from filelock import FileLock, Timeout
    lock=FileLock(str(root/'state/update.lock'),timeout=1,mode=0o600)
    try:
        try: lock.acquire()
        except Timeout as exc: raise RuntimeError('Another release update is already running.') from exc
        yield
    finally: lock.release()  # OS lock is also released after process failure.

def _read(path):return installer().setup_helpers().read(Path(path))


def _digest(data):return None if data is None else hashlib.sha256(data).hexdigest()


def snapshot(root,python,plan=None,*,controller_checkout=None,controller_identity=None):
    """Save exactly the sealed paths before stop, never live index/auth/note state."""
    helpers=installer();support=helpers.setup_helpers()
    plan=plan or {'format':1,'root':str(root),'python':str(python),'files':[]}
    support.validate_plan(plan,root,python);support.current(plan)
    helpers.private_dir(root/'backups')
    target=Path(tempfile.mkdtemp(prefix='upgrade-',dir=root/'backups'));helpers.private_dir(target)
    installed=root/'app'
    app_hashes={}
    if installed.exists():
        if installed.is_symlink() or os.path.isjunction(installed):raise ValueError('Linked installed app cannot be snapshotted.')
        for path in installed.rglob('*'):
            if path.is_symlink() or os.path.isjunction(path):raise ValueError('Linked app payload cannot be snapshotted.')
        device=json.loads(_read(root/'device.json'))
        manifest=json.loads(_read(Path(device['release_root'])/'release-manifest.json'))
        if manifest.get('version')!=device.get('version'):raise ValueError('Previous app manifest/version disagrees; inspect before stopping.')
        expected={Path(name).name:sha for name,sha in manifest.get('sha256',{}).items() if name.startswith('app/') and len(Path(name).parts)==2 and name.endswith('.py')}
        actual={path.relative_to(installed).as_posix():_digest(path.read_bytes()) for path in installed.rglob('*') if path.is_file() and '__pycache__' not in path.relative_to(installed).parts}
        if actual!=expected:raise ValueError('Installed app differs from the previous release payload; preserve extras for review before stopping.')
        shutil.copytree(installed,target/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        app_hashes={path.relative_to(target/'app').as_posix():_digest(path.read_bytes()) for path in (target/'app').rglob('*') if path.is_file()}
        if app_hashes!=expected:raise ValueError('App changed while snapshotting; old service is still healthy.')
    records=[]
    device=_read(root/'device.json')
    if plan.get('device_before_sha256') and _digest(device)!=plan['device_before_sha256']:raise ValueError('Device configuration changed before stop; re-plan.')
    rows=[{'path':str(root/'device.json'),'client':'device','before_sha256':_digest(device),'after_sha256':plan.get('device_after_sha256')}]+plan['files']
    for index,row in enumerate(rows):
        path=Path(row['path']);data=_read(path)
        if _digest(data)!=row['before_sha256']:raise ValueError('Client changed before stop; old service is still healthy.')
        if data is not None:helpers.atomic(target/str(index),data)
        records.append({key:row.get(key) for key in ('path','client','before_sha256','after_sha256')})
    value={'format':2,'root':str(root),'controller_python':str(python),'controller_checkout':str(controller_checkout) if controller_checkout else None,
           'controller_identity':controller_identity,'previous':json.loads(device),
           'files':records,'app_sha256':app_hashes}
    helpers.atomic(target/'snapshot.json',json.dumps(value,indent=2)+'\n')
    support.current(plan)
    return target


def _snapshot(root,target):
    target=Path(target)
    if target.is_symlink() or os.path.isjunction(target) or not target.resolve().is_relative_to(root/'backups'):raise ValueError('Unsafe upgrade snapshot path.')
    value=json.loads(_read(target/'snapshot.json'))
    if not isinstance(value,dict) or value.get('format')!=2 or value.get('root')!=str(root):raise ValueError('Unsupported snapshot identity; preserve it for review.')
    for index,row in enumerate(value['files']):
        if row['before_sha256'] is not None and _digest(_read(target/str(index)))!=row['before_sha256']:raise ValueError('Upgrade snapshot bytes changed; no restore performed.')
    for name,expected in value['app_sha256'].items():
        path=target/'app'/name
        if not path.resolve().is_relative_to(target/'app') or _digest(_read(path))!=expected:raise ValueError('Upgrade code snapshot changed; no restore performed.')
    actual={path.relative_to(target/'app').as_posix() for path in (target/'app').rglob('*') if path.is_file()}
    if actual!=set(value['app_sha256']):raise ValueError('Upgrade snapshot contains extra code; no restore performed.')
    return value


def restore(root,target):
    helpers=installer();value=_snapshot(root,target);deferred=[]
    # Device CAS is mandatory before changing code. Client edits are preserved.
    device=value['files'][0]
    if _digest(_read(Path(device['path']))) not in (device['before_sha256'],device['after_sha256']):raise ValueError('Device changed outside this upgrade; review before recovery.')
    saved=target/'app';app=root/'app'
    if saved.exists():
        if app.is_symlink() or os.path.isjunction(app) or app.resolve()!=root/'app':raise ValueError('Unsafe runtime app restore path.')
        prepared=Path(tempfile.mkdtemp(prefix='restore-',dir=root/'backups'))
        shutil.copytree(saved,prepared/'app')
        displaced=root/'backups'/('interrupted-app-'+secrets.token_hex(8))
        # All fixed absolute targets remain inside this explicit synthetic/owner runtime.
        if app.exists():os.replace(app,displaced)
        os.replace(prepared/'app',app)
        prepared.rmdir()
    for index,row in enumerate(value['files']):
        path=Path(row['path']);current=_digest(_read(path))
        if current==row['before_sha256']:continue
        if row['client']=='claude-desktop' and helpers.setup_helpers().desktop_busy():
            deferred.append({'path':str(path),'reason':'Desktop reopened; rollback write deferred'});continue
        if current!=row['after_sha256']:
            deferred.append({'path':str(path),'reason':'concurrent edit preserved during rollback'});continue
        if row['before_sha256'] is None:path.unlink(missing_ok=True)
        else:helpers.atomic(path,_read(target/str(index)))
    return {'status':'restored','deferred_clients':deferred}


def release_identity(checkout,version):
    """Verify a trusted local tag/cache without running cached code or Git helpers."""
    def refuse():
        raise ValueError('Cached release identity/content is not the requested approved tag; preserve it for review.')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version): refuse()
    checkout=checkout.resolve()
    if not (checkout/'.git').is_dir() or (checkout/'.git').is_symlink(): refuse()
    git=shutil.which('git')
    if git is None: refuse()
    git_path=Path(git)
    forbidden=[checkout,Path.cwd().resolve()]
    if checkout.parent.name=='releases':forbidden.append(checkout.parent.parent)
    if not git_path.is_absolute() or not git_path.is_file() or any(git_path.resolve().is_relative_to(folder) for folder in forbidden):refuse()
    git=str(git_path.resolve())
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='',
               GIT_NO_LAZY_FETCH='1',GIT_TERMINAL_PROMPT='0',GIT_NO_REPLACE_OBJECTS='1',GIT_OPTIONAL_LOCKS='0')
    def read(*arguments):
        try:
            result=subprocess.run([git,'--no-optional-locks','-c','core.fsmonitor=false',
                '-c','core.hooksPath='+os.devnull,'-C',str(checkout),*arguments],
                env=env,capture_output=True,timeout=30,check=False)
        except (OSError,subprocess.TimeoutExpired): refuse()
        if result.returncode: refuse()
        return result.stdout
    try:
        if Path(read('rev-parse','--show-toplevel').decode('utf-8').strip()).resolve()!=checkout: refuse()
        origin=read('config','--local','--no-includes','--get-all','remote.origin.url').decode('utf-8').strip()
        if not re.fullmatch(r'(?:https://github\.com(?::443)?/|git@github\.com:|ssh://git@github\.com(?::22)?/)'
                            r'code-by-muhammad/second-brain-system(?:\.git)?/?',origin,re.IGNORECASE): refuse()
        head=read('rev-parse','--verify','HEAD^{commit}').decode('ascii').strip()
        tag=read('rev-parse','--verify','refs/tags/'+version+'^{commit}').decode('ascii').strip()
        algorithm=read('rev-parse','--show-object-format').decode('ascii').strip()
        if algorithm not in ('sha1','sha256') or not re.fullmatch(r'[0-9a-f]{'+('40' if algorithm=='sha1' else '64')+'}',head) or head!=tag: refuse()
        tree={}
        for entry in read('ls-tree','-r','-z','HEAD').split(b'\0'):
            if not entry: continue
            metadata,raw_name=entry.split(b'\t',1)
            mode,kind,oid=metadata.decode('ascii').split()
            name=raw_name.decode('utf-8')
            if mode not in ('100644','100755') or kind!='blob' or name in tree: refuse()
            tree[name]=(mode,oid)
        index={}
        for entry in read('ls-files','--stage','-z').split(b'\0'):
            if not entry: continue
            metadata,raw_name=entry.split(b'\t',1)
            mode,oid,stage=metadata.decode('ascii').split()
            name=raw_name.decode('utf-8')
            if stage!='0' or name in index: refuse()
            index[name]=(mode,oid)
        if index!=tree or 'release-manifest.json' not in tree: refuse()
        for name,(_,oid) in tree.items():
            path=checkout/name
            if not path.is_file() or path.is_symlink() or path.resolve()!=path: refuse()
            content=path.read_bytes()
            if hashlib.new(algorithm,b'blob '+str(len(content)).encode('ascii')+b'\0'+content).hexdigest()!=oid: refuse()
    except (OSError,UnicodeError,KeyError,TypeError,ValueError): refuse()
    return {'repository':'code-by-muhammad/second-brain-system','tag':version,'commit':head,
            'manifest_sha256':hashlib.sha256((checkout/'release-manifest.json').read_bytes()).hexdigest(),
            'tracked_files':len(tree),'verification':'trusted-local-origin-tag-index-worktree',
            '_paths':set(tree)}

def verify(checkout,*,version=None):
    identity=release_identity(checkout,version) if version is not None else None
    manifest=json.loads((checkout/'release-manifest.json').read_text(encoding='utf-8'))
    if identity is not None:
        paths=identity.pop('_paths')
        if (manifest.get('repository')!=identity['repository']
                or set(manifest['sha256'])!=paths-{'release-manifest.json'}):
            raise ValueError('Cached release manifest does not describe its committed tag payload.')
    required={'install.py','update.py','pyproject.toml','uv.lock'} | {p.relative_to(checkout).as_posix() for p in (checkout/'app').rglob('*.py')}
    if 'sealed-client-refresh-v1' in manifest.get('capabilities',[]):required.add('setup_runtime.py')
    if not required <= set(manifest['sha256']): raise ValueError('Release manifest omits executable or dependency files.')
    for relative,expected in manifest['sha256'].items():
        target=(checkout/relative).resolve()
        if not target.is_relative_to(checkout.resolve()): raise ValueError('Unsafe manifest path.')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('Release integrity mismatch: '+relative)
    return {**manifest,'git_identity':identity} if identity is not None else manifest

def cli_environment(root=None):
    """Controller reads canonical device paths; no ambient runtime/provider drift."""
    env={key:value for key,value in os.environ.items() if not key.upper().startswith(('BRAIN_','BASIC_MEMORY_','PYTHON'))}
    env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    if root is not None:
        device=json.loads(_read(root/'device.json'))
        env.update(BRAIN_SYSTEM_ROOT=str(root),BRAIN_STATE=str(root/'state'),BRAIN_VAULT=device['vault'],BRAIN_PORT=str(device.get('port',8765)))
    return env


def controller(python,checkout,root,command,action=None,*flags):
    app=Path(checkout)/'app';script=app/'brain_cli.py'
    code='import runpy,sys;sys.path.insert(0,'+repr(str(app))+');runpy.run_path('+repr(str(script))+',run_name="__main__")'
    support=installer().setup_helpers()
    arguments=[*support.isolated_python(python,root),'-c',code,command]+([action] if action else [])+list(flags)
    result=support.run(arguments,source_app=Path(__file__).resolve().parent/'app',env=cli_environment(root),capture_output=True,text=True,encoding='utf-8',timeout=900,check=False)
    if result.returncode:raise RuntimeError('Verified controller refused '+command+' '+str(action or '')+' (exit '+str(result.returncode)+'); inspect private service control receipt.')
    value=json.loads(result.stdout)
    if not isinstance(value,dict):raise ValueError('Invalid controller receipt.')
    return value


def wait_health(python,root,version,timeout=120,*,checkout=None):
    deadline=time.monotonic()+timeout
    checkout=Path(checkout or Path(__file__).resolve().parent)
    while time.monotonic()<deadline:
        try:
            data=controller(python,checkout,root,'health')
            if data.get('status')=='running' and data.get('version')==version:return data
        except (RuntimeError,ValueError,subprocess.TimeoutExpired):pass
        time.sleep(2)
    raise RuntimeError('MCP did not report the expected release before the startup deadline.')


def wiki_command(python,root,vault,action,*flags):
    """Explicit local receipt maintenance; no compiler/model call is made here."""
    device=json.loads(_read(root/'device.json'))
    if Path(device['vault']).resolve()!=Path(vault).resolve():raise ValueError('Migration vault disagrees with the device.')
    receipt=controller(python,root,root,'wiki',action,*flags)
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

def _work_locks(root,timeout=30):
    from contextlib import ExitStack
    from filelock import FileLock
    locks=ExitStack()
    try:
        for name in ('wiki-worker.lock','index-bootstrap.lock','maintenance.lock','writer.lock'):
            locks.enter_context(FileLock(str(root/'state'/name),timeout=timeout))
        return locks
    except BaseException:locks.close();raise


def _receipt(root,value):
    history=root/'state/upgrade-history';installer().private_dir(history)
    event=str(time.time_ns())+'-'+secrets.token_hex(4)+'.json'
    installer().atomic(history/event,json.dumps(value,indent=2)+'\n')
    installer().atomic(root/'state/upgrade-status.json',json.dumps(value,indent=2)+'\n')
    return value


def _restart_previous(root,saved,python,checkout,hold,*,restore_bytes=True):
    previous=_snapshot(root,saved)['previous']
    # No parent work locks may be held during a controller stop.
    if restore_bytes:controller(python,checkout,root,'service','stop','--hold-id',hold,'--wait-seconds','300')
    with _work_locks(root):
        recovery=restore(root,saved) if restore_bytes else {'status':'runtime not switched; original bytes retained','deferred_clients':[]}
        controller(python,checkout,root,'service','install')
    started=controller(python,checkout,root,'service','start','--hold-id',hold,'--allow-legacy')
    health=wait_health(python,root,previous['version'],checkout=checkout)
    finished=controller(python,checkout,root,'service','finish','--hold-id',hold,'--expected-version',previous['version'],'--allow-legacy')
    return {'status':'previous runtime restored','version':previous['version'],'health':health,'start':started,'finish':finished,**recovery}


def _recovery_binding(root,hold):
    """Verify the running recovery checkout before loading any sibling helper."""
    if not re.fullmatch('[a-f0-9]{32}',hold or ''):raise ValueError('Recovery requires the exact transaction UUID.')
    def read_json(path):
        path=Path(path)
        for component in (path,*path.parents):
            if component.is_symlink() or component.is_junction():raise ValueError('Linked recovery metadata is refused.')
        if not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Recovery metadata is unavailable or exceeds its bound.')
        with path.open('rb') as stream:raw=stream.read(64*1024*1024+1)
        if len(raw)>64*1024*1024:raise ValueError('Recovery metadata exceeds its bound.')
        return raw,json.loads(raw)
    _,transaction=read_json(root/'state/service-transaction.json')
    if transaction.get('hold_id')!=hold or transaction.get('root')!=str(root):raise ValueError('Recovery UUID/root mismatch; no changes made.')
    saved=Path(transaction['snapshot'])
    if not saved.resolve().is_relative_to(root/'backups'):raise ValueError('Recovery snapshot leaves this runtime.')
    raw,value=read_json(saved/'snapshot.json')
    if _digest(raw)!=transaction.get('snapshot_sha256'):raise ValueError('Recovery snapshot changed; preserve pending work for review.')
    if not value.get('controller_checkout') or not value.get('controller_identity'):raise ValueError('Recovery snapshot has no verified controller identity.')
    selected=Path(value['controller_checkout'])
    if Path(__file__).resolve().parent!=selected.resolve():raise ValueError('Run recovery from the original verified forward controller checkout; no sibling helper loaded.')
    info=verify(selected,version=value['controller_identity']['tag'])
    if info.get('git_identity')!=value['controller_identity'] or 'owned-service-control-v1' not in info.get('capabilities',[]):raise ValueError('Recovery controller identity changed; preserve pending work.')
    return saved,value,selected


def recover(root,hold,checkout=None):
    _qualified_platform()
    saved,value,selected=_recovery_binding(root,hold)
    if checkout is not None and Path(checkout).resolve()!=selected.resolve():raise ValueError('Recovery controller checkout mismatch.')
    value=_snapshot(root,saved);python=Path(value['controller_python']);checkout=selected
    installer().setup_helpers().service_python(python)
    _receipt(root,{'status':'recovery in progress','hold_id':hold,'snapshot':str(saved),'observed_version':json.loads(_read(root/'device.json')).get('version')})
    try:
        result=_restart_previous(root,saved,python,checkout,hold)
    except BaseException as error:
        _receipt(root,{'status':'recovery required','hold_id':hold,'snapshot':str(saved),'error_type':type(error).__name__,'pending_work':'preserved'})
        raise
    return _receipt(root,{'status':'recovered','hold_id':hold,'snapshot':str(saved),'recovery':result})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--version')
    parser.add_argument('--uv');parser.add_argument('--desktop-config');parser.add_argument('--skip-desktop-refresh',action='store_true')
    parser.add_argument('--recover',action='store_true',help='With the exact hold UUID, restore the sealed previous runtime and preserve concurrent client edits.')
    parser.add_argument('--hold-id')
    parser.add_argument('--migrate-wiki',action='store_true',help='Maintainer only: verify legacy wiki receipts and repair source links during this upgrade.')
    parser.add_argument('--requeue-unverified',action='store_true')
    args=parser.parse_args();root=Path(args.root).expanduser().resolve()
    _qualified_platform()  # Before recovery reads, helper loads or state locks.
    if not args.recover and not re.fullmatch(r'v\d+\.\d+\.\d+',args.version or ''):parser.error('Select an explicit approved version.')
    if args.recover and (args.version or not args.hold_id):parser.error('--recover requires --hold-id and does not take --version.')
    if args.requeue_unverified and not args.migrate_wiki:parser.error('--requeue-unverified requires --migrate-wiki.')
    if args.recover:_recovery_binding(root,args.hold_id)
    else:_forward_binding(root,args.version)
    installer().setup_helpers().host_context()  # Before state/clone/provision/client writes.
    with update_lock(root):
        result=recover(root,args.hold_id) if args.recover else perform(root,args.version,args.migrate_wiki,args.requeue_unverified,
                               uv=args.uv,desktop_config=args.desktop_config,skip_desktop=args.skip_desktop_refresh)
    print(json.dumps(result,indent=2))


def perform(root,version,migrate_wiki=False,requeue_unverified=False,*,uv=None,desktop_config=None,skip_desktop=False):
    _qualified_platform()
    release=_forward_binding(root,version)
    support=installer().setup_helpers();support.host_context()
    if requeue_unverified and not migrate_wiki:raise ValueError('--requeue-unverified requires --migrate-wiki.')
    if (root/'state/service-transaction.json').exists():raise ValueError('An interrupted transaction exists; inspect service recover and use the exact --hold-id before a new update.')
    device=json.loads(_read(root/'device.json'))
    if device.get('version')==version.removeprefix('v'):raise ValueError('This version is already installed; use client refresh or the recorded recovery flow.')
    if migrate_wiki and device.get('role')!='maintainer':raise PermissionError('--migrate-wiki requires the designated maintainer device.')
    if not release.exists():
        git=support.executable('git',None,root,Path(__file__).resolve().parent)
        clone_env={key:value for key,value in os.environ.items() if not key.upper().startswith('GIT_')}
        clone_env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='https',GIT_TERMINAL_PROMPT='0')
        support.run([str(git),'-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','clone','--depth','1','--branch',version,'--',REMOTE,str(release)],
                    source_app=Path(__file__).resolve().parent/'app',env=clone_env,capture_output=True,text=True,encoding='utf-8',check=True,timeout=600)
    info=verify(release,version=version)
    if info['version']!=version.removeprefix('v'):raise ValueError('Release version does not match requested tag.')
    if not {'sealed-client-refresh-v1','owned-service-control-v1'}<=set(info.get('capabilities',[])):
        raise ValueError('Target release does not support safe client refresh/recovery; no service or client changes made.')
    python=support.provision(release,root,uv)
    hold=secrets.token_hex(16);plan_path=root/'state'/('upgrade-client-plan-'+hold+'.json')
    options=(['--desktop-config',desktop_config] if desktop_config else [])+(['--skip-desktop-refresh'] if skip_desktop else [])
    common=[str(python),'-I',str(release/'install.py'),'--root',str(root),'--vault',device['vault'],'--reuse-python',str(python),'--refresh-clients',*options]
    # A fresh cache namespace avoids executing cached checkout bytecode.
    common=[*support.isolated_python(python,root),str(release/'install.py'),*common[3:]]
    result=support.run([*common,'--install-service','--validate-only','--plan-out',str(plan_path)],source_app=Path(__file__).resolve().parent/'app',
                       env=cli_environment(root),check=True,capture_output=True,text=True,encoding='utf-8',timeout=120)
    preflight=json.loads(result.stdout);plan_bytes=_read(plan_path)
    if preflight.get('plan_sha256')!=_digest(plan_bytes):raise ValueError('Client planning receipt changed before stop.')
    plan=support.validate_plan(support.json_object(plan_bytes),root,python)
    saved=snapshot(root,python,plan,controller_checkout=release,controller_identity=info.get('git_identity'))
    previous={key:device[key] for key in ('release_root','python','version')}
    installer().atomic(root/'state/previous-release.json',json.dumps({**previous,'controller_python':str(python),'snapshot':str(saved)},indent=2)+'\n')
    support.current(plan)
    if _digest(_read(root/'device.json'))!=plan.get('device_before_sha256'):raise ValueError('Device changed before stop; re-plan.')
    stopped=False;switched=False;migration=None
    _receipt(root,{'status':'in progress','hold_id':hold,'snapshot':str(saved),'target':info['version']})
    try:
        inspected=controller(python,release,root,'service','inspect')
        if inspected.get('status')!='inspected':raise ValueError('Task ownership preflight did not complete; no hold created.')
        if inspected.get('task_present') is not True or inspected.get('task_enabled') is not True:
            raise ValueError('The owned task is absent or explicitly stopped; inspect it and explicitly start it before requesting this update.')
        controller(python,release,root,'service','hold','--hold-id',hold,'--snapshot',str(saved))
        stop=controller(python,release,root,'service','stop','--hold-id',hold,'--wait-seconds','300')
        if stop.get('status')!='stopped' or not stop.get('empty_at_filetime'):raise ValueError('Controller supplied no verified quiescence.')
        stopped=True
        # Stop owns admission; only now may the updater acquire work locks.
        with _work_locks(root):
            switched=True  # Includes a partially failed deploy child.
            result=support.run([*common,'--client-plan',str(plan_path),'--plan-sha256',preflight['plan_sha256'],'--install-service'],source_app=Path(__file__).resolve().parent/'app',
                               env=cli_environment(root),check=True,capture_output=True,text=True,encoding='utf-8',timeout=180)
            installed=json.loads(result.stdout)
        if migrate_wiki:
            from filelock import FileLock
            # Exclude compilation across BOTH migration/relink calls. Their own
            # writer/maintenance admission remains free; release before any stop.
            with FileLock(str(root/'state/wiki-worker.lock'),timeout=30):
                migration=apply_wiki_migration(python,root,device['vault'],requeue_unverified)
        started=controller(python,release,root,'service','start','--hold-id',hold,'--since',str(stop['empty_at_filetime']))
        health=wait_health(python,root,info['version'],checkout=release)
        overwritten=support.after_check(plan)
        finished=controller(python,release,root,'service','finish','--hold-id',hold,'--expected-version',info['version'])
    except BaseException as error:
        if not stopped:
            if not (root/'state/service-transaction.json').exists():
                _receipt(root,{'status':'update deferred; no hold created','hold_id':hold,'snapshot':str(saved),
                               'error_type':type(error).__name__,'pending_work':'preserved'})
                raise RuntimeError('Update deferred before a hold was created; original runtime retained. Inspect upgrade-status.json.') from error
            try:observed=json.loads(_read(root/'state/service-transaction.json'))
            except (OSError,ValueError,TypeError):observed=None
            if (not isinstance(observed,dict) or observed.get('hold_id')!=hold
                    or observed.get('root')!=str(root) or observed.get('snapshot')!=str(saved)):
                _receipt(root,{'status':'transaction inspection required','attempt_hold_id':hold,'snapshot':str(saved),
                               'phase':'pre-stop hold ownership','error_type':type(error).__name__,'pending_work':'preserved'})
                raise RuntimeError('A different or unreadable transaction was observed; preserve it and inspect service recover before any further action.') from error
            try:
                cancellation=controller(python,release,root,'service','cancel','--hold-id',hold)
            except Exception:
                _receipt(root,{'status':'recovery required','hold_id':hold,'snapshot':str(saved),'phase':'stop refused; no runtime switch','error_type':type(error).__name__,'pending_work':'preserved'})
                raise error
            _receipt(root,{'status':'update deferred; runtime not switched','hold_id':hold,'snapshot':str(saved),'cancellation':cancellation,'error_type':type(error).__name__,'pending_work':'preserved'})
            raise RuntimeError('Update deferred before runtime switch. The exact pre-End refusal was verified; no recovery hold remains.') from error
        try:
            recovery=_restart_previous(root,saved,python,release,hold,restore_bytes=switched)
            _receipt(root,{'status':'update failed; previous runtime verified','hold_id':hold,'snapshot':str(saved),'recovery':recovery,'error_type':type(error).__name__})
        except Exception as recovery_error:
            _receipt(root,{'status':'recovery required','hold_id':hold,'snapshot':str(saved),'error_type':type(error).__name__,'recovery_error_type':type(recovery_error).__name__,'pending_work':'preserved'})
            raise RuntimeError('Upgrade recovery is incomplete; preserve pending work and use the recorded exact recovery UUID.') from recovery_error
        raise RuntimeError('Upgrade failed; previous runtime health verified. Inspect upgrade-status.json for preserved client edits.') from error
    clients=installed.get('client_result',{})
    deferred=clients.get('deferred',[])
    return _receipt(root,{'status':'updated','version':info['version'],'release_identity':info.get('git_identity'),'hold_id':hold,
        'snapshot':str(saved),'previous':previous,'health':health,'start':started,'finish':finished,'wiki_migration':migration,
        'clients':clients,'client_overwrites_after_health':overwritten,
        'client_acceptance':'pending refresh/named-client verification' if deferred or overwritten else 'configuration checked; named-client verification pending'})


if __name__=='__main__':main()
