"""Install a tagged checkout. Device data stays outside this software repository."""
import sys
if __name__=='__main__' and not sys.flags.isolated:
    raise SystemExit('Run this installer with Python -I from the independently verified release.')
import argparse, hashlib, json, os, re, secrets, shutil, stat, subprocess, tempfile
from pathlib import Path

REPO=Path(__file__).resolve().parent
VERSION='0.1.8'
INDEX_IGNORE_TEXT='.git/\n.obsidian/\n.trash/\n'+'/02-Areas/AI-Memory/Wiki/Log-Archives/'+'[0-9a-f]'*64+'.md\n'
_SETUP_CACHE=None
_SETUP_SHA256=None

def _system_tool(name):
    import ctypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    function=api.GetSystemDirectoryW
    function.argtypes=[ctypes.c_wchar_p,ctypes.c_uint];function.restype=ctypes.c_uint
    buffer=ctypes.create_unicode_buffer(32768);count=function(buffer,len(buffer))
    if not 0<count<len(buffer):raise ValueError('Windows system directory cannot be verified.')
    path=Path(buffer.value)/name
    if not path.is_file():raise ValueError('Required Windows system tool is unavailable.')
    return str(path)

def _account_sid():
    """Use the actual process token, never caller-supplied USERNAME/USERDOMAIN."""
    import ctypes
    from ctypes import wintypes as wt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);security=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wt.HANDLE
    kernel.CloseHandle.argtypes=[wt.HANDLE];kernel.CloseHandle.restype=wt.BOOL
    kernel.LocalFree.argtypes=[wt.HLOCAL];kernel.LocalFree.restype=wt.HLOCAL
    security.OpenProcessToken.argtypes=[wt.HANDLE,wt.DWORD,ctypes.POINTER(wt.HANDLE)];security.OpenProcessToken.restype=wt.BOOL
    security.GetTokenInformation.argtypes=[wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD)];security.GetTokenInformation.restype=wt.BOOL
    security.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(wt.LPWSTR)];security.ConvertSidToStringSidW.restype=wt.BOOL
    token=wt.HANDLE();value=wt.LPWSTR()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
        size=wt.DWORD()
        if security.GetTokenInformation(token,1,None,0,ctypes.byref(size)) or ctypes.get_last_error()!=122 or not 0<size.value<=65536:
            raise ValueError('Current process token cannot be verified.')
        buffer=ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not security.ConvertSidToStringSidW(sid,ctypes.byref(value)):raise ctypes.WinError(ctypes.get_last_error())
        if not re.fullmatch(r'S-1-[0-9-]+',value.value or ''):raise ValueError('Invalid current process SID.')
        return '*'+value.value
    finally:
        if value:kernel.LocalFree(ctypes.cast(value,wt.HLOCAL))
        if token:kernel.CloseHandle(token)

def private(path,directory=False):
    """Restrict a local state object before putting private content in it."""
    if os.name=='nt':
        account=_account_sid()
        flags='(OI)(CI)F' if directory else 'F'
        subprocess.run([_system_tool('icacls.exe'),str(path),'/inheritance:r','/grant:r',account+':'+flags,'*S-1-5-18:'+flags],
                       check=True,stdout=subprocess.DEVNULL,timeout=30)
    else: os.chmod(path,0o700 if directory else 0o600)

def private_dir(path):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    private(path,True)

_OBSERVE_DESTINATION=object()

def atomic(path,text,*,expected_sha256=_OBSERVE_DESTINATION):
    path=Path(path)
    support=setup_helpers();before=support.read(path)
    if expected_sha256 is _OBSERVE_DESTINATION:expected_sha256=support.digest(before)
    if support.digest(before)!=expected_sha256:raise support.ConcurrentWriteError('Concurrent edit before staging; original preserved.')
    path.parent.mkdir(parents=True,exist_ok=True)
    mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    tmp=Path(name)
    try:
        # mkstemp is owner-only on POSIX. On Windows restrict its empty ACL before
        # writing, since the CRT mode bits alone do not enforce DACL privacy.
        if os.name=='nt': private(tmp)
        options={} if isinstance(text,bytes) else {'encoding':'utf-8','newline':''}
        with os.fdopen(fd,'wb' if isinstance(text,bytes) else 'w',**options) as output:
            fd=None
            output.write(text); output.flush(); os.fsync(output.fileno())
        if os.name!='nt': os.chmod(tmp,mode)
        # Recheck after the potentially slow ACL/fsync operations. Existing
        # files still need a cooperative quiet window; rename is not OS CAS.
        if support.digest(support.read(path))!=expected_sha256:
            raise support.ConcurrentWriteError('Concurrent edit during staging; original preserved.')
        if expected_sha256 is None:
            try:
                if os.name=='nt':os.rename(tmp,path)
                else:os.link(tmp,path)
            except FileExistsError as error:
                raise support.ConcurrentWriteError('New destination appeared; original preserved.') from error
        else:os.replace(tmp,path)
    finally:
        if fd is not None: os.close(fd)
        tmp.unlink(missing_ok=True)

