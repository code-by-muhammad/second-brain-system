"""Install an explicit approved software release; never track main or sync vault Git."""
import sys
if __name__=='__main__' and not sys.flags.isolated:
    raise SystemExit('Run this updater with Python -I from the independently verified release.')
import argparse, contextlib, hashlib, importlib.util, json, os, re, shutil, stat, secrets, subprocess, tempfile, time
from pathlib import Path

REMOTE='https://github.com/code-by-muhammad/second-brain-system.git'
_INSTALLER_CACHE=None
_VERIFIED_HASHES={}
_RELEASE_HELPER=None
CATALOG='predecessor-builds.json'
HELD_DEPLOYMENT='held-deployment-v1'


def release_helpers(checkout=None, hashes=None):
    global _RELEASE_HELPER
    import types
    checkout=Path(checkout or Path(__file__).resolve().parent)
    path=checkout/'app/brain_release.py'
    raw=path.read_bytes()
    actual=hashlib.sha256(raw).hexdigest()
    expected=(hashes if hashes is not None else _VERIFIED_HASHES).get('app/brain_release.py')
    if expected is not None and actual!=expected:
        raise ValueError('Verified application inventory helper changed.')
    if _RELEASE_HELPER is not None and _RELEASE_HELPER[:2]==(path,actual):
        return _RELEASE_HELPER[2]
    module=types.ModuleType('_brain_upgrade_release')
    exec(compile(raw,str(path),'exec'),module.__dict__)
    _RELEASE_HELPER=(path,actual,module)
    return module


def _predecessor(root, device, checkout, target):
    helper=release_helpers()
    installed=helper.inventory(root/'app')
    version=device.get('version')
    if not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise ValueError('Installed release version is invalid.')
    try:
        recorded=Path(device['release_root'])
        if not recorded.is_absolute() or recorded.resolve()!=recorded:
            raise ValueError('Recorded release path is unavailable.')
        old=verify(recorded,version='v'+version)
        expected=helper.manifest_app(old,version)
        if installed==expected:
            return installed,{'source':'recorded checkout','version':version,'identity':old['git_identity']}
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):
        pass
    expected=target['sha256'].get(CATALOG)
    if expected is not None:
        data,_=helper.file_image(checkout/CATALOG)
        if _digest(data)!=expected:
            raise ValueError('Verified predecessor catalog changed before admission.')
        for build in helper.catalog(data):
            if build['version']==version and installed==build['app_sha256']:
                return installed,{'source':'candidate catalog','version':version,'catalog_sha256':expected,
                                  'provenance':build['provenance']}
    raise ValueError('Installed app differs from every verified predecessor payload for '+version+'.')


def _candidate_platform():
    """Admit the declared native candidate matrix; this is not release approval."""
    import errno
    import struct
    if struct.calcsize('P')!=8:
        raise ValueError('This candidate requires a native 64-bit supported interpreter; no changes made.')
    if sys.platform=='win32':
        # Query the kernel, not WMI/environment-derived architecture strings.
        try:import ctypes
        except ImportError:raise ValueError('Native Windows architecture support is unavailable; no changes made.') from None
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=ctypes.c_void_p
            query=kernel.IsWow64Process2
            query.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ushort),ctypes.POINTER(ctypes.c_ushort)]
            query.restype=ctypes.c_int
            process_machine,native_machine=ctypes.c_ushort(),ctypes.c_ushort()
            if not query(kernel.GetCurrentProcess(),ctypes.byref(process_machine),ctypes.byref(native_machine)):
                raise OSError('Architecture query failed.')
        except (AttributeError,OSError,ctypes.ArgumentError):
            raise ValueError('Native Windows architecture is unavailable; no changes made.') from None
        if process_machine.value!=0 or native_machine.value!=0x8664:
            raise ValueError('This candidate supports native Windows x64 only; no changes made.')
        return
    if sys.platform not in ('darwin','linux'):
        raise ValueError('Unsupported candidate operating system; no changes made.')
    try:machine=os.uname().machine
    except (AttributeError,OSError):raise ValueError('Native architecture is unavailable; no changes made.') from None
    if machine not in (('arm64','x86_64') if sys.platform=='darwin' else ('x86_64',)):
        raise ValueError('Unsupported candidate architecture; no changes made.')
    if sys.platform=='darwin':
        try:import ctypes
        except ImportError:raise ValueError('Native Mac architecture support is unavailable; no changes made.') from None
        try:
            query=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).sysctlbyname
            query.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
            query.restype=ctypes.c_int
            translated=ctypes.c_int();size=ctypes.c_size_t(ctypes.sizeof(translated))
            result=query(b'sysctl.proc_translated',ctypes.byref(translated),ctypes.byref(size),None,0)
            if result==-1 and ctypes.get_errno()==errno.ENOENT:return
            if result!=0 or size.value!=ctypes.sizeof(translated) or translated.value!=0:
                raise OSError('Translated or uncertain process architecture.')
        except (AttributeError,OSError,ctypes.ArgumentError):
            raise ValueError('Use a native Mac interpreter; Rosetta or uncertain translation is refused before changes.') from None


def _qualified_platform():
    _candidate_platform()

def _forward_binding(root,version):
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version or ''):raise ValueError('Select an explicit approved release tag.')
    release=Path(root)/'releases'/version
    if any(path.is_symlink() or path.is_junction() or path.resolve()!=path for path in (release.parent,release)):
        raise ValueError('Use a plain release directory owned by this runtime; linked/shared release aliases are refused.')
    if Path(__file__).resolve().parent!=release:
        raise ValueError('Run the verified forward updater from exactly '+str(release/'update.py')+' using the retained installed Python outside the target venv.')
    return release

