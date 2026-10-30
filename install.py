"""Install a tagged checkout. Device data stays outside this software repository."""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

REPO=Path(__file__).resolve().parent
VERSION='0.1.8'

def private(path,directory=False):
    """Restrict a local state object before putting private content in it."""
    if os.name=='nt':
        account=os.environ.get('USERDOMAIN','.')+'\\'+os.environ.get('USERNAME','')
        flags='(OI)(CI)F' if directory else 'F'
        subprocess.run(['icacls.exe',str(path),'/inheritance:r','/grant:r',account+':'+flags,'SYSTEM:'+flags],
                       check=True,stdout=subprocess.DEVNULL)
    else: os.chmod(path,0o700 if directory else 0o600)

def private_dir(path):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    private(path,True)

def atomic(path,text):
    path=Path(path)
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
        os.replace(tmp,path)
    finally:
        if fd is not None: os.close(fd)
        tmp.unlink(missing_ok=True)

def backup(path,root):
    if path.exists():
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        target=root/'backups'/'client-configs'/digest
        private_dir(target.parent)
        if not target.exists(): atomic(target,path.read_bytes())

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
    import types
    path=Path(__file__).resolve().with_name('setup_runtime.py')
    module=types.ModuleType('_brain_setup_runtime');module.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
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


def deploy(root,python,vault,role='capture',configure_clients=False,client_value=None):
    old=validate(root,vault,python,False)
    if configure_clients and client_value is None:client_value=sealed_client_plan(root,python)
    if client_value is not None:setup_helpers().validate_plan(client_value,root,python)
    if client_value is not None and client_value.get('device_before_sha256'):
        if setup_helpers().digest(setup_helpers().read(root/'device.json'))!=client_value['device_before_sha256']:
            raise ValueError('Device configuration changed after planning; no deployment writes made.')
    token=_auth_recovery_token(root)
    for folder in ('state','logs','models','backups'): private_dir(root/folder)
    device=root/'device.json'
    settings=device_settings(old,vault,python,role)
    backup(device,root)
    atomic(device,json.dumps(settings,indent=2)+'\n')
    installed=root/'app'
    if installed.is_symlink() or installed.is_junction() or installed.resolve()!=root/'app':raise ValueError('Unsafe installed app path.')
    prepared=Path(tempfile.mkdtemp(prefix='app-',dir=root/'backups'))
    private_dir(prepared)
    payload=prepared/'app';payload.mkdir()
    for source in (REPO/'app').glob('*.py'):
        if source.is_symlink():raise ValueError('Linked application source is not deployable.')
        shutil.copy2(source,payload/source.name)
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
        token=secrets.token_urlsafe(48)
        atomic(access,json.dumps({'token':token})+'\n')
    if not (root/'state/access.json').exists():
        atomic(root/'state/access.json',json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest()})+'\n')
    for path in (access,root/'state/access.json',device): private(path)
    config=root/'state/config.json'
    if not config.exists():
        atomic(config,json.dumps({'projects':{'brain':{'path':str(vault),'mode':'local'}},'default_project':'brain','project_root':str(vault),
               'database_backend':'sqlite','semantic_search_enabled':True,'semantic_embedding_provider':'fastembed',
               'semantic_embedding_model':'bge-small-en-v1.5','semantic_embedding_dimensions':384,
               'semantic_embedding_cache_dir':str(root/'models'),'semantic_embedding_threads':4,'semantic_embedding_batch_size':8,
               'auto_update':False,'logfire_enabled':False,'logfire_send_to_logfire':False,'disable_permalinks':True,
               'ensure_frontmatter_on_sync':False,'format_on_save':False},indent=2)+'\n')
    if not (root/'state/.bmignore').exists():atomic(root/'state/.bmignore','.git/\n.obsidian/\n.trash/\n')
    client_result=setup_helpers().apply(client_value,atomic,backup,root) if client_value is not None else {'changed':[],'deferred':[],'client_acceptance':'not selected'}
    return {'status':'installed','root':str(root),'vault':str(vault),'role':settings['role'],'device_id':settings['device_id'],
            'clients':client_result['changed'],'client_result':client_result}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault',required=True); parser.add_argument('--root',default=str(Path.home()/'.local/share/second-brain'))
    parser.add_argument('--role',choices=['capture','maintainer'],default='capture')
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
    if existing and args.configure_clients:
        raise ValueError('Existing runtimes require the verified forward update.py flow on Windows; macOS/Linux must keep accepted v0.1.7. The legacy configure-client preflight is refused before service stop.')
    if existing and args.refresh_clients and not existing.get('device_id'):raise ValueError('Existing runtime has no device identity; review it before an upgrade.')
    if args.refresh_only:
        if not existing or existing.get('version')!=VERSION:
            raise ValueError('Client-only refresh requires this same installed release; use the verified forward updater for an older runtime.')
        if not args.reuse_python or Path(os.path.abspath(os.path.expanduser(args.reuse_python)))!=Path(existing['python']).absolute():
            raise ValueError('Client-only refresh must reuse the exact Python recorded in device.json; no provisioning or client changes made.')
    if existing and not args.validate_only and not args.refresh_only and not args.client_plan:
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
            child=subprocess.run([str(python),'-I',str(REPO/'install.py'),*child_arguments],check=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(exc.returncode) from None
        raise SystemExit(child.returncode)
    value=None
    if client_mode:
        if args.client_plan:
            raw=helpers.read(Path(args.client_plan))
            if helpers.digest(raw)!=args.plan_sha256:raise ValueError('Sealed client plan changed; no setup changes made.')
            value=helpers.validate_plan(helpers.json_object(raw),root,python)
            if value.get('refresh')!=args.refresh_clients:raise ValueError('Sealed plan mode mismatch.')
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
    if args.refresh_only:
        result={'status':'clients refreshed','client_result':helpers.apply(value,atomic,backup,root)}
    else:result=deploy(root,python,vault,args.role,False,client_value=value)
    if args.download_model:
        code='from fastembed import TextEmbedding; TextEmbedding(model_name="BAAI/bge-small-en-v1.5",cache_dir='+repr(str(root/'models'))+',threads=4)'
        env=os.environ.copy(); env.pop('HF_HUB_OFFLINE',None); env['HF_HUB_DISABLE_TELEMETRY']='1'
        subprocess.run([str(python),'-I','-c',code],env=env,check=True)
    if args.install_service:
        code='import runpy,sys;sys.path.insert(0,'+repr(str(root/'app'))+');runpy.run_path('+repr(str(root/'app/brain_cli.py'))+',run_name="__main__")'
        service=helpers.run([*helpers.isolated_python(python,root),'-c',code,'service','install'],source_app=REPO/'app',
                            env=helpers.child_environment(root),capture_output=True,text=True,encoding='utf-8',check=True,timeout=90)
        result['service']=json.loads(service.stdout)
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