def backup(path,root):
    if path.exists():
        raw=setup_helpers().read(path);digest=hashlib.sha256(raw).hexdigest()
        target=root/'backups'/'client-configs'/digest
        private_dir(target.parent)
        if target.exists():
            if setup_helpers().read(target)!=raw:raise ValueError('Client backup integrity failed; preserve it for review.')
        else:atomic(target,raw,expected_sha256=None)

def _same_toml_value(left,right):
    """Compare parsed owner values, including TOML's non-finite floats."""
    if type(left) is not type(right): return False
    if isinstance(left,dict):
        return left.keys()==right.keys() and all(_same_toml_value(value,right[key]) for key,value in left.items())
    if isinstance(left,list):
        return len(left)==len(right) and all(_same_toml_value(a,b) for a,b in zip(left,right))
    if isinstance(left,float):
        import math
        if math.isnan(left): return math.isnan(right)
    return left==right

def _codex_toml_plan(content,spec):
    """Replace one logical server with a locked parser; never write or spawn."""
    import tomllib
    original=tomllib.loads(content)
    servers=original.get('mcp_servers',{})
    if not isinstance(servers,dict): raise ValueError('Invalid TOML MCP parent object; no client configuration changed.')
    try:
        import tomlkit
    except ImportError as exc:
        raise ValueError('Client configuration requires locked tomlkit==0.15.1. Run install.py using the synced project Python environment; no client configuration changed.') from exc
    if getattr(tomlkit,'__version__',None)!='0.15.1':
        raise ValueError('Client configuration requires locked tomlkit==0.15.1; no client configuration changed.')
    try: document=tomlkit.parse(content)
    except tomlkit.exceptions.ParseError as exc:
        raise ValueError('Cannot safely parse this TOML configuration; no client configuration changed.') from exc
    prior=servers.get('second-brain',{})
    if not isinstance(prior,dict):prior={}
    managed={'startup_timeout_sec':60,'tool_timeout_sec':120,**prior,**spec}
    if 'mcp_servers' not in document: document['mcp_servers']={}
    if 'second-brain' in document['mcp_servers']: del document['mcp_servers']['second-brain']
    document['mcp_servers']['second-brain']=managed
    result=tomlkit.dumps(document)
    expected={**original,'mcp_servers':{**servers,'second-brain':managed}}
    if not _same_toml_value(expected,tomllib.loads(result)):
        raise ValueError('TOML parser changed unrelated owner settings; no client configuration changed.')
    return result

def setup_helpers():
    # -I excludes the checkout from import search. Load this fixed sibling only;
    # never import a module by an untracked checkout-controlled search name.
    global _SETUP_CACHE
    import types
    path=Path(__file__).resolve().with_name('setup_runtime.py')
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if (_SETUP_SHA256 is not None and sha!=_SETUP_SHA256) or (_SETUP_CACHE is not None and _SETUP_CACHE[0]!=sha):
        raise ValueError('Setup helper bytes changed; preserve the release for review.')
    if _SETUP_CACHE is not None:return _SETUP_CACHE[1]
    module=types.ModuleType('_brain_setup_runtime');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    _SETUP_CACHE=(sha,module)
    return module


def sealed_client_plan(root,python,home=None,**options):
    return setup_helpers().plan(root,python,(REPO/'docs/AI-PROTOCOL.md').read_text(encoding='utf-8'),
                                _codex_toml_plan,home=home,**options)


def client_plan(root,python,home=None):
    # Compatibility API for an explicitly requested full client install.
    return [(Path(row['path']),row['after']) for row in sealed_client_plan(root,python,home)['files']]


def clients(root,python,home=None):
    value=sealed_client_plan(root,python,home)
    result=setup_helpers().apply(value,atomic,backup,root)
    if result['deferred']:raise ValueError('Client settings changed during installation; re-plan the deferred targets.')
    return result['changed']