def installer():
    global _INSTALLER_CACHE
    import types
    path=Path(__file__).resolve().with_name('install.py')
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    expected=_VERIFIED_HASHES.get('install.py')
    if expected is not None and sha!=expected:raise ValueError('Verified installer bytes changed; no helper executed.')
    if _INSTALLER_CACHE is not None and _INSTALLER_CACHE[:2]==(str(path),sha):
        _INSTALLER_CACHE[2]._SETUP_SHA256=_VERIFIED_HASHES.get('setup_runtime.py')
        _INSTALLER_CACHE[2]._CONNECTION_SHA256=_VERIFIED_HASHES.get('app/brain_connection.py')
        return _INSTALLER_CACHE[2]
    module=types.ModuleType('_brain_update_installer');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    module._SETUP_SHA256=_VERIFIED_HASHES.get('setup_runtime.py')
    module._CONNECTION_SHA256=_VERIFIED_HASHES.get('app/brain_connection.py')
    _INSTALLER_CACHE=(str(path),sha,module)
    return module

def _bind_release(info):
    global _VERIFIED_HASHES
    _VERIFIED_HASHES=dict(info['sha256'])

def _forward_release(root,version):
    release=_forward_binding(root,version)
    info=verify(release,version=version)
    if info['version']!=version.removeprefix('v'):raise ValueError('Release version does not match requested tag.')
    if not {'sealed-client-refresh-v1','owned-service-control-v1'}<=set(info.get('capabilities',[])):
        raise ValueError('Target release does not support safe client refresh/recovery; no service or client changes made.')
    _bind_release(info)
    if HELD_DEPLOYMENT in info.get('capabilities',[]) or info['version']=='0.2.16':
        data,_=release_helpers().file_image(release/CATALOG)
        if data is None or _digest(data)!=info['sha256'].get(CATALOG):
            raise ValueError('Release predecessor catalog is absent or changed.')
        release_helpers().catalog(data)
    return release,info

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


def _registration_record(record,previous):
    """The native controller supplies authority; seal its exact bounded record."""
    if sys.platform=='linux' and record=={'kind':'linux','backend':'user-space','present':False}:
        return dict(record)
    common={'kind','definition_sha256','enabled','interpreter'}
    if (not isinstance(record,dict) or record.get('kind')!=sys.platform
        or set(record)!=(common|({'xml','owner_sid'} if sys.platform=='win32' else set()))
        or record.get('enabled') is not True
        or not isinstance(record.get('definition_sha256'),str)
        or not re.fullmatch('[a-f0-9]{64}',record['definition_sha256'])
        or not isinstance(record.get('interpreter'),str) or not Path(record['interpreter']).is_absolute()
        or record['interpreter']!=previous['python']):
        raise ValueError('Native registration is not the enabled recorded device interpreter; no hold created.')
    if sys.platform=='win32':
        if (not isinstance(record['xml'],str) or not 0<len(record['xml'].encode('utf-8'))<=65536
            or not isinstance(record['owner_sid'],str) or not re.fullmatch(r'S-1-[0-9]+(?:-[0-9]+)+',record['owner_sid'])):
            raise ValueError('Native task snapshot is malformed or exceeds its bound.')
    return dict(record)


def _binding_payload(previous,python,target):
    selected={key:previous[key] for key in ('release_root','python','version')}
    return (json.dumps({**selected,'controller_python':str(python),'snapshot':str(target)},indent=2)+'\n').encode('utf-8')


def _binding_images(target,value):
    record=value.get('previous_binding')
    if (not isinstance(record,dict) or set(record)!={'before_sha256','after_sha256'}
        or record['before_sha256'] is not None and not re.fullmatch('[a-f0-9]{64}',str(record['before_sha256']))
        or not re.fullmatch('[a-f0-9]{64}',str(record['after_sha256']))):
        raise ValueError('Snapshot previous-binding identity is incomplete.')
    before=_read(Path(target)/'previous-binding.before');after=_read(Path(target)/'previous-binding.after')
    if (before is not None and len(before)>65536 or after is None or len(after)>65536
        or _digest(before)!=record['before_sha256'] or _digest(after)!=record['after_sha256']
        or after!=_binding_payload(value['previous'],value['controller_python'],target)):
        raise ValueError('Snapshot previous-binding bytes changed; no restore performed.')
    return before,after


def _seal_artifacts(target, images):
    helper=release_helpers()
    rows=[]
    seen=set()
    for index,(kind,path,before,after,phase) in enumerate(images):
        if str(path) in seen:
            raise ValueError('Duplicate sealed deployment artifact.')
        seen.add(str(path))
        row={'kind':kind,'path':str(path),'phase':phase}
        for name,(data,mode) in (('before',before),('after',after)):
            row[name]={'sha256':_digest(data),'mode':mode}
            if data is not None:
                installer().atomic(target/('artifact-'+str(index)+'.'+name),data,expected_sha256=None)
                helper.sync_directory(target)
        rows.append(row)
    return rows


