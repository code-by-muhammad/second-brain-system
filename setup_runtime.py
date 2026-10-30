"""Bootstrap host and sealed client plans. No runtime imports or side effects."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid


def run(command,*,source_app,**options):
    """Installer children must release their complete private Windows Job first."""
    check=options.pop('check',False)
    if os.name=='nt':
        import types
        path=Path(source_app)/'brain_windows_process.py'
        module=sys.modules.get('brain_windows_process')
        if module is None:
            module=types.ModuleType('brain_windows_process');module.__file__=str(path)
            exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
            sys.modules['brain_windows_process']=module
        elif Path(module.__file__).resolve()!=path.resolve():raise ValueError('Native child runner origin mismatch; use an isolated setup process.')
        result=module.run(command,**options)
        if check:result.check_returncode()
        return result
    # Each POSIX child has a dedicated group. On timeout/cancellation signal it
    # before reaping its leader; native POSIX descendant qualification is separate.
    import signal
    timeout=options.pop('timeout',None)
    capture=options.pop('capture_output',False)
    if capture:options.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    child=subprocess.Popen(command,start_new_session=True,**options)
    try:stdout,stderr=child.communicate(timeout=timeout)
    except BaseException:
        try:os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.communicate(timeout=5)
        raise
    result=subprocess.CompletedProcess(command,child.returncode,stdout,stderr)
    if check:result.check_returncode()
    return result


def child_environment(root):
    device=json_object(read(Path(root)/'device.json'))
    environment={key:value for key,value in os.environ.items() if not key.upper().startswith(('BRAIN_','BASIC_MEMORY_','PYTHON'))}
    environment.update(BRAIN_SYSTEM_ROOT=str(root),BRAIN_STATE=str(Path(root)/'state'),BRAIN_VAULT=device['vault'],
                       BRAIN_PORT=str(device.get('port',8765)),PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    return environment


def isolated_python(python,root):
    """Fresh empty cache prefix prevents reading cached untracked app bytecode."""
    import tempfile
    runtime_paths(root)
    parent=Path(root)/'state/import-cache'
    if parent.is_symlink() or parent.is_junction():raise ValueError('Linked import-cache directory is refused.')
    parent.mkdir(parents=True,exist_ok=True)
    cache=Path(tempfile.mkdtemp(prefix='run-',dir=parent))
    return [str(python),'-I','-B','-X','utf8','-X','pycache_prefix='+str(cache)]


def runtime_paths(root):
    root=Path(root)
    for name in ('app','state','models','backups','logs'):
        target=root/name
        if target.is_symlink() or target.is_junction() or target.resolve()!=target:raise ValueError('Linked/noncanonical runtime folder is unsafe: '+name)

MAX_CONFIG=64*1024*1024
BEGIN='<!-- BEGIN SECOND-BRAIN -->'
END='<!-- END SECOND-BRAIN -->'
CLIENTS=('claude-code','claude-desktop','codex','hooks')


def host_context():
    if os.name!='nt':return {'packaged':False,'platform':sys.platform}
    size=ctypes.c_uint32(0)
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    function=api.GetCurrentPackageFullName
    function.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_wchar_p];function.restype=ctypes.c_long
    result=function(ctypes.byref(size),None)
    if result==15700:return {'packaged':False,'platform':'win32'}
    if result==122:raise ValueError('Packaged app context: use an ordinary host PowerShell/Terminal before provisioning Python, clients or services.')
    raise ValueError('Cannot establish host package identity; no setup changes made.')


def service_python(python):
    python=Path(python)
    if os.name!='nt':return
    if 'windowsapps' in {part.lower() for part in python.parts}:raise ValueError('Store Python is not qualified for this service; use host uv-managed Python.')
    config=python.parent.parent/'pyvenv.cfg'
    if not config.is_file() or config.stat().st_size>16384:raise ValueError('Service requires a verifiable Python venv.')
    homes=[line.partition('=')[2].strip() for line in config.read_text(encoding='utf-8').splitlines() if line.partition('=')[0].strip()=='home']
    if len(homes)!=1:raise ValueError('Python base interpreter cannot be established.')
    base=Path(homes[0])
    if not base.is_absolute() or 'windowsapps' in {part.lower() for part in base.parts}:raise ValueError('Store/relative base Python is not qualified for this service.')
    if not all(path.is_file() for path in (python,python.with_name('pythonw.exe'),base/'python.exe',base/'pythonw.exe')):
        raise ValueError('Console and windowless Python launchers/base interpreters must be visible on this host.')


def executable(name,explicit,root,repo):
    value=explicit or shutil.which(name)
    if not value:raise ValueError('Install '+name+' in an ordinary host shell, then pass its absolute path.')
    path=Path(value)
    if not path.is_absolute():raise ValueError('Use an absolute '+name+' executable path.')
    path=path.resolve()
    if not path.is_file() or any(path.is_relative_to(Path(folder).resolve()) for folder in (root,repo,Path.cwd())):
        raise ValueError('Refusing '+name+' inside the runtime, checkout or current working directory.')
    return path


def provision(repo,root,uv=None):
    host_context()
    tool=executable('uv',uv,root,repo)
    environment={key:value for key,value in os.environ.items() if not key.upper().startswith(('UV_','VIRTUAL_ENV','PYTHON'))}
    environment['UV_NO_CONFIG']='1'
    find=[str(tool),'python','find','--system','--no-project','--no-python-downloads','3.12']
    source_app=Path(__file__).resolve().parent/'app'
    result=run(find,source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=60)
    if result.returncode:
        run([str(tool),'python','install','3.12'],source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',check=True,timeout=600)
        result=run(find,source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',check=True,timeout=60)
    base=Path(result.stdout.strip())
    if not base.is_absolute() or not base.is_file():raise ValueError('uv did not return an existing absolute Python path.')
    if any(base.resolve().is_relative_to(Path(folder).resolve()) for folder in (root,repo)):
        raise ValueError('Refusing a Python base inside the runtime or checkout; select a system/uv-managed Python.')
    if os.name=='nt' and 'windowsapps' in {part.lower() for part in base.parts}:raise ValueError('Store Python is not qualified; install uv-managed Python 3.12 in the host shell.')
    environment_path=Path(repo)/'.venv'
    if environment_path.exists():
        if environment_path.is_symlink() or environment_path.is_junction() or environment_path.resolve().parent!=Path(repo).resolve():raise ValueError('Linked environment cannot be reprovisioned.')
        if Path(sys.executable).absolute().is_relative_to(environment_path.absolute()):raise ValueError('Run provisioning with the retained installed/host Python, outside the target venv.')
        device_path=Path(root)/'device.json'
        if device_path.exists():
            configured=json_object(read(device_path)).get('python')
            if configured and Path(configured).absolute().is_relative_to(environment_path.absolute()):raise ValueError('Target venv is the configured service interpreter; no provisioning changes made.')
        # A cached venv is not package integrity evidence. Preserve it privately
        # by rename, then let locked uv sync build a clean environment.
        os.replace(environment_path,Path(repo)/('.venv-quarantined-'+uuid.uuid4().hex))
    run([str(tool),'sync','--locked','--no-dev','--project',str(repo),'--python',str(base)],source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',check=True,timeout=900)
    python=Path(repo)/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    service_python(python)
    return python


def read(path):
    path=Path(path)
    if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):raise ValueError('Client file is linked; preserve it and select a regular file: '+str(path))
    for parent in path.parents:
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):raise ValueError('Client parent is linked: '+str(path))
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>MAX_CONFIG:raise ValueError('Client file exceeds the 64 MiB planning bound or is not regular: '+str(path))
    with path.open('rb') as stream:data=stream.read(MAX_CONFIG+1)
    if len(data)>MAX_CONFIG:raise ValueError('Client file exceeds the 64 MiB planning bound: '+str(path))
    return data


def digest(data):return None if data is None else hashlib.sha256(data).hexdigest()


def json_object(data):
    def pairs(entries):
        result={}
        for key,value in entries:
            if key in result:raise ValueError('Duplicate JSON keys; preserve and repair the selected client file first.')
            result[key]=value
        return result
    value=json.loads((data or b'{}').decode('utf-8-sig'),object_pairs_hook=pairs)
    if not isinstance(value,dict):raise ValueError('Expected a client JSON object.')
    return value


def _path(value):return os.path.normcase(str(Path(value).resolve()))


def _owned(spec,root,script='brain_client.py'):
    return (isinstance(spec,dict) and isinstance(spec.get('args'),list) and len(spec['args'])==1
            and isinstance(spec['args'][0],str) and _path(spec['args'][0])==_path(Path(root)/'app'/script))


def _hook_owned(command,root):
    try:tokens=shlex.split(command,posix=os.name!='nt')
    except ValueError:return False
    tokens=[token.strip('"') for token in tokens]
    return len(tokens)==2 and _path(tokens[1])==_path(Path(root)/'app/brain_hooks.py')


def desktop_executable(path):
    path=Path(path);parts={part.lower() for part in path.parts}
    if path.name.lower() not in ('claude.exe','claude'):return False
    if sys.platform=='darwin':return 'Claude.app' in path.parts
    packaged=('windowsapps' in parts and path.parent.name.lower()=='app'
              and path.parent.parent.name.lower().startswith(('claude_','anthropicclaude_')))
    standard=('anthropicclaude' in parts and path.parent.name.lower().startswith('app-'))
    standard=standard or ('claude' in parts and ('anthropic' in parts or 'programs' in parts))
    return packaged or standard


def desktop_busy():
    """Only current-session, current-user, qualified GUI executables count."""
    import psutil
    own=psutil.Process();username=own.username()
    if os.name=='nt':
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        def session(pid):
            value=ctypes.c_uint32()
            if not api.ProcessIdToSessionId(pid,ctypes.byref(value)):raise ValueError('Cannot qualify Desktop process session.')
            return value.value
        current_session=session(os.getpid())
    for process in psutil.process_iter(['pid','name']):
        if (process.info.get('name') or '').lower() not in ('claude.exe','claude'):continue
        try:
            if os.name=='nt' and session(process.pid)!=current_session:continue
            if process.username()!=username:continue
            if desktop_executable(process.exe()):return True
        except psutil.NoSuchProcess:continue
        except psutil.Error as error:raise ValueError('Cannot qualify a current-session Claude GUI process; close Desktop and retry.') from error
    return False


def desktop_path(home,explicit=None,skip=False):
    if skip:return None
    if explicit:return Path(explicit).expanduser().absolute()
    if sys.platform=='win32':
        standard=Path(os.environ.get('APPDATA',home/'AppData/Roaming'))/'Claude/claude_desktop_config.json'
        packages=Path(os.environ.get('LOCALAPPDATA',home/'AppData/Local'))/'Packages'
        candidates=[standard] if standard.exists() else []
        if packages.is_dir():
            for count,package in enumerate(packages.iterdir()):
                if count>=4096:raise ValueError('Desktop package inventory exceeds its planning bound.')
                if 'claude' not in package.name.lower():continue
                candidate=package/'LocalCache/Roaming/Claude/claude_desktop_config.json'
                if candidate.exists():candidates.append(candidate)
        if len(candidates)>1:raise ValueError('Multiple Desktop configurations exist; pass --desktop-config or --skip-desktop-refresh.')
        return candidates[0] if candidates else standard
    if sys.platform=='darwin':return home/'Library/Application Support/Claude/claude_desktop_config.json'
    return None


def plan(root,python,protocol,toml_plan,*,home=None,selected=None,refresh=False,desktop=None,skip_desktop=False):
    root=Path(root).resolve();home=Path(home or Path.home());python=Path(python)
    selected=set(CLIENTS if selected is None else selected)
    if not selected<=set(CLIENTS):raise ValueError('Unrecognized client selection.')
    specs={'command':str(python),'args':[str(root/'app/brain_client.py')]}
    targets=[]
    if 'claude-code' in selected:targets.append(('claude-code',home/'.claude.json','json'))
    if 'claude-desktop' in selected:
        path=desktop_path(home,desktop,skip_desktop)
        if path is not None:targets.append(('claude-desktop',path,'json'))
    if 'codex' in selected:targets.extend([('codex',home/'.codex/config.toml','toml'),('codex',home/'.codex/AGENTS.md','protocol')])
    if 'hooks' in selected:targets.append(('hooks',home/'.claude/settings.json','hooks'))
    records=[];skipped=[]
    for client,path,kind in targets:
        before=read(path)
        if refresh and before is None:skipped.append({'client':client,'path':str(path),'reason':'absent; refresh creates no client file'});continue
        # Declined clients are never opened. Malformed unrelated existing files
        # without an owned entry remain byte-for-byte untouched in refresh mode.
        if refresh and kind not in ('protocol',) and str(root).replace('\\','/').lower() not in (before or b'').decode('utf-8-sig',errors='replace').replace('\\\\','/').replace('\\','/').lower():
            skipped.append({'client':client,'path':str(path),'reason':'no runtime path; not owned'});continue
        if kind=='json':
            data=json_object(before);servers=data.get('mcpServers',{})
            if not isinstance(servers,dict):raise ValueError('Invalid MCP parent object: '+str(path))
            prior=servers.get('second-brain',{})
            if refresh and not _owned(prior,root):skipped.append({'client':client,'path':str(path),'reason':'entry not owned'});continue
            if not isinstance(prior,dict):raise ValueError('Invalid MCP entry: '+str(path))
            if not refresh and 'second-brain' in servers and not _owned(prior,root):
                raise ValueError('Existing second-brain entry belongs to another or unknown runtime; preserve it for review: '+str(path))
            data['mcpServers']={**servers,'second-brain':{**prior,**specs}}
            after=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
        elif kind=='toml':
            import tomllib
            content=(before or b'').decode('utf-8-sig');data=tomllib.loads(content)
            servers=data.get('mcp_servers',{})
            if not isinstance(servers,dict):raise ValueError('Invalid TOML MCP parent object: '+str(path))
            prior=servers.get('second-brain',{})
            if refresh and not _owned(prior,root):skipped.append({'client':client,'path':str(path),'reason':'entry not owned'});continue
            if not refresh and 'second-brain' in servers and not _owned(prior,root):
                raise ValueError('Existing second-brain entry belongs to another or unknown runtime; preserve it for review: '+str(path))
            after=toml_plan(content,specs)
        elif kind=='protocol':
            content=(before or b'').decode('utf-8-sig');starts=content.count(BEGIN);ends=content.count(END)
            if starts!=ends or starts>1:raise ValueError('Ambiguous/incomplete managed protocol markers: '+str(path))
            if refresh and starts==0:skipped.append({'client':client,'path':str(path),'reason':'no managed protocol block'});continue
            block=BEGIN+'\n'+protocol.rstrip()+'\n'+END
            if starts:
                first=content.index(BEGIN);last=content.index(END)
                if last<first:raise ValueError('Managed protocol marker order is invalid: '+str(path))
                after=content[:first]+block+content[last+len(END):]
            else:after=content.rstrip()+'\n\n'+block+'\n'
        else:
            data=json_object(before);hooks=data.get('hooks',{})
            if not isinstance(hooks,dict):raise ValueError('Invalid hooks object: '+str(path))
            found=False
            command=('"'+str(python).replace('\\','/')+'" "'+str(root/'app/brain_hooks.py').replace('\\','/')+'"') if os.name=='nt' else shlex.join([str(python),str(root/'app/brain_hooks.py')])
            for event in ('SessionStart','Stop'):
                entries=hooks.get(event,[])
                if not isinstance(entries,list):raise ValueError('Invalid hook event: '+str(path))
                replacement=[];event_owned=False
                for entry in entries:
                    if not isinstance(entry,dict) or not isinstance(entry.get('hooks',[]),list):raise ValueError('Invalid hook entry: '+str(path))
                    handlers=[]
                    for handler in entry.get('hooks',[]):
                        if not isinstance(handler,dict) or not isinstance(handler.get('command',''),str):raise ValueError('Invalid hook command: '+str(path))
                        if _hook_owned(handler.get('command',''),root):
                            handlers.append({**handler,'command':command});event_owned=True;found=True
                        else:handlers.append(handler)
                    replacement.append({**entry,'hooks':handlers})
                if not refresh and not event_owned:replacement.append({'hooks':[{'type':'command','command':command,'timeout':10}]})
                if event in hooks or not refresh:hooks[event]=replacement
            if refresh and not found:skipped.append({'client':client,'path':str(path),'reason':'no exact owned hook'});continue
            data['hooks']=hooks;after=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
        encoded=after.encode('utf-8')
        records.append({'client':client,'path':str(path.absolute()),'before_sha256':digest(before),'after_sha256':digest(encoded),'after':after})
    if any(row['client']=='claude-desktop' for row in records) and desktop_busy():raise ValueError('Claude Desktop is open. Close it or explicitly skip its refresh before continuing.')
    return {'format':1,'root':str(root),'python':str(python),'refresh':refresh,'files':records,'skipped':skipped}


def validate_plan(value,root,python=None):
    if not isinstance(value,dict) or value.get('format')!=1 or value.get('root')!=str(Path(root).resolve()) or not isinstance(value.get('files'),list):raise ValueError('Invalid sealed client plan identity.')
    if python is not None and value.get('python')!=str(python):raise ValueError('Sealed plan selects another interpreter.')
    seen=set()
    for row in value['files']:
        path=Path(row['path'])
        if not path.is_absolute() or _path(path) in seen or digest(row['after'].encode('utf-8'))!=row['after_sha256']:raise ValueError('Invalid sealed client plan payload.')
        if row.get('client') not in CLIENTS:raise ValueError('Invalid sealed client identity.')
        seen.add(_path(path))
    return value


def load_plan(path,root,python=None):
    return validate_plan(json_object(read(Path(path))),root,python)


def current(value):
    if any(row['client']=='claude-desktop' for row in value['files']) and desktop_busy():raise ValueError('Claude Desktop reopened; re-plan while the old service is healthy.')
    changed=[]
    for row in value['files']:
        if digest(read(Path(row['path'])))!=row['before_sha256']:changed.append(row['path'])
    if changed:raise ValueError('Client settings changed since planning; re-plan while the old service is healthy: '+', '.join(changed))


def apply(value,atomic,backup,root):
    validate_plan(value,root)
    changed=[];deferred=[]
    for row in value['files']:
        path=Path(row['path'])
        if row['client']=='claude-desktop' and desktop_busy():reason='Desktop reopened'
        elif digest(read(path))!=row['before_sha256']:reason='concurrent edit; preserved'
        else:
            backup(path,root)
            if digest(read(path))!=row['before_sha256']:reason='concurrent edit during backup; preserved'
            else:
                atomic(path,row['after']);changed.append(str(path));continue
        deferred.append({'path':str(path),'client':row['client'],'reason':reason})
    return {'changed':changed,'deferred':deferred,'client_acceptance':'pending' if deferred else 'configuration written; named-client check pending'}


def after_check(value):
    return [row['path'] for row in value['files'] if digest(read(Path(row['path'])))!=row['after_sha256']]