def _auth_recovery_token(root):
    """Validate existing auth and recover only a missing server hash."""
    access=root/'state/client-access.json'
    server=root/'state/access.json'
    if server.exists() and not access.exists():
        raise ValueError('Existing server credential has no client credential; refusing to replace it.')
    if not access.exists(): return None
    try:
        data=setup_helpers().json_object(setup_helpers().read(access))
        token=data.get('token') if isinstance(data,dict) else None
        if not isinstance(token,str) or not token: raise ValueError('Invalid client credential.')
        expected=hashlib.sha256(token.encode('utf-8')).hexdigest()
        if server.exists():
            data=setup_helpers().json_object(setup_helpers().read(server))
            actual=data.get('token_sha256') if isinstance(data,dict) else None
            if not isinstance(actual,str) or actual!=expected: raise ValueError('Invalid server credential.')
    except (ValueError,UnicodeError) as exc:
        raise ValueError('Invalid or mismatched existing authentication; no credentials changed.') from exc
    return token

def validate(root,vault,python,configure_clients=False):
    if root==vault or root.is_relative_to(vault) or vault.is_relative_to(root):
        raise ValueError('Runtime and vault must be separate, non-nested directories.')
    setup_helpers().runtime_paths(root)
    device=root/'device.json'
    old=setup_helpers().json_object(setup_helpers().read(device)) if device.exists() else {}
    if not isinstance(old,dict): raise ValueError('Invalid existing device configuration.')
    if old.get('vault') and Path(old['vault']).expanduser().resolve()!=vault.resolve():
        raise ValueError('Changing --vault on an existing runtime is unsupported; create a new runtime and rebuild its local index.')
    config=root/'state/config.json'
    if config.exists():
        indexed=setup_helpers().json_object(setup_helpers().read(config))
        if not isinstance(indexed,dict): raise ValueError('Invalid existing retrieval configuration.')
        projects=indexed.get('projects',{})
        if not isinstance(projects,dict): raise ValueError('Invalid existing project configuration.')
        project=projects.get('brain',{})
        if not isinstance(project,dict): raise ValueError('Invalid existing brain project configuration.')
        for existing in (indexed.get('project_root'),project.get('path')):
            if existing and Path(existing).expanduser().resolve()!=vault.resolve():
                raise ValueError('Existing index targets another vault; create a new runtime and rebuild its local index.')
    _auth_recovery_token(root)
    if configure_clients: client_plan(root,python)
    return old

def device_settings(old,vault,python,role='capture'):
    return {**old,'vault':str(vault),'python':str(python),'release_root':str(REPO),
              'role':old.get('role',role),'device_id':old.get('device_id',secrets.token_hex(8)),
              'port':old.get('port',8765),'timezone':old.get('timezone','Asia/Karachi'),'version':VERSION}


def retrieval_settings(root,vault):
    return {'projects':{'brain':{'path':str(vault),'mode':'local'}},'default_project':'brain','project_root':str(vault),
            'database_backend':'sqlite','semantic_search_enabled':True,'semantic_embedding_provider':'fastembed',
            'semantic_embedding_model':'bge-small-en-v1.5','semantic_embedding_dimensions':384,
            'semantic_embedding_cache_dir':str(root/'models'),'semantic_embedding_threads':4,'semantic_embedding_batch_size':8,
            'auto_update':False,'logfire_enabled':False,'logfire_send_to_logfire':False,'disable_permalinks':True,
            'ensure_frontmatter_on_sync':False,'format_on_save':False}