def snapshot(root,python,plan=None,*,controller_checkout=None,controller_identity=None,registration=None,
             accepted=None, target_app=None, hold=None, service_policy='registered', images=()):
    """Save sealed runtime preimages and an attempt-specific publication layout."""
    helpers=installer();support=helpers.setup_helpers();release=release_helpers()
    plan=plan or {'format':1,'root':str(root),'python':str(python),'files':[]}
    support.validate_plan(plan,root,python);support.current(plan)
    device=_read(root/'device.json')
    previous=json.loads(device)
    registration=_registration_record(registration,previous)
    binding_before=_read(root/'state/previous-release.json')
    if binding_before is not None and len(binding_before)>65536:raise ValueError('Previous-release metadata exceeds its bound.')
    if accepted is None:
        accepted=_predecessor(root,previous,Path(controller_checkout or previous['release_root']),{'sha256':{}})
    helpers.private_dir(root/'backups')
    target=Path(tempfile.mkdtemp(prefix='upgrade-',dir=root/'backups'));helpers.private_dir(target)
    expected,provenance=accepted
    if (release.inventory(root/'app',copy_to=target/'app')!=expected
            or release.inventory(target/'app',allow_cache=False)!=expected):
        raise ValueError('App changed while snapshotting; old service is still healthy.')
    records=[]
    if plan.get('device_before_sha256') and _digest(device)!=plan['device_before_sha256']:raise ValueError('Device configuration changed before stop; re-plan.')
    rows=[{'path':str(root/'device.json'),'client':'device','before_sha256':_digest(device),'after_sha256':plan.get('device_after_sha256')}]+plan['files']
    for index,row in enumerate(rows):
        path=Path(row['path'])
        data,mode=release.file_image(path) if index==0 else support.client_image(path)
        if _digest(data)!=row['before_sha256']:raise ValueError('Client changed before stop; old service is still healthy.')
        if data is not None:helpers.atomic(target/str(index),data)
        default_mode=0o666 if os.name=='nt' else 0o600
        records.append({**{key:row.get(key) for key in ('path','client','before_sha256','after_sha256')},
                        'before_mode':mode,'after_mode':mode if mode is not None else default_mode})
    binding_after=_binding_payload(previous,python,target)
    if binding_before is not None:helpers.atomic(target/'previous-binding.before',binding_before)
    helpers.atomic(target/'previous-binding.after',binding_after)
    value={'format':3,'root':str(root),'controller_python':str(python),'controller_checkout':str(controller_checkout) if controller_checkout else None,
           'controller_identity':controller_identity,'previous':previous,'files':records,'app_sha256':expected,
           'predecessor':provenance,'registration':registration,
           'previous_binding':{'before_sha256':_digest(binding_before),'after_sha256':_digest(binding_after)}}
    if hold is not None:
        value['deployment']={'hold_id':hold,'service_policy':service_policy,'target_app_sha256':release.app_map(target_app),
                             'artifacts':_seal_artifacts(target,images)}
    helpers.atomic(target/'snapshot.json',json.dumps(value,indent=2)+'\n',expected_sha256=None)
    release.sync_directory(target);release.sync_directory(target.parent)
    support.current(plan)
    if _read(root/'state/previous-release.json')!=binding_before:raise ValueError('Previous-release metadata changed before stop; old service is still healthy.')
    return target


def _snapshot(root,target):
    target=Path(target);release=release_helpers()
    if target.is_symlink() or target.is_junction() or not target.resolve().is_relative_to(root/'backups'):raise ValueError('Unsafe upgrade snapshot path.')
    value=json.loads(_read(target/'snapshot.json'))
    if not isinstance(value,dict) or value.get('format')!=3 or value.get('root')!=str(root):raise ValueError('Unsupported snapshot identity; preserve it for review.')
    for index,row in enumerate(value['files']):
        if row['before_sha256'] is not None and _digest(_read(target/str(index)))!=row['before_sha256']:raise ValueError('Upgrade snapshot bytes changed; no restore performed.')
    if release.inventory(target/'app',allow_cache=False)!=release.app_map(value['app_sha256']):
        raise ValueError('Upgrade code snapshot changed or contains extra code; no restore performed.')
    _registration_record(value.get('registration'),value['previous'])
    _binding_images(target,value)
    if 'deployment' in value:
        release.artifact_rows(target,value)
        release.app_map(value['deployment']['target_app_sha256'])
    return value


def _check_preimages(root, saved, sealed):
    release=release_helpers()
    if release.inventory(root/'app')!=sealed['app_sha256']:
        raise ValueError('Installed app changed after preflight; no runtime switch was made.')
    for row,before,_ in release.artifact_rows(saved,sealed):
        if release.file_image(Path(row['path']))!=before:
            raise ValueError('Deployment artifact changed before stop: '+row['path'])


def _restore_app(root,target,value):
    release=release_helpers()
    app=root/'app'; old=value['app_sha256']; deployment=value.get('deployment')
    def current(path):
        if not os.path.lexists(path):return None
        return release.inventory(path)
    installed=current(app)
    if installed==old:return
    if deployment is None:
        # Existing format-3 holds predate attempt-specific displacement.
        if installed is None:raise ValueError('Missing app has no sealed displacement evidence.')
        checkout=Path(value['controller_checkout'])
        manifest=json.loads(_read(checkout/'release-manifest.json'))
        if installed!=release.manifest_app(manifest,manifest['version']):
            raise ValueError('Runtime app is not the sealed previous or forward payload.')
    else:
        old_displaced=current(target/'displaced-app')
        new_displaced=current(target/'rollback-app')
        forward=deployment['target_app_sha256']
        if old_displaced not in (None,old) or new_displaced not in (None,forward):
            raise ValueError('Attempt displacement contains unexplained app bytes.')
        if installed is None and old_displaced!=old and new_displaced!=forward:
            raise ValueError('Missing app has no verified attempt displacement.')
        if installed not in (None,forward):
            raise ValueError('Runtime app changed outside this upgrade.')
    with tempfile.TemporaryDirectory(prefix='restore-',dir=target) as container:
        prepared=Path(container)/'app'
        if release.inventory(target/'app',copy_to=prepared,allow_cache=False)!=old or release.inventory(prepared)!=old:
            raise ValueError('Previous app changed before restoration.')
        release.sync_directory(prepared)
        if installed is not None:
            displaced=target/'rollback-app'
            if os.path.lexists(displaced):
                raise ValueError('Attempt rollback displacement is already occupied.')
            if current(app)!=installed:raise ValueError('Runtime app changed during restoration.')
            os.replace(app,displaced)
            release.sync_directory(target);release.sync_directory(root)
        os.replace(prepared,app)
        release.sync_directory(root)


def restore(root,target):
    helpers=installer();support=helpers.setup_helpers();release=release_helpers()
    target=Path(target);value=_snapshot(root,target);deferred=[]
    images=release.artifact_rows(target,value)
    for row,before,after in images:
        if row['phase']=='deploy' and release.file_image(Path(row['path'])) not in (before,after):
            raise ValueError('Mandatory deployment artifact changed; hold preserved: '+row['path'])
    device=value['files'][0];path=Path(device['path']);current=_digest(_read(path))
    if current not in (device['before_sha256'],device['after_sha256']):raise ValueError('Device changed outside this upgrade; review before recovery.')
    if 'deployment' not in value and current!=device['before_sha256']:
        helpers.atomic(path,_read(target/'0'),expected_sha256=device['after_sha256'])
    _restore_app(root,target,value)
    for row,before,after in reversed(images):
        if row['phase']!='deploy':continue
        path=Path(row['path'])
        if release.file_image(path)!=before:
            release.write_image(path,before,after,protect=helpers.private if os.name=='nt' else None)
    for index,row in enumerate(value['files'][1:],1):
        path=Path(row['path'])
        try:data,mode=support.client_image(path)
        except (OSError,ValueError,RuntimeError):
            deferred.append({'path':str(path),'reason':'unsafe client path preserved during rollback'});continue
        current=_digest(data)
        if current==row['before_sha256'] and mode==row.get('before_mode',mode):continue
        if row['client']=='claude-desktop' and support.desktop_busy():
            deferred.append({'path':str(path),'reason':'Desktop reopened; rollback write deferred'});continue
        if current!=row['after_sha256'] or mode!=row.get('after_mode',mode):
            deferred.append({'path':str(path),'reason':'concurrent edit preserved during rollback'});continue
        try:
            helpers.write_client(path,_read(target/str(index)),expected_sha256=row['after_sha256'],mode=row.get('before_mode'))
        except (OSError,ValueError,RuntimeError):
            deferred.append({'path':str(path),'reason':'concurrent or unsafe client edit during rollback staging; preserved'})
    return {'status':'restored','deferred_clients':deferred}


def release_identity(checkout,version):
    """Verify a trusted local tag/cache without running cached code or Git helpers."""
    def refuse():
        raise ValueError('Cached release identity/content is not the requested approved tag; preserve it for review.')
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version): refuse()
    checkout=checkout.resolve()
    if not (checkout/'.git').is_dir() or (checkout/'.git').is_symlink() or (checkout/'.git').is_junction(): refuse()
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
            if not path.is_file() or path.is_symlink() or path.is_junction() or path.resolve()!=path: refuse()
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
    if HELD_DEPLOYMENT in manifest.get('capabilities',[]) or manifest.get('version')=='0.2.16':
        required.update({CATALOG,'app/brain_release.py','app/brain_index_worker.py','app/brain_index_bootstrap.py'})
    if not required <= set(manifest['sha256']): raise ValueError('Release manifest omits executable or dependency files.')
    for relative,expected in manifest['sha256'].items():
        target=(checkout/relative).resolve()
        if not target.is_relative_to(checkout.resolve()): raise ValueError('Unsafe manifest path.')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected: raise ValueError('Release integrity mismatch: '+relative)
    import importlib.machinery
    for directory,dirs,files in os.walk(checkout/'app',followlinks=False):
        parent=Path(directory)
        for name in dirs+files:
            target=parent/name
            if target.is_symlink() or target.is_junction():raise ValueError('Linked controller payload is refused.')
        dirs[:]=[name for name in dirs if name!='__pycache__']
        for name in files:
            target=parent/name
            if any(name.endswith(suffix) for suffix in (*importlib.machinery.all_suffixes(),'.pyd','.so')) and target.relative_to(checkout).as_posix() not in manifest['sha256']:
                raise ValueError('Unexpected importable controller payload; preserve the cache for review.')
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
    isolated=support.isolated_python(python,root)
    cache=Path(isolated[-1].removeprefix('pycache_prefix='))
    cache_parent=Path(root)/'state/import-cache'
    before=None
    try:
        if (isolated[-1].startswith('pycache_prefix=') and cache.parent==cache_parent
                and cache.name.startswith('run-') and cache.name!='run-'
                and not any(p.is_symlink() or p.is_junction() for p in (cache,*cache.parents)) and cache.resolve()==cache):
            before=cache.stat(follow_symlinks=False)
    except (OSError,RuntimeError):pass
    arguments=[*isolated,'-c',code,command]+([action] if action else [])+list(flags)
    try:
        result=support.run(arguments,source_app=Path(__file__).resolve().parent/'app',env=cli_environment(root),capture_output=True,text=True,encoding='utf-8',timeout=900,check=False)
        if result.returncode:
            # The controller's last error line is the reason an operator needs; it names no credential.
            reason=((getattr(result,'stderr',None) or '').strip().splitlines() or [''])[-1][:300]
            raise RuntimeError('Verified controller refused '+command+' '+str(action or '')+' (exit '+str(result.returncode)+')'+(': '+reason if reason else '')+'; inspect private service control receipt.')
        value=json.loads(result.stdout)
        if not isinstance(value,dict):raise ValueError('Invalid controller receipt.')
        return value
    finally:
        if before is not None:
            try:
                after=cache.stat(follow_symlinks=False)
                if (before.st_ino>0 and not any(p.is_symlink() or p.is_junction() for p in (cache,*cache.parents))
                        and cache.resolve()==cache and (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino)):
                    cache.rmdir()  # Atomic empty-directory removal only; retain nonempty/uncertain paths.
            except (OSError,RuntimeError):pass


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

def apply_wiki_migration(python,root,vault,requeue_unverified=False,hold_id=None):
    held=['--hold-id',hold_id] if hold_id is not None else []
    flags=['--apply']+(['--requeue-unverified'] if requeue_unverified else [])+held
    migration=wiki_command(python,root,vault,'migrate',*flags)
    if migration.get('dry_run') is not False: raise ValueError('Wiki migration did not return an applied receipt.')
    relink=wiki_command(python,root,vault,'relink',*held)
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
    sealed=_snapshot(root,saved);previous=sealed['previous']
    before,after=_binding_images(saved,sealed)
    if _read(root/'state/previous-release.json') not in (before,after):
        raise ValueError('Previous-release metadata changed; recovery refused before stopping.')
    absent=sealed['registration'].get('present') is False
    if restore_bytes or absent:
        stopped=controller(python,checkout,root,'service','stop','--hold-id',hold,'--wait-seconds','300')
        if stopped.get('status')!='stopped' or not stopped.get('empty_at_filetime'):
            raise ValueError('Rollback requires fresh verified quiescence.')
    if restore_bytes or absent:
        with _work_locks(root,timeout=300):
            _check_held(root,hold,saved,'stopped')
            _check_user_space_quiescence(root,checkout,python,sealed,hold,restoring=True)
            if absent:
                withdrawn=controller(python,checkout,root,'service','withdraw-first','--hold-id',hold)
                if withdrawn.get('status')!='withdrawn' or withdrawn.get('file_present') is not False:
                    raise ValueError('First native registration withdrawal was not verified; hold preserved.')
            recovery=restore(root,saved) if restore_bytes else {'status':'original runtime retained','deferred_clients':[]}
            if restore_bytes and not absent:
                registration=controller(python,checkout,root,'service','install','--hold-id',hold)
                if (registration.get('restored') not in ('sealed-retained','sealed-restored')
                    or registration.get('definition_sha256')!=sealed['registration']['definition_sha256']):
                    raise ValueError('Original native registration restoration was not verified; hold preserved.')
                recovery['registration']=registration
    else:
        recovery={'status':'runtime not switched; original bytes retained','deferred_clients':[]}
    binding=_restore_previous_binding(root,saved)
    started=health=None
    if not absent:
        started=controller(python,checkout,root,'service','start','--hold-id',hold,'--allow-legacy')
        health=wait_health(python,root,previous['version'],checkout=checkout)
    finished=_finish(root,saved,python,checkout,hold,previous['version'],*(() if absent else ('--allow-legacy',)))
    _retire_displacement(root,saved,hold)
    return {'status':'previous runtime restored','version':previous['version'],'health':health,'start':started,
            'finish':finished,'previous_binding':binding,**recovery}