def deploy(root,python,vault,role='capture',configure_clients=False,client_value=None,*,device_value=None,auth_value=None,source_sha256=None):
    old=validate(root,vault,python,False)
    if configure_clients and client_value is None:client_value=sealed_client_plan(root,python)
    if client_value is not None:setup_helpers().validate_plan(client_value,root,python)
    if client_value is not None and client_value.get('device_before_sha256'):
        if setup_helpers().digest(setup_helpers().read(root/'device.json'))!=client_value['device_before_sha256']:
            raise ValueError('Device configuration changed after planning; no deployment writes made.')
    token=_auth_recovery_token(root)
    for folder in ('state','logs','models','backups'): private_dir(root/folder)
    device=root/'device.json'
    settings=device_value if device_value is not None else device_settings(old,vault,python,role)
    backup(device,root)
    installed=root/'app'
    if installed.is_symlink() or installed.is_junction() or installed.resolve()!=root/'app':raise ValueError('Unsafe installed app path.')
    prepared=Path(tempfile.mkdtemp(prefix='app-',dir=root/'backups'))
    private_dir(prepared)
    payload=prepared/'app';payload.mkdir()
    for source in (REPO/'app').glob('*.py'):
        if source.is_symlink():raise ValueError('Linked application source is not deployable.')
        shutil.copy2(source,payload/source.name)
    if source_sha256 is not None:
        expected={name.removeprefix('app/'):sha for name,sha in source_sha256.items() if name.startswith('app/')}
        actual={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in payload.iterdir()}
        if not expected or actual!=expected:raise ValueError('Prepared application differs from the verified source; runtime not switched.')
    atomic(device,json.dumps(settings,indent=2)+'\n')
    if installed.exists():
        old_digest=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(installed.glob('*.py')))).hexdigest()[:16]
        target=root/'backups'/'app'/old_digest
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():target=target.with_name(old_digest+'-'+secrets.token_hex(8))
        if not target.resolve().is_relative_to(root/'backups/app'):raise ValueError('Unsafe app preservation path.')
        os.replace(installed,target)
    os.replace(payload,installed);prepared.rmdir()
    access=root/'state/client-access.json'
    if not access.exists():
        token=auth_value if auth_value is not None else secrets.token_urlsafe(48)
        atomic(access,json.dumps({'token':token})+'\n')
    if not (root/'state/access.json').exists():
        atomic(root/'state/access.json',json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest()})+'\n')
    for path in (access,root/'state/access.json',device): private(path)
    config=root/'state/config.json'
    if not config.exists():
        atomic(config,json.dumps(retrieval_settings(root,vault),indent=2)+'\n')
    if not (root/'state/.bmignore').exists():atomic(root/'state/.bmignore',INDEX_IGNORE_TEXT)
    client_result=setup_helpers().apply(client_value,atomic,backup,root) if client_value is not None else {'changed':[],'deferred':[],'client_acceptance':'not selected'}
    return {'status':'installed','root':str(root),'vault':str(vault),'role':settings['role'],'device_id':settings['device_id'],
            'clients':client_result['changed'],'client_result':client_result}