def _restore_previous_binding(root,saved):
    value=_snapshot(root,saved);before,after=_binding_images(saved,value)
    path=root/'state/previous-release.json';current=_read(path)
    if current==before:return {'status':'original binding retained','sha256':_digest(before)}
    if current!=after:raise ValueError('Previous-release metadata changed; preserve the active hold for review.')
    if before is None:
        if _read(path)!=after:raise ValueError('Previous-release metadata changed before removal; hold preserved.')
        path.unlink()
    else:installer().atomic(path,before,expected_sha256=_digest(after))
    if _read(path)!=before:raise ValueError('Previous-release restoration was not verified; hold preserved.')
    return {'status':'original binding restored','sha256':_digest(before)}


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
    _bind_release(info)
    return saved,value,selected


def _recovery_receipt_fields(root,hold,saved):
    """Only advertise a recovery UUID when its matching active hold is readable."""
    try:
        raw=_read(root/'state/service-transaction.json')
        if raw is None:observed='absent'
        else:
            value=json.loads(raw)
            if (isinstance(value,dict) and value.get('hold_id')==hold and value.get('root')==str(root)
                    and isinstance(value.get('snapshot'),str) and saved is not None
                    and Path(value['snapshot']).resolve()==Path(saved).resolve()):
                return {'status':'recovery required','hold_id':hold}
            observed='mismatch'
    except FileNotFoundError:observed='absent'
    except (OSError,ValueError,TypeError,RecursionError,RuntimeError):observed='unreadable'
    return {'status':'transaction outcome inspection required','attempt_hold_id':hold,
            'observed_transaction':observed,'transaction_evidence_path':str(root/'state/service-transactions'/(hold+'.json'))}


def recover(root,hold,checkout=None):
    _qualified_platform()
    saved,value,selected=_recovery_binding(root,hold)
    if checkout is not None and Path(checkout).resolve()!=selected.resolve():raise ValueError('Recovery controller checkout mismatch.')
    value=_snapshot(root,saved);python=Path(value['controller_python']);checkout=selected
    installer().setup_helpers().service_python(python)
    _receipt(root,{'status':'recovery in progress','hold_id':hold,'snapshot':str(saved) if saved is not None else None,'observed_version':json.loads(_read(root/'device.json')).get('version')})
    try:
        completed=_completion_record(root,saved,hold)
        if completed is None:
            result=_restart_previous(root,saved,python,checkout,hold)
        else:
            version=completed[1]
            flags=('--allow-legacy',) if version==value['previous']['version'] else ()
            result={'version':version,'finish':_finish(root,saved,python,checkout,hold,version,*flags)}
            _retire_displacement(root,saved,hold)
    except BaseException as error:
        _receipt(root,{**_recovery_receipt_fields(root,hold,saved),'snapshot':str(saved) if saved is not None else None,'error_type':type(error).__name__,'pending_work':'preserved'})
        raise
    return _receipt(root,{'status':'recovered','hold_id':hold,'snapshot':str(saved) if saved is not None else None,'recovery':result})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--version')
    parser.add_argument('--uv');parser.add_argument('--desktop-config');parser.add_argument('--skip-desktop-refresh',action='store_true')
    parser.add_argument('--recover',action='store_true',help='With the exact hold UUID, restore the sealed previous runtime and preserve concurrent client edits.')
    parser.add_argument('--hold-id')
    parser.add_argument('--no-service',action='store_true',help='Keep native registration absent on a verified unregistered non-systemd Linux upgrade.')
    parser.add_argument('--clients',help='Comma-separated assistants to refresh. An empty value selects none.')
    parser.add_argument('--no-hooks',action='store_true',help='Skip Claude Code hook refresh.')
    parser.add_argument('--migrate-wiki',action='store_true',help='Maintainer only: verify legacy wiki receipts and repair source links during this upgrade.')
    parser.add_argument('--requeue-unverified',action='store_true')
    args=parser.parse_args();root=Path(args.root).expanduser().resolve()
    _qualified_platform()  # Before recovery reads, helper loads or state locks.
    if not args.recover and not re.fullmatch(r'v\d+\.\d+\.\d+',args.version or ''):parser.error('Select an explicit approved version.')
    if args.recover and (args.version or not args.hold_id):parser.error('--recover requires --hold-id and does not take --version.')
    if args.recover and (args.no_service or args.clients is not None or args.no_hooks):parser.error('Recovery uses the sealed prior options.')
    if args.requeue_unverified and not args.migrate_wiki:parser.error('--requeue-unverified requires --migrate-wiki.')
    if args.recover:_recovery_binding(root,args.hold_id)
    else:_forward_release(root,args.version)
    installer().setup_helpers().host_context()  # Before state/clone/provision/client writes.
    with update_lock(root):
        result=recover(root,args.hold_id) if args.recover else perform(root,args.version,args.migrate_wiki,args.requeue_unverified,
                               uv=args.uv,desktop_config=args.desktop_config,skip_desktop=args.skip_desktop_refresh,
                               no_service=args.no_service,clients=args.clients.split(',') if args.clients else [] if args.clients=='' else None,no_hooks=args.no_hooks)
    print(json.dumps(result,indent=2))