def _fresh_source():
    paths=[REPO/name for name in ('install.py','setup_runtime.py','pyproject.toml','uv.lock','release-manifest.json')]
    paths.extend(sorted((REPO/'app').glob('*.py')))
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError('Initial install source is unavailable or linked.')
    return {path.relative_to(REPO).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _fresh_identity(root,vault,python,args):
    return {'root':str(root),'vault':str(vault),'python':os.path.normcase(str(python)),'release_root':str(REPO),
            'version':VERSION,'role':args.role,'download_model':args.download_model,
            'install_service':args.install_service,'configure_clients':args.configure_clients,
            'clients':sorted(set(args.client)) if args.client else None,'include_hooks':args.include_hooks,
            'desktop_config':str(Path(args.desktop_config).expanduser().absolute()) if args.desktop_config else None,
            'skip_desktop_refresh':args.skip_desktop_refresh,'source_sha256':_fresh_source()}


def _load_fresh(root,vault,python,args):
    value=setup_helpers().json_object(setup_helpers().read(root/'state/install-transaction.json'))
    if (not isinstance(value,dict) or value.get('format')!=1 or not re.fullmatch('[a-f0-9]{32}',value.get('install_id','')) or value.get('install_id')!=args.resume_install
            or value.get('stage') not in ('prepared','deploying','deployed','model','service-intent','service-done')
            or value.get('identity')!=_fresh_identity(root,vault,python,args)):
        raise ValueError('Initial install resume identity changed or is complete; no setup changes made.')
    return value


def _fresh_check(root,value):
    support=setup_helpers();support.runtime_paths(root)
    if (root/'state/service-transaction.json').exists():
        raise ValueError('A service update owns this runtime; initial install resume is refused.')
    names=('device.json','state/client-access.json','state/access.json','state/config.json','state/.bmignore')
    if not isinstance(value.get('writes'),dict) or set(value['writes'])!=set(names):
        raise ValueError('Invalid initial install artifact identity.')
    device=json.loads(value['writes']['device.json']);identity=value['identity']
    if (not isinstance(device,dict) or any(device.get(name)!=identity[name] for name in ('vault','role','version','release_root'))
            or os.path.normcase(device.get('python',''))!=identity['python'] or not device.get('device_id')):
        raise ValueError('Planned initial device disagrees with its transaction identity.')
    token=json.loads(value['writes']['state/client-access.json']).get('token')
    access=json.loads(value['writes']['state/access.json'])
    if not isinstance(token,str) or not token or not isinstance(access,dict) or access.get('token_sha256')!=hashlib.sha256(token.encode()).hexdigest():
        raise ValueError('Planned initial authentication is inconsistent; preserve its journal.')
    for name,text in value['writes'].items():
        raw=support.read(root/name)
        if raw is None and value['stage'] not in ('prepared','deploying'):
            raise ValueError('Published initial artifact is missing: '+name+'; preserve the deletion and journal for review.')
        if raw is not None and raw!=text.encode('utf-8'):
            raise ValueError('Initial install artifact changed; preserve owner edits and inspect the journal.')
    app=root/'app'
    if not app.is_dir() and value['stage'] not in ('prepared','deploying'):
        raise ValueError('Published initial application is missing; preserve the deletion and journal for review.')
    if app.exists():
        if any(path.is_symlink() or path.is_junction() for path in app.rglob('*')):
            raise ValueError('Linked initial application payload is refused.')
        actual={path.relative_to(app).as_posix():support.digest(support.read(path))
                for path in app.rglob('*') if path.is_file() and '__pycache__' not in path.relative_to(app).parts}
        expected={name.removeprefix('app/'):sha for name,sha in value['identity']['source_sha256'].items() if name.startswith('app/')}
        if actual!=expected:raise ValueError('Initial application payload changed; resume is refused.')
    plan=value.get('client_plan')
    if plan is not None and value['stage'] in ('prepared','deploying'):
        if os.path.normcase(plan.get('python',''))!=identity['python']:raise ValueError('Initial client interpreter changed.')
        support.validate_plan(plan,root,Path(plan['python']))
        for row in plan['files']:
            if support.digest(support.read(Path(row['path']))) not in (row['before_sha256'],row['after_sha256']):
                raise ValueError('Client settings changed during initial setup; edits preserved, resume refused.')


def _run_fresh(root,python,vault,args,client_value,finish):
    """Resume only this explicitly admitted initial setup; never an upgrade."""
    from filelock import FileLock
    private_dir(root/'state')
    with FileLock(str(root/'state/install.lock'),timeout=0):
        path=root/'state/install-transaction.json'
        if args.resume_install:
            value=_load_fresh(root,vault,python,args)
        else:
            if path.exists() or any((root/name).exists() for name in ('device.json','app','state/client-access.json','state/access.json','state/config.json','state/.bmignore','state/service-transaction.json')):
                raise ValueError('Initial setup requires a fresh runtime; preserve existing state and use its recorded recovery flow.')
            settings=device_settings({},vault,python,args.role)
            token=secrets.token_urlsafe(48)
            value={'format':1,'install_id':secrets.token_hex(16),'identity':_fresh_identity(root,vault,python,args),
                   'stage':'prepared','client_plan':client_value,'writes':{
                       'device.json':json.dumps(settings,indent=2)+'\n',
                       'state/client-access.json':json.dumps({'token':token})+'\n',
                       'state/access.json':json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest()})+'\n',
                       'state/config.json':json.dumps(retrieval_settings(root,vault),indent=2)+'\n',
                       'state/.bmignore':INDEX_IGNORE_TEXT}}
            if client_value is not None:setup_helpers().current(client_value)
            # Owner-only planned auth permits exact recovery even if interrupted
            # before either credential file exists; never export this journal.
            atomic(path,json.dumps(value,indent=2)+'\n')
        _fresh_check(root,value)
        if value['stage'] in ('prepared','deploying'):
            value['stage']='deploying';atomic(path,json.dumps(value,indent=2)+'\n')
            plan=value.get('client_plan')
            if plan is not None:
                plan={**plan,'files':[row for row in plan['files']
                    if setup_helpers().digest(setup_helpers().read(Path(row['path'])))!=row['after_sha256']]}
            result=deploy(root,python,vault,args.role,client_value=plan,
                          device_value=json.loads(value['writes']['device.json']),
                          auth_value=json.loads(value['writes']['state/client-access.json'])['token'],
                          source_sha256=value['identity']['source_sha256'])
            if result['client_result']['deferred']:result['status']='installed; client configuration deferred'
            value['stage']='deployed';value['result']=result;atomic(path,json.dumps(value,indent=2)+'\n')
        else:result=value['result']
        def checkpoint(stage):
            value['stage']=stage;atomic(path,json.dumps(value,indent=2)+'\n')
        finish(result,checkpoint,value['stage'])
        value['stage']='complete'
        value['artifact_sha256']={name:hashlib.sha256(text.encode()).hexdigest() for name,text in value.pop('writes').items()}
        value.pop('client_plan',None)
        atomic(path,json.dumps(value,indent=2)+'\n')
        return {**result,'install_id':value['install_id']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault',required=True); parser.add_argument('--root',default=str(Path.home()/'.local/share/second-brain'))
    parser.add_argument('--role',choices=['capture','maintainer'],default='capture')
    parser.add_argument('--resume-install',help='Resume only the matching incomplete initial install UUID, using its exact retained Python and options.')
    parser.add_argument('--inspect-install',action='store_true',help='Show only non-secret initial setup identity/options and its recovery UUID.')
    parser.add_argument('--abandon-install',help='Abandon a matching incomplete initial setup only if no runtime artifact was published.')
    parser.add_argument('--configure-clients',action='store_true'); parser.add_argument('--install-service',action='store_true')
    parser.add_argument('--refresh-clients',action='store_true',help='Refresh only existing exact owned entries; create no new client integration or hook.')
    parser.add_argument('--client',action='append',choices=['claude-code','claude-desktop','codex','hooks'])
    parser.add_argument('--include-hooks',action='store_true',help='Explicitly opt into optional Claude hooks during fresh client configuration.')
    parser.add_argument('--desktop-config');parser.add_argument('--skip-desktop-refresh',action='store_true')
    parser.add_argument('--client-plan');parser.add_argument('--plan-sha256')
    parser.add_argument('--plan-out',help='With --validate-only, write exactly one private sealed client plan; runtime/client/service files remain unchanged.')
    parser.add_argument('--refresh-only',action='store_true',help='Refresh client configuration without redeploying or controlling the service.')
    parser.add_argument('--uv',help='Absolute host uv executable; ignored with --reuse-python.')
    parser.add_argument('--download-model',action='store_true'); parser.add_argument('--reuse-python')
    parser.add_argument('--validate-only',action='store_true',help='Check configuration without changing runtime, clients or services.')
    args=parser.parse_args(); root=Path(args.root).expanduser().resolve(); vault=Path(args.vault).expanduser().resolve()
    if sum(bool(value) for value in (args.resume_install,args.inspect_install,args.abandon_install))>1:
        parser.error('Choose one initial-install recovery action.')
    if args.abandon_install and sys.platform!='win32':
        raise ValueError('v0.1.8 mutating recovery is qualified for Windows only; no setup changes made.')
    if args.inspect_install or args.abandon_install:
        helpers=setup_helpers();helpers.host_context();helpers.runtime_paths(root)
        path=root/'state/install-transaction.json';record=helpers.json_object(helpers.read(path))
        if (record.get('format')!=1 or record.get('identity',{}).get('root')!=str(root)
                or record.get('identity',{}).get('vault')!=str(vault)
                or not re.fullmatch('[a-f0-9]{32}',record.get('install_id',''))):raise ValueError('No matching initial-install record.')
        if args.inspect_install:
            print(json.dumps({'install_id':record['install_id'],'stage':record['stage'],'options':record['identity'],
                              'guidance':'Resume using this UUID and the same options; never share the private transaction file.'},indent=2));return
        from filelock import FileLock
        with FileLock(str(root/'state/install.lock'),timeout=0):
            record=helpers.json_object(helpers.read(path))
            if (record.get('install_id')!=args.abandon_install or record.get('stage') not in ('prepared','deploying')
                    or any((root/name).exists() for name in ('device.json','app','state/client-access.json','state/access.json','state/config.json','state/.bmignore','state/service-transaction.json'))):
                raise ValueError('Only an unpublished initial setup can be abandoned; runtime and private evidence preserved.')
            target=root/'state/install-transactions'/(record['install_id']+'.json')
            if target.exists():raise ValueError('Initial setup UUID was already archived.')
            private_dir(target.parent)
            atomic(target,json.dumps({'format':1,'install_id':record['install_id'],'identity':record['identity'],'stage':'abandoned before publication'})+'\n')
            path.unlink()
            print(json.dumps({'status':'abandoned before publication','install_id':record['install_id']}));return
    if args.resume_install and (not args.reuse_python or args.validate_only or args.refresh_clients or args.refresh_only or args.client_plan):
        parser.error('--resume-install requires exact --reuse-python and the original initial-setup options, without refresh/validation/client-plan modes.')
    if args.configure_clients and args.refresh_clients:parser.error('Choose configure or refresh clients.')
    client_mode=args.configure_clients or args.refresh_clients
    if args.plan_out and (not args.validate_only or not client_mode):parser.error('--plan-out requires --validate-only and a client mode.')
    if args.client_plan and (not client_mode or not args.plan_sha256):parser.error('--client-plan requires a client mode and its --plan-sha256.')
    if args.refresh_only and (not args.refresh_clients or args.install_service or args.download_model):parser.error('--refresh-only requires --refresh-clients and cannot deploy models/services.')
    if sys.platform!='win32' and (not args.validate_only or args.plan_out):
        raise ValueError('v0.1.8 runtime/service setup is qualified for Windows only. Keep the accepted v0.1.7 on macOS/Linux until native qualification; no setup changes made.')
    helpers=setup_helpers()
    if not args.validate_only or client_mode or args.install_service:helpers.host_context()
    if not vault.is_dir(): parser.error('Clone/open the existing vault first; --vault must exist.')
    # Refuse before provisioning: the checkout's venv may power this live device.
    existing=validate(root,vault,Path(args.reuse_python or sys.executable),False)
    resumed=_load_fresh(root,vault,Path(os.path.abspath(os.path.expanduser(args.reuse_python))),args) if args.resume_install else None
    if resumed is not None:_fresh_check(root,resumed)
    if existing and args.configure_clients and resumed is None:
        raise ValueError('Existing runtimes require the verified forward update.py flow on Windows; macOS/Linux must keep accepted v0.1.7. The legacy configure-client preflight is refused before service stop.')
    if existing and args.refresh_clients and not existing.get('device_id'):raise ValueError('Existing runtime has no device identity; review it before an upgrade.')
    if args.refresh_only:
        if not existing or existing.get('version')!=VERSION:
            raise ValueError('Client-only refresh requires this same installed release; use the verified forward updater for an older runtime.')
        if not args.reuse_python or Path(os.path.abspath(os.path.expanduser(args.reuse_python)))!=Path(existing['python']).absolute():
            raise ValueError('Client-only refresh must reuse the exact Python recorded in device.json; no provisioning or client changes made.')
        refresh_device_sha256=helpers.digest(helpers.read(root/'device.json'))
    if existing and not args.validate_only and not args.refresh_only and resumed is None:
        raise ValueError('Use the approved update.py flow for an existing runtime; direct setup must not replace an active installation.')
    if args.reuse_python: python=Path(os.path.abspath(os.path.expanduser(args.reuse_python)))
    else:
        if args.validate_only: parser.error('--validate-only requires --reuse-python.')
        python=helpers.provision(REPO,root,args.uv)
    if not python.is_file(): parser.error('The selected Python executable does not exist.')
    if args.install_service:helpers.service_python(python)
    if not args.reuse_python:
        # uv sync prepares the locked dependencies; it does not switch this
        # bootstrap interpreter. Preserve the venv's lexical symlink path and
        # use the existing reuse flag to terminate the handoff after one child.
        child_arguments=list(sys.argv[1:])
        terminator=child_arguments.index('--') if '--' in child_arguments else len(child_arguments)
        child_arguments[terminator:terminator]=['--reuse-python',str(python)]
        try:
            child=helpers.run([str(python),'-I',str(REPO/'install.py'),*child_arguments],source_app=REPO/'app',
                              capture_output=True,text=True,encoding='utf-8',check=True,timeout=3600)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(exc.returncode) from None
        if child.stdout:print(child.stdout,end='')
        if child.stderr:print(child.stderr,end='',file=sys.stderr)
        raise SystemExit(child.returncode)
    value=None
    if resumed is not None:value=resumed.get('client_plan')
    elif client_mode:
        if args.client_plan:
            raw=helpers.read(Path(args.client_plan))
            if helpers.digest(raw)!=args.plan_sha256:raise ValueError('Sealed client plan changed; no setup changes made.')
            value=helpers.validate_plan(helpers.json_object(raw),root,python)
            if value.get('refresh')!=args.refresh_clients:raise ValueError('Sealed plan mode mismatch.')
            selected=args.client or (list(helpers.CLIENTS) if args.refresh_clients else ['claude-code','claude-desktop','codex'])
            if args.include_hooks:selected=[*selected,'hooks']
            helpers.authorize_plan(value,selected=selected,desktop=args.desktop_config,skip_desktop=args.skip_desktop_refresh)
        else:
            selected=args.client or (['claude-code','claude-desktop','codex','hooks'] if args.refresh_clients else ['claude-code','claude-desktop','codex'])
            if args.include_hooks:selected=[*selected,'hooks']
            value=sealed_client_plan(root,python,selected=selected,refresh=args.refresh_clients,desktop=args.desktop_config,skip_desktop=args.skip_desktop_refresh)
            if existing:
                value['device_before_sha256']=helpers.digest(helpers.read(root/'device.json'))
                settings=device_settings(existing,vault,python,args.role)
                value['device_after_sha256']=helpers.digest((json.dumps(settings,indent=2)+'\n').encode('utf-8'))
    if args.validate_only:
        if value is not None:helpers.current(value)
        receipt={'status':'validated','client_paths':[row['path'] for row in value['files']] if value else []}
        if args.plan_out:
            target=Path(args.plan_out).expanduser().absolute()
            if target.exists():raise ValueError('Use a new private plan output path; existing files are preserved.')
            payload=json.dumps(value,indent=2,ensure_ascii=False)+'\n'
            atomic(target,payload)
            receipt.update(plan=str(target),plan_sha256=helpers.digest(payload.encode('utf-8')))
        print(json.dumps(receipt));return
    def finish(result,checkpoint,stage):
        if args.download_model and stage=='deployed':
            code='from fastembed import TextEmbedding; TextEmbedding(model_name="BAAI/bge-small-en-v1.5",cache_dir='+repr(str(root/'models'))+',threads=4)'
            env=os.environ.copy(); env.pop('HF_HUB_OFFLINE',None); env['HF_HUB_DISABLE_TELEMETRY']='1'
            helpers.run([str(python),'-I','-c',code],source_app=REPO/'app',env=env,
                        capture_output=True,text=True,encoding='utf-8',check=True,timeout=1800)
            checkpoint('model')
        if args.install_service:
            code='import runpy,sys;sys.path.insert(0,'+repr(str(root/'app'))+');runpy.run_path('+repr(str(root/'app/brain_cli.py'))+',run_name="__main__")'
            def control(action):
                service=helpers.run([*helpers.isolated_python(python,root),'-c',code,'service',action],source_app=REPO/'app',
                                    env=helpers.child_environment(root),capture_output=True,text=True,encoding='utf-8',check=True,timeout=90)
                return json.loads(service.stdout)
            if stage in ('service-intent','service-done'):
                # inspect positively validates the native Task's action,
                # interpreter, working directory and principal via _windows_task.
                inspected=control('inspect')
                if inspected.get('status')!='inspected' or type(inspected.get('task_present')) is not bool:
                    raise ValueError('Initial service registration cannot be verified; journal preserved.')
                if inspected['task_present']:
                    if inspected.get('task_enabled') is not True:
                        raise ValueError('The owned initial Task was disabled; preserve that explicit owner action.')
                    result['service']={'status':'recovered owned registration'}
                    checkpoint('service-done');return
                if stage=='service-done':
                    raise ValueError('The recorded initial Task was removed; preserve that explicit owner action.')
            checkpoint('service-intent')
            result['service']=control('install')
            checkpoint('service-done')
    if args.refresh_only:
        if (root/'state/service-transaction.json').exists():raise ValueError('A service update owns this runtime; client refresh is refused.')
        journal_bytes=helpers.read(root/'state/install-transaction.json')
        if journal_bytes is not None and helpers.json_object(journal_bytes).get('stage')!='complete':raise ValueError('Initial install is incomplete; resume its recorded UUID before client refresh.')
        from filelock import FileLock
        with FileLock(str(root/'state/update.lock'),timeout=0):
            if (helpers.digest(helpers.read(root/'device.json'))!=refresh_device_sha256
                    or (root/'state/service-transaction.json').exists()):
                raise ValueError('Runtime changed or an update owns it; re-plan client refresh.')
            current_initial=helpers.read(root/'state/install-transaction.json')
            if current_initial is not None and helpers.json_object(current_initial).get('stage')!='complete':
                raise ValueError('Initial setup is incomplete; client refresh refused.')
            result={'status':'clients refreshed','client_result':helpers.apply(value,atomic,backup,root)}
    else:result=_run_fresh(root,python,vault,args,value,finish)
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