def _unregistered_user_service(inspected):
    """A non-systemd host whose service file was never written.

    systemd, macOS and Windows do not match: an absent or stopped task there
    still refuses the update. An explicitly disabled user-space service does too.
    """
    if sys.platform!='linux' or not isinstance(inspected,dict):return False
    native=inspected.get('native')
    if not isinstance(native,dict) or native.get('backend')!='user-space':return False
    return (inspected.get('task_present') is False and inspected.get('task_enabled') is None
            and inspected.get('registration') is None and native.get('file_present') is False
            and native.get('enabled') is None and native.get('loaded') is False
            and native.get('running') is False and native.get('quiescent') is True
            and native.get('main_pid') is None)


def _native_images(python,release,root,policy):
    if policy=='absent':
        return [],[]
    planned=controller(python,release,root,'service','plan-registration','--candidate-python',str(python))
    if planned.get('status')!='registration planned':
        raise ValueError('Native registration images were not measured.')
    result=[]
    for row in planned['artifacts']:
        def image(phase):
            raw=row[phase]['hex'];mode=row[phase]['mode']
            return (bytes.fromhex(raw) if raw is not None else None,mode)
        result.append((row['kind'],Path(row['path']),image('before'),image('after'),'native'))
    return result,planned.get('skipped',[])


def _check_user_space_quiescence(root,checkout,python,sealed,hold,*,restoring=False):
    deployment=sealed.get('deployment')
    if deployment is None:
        return
    if (sealed['registration'].get('backend')=='user-space'
            or any(row['phase']=='native' for row in deployment['artifacts'])):
        flags=['--restoring'] if restoring else []
        result=controller(python,checkout,root,'service','check-stopped','--hold-id',hold,*flags)
        if result.get('status')!='quiescent' or result.get('hold_id')!=hold:
            raise ValueError('Fresh Linux user-space quiescence was not verified.')


def _static_grok_acceptance(support,plan,overwritten,root,python):
    expected=next((row['path'] for row in plan['files']
                   if row['client']=='grok' and row['after'].startswith('{')),None)
    if expected is None or expected in overwritten:
        return
    home=Path.home()
    try:path=support.resolve_client_path(support.grok_directory(home)/'mcp-registration.json',home)
    except (support.ClientSkip,OSError):path=None
    if str(path)!=expected:
        overwritten.append(expected)
        return
    support.validated_grok(home,root,python)


def _deployment_images(root,release,info,device,python,plan):
    helper=release_helpers()
    install=installer()
    vault=Path(device['vault']).resolve()
    mode=0o666 if os.name=='nt' else 0o600
    def encode(value,*,indent=None):
        return (json.dumps(value,indent=indent)+'\n').encode()
    settings=install.device_settings(device,vault,python)
    settings.update(release_root=str(release),version=info['version'])
    device_image=encode(settings,indent=2)
    if _digest(device_image)!=plan['device_after_sha256']:
        raise ValueError('Candidate device image disagrees with its validated plan.')
    launcher,body,launcher_mode=install.launcher_image(root,python)
    images=[('device',root/'device.json',helper.file_image(root/'device.json'),(device_image,mode),'deploy'),
            ('launcher',launcher,helper.file_image(launcher),(body,launcher_mode if os.name!='nt' else mode),'deploy')]
    token=install._auth_recovery_token(root) or secrets.token_urlsafe(48)
    for kind,relative,value in (
            ('auth-client','client-access.json',encode({'token':token})),
            ('auth-server','access.json',encode({'token_sha256':_digest(token.encode())})),
            ('config','config.json',encode(install.retrieval_settings(root,vault),indent=2)),
            ('ignore','.bmignore',install.INDEX_IGNORE_TEXT.encode())):
        path=root/'state'/relative
        before=helper.file_image(path)
        if before[0] is None:
            images.append((kind,path,before,(value,mode),'deploy'))
    return images


def _check_held(root,hold,saved,stage=None):
    current=json.loads(_read(root/'state/service-transaction.json'))
    if (not isinstance(current,dict) or current.get('hold_id')!=hold or current.get('root')!=str(root)
            or Path(current.get('snapshot','')).resolve()!=saved.resolve()
            or current.get('snapshot_sha256')!=_digest(_read(saved/'snapshot.json'))
            or stage is not None and current.get('stage')!=stage):
        raise ValueError('Exact transaction UUID, phase, or snapshot could not be verified.')


class CompletionPending(RuntimeError):
    """The exact committed outcome exists while the active marker remains."""


def _completion_record(root,saved,hold,version=None):
    raw=_read(root/'state/service-transactions'/(hold+'.json'))
    record=json.loads(raw) if raw is not None else {}
    if not isinstance(record,dict):return None
    expected=record.get('authenticated_version') or record.get('absent_version')
    if (record.get('stage')!='complete' or record.get('hold_id')!=hold
            or record.get('root')!=str(root) or record.get('snapshot')!=str(saved)
            or record.get('snapshot_sha256')!=_digest(_read(saved/'snapshot.json'))
            or not isinstance(expected,str) or (version is not None and expected!=version)):
        return None
    return record,expected


def _finish(root,saved,python,checkout,hold,version,*flags):
    arguments=(python,checkout,root,'service','finish','--hold-id',hold,'--expected-version',version,*flags)
    try:
        result=controller(*arguments)
        if result.get('status')!='transaction released' or result.get('hold_id')!=hold:
            raise ValueError('Service did not report exact transaction completion.')
        return result
    except (RuntimeError,ValueError,OSError):
        completed=_completion_record(root,saved,hold,version)
        if completed is None:
            raise
        if os.path.lexists(root/'state/service-transaction.json'):
            try:
                _check_held(root,hold,saved)
                controller(*arguments)
            except (RuntimeError,ValueError,OSError) as error:
                raise CompletionPending('Durable completion exists. Preserve the hold until the exact committed outcome is released.') from error
            if os.path.lexists(root/'state/service-transaction.json'):
                raise CompletionPending('Durable completion exists and its active hold remains.')
        return {'status':'transaction released','hold_id':hold,'identity':completed[0].get('identity'),'durable_receipt':True}


def _retire_displacement(root,saved,hold):
    release=release_helpers()
    value=_snapshot(root,saved)
    deployment=value.get('deployment')
    if deployment is None or deployment['hold_id']!=hold:return
    for name,key in (('displaced-app','app_sha256'),('rollback-app','target_app_sha256')):
        path=saved/name
        if not os.path.lexists(path):continue
        expected=value[key] if name=='displaced-app' else deployment[key]
        if release.inventory(path)==expected:shutil.rmtree(path)


def perform(root,version,migrate_wiki=False,requeue_unverified=False,*,uv=None,desktop_config=None,skip_desktop=False,
            no_service=False,clients=None,no_hooks=False):
    _qualified_platform()
    release,info=_forward_release(root,version)
    support=installer().setup_helpers();support.host_context()
    initial=support.read(root/'state/install-transaction.json')
    if initial is not None and support.json_object(initial).get('stage')!='complete':
        raise ValueError('Initial installation is incomplete; use its exact recorded resume UUID before updating.')
    if requeue_unverified and not migrate_wiki:raise ValueError('--requeue-unverified requires --migrate-wiki.')
    if os.path.lexists(root/'state/service-transaction.json'):
        raise ValueError('An interrupted transaction exists; inspect service recover and use the exact --hold-id before a new update.')
    device=support.json_object(_read(root/'device.json'))
    if device.get('version')==info['version']:raise ValueError('This version is already installed; use client refresh or the recorded recovery flow.')
    if migrate_wiki and device.get('role')!='maintainer':raise PermissionError('--migrate-wiki requires the designated maintainer device.')
    old_python=Path(device['python'])
    if (not old_python.is_absolute() or not old_python.is_file() or not os.access(old_python,os.X_OK)
            or old_python.parent.resolve()!=old_python.parent):
        raise ValueError('Retained predecessor interpreter is missing, unexecutable or has a noncanonical parent.')
    support.service_python(old_python)
    inspected=controller(old_python,release,root,'service','inspect')
    if inspected.get('status')!='inspected':
        raise ValueError('Native ownership preflight did not complete; no hold created.')
    if _unregistered_user_service(inspected):
        if initial is None:
            raise ValueError('Completed initial install evidence is required for an unregistered upgrade.')
        registration={'kind':'linux','backend':'user-space','present':False}
        policy='absent' if no_service else 'first'
    else:
        if no_service:
            raise ValueError('--no-service upgrades require verified unregistered non-systemd Linux.')
        if inspected.get('task_present') is not True or inspected.get('task_enabled') is not True:
            raise ValueError('The owned task is absent or explicitly stopped; inspect it and explicitly start it before requesting this update.')
        registration=_registration_record(inspected.get('registration'),device)
        policy='registered'
    accepted=_predecessor(root,device,release,info)
    python=support.provision(release,root,uv)
    hold=secrets.token_hex(16);plan_path=root/'state'/('upgrade-client-plan-'+hold+'.json')
    options=(['--desktop-config',desktop_config] if desktop_config else [])+(['--skip-desktop-refresh'] if skip_desktop else [])
    if clients is not None:options+=['--clients',','.join(clients)]
    if no_hooks:options.append('--no-hooks')
    common=[*support.isolated_python(python,root),str(release/'install.py'),'--root',str(root),'--vault',device['vault'],
            '--reuse-python',str(python),'--refresh-clients',*options]
    result=support.run([*common,'--install-service','--validate-only','--plan-out',str(plan_path)],source_app=Path(__file__).resolve().parent/'app',
                       env=cli_environment(root),check=True,capture_output=True,text=True,encoding='utf-8',timeout=120)
    preflight=json.loads(result.stdout);plan_bytes=_read(plan_path)
    if preflight.get('plan_sha256')!=_digest(plan_bytes):raise ValueError('Client planning receipt changed before stop.')
    plan=support.validate_plan(support.json_object(plan_bytes),root,python)
    selected=support.selected_clients(clients,refresh=True,no_hooks=no_hooks)
    support.authorize_plan(plan,selected=selected,desktop=desktop_config,skip_desktop=skip_desktop)
    # Recovery consumes snapshot.json, never this duplicate plaintext plan.
    # Only replace a plan whose child receipt and full identity were verified.
    summary={key:value for key,value in plan.items() if key!='files'}
    summary.update(kind='non-executable client-plan summary',plan_sha256=_digest(plan_bytes),
                   files=[{key:row.get(key) for key in ('path','client','before_sha256','after_sha256')} for row in plan['files']])
    installer().atomic(plan_path,json.dumps(summary,indent=2)+'\n',expected_sha256=_digest(plan_bytes))
    previous={key:device[key] for key in ('release_root','python','version')}
    saved=None;stopped=False;switched=False;migration=None
    target_map=release_helpers().manifest_app(info,info['version'])
    images=_deployment_images(root,release,info,device,python,plan)
    native_hook_skips=[]
    if inspected.get('native',{}).get('backend')=='user-space':
        native_images,native_hook_skips=_native_images(python,release,root,policy)
        images+=native_images
    try:
        saved=snapshot(root,python,plan,controller_checkout=release,controller_identity=info.get('git_identity'),
                       registration=registration,accepted=accepted,target_app=target_map,hold=hold,service_policy=policy,images=images)
        sealed=_snapshot(root,saved);_,previous_after=_binding_images(saved,sealed)
        _check_preimages(root,saved,sealed)
        support.current(plan)
        if _digest(_read(root/'device.json'))!=plan.get('device_before_sha256'):raise ValueError('Device changed before stop; re-plan.')
        _receipt(root,{'status':'in progress','hold_id':hold,'snapshot':str(saved),'target':info['version'],
                       'predecessor':accepted[1]})
        controller(python,release,root,'service','hold','--hold-id',hold,'--snapshot',str(saved))
        _check_held(root,hold,saved,'prepared')
        installer().atomic(root/'state/previous-release.json',previous_after,expected_sha256=sealed['previous_binding']['before_sha256'])
        stop=controller(python,release,root,'service','stop','--hold-id',hold,'--wait-seconds','300')
        if stop.get('status')!='stopped' or not stop.get('empty_at_filetime'):raise ValueError('Controller supplied no verified quiescence.')
        stopped=True
        with _work_locks(root,timeout=300):
            _check_held(root,hold,saved,'stopped')
            _check_user_space_quiescence(root,release,python,sealed,hold)
            switched=True
            installed=installer().deploy(root,python,Path(device['vault']).resolve(),client_value=plan,
                                          source_sha256=info.get('sha256'),sealed=(saved,sealed),release_tools=release_helpers())
            if _digest(_read(root/'device.json'))!=plan['device_after_sha256']:
                raise ValueError('Deployed device differs from the sealed update plan; recovery required.')
            if policy!='absent':installed['service']=controller(python,release,root,'service','install','--hold-id',hold)
        if migrate_wiki:
            from filelock import FileLock
            with FileLock(str(root/'state/wiki-worker.lock'),timeout=30):
                migration=apply_wiki_migration(python,root,device['vault'],requeue_unverified,hold_id=hold)
        started=health=None
        if policy!='absent':
            started=controller(python,release,root,'service','start','--hold-id',hold,'--since',str(stop['empty_at_filetime']))
            health=wait_health(python,root,info['version'],checkout=release)
        overwritten=support.after_check(plan)
        if policy=='absent':_static_grok_acceptance(support,plan,overwritten,root,python)
        finished=_finish(root,saved,python,release,hold,info['version'])
    except CompletionPending:
        _receipt(root,{**_recovery_receipt_fields(root,hold,saved),'snapshot':str(saved),
                       'phase':'committed completion awaiting hold release','pending_work':'preserved'})
        raise
    except BaseException as error:
        if not stopped:
            if not (root/'state/service-transaction.json').exists():
                _receipt(root,{'status':'update deferred; no hold created','hold_id':hold,'snapshot':str(saved) if saved is not None else None,
                               'error_type':type(error).__name__,'pending_work':'preserved'})
                raise RuntimeError('Update deferred before a hold was created; original runtime retained. Inspect upgrade-status.json.') from error
            try:observed=json.loads(_read(root/'state/service-transaction.json'))
            except (OSError,ValueError,TypeError):observed=None
            if (not isinstance(observed,dict) or observed.get('hold_id')!=hold
                    or observed.get('root')!=str(root) or observed.get('snapshot')!=str(saved)):
                _receipt(root,{'status':'transaction inspection required','attempt_hold_id':hold,'snapshot':str(saved) if saved is not None else None,
                               'phase':'pre-stop hold ownership','error_type':type(error).__name__,'pending_work':'preserved'})
                raise RuntimeError('A different or unreadable transaction was observed; preserve it and inspect service recover before any further action.') from error
            try:
                _restore_previous_binding(root,saved)
                cancellation=controller(python,release,root,'service','cancel','--hold-id',hold)
            except Exception as recovery_error:
                _receipt(root,{**_recovery_receipt_fields(root,hold,saved),'snapshot':str(saved) if saved is not None else None,'phase':'stop refused; no runtime switch','error_type':type(error).__name__,'recovery_error_type':type(recovery_error).__name__,'pending_work':'preserved'})
                raise error
            _receipt(root,{'status':'update deferred; runtime not switched','hold_id':hold,'snapshot':str(saved) if saved is not None else None,'cancellation':cancellation,'error_type':type(error).__name__,'pending_work':'preserved'})
            raise RuntimeError('Update deferred before runtime switch. The exact pre-End refusal was verified; no recovery hold remains.') from error
        outcome=_recovery_receipt_fields(root,hold,saved)
        if outcome['status']!='recovery required':
            _receipt(root,{**outcome,'snapshot':str(saved) if saved is not None else None,'phase':'post-stop transaction outcome',
                           'error_type':type(error).__name__,'pending_work':'preserved'})
            raise RuntimeError('Transaction outcome is uncertain; preserve the current runtime and inspect '+outcome['transaction_evidence_path']+' and service recover before further action.') from error
        try:
            recovery=_restart_previous(root,saved,python,release,hold,restore_bytes=switched)
            _receipt(root,{'status':'update failed; previous runtime verified','hold_id':hold,'snapshot':str(saved) if saved is not None else None,'recovery':recovery,'error_type':type(error).__name__})
        except Exception as recovery_error:
            outcome=_recovery_receipt_fields(root,hold,saved)
            _receipt(root,{**outcome,'snapshot':str(saved) if saved is not None else None,'error_type':type(error).__name__,'recovery_error_type':type(recovery_error).__name__,'pending_work':'preserved'})
            if outcome['status']!='recovery required':
                raise RuntimeError('Recovery outcome is uncertain; preserve the current runtime and inspect '+outcome['transaction_evidence_path']+' and service recover before further action.') from recovery_error
            raise RuntimeError('Upgrade recovery is incomplete; preserve pending work and use the recorded exact recovery UUID.') from recovery_error
        proof='service absence verified' if policy!='registered' else 'previous runtime health verified'
        raise RuntimeError('Upgrade failed; '+proof+'. Inspect upgrade-status.json for preserved client edits.') from error
    _retire_displacement(root,saved,hold)
    clients=installed.get('client_result',{})
    deferred=clients.get('deferred',[])
    return _receipt(root,{'status':'updated','version':info['version'],'release_identity':info.get('git_identity'),'hold_id':hold,
        'snapshot':str(saved),'previous':previous,'predecessor':accepted[1],'service_policy':policy,
        'first_user_service':policy=='first','health':health,'start':started,'finish':finished,'wiki_migration':migration,
        'clients':clients,'client_overwrites_after_health':overwritten,'native_hook_skips':native_hook_skips,
        'client_acceptance':'pending refresh/named-client verification' if deferred or overwritten else 'configuration checked; named-client verification pending'})


if __name__=='__main__':main()
