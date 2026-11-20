"""Bootstrap host and sealed client plans. No runtime imports or side effects."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid


def run(command,*,source_app,delegated_cleanup=False,**options):
    """Installer children retain admission until their owned lifetime is complete."""
    if type(delegated_cleanup) is not bool:raise ValueError('Explicit trusted cleanup policy required.')
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
    # One local waiter owns this session leader. Its unreaped PID reserves
    # the PGID through TERM/grace/KILL; never signal after a recorded reap.
    import signal,time
    if signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
        raise ValueError('Setup refuses an overriding child-reaping disposition.')
    timeout=options.pop('timeout',None)
    capture=options.pop('capture_output',False)
    if capture:options.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    child=None;stopped=False;handlers={}
    def check_interrupt():
        if stopped:raise InterruptedError('Setup operation interrupted.')
    def interrupted(signum,frame):
        nonlocal stopped
        stopped=True
        # Let Popen return the ownership object before raising cancellation.
        # Never raise inside subprocess waitpid/exit-status bookkeeping.
        # A short communicate timeout checks the flag outside that boundary.
    try:
        for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            previous=signal.getsignal(signum)
            if previous is None:raise ValueError('Setup refuses an unrestoreable native signal handler.')
            if previous is not signal.SIG_IGN:handlers[signum]=previous
        for signum in handlers:signal.signal(signum,interrupted)
        check_interrupt()
        child=subprocess.Popen(command,start_new_session=True,**options)
        check_interrupt()
        deadline=None if timeout is None else time.monotonic()+timeout
        while True:
            check_interrupt()
            remaining=None if deadline is None else deadline-time.monotonic()
            if remaining is not None and remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
            try:
                stdout,stderr=child.communicate(timeout=.1 if remaining is None else min(.1,remaining))
                break
            except subprocess.TimeoutExpired:
                check_interrupt()
        check_interrupt()
        result=subprocess.CompletedProcess(command,child.returncode,stdout,stderr)
    finally:
        try:
            if child is not None:
                if child.returncode is None:
                    if delegated_cleanup:
                        try:print('Setup delegated cleanup pending: waiting for the trusted installer; admission remains held.',file=sys.stderr,flush=True)
                        except (OSError,ValueError):pass
                        # Only our trusted installer handoff delegates nested-session
                        # cleanup. TERM requests its own runner to drain/reap; KILL
                        # here could strand that runner's separate child session.
                        try:os.killpg(child.pid,signal.SIGTERM)
                        except OSError:pass
                        while child.returncode is None:
                            try:child.communicate(timeout=.1)
                            except subprocess.TimeoutExpired:pass
                            except (OSError,ValueError):
                                # No destructive signals follow this read-only reap.
                                try:child.poll()
                                except OSError:pass
                                time.sleep(.1)
                    else:
                        for signum in (signal.SIGTERM,signal.SIGKILL):
                            try:os.killpg(child.pid,signum)
                            except OSError:pass
                            if signum==signal.SIGTERM:time.sleep(2)
                try:child.wait(timeout=5)
                except (subprocess.TimeoutExpired,OSError):pass
                notified=False
                while True:
                    # A reaped leader grants no further signal authority.
                    # Unknown/remaining membership retains the caller's admission.
                    try:reaped=child.poll() is not None
                    except OSError:reaped=False
                    if reaped:
                        try:os.killpg(child.pid,0)
                        except ProcessLookupError:break
                        except OSError:pass
                    if not notified:
                        notified=True
                        try:print('Setup cleanup pending: group absence remains unverified; admission remains held.',file=sys.stderr,flush=True)
                        except (OSError,ValueError):pass
                    time.sleep(.1)
                for stream in (child.stdin,child.stdout,child.stderr):
                    if stream is not None:stream.close()
        finally:
            for signum,previous in handlers.items():signal.signal(signum,previous)
    check_interrupt()
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
CLIENTS=('claude-code','claude-desktop','codex','hooks','grok')
SKILL_FRONT='''---
name: second-brain
description: Retrieve dated evidence from the user's shared Obsidian Second Brain, or save a meaningful work milestone. Use for prior work, decisions and explicitly confirmed preferences; do not scan the vault for unrelated tasks.
---
'''
BASE_PROBE=('import sqlite3,sys,sysconfig;sqlite3.connect(":memory:").enable_load_extension(True);'
            'sys.exit(0 if sys.platform!="darwin" or sysconfig.get_config_var("PYTHONFRAMEWORK") else 1)')
MAC_FRAMEWORK_PYTHONS=('/opt/homebrew/bin/python3.12','/usr/local/bin/python3.12',
                       '/opt/homebrew/opt/python@3.12/bin/python3.12','/usr/local/opt/python@3.12/bin/python3.12')

class ConcurrentWriteError(ValueError):
    """Destination changed while a private replacement was being prepared."""


class ClientPlanError(ValueError):
    """Planning cannot safely proceed with the selected client files."""


class TomlSupportError(ClientPlanError):
    """The locked TOML writer is unavailable."""


class DuplicateJSONError(ValueError):
    """A JSON mapping repeats a key."""


class ClientSkip(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code=code


_CONNECTION=None
_CONNECTION_SHA256=None


def connection():
    """Load the fixed sibling source under Python -I without adding import paths."""
    global _CONNECTION
    if _CONNECTION is not None and _CONNECTION_SHA256 is None:
        return _CONNECTION[1]
    path=Path(__file__).resolve().parent/'app/brain_connection.py'
    raw=read(path,limit=65536)
    actual=digest(raw)
    if raw is None or (_CONNECTION_SHA256 is not None and actual!=_CONNECTION_SHA256):
        raise ClientPlanError('The verified MCP connection renderer is missing or changed.')
    if _CONNECTION is None:
        import types
        module=types.ModuleType('_brain_setup_connection')
        module.__file__=str(path)
        exec(compile(raw,str(path),'exec'),module.__dict__)
        _CONNECTION=(actual,module)
    if _CONNECTION[0]!=actual:
        raise ClientPlanError('The MCP connection renderer changed after loading.')
    return _CONNECTION[1]


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
    def checked(output):
        base=Path(output.strip())
        if not base.is_absolute() or not base.is_file():raise ValueError('uv did not return an existing absolute Python path.')
        if any(base.resolve().is_relative_to(Path(folder).resolve()) for folder in (root,repo)):
            raise ValueError('Refusing a Python base inside the runtime or checkout; select a system/uv-managed Python.')
        if os.name=='nt' and 'windowsapps' in {part.lower() for part in base.parts}:raise ValueError('Store Python is not qualified; install uv-managed Python 3.12 in the host shell.')
        return base
    base=checked(result.stdout)
    # The search index loads the sqlite-vec extension, and the Mac service is qualified on framework builds
    # only. python.org and Xcode Pythons on macOS cannot load SQLite extensions and uv's own build is not a
    # framework: on a Mac, Homebrew's Python is the one that is both. Elsewhere uv's build replaces a base
    # that cannot load extensions.
    def usable(candidate):
        return run([str(candidate),'-I','-c',BASE_PROBE],source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=60).returncode==0
    if not usable(base):
        if sys.platform=='darwin':
            base=next((checked(item) for item in MAC_FRAMEWORK_PYTHONS if Path(item).is_file() and usable(item)),None)
            if base is None:raise ValueError('This Mac has no Python 3.12 that can run both the service and the search index. Install Homebrew Python (brew install python@3.12) and run this again.')
        else:
            run([str(tool),'python','install','3.12'],source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',check=True,timeout=600)
            base=checked(run([str(tool),'python','find','--managed-python','--no-project','--no-python-downloads','3.12'],
                             source_app=source_app,env=environment,capture_output=True,text=True,encoding='utf-8',check=True,timeout=60).stdout)
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


def _posix_uid(info, allow_root):
    if os.name!='posix':return True
    if info.st_uid==os.geteuid():return True
    return bool(allow_root and info.st_uid==0)


def _directory_ok(info, allow_root):
    """Require an approved owner and reject world-write or group-write by a different owner."""
    if not _posix_uid(info, allow_root):return False
    if os.name!='posix':return True
    if info.st_mode & stat.S_IWOTH:return False
    if info.st_uid!=os.geteuid() and info.st_mode & stat.S_IWGRP:return False
    return True


def _link_text(raw):
    """Windows readlink can return an extended path. Callers need the normal form."""
    if isinstance(raw,str) and raw.startswith('\\\\?\\UNC\\'):return '\\\\'+raw[8:]
    if isinstance(raw,str) and raw.startswith('\\\\?\\'):return raw[4:]
    return raw


def _plain(path):
    """Compare client paths without following a parent junction to another prefix."""
    text=_link_text(os.path.abspath(str(path)))
    return os.path.normcase(os.path.normpath(text))


def _inside_home(path, home):
    home_text=_plain(home);path_text=_plain(path)
    return path_text==home_text or path_text.startswith(home_text+os.sep)


def resolve_client_path(path, home):
    """Follow a client path only when every link stays inside the home folder.

    A root-owned link is accepted when its target is this user's directory.
    A link into another directory, a loop, or a link planted in a directory
    other people can write is refused.
    """
    home=Path(os.path.abspath(str(home)));path=Path(path)
    if not path.is_absolute():path=home/path
    anchor=home.resolve(strict=True)
    def outside():return ClientSkip('outside_home','Client file leaves the home folder; select a regular file there: '+str(path))
    def relative_parts(target):
        for base in (home,anchor):
            if target.is_relative_to(base):return list(target.relative_to(base).parts)
        raise outside()
    cursor=anchor;pending=relative_parts(path);hops=0;seen=set()
    while pending:
        name=pending.pop(0)
        if name=='..':
            if cursor==anchor:raise outside()
            cursor=cursor.parent
            continue
        if not _directory_ok(cursor.lstat(),False):
            raise ClientSkip('inaccessible','Client path directory is not private: '+str(path))
        candidate=cursor/name
        try:info=candidate.lstat()
        except FileNotFoundError:
            if '..' in pending:raise ClientSkip('inaccessible','Client path traverses a missing directory: '+str(path))
            return candidate.joinpath(*pending)
        if not (stat.S_ISLNK(info.st_mode) or candidate.is_junction()):
            if not _directory_ok(info,False):
                raise ClientSkip('inaccessible','Client path is not owned by this user or is writable by others: '+str(path))
            if pending and not stat.S_ISDIR(info.st_mode):
                raise ClientSkip('inaccessible','Client path parent is not a directory: '+str(path))
            cursor=candidate
            continue
        hops+=1
        if hops>8:raise ClientSkip('inaccessible','Client path has too many links: '+str(path))
        raw_link=os.readlink(candidate);mark=(str(candidate),raw_link)
        if mark in seen:raise ClientSkip('inaccessible','Client path link loops: '+str(path))
        seen.add(mark)
        if not _posix_uid(info,True):raise ClientSkip('inaccessible','Client path link is not owned by this user: '+str(path))
        target=Path(_link_text(raw_link))
        if not target.is_absolute():target=cursor/target
        cursor=anchor;pending=relative_parts(target)+pending
    return cursor


@contextmanager
def _open_parent(path, *, create=False):
    """Pin each directory component without following links."""
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    descriptor=os.open(path.anchor,flags)
    try:
        for name in path.parts[1:-1]:
            try:child=os.open(name,flags,dir_fd=descriptor)
            except FileNotFoundError:
                if not create:raise
                try:os.mkdir(name,0o700,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(name,flags,dir_fd=descriptor)
            os.close(descriptor)
            descriptor=child
        yield descriptor
    finally:os.close(descriptor)


def _identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _file_image(path,parent=None,*,limit=None):
    limit=MAX_CONFIG if limit is None else limit
    try:
        if parent is None:
            if _linked(path):raise ClientSkip('inaccessible','Linked file or ancestor is refused: '+str(path))
            first=path.lstat()
            if not stat.S_ISREG(first.st_mode):raise ClientSkip('inaccessible','Client file is not regular: '+str(path))
            stream=path.open('rb')
        else:
            stream=os.fdopen(os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent),'rb')
    except FileNotFoundError:return None,None
    with stream:
        info=os.fstat(stream.fileno())
        if parent is None and (_identity(first)!=_identity(info) or _linked(path)):
            raise ConcurrentWriteError('Client file or ancestor changed while opening: '+str(path))
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ClientSkip('inaccessible','Client file exceeds the planning bound or is not regular: '+str(path))
        data=stream.read(limit+1)
        if len(data)>limit:raise ClientSkip('inaccessible','Client file exceeds the planning bound: '+str(path))
        if _identity(os.fstat(stream.fileno()))!=_identity(info):raise ConcurrentWriteError('File changed during its read: '+str(path))
    try:current=os.stat(path.name,dir_fd=parent,follow_symlinks=False) if parent is not None else path.lstat()
    except FileNotFoundError:raise ConcurrentWriteError('File disappeared during its read: '+str(path)) from None
    if _identity(current)!=_identity(info):raise ConcurrentWriteError('File changed during its read: '+str(path))
    return data,info


def client_image(path, *, limit=None):
    path=Path(path).absolute()
    if '..' in path.parts:raise ClientSkip('inaccessible','Noncanonical file path is refused: '+str(path))
    if os.name=='posix':
        try:
            with _open_parent(path) as parent:data,info=_file_image(path,parent,limit=limit)
        except FileNotFoundError:return None,None
    else:data,info=_file_image(path,limit=limit)
    return data,stat.S_IMODE(info.st_mode) if info is not None else None


def read(path, *, limit=None):
    """Read a bounded, regular file through a path that contains no links."""
    return client_image(path,limit=limit)[0]


read_client=read


def write_client(path,data,*,expected_sha256,mode=None):
    """Compare and publish a client's canonical POSIX target through its directory handle."""
    path=Path(path).absolute()
    if '..' in path.parts:raise ClientSkip('inaccessible','Noncanonical client path is refused: '+str(path))
    with _open_parent(path,create=data is not None) as parent:
        before,info=_file_image(path,parent)
        if digest(before)!=expected_sha256:raise ConcurrentWriteError('Concurrent edit before staging; original preserved.')
        temporary='.'+path.name+'.'+uuid.uuid4().hex+'.tmp'
        staged=False
        try:
            if data is not None:
                descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                staged=True
                with os.fdopen(descriptor,'wb') as stream:
                    stream.write(data.encode('utf-8') if isinstance(data,str) else data)
                    stream.flush()
                    os.fchmod(stream.fileno(),mode if mode is not None else (stat.S_IMODE(info.st_mode) if info else 0o600))
                    os.fsync(stream.fileno())
            with _open_parent(path) as current_parent:
                if not os.path.samestat(os.fstat(parent),os.fstat(current_parent)):
                    raise ConcurrentWriteError('Client directory changed during staging; original preserved.')
            current,current_info=_file_image(path,parent)
            if (digest(current)!=expected_sha256 or (info is not None and _identity(info)!=_identity(current_info))):
                raise ConcurrentWriteError('Concurrent edit during staging; original preserved.')
            if data is None:
                if info is None:return
                os.unlink(path.name,dir_fd=parent)
            elif info is None:
                try:os.link(temporary,path.name,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
                except FileExistsError as error:raise ConcurrentWriteError('New destination appeared; original preserved.') from error
            else:os.replace(temporary,path.name,src_dir_fd=parent,dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if staged:
                try:os.unlink(temporary,dir_fd=parent)
                except FileNotFoundError:pass


def digest(data):return None if data is None else hashlib.sha256(data).hexdigest()


def _linked(path):
    path=Path(path)
    return any(part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()) for part in (path,*path.parents))


def block_file(path,home,imports=False,shared=()):
    """The one file this assistant loads the managed block from, or the file to add it to.

    Claude Code also loads files named as @path, so a block kept in a shared rules file is
    refreshed there and never duplicated. With no block yet, a loaded file that another
    selected assistant also writes (shared) receives the one block."""
    first=resolve_client_path(path,home);texts={};loaded=[];outside=set();queue=[(first,0)]
    while queue:
        current,depth=queue.pop(0)
        if current in loaded:continue
        loaded.append(current)
        try:data=read(current)
        except (ValueError,OSError):
            if depth:continue  # An imported path that is not a plain file is not ours to manage.
            raise
        if data is None:continue
        texts[current]=data.decode('utf-8-sig',errors='replace')
        if not imports or depth>=5:continue
        fenced=False
        for line in texts[current].splitlines():
            if line.lstrip().startswith('```'):fenced=not fenced
            if fenced:continue  # Claude Code does not import from inside a code block.
            for name in re.findall(r'(?:^|\s)@([^\s`]+)',line):
                target=Path(home)/name[2:] if name.startswith('~/') else Path(name)
                if not target.is_absolute():target=current.parent/target
                try:queue.append((resolve_client_path(target,home),depth+1))
                except ClientSkip as error:
                    # Outside the home folder: read to see what the assistant loads, never written.
                    if error.code!='outside_home':continue
                    try:beyond=target.resolve()
                    except (ValueError,OSError,RuntimeError):continue
                    outside.add(beyond);queue.append((beyond,depth+1))
                except OSError:pass
    holders=[item for item,text in texts.items() if BEGIN in text]
    if len(holders)>1:raise ClientSkip('multiple_blocks','The managed protocol block is loaded from more than one file; keep one: '+', '.join(map(str,holders)))
    if holders and holders[0] in outside:raise ClientSkip('outside_home','The managed protocol block is loaded from outside the home folder: '+str(holders[0]))
    wanted={_plain(item) for item in shared}
    return holders[0] if holders else next((item for item in loaded if _plain(item) in wanted),first)


def _load_json(data):
    duplicate=False
    def pairs(entries):
        nonlocal duplicate
        result=dict(entries)
        duplicate=duplicate or len(result)!=len(entries)
        return result
    try:value=json.loads((data or b'{}').decode('utf-8-sig'),object_pairs_hook=pairs)
    except RecursionError as error:raise ValueError('Client JSON exceeds its nesting bound.') from error
    if duplicate:raise DuplicateJSONError('Duplicate JSON keys; preserve and repair the selected client file first.')
    return value


def _require_json_object(value):
    if not isinstance(value,dict):raise ValueError('Expected a client JSON object.')
    return value


def json_object(data):
    return _require_json_object(_load_json(data))


def _path(value):return os.path.normcase(str(Path(value).resolve()))


def _owned(spec,root,script='brain_client.py'):
    return (isinstance(spec,dict) and isinstance(spec.get('args'),list) and len(spec['args'])==1
            and isinstance(spec['args'][0],str) and _path(spec['args'][0])==_path(Path(root)/'app'/script))


def connection_spec(root,python):
    return connection().connection_spec(root,python)


def _connection_owned(spec,root):
    """An entry this runtime may replace: the old Python adapter, or the stable launcher."""
    if _owned(spec,root):return True
    return (isinstance(spec,dict) and spec.get('args')==[] and isinstance(spec.get('command'),str)
            and _path(spec['command'])==_path(Path(root)/'bin'/'second-brain-mcp'))


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


def codex_skill(home):
    """Codex also reads ~/.agents/skills. A copy that is already there is the one to keep current; no second one is made."""
    own=home/'.codex/skills/second-brain/SKILL.md';common=home/'.agents/skills/second-brain/SKILL.md'
    return common if common.is_file() and not own.exists() else own


def composed_skill(protocol):
    """One instruction text for every assistant. The skill front matter only says when to use it."""
    return SKILL_FRONT+'\n'+BEGIN+'\n'+protocol.strip()+'\n'+END+'\n'


def grok_directory(home):
    """Grok Bot reads skills from this folder. It does not read an MCP config file from disk.

    ``~/agent-data`` may be a link to the real directory inside the home folder.
    When that link is safe, the files are planned at the real path.
    """
    return Path(home)/'agent-data'/'workflows'/'second-brain'


def grok_present(home):
    return (Path(home)/'agent-data'/'workflows').is_dir()


def validated_grok(home,root,python):
    spec_path=resolve_client_path(grok_directory(home)/'mcp-registration.json',home)
    shared=connection()
    python=Path(python)
    if not python.is_absolute() or not python.is_file() or not os.access(python,os.X_OK):
        raise ValueError('The recorded Python executable is unavailable.')
    entry=shared.validate_grok_spec(read_client(spec_path,limit=shared.GROK_SPEC_LIMIT),root,python)
    if os.name=='posix':shared.validate_launcher(root,python)
    return spec_path,entry


def selected_clients(names, *, refresh, include_hooks=False, no_hooks=False):
    selected=set(('claude-code','claude-desktop','codex','hooks') if refresh else
                 ('claude-code','claude-desktop','codex')) if names is None else set(names)
    if not selected<=set(CLIENTS):raise ClientPlanError('Unrecognized client selection.')
    if include_hooks or (refresh and 'claude-code' in selected):selected.add('hooks')
    if no_hooks:selected.discard('hooks')
    return selected


def _target(path,home,explicit_desktop=False):
    path=Path(path)
    if not explicit_desktop or _inside_home(path,home):return resolve_client_path(path,home)
    if _linked(path):raise ClientSkip('inaccessible','Explicit Desktop path or ancestor is linked: '+str(path))
    return Path(os.path.abspath(path))


def _update_entry(servers,root,path,refresh):
    if not isinstance(servers,dict):raise ValueError('Invalid MCP parent object: '+str(path))
    if 'second-brain' not in servers:
        if refresh:raise ClientSkip('missing_owned_entry','No existing second-brain entry.')
        return {}
    prior=servers['second-brain']
    if not isinstance(prior,dict):raise ValueError('Invalid MCP entry: '+str(path))
    if not _connection_owned(prior,root):
        raise ClientSkip('foreign_entry','Existing second-brain entry belongs to another or unknown runtime; preserve it for review: '+str(path))
    return prior


def _mentions_runtime(data,root):
    literal=str(root).replace('\\','/').casefold()
    raw=data.decode('utf-8-sig',errors='replace').replace('\\\\','/').replace('\\','/').casefold()
    return literal in raw


def _parse_client_config(raw,kind,path,root,refresh):
    try:
        if kind=='toml':
            import tomllib
            content=(raw or b'').decode('utf-8-sig')
            return tomllib.loads(content),content
        value=_load_json(raw)
    except DuplicateJSONError as error:
        if not refresh:raise
        raise ClientSkip('duplicate_json',str(error)) from error
    except (ValueError,RecursionError) as error:
        if not refresh:raise
        if _mentions_runtime(raw,root):
            raise ClientPlanError('Unparseable client file names this runtime: '+str(path)) from error
        raise ClientSkip('unparseable_unowned','unparseable; ownership not inspected') from error
    return _require_json_object(value),None


def plan(root,python,protocol,toml_plan,*,home=None,selected=None,refresh=False,desktop=None,skip_desktop=False,skill=None):
    root=Path(root).resolve();home=Path(home or Path.home());python=Path(python)
    selected=set(CLIENTS if selected is None else selected)
    if not selected<=set(CLIENTS):raise ValueError('Unrecognized client selection.')
    specs=connection_spec(root,python)
    targets=[];records=[];skipped=[];claimed=set()
    def skip(client,path,code,reason):
        skipped.append({'client':client,'path':str(path),'code':code,'reason':str(reason)})
    def shared(client,path,kind,locate):
        # An instruction or skill file that cannot be managed safely (it leaves the home folder, or
        # the block is loaded twice) is left alone and reported. It never stops the rest of the
        # plan or an update; check_setup.py then shows what that assistant still lacks.
        try:located=locate()
        except ClientSkip as error:skip(client,path,error.code,error);return None
        except (ValueError,OSError) as error:skip(client,path,'inaccessible',error);return None
        targets.append((client,located,kind));return located
    codex_block=shared('codex',home/'.codex/AGENTS.md','protocol',lambda:block_file(home/'.codex/AGENTS.md',home)) if 'codex' in selected else None
    codex_rows=targets[:];targets.clear()  # Claude Code is planned first; a file both share is then written once.
    if 'claude-code' in selected:
        targets.append(('claude-code',home/'.claude.json','json'))
        shared('claude-code',home/'.claude/CLAUDE.md','protocol',lambda:block_file(home/'.claude/CLAUDE.md',home,True,[codex_block] if codex_block else ()))
        if skill is not None:shared('claude-code',home/'.claude/skills/second-brain/SKILL.md','skill',lambda:resolve_client_path(home/'.claude/skills/second-brain/SKILL.md',home))
    if 'claude-desktop' in selected:
        path=desktop_path(home,desktop,skip_desktop)
        if path is not None:targets.append(('claude-desktop',path,'json'))
    if 'codex' in selected:
        targets.extend([('codex',home/'.codex/config.toml','toml'),*codex_rows])
        if skill is not None:shared('codex',codex_skill(home),'skill',lambda:resolve_client_path(codex_skill(home),home))
    if 'hooks' in selected:targets.append(('hooks',home/'.claude/settings.json','hooks'))
    if 'grok' in selected:
        folder=grok_directory(home)
        if skill is not None:targets.append(('grok',folder/'SKILL.md','skill'))
        targets.append(('grok',folder/'mcp-registration.json','grok'))
    for client,path,kind in targets:
        # One shared rules or skill file can serve several assistants; write it once.
        # One assistant's refusal is recorded here and the next assistant is planned.
        try:
            path=_target(path,home,client=='claude-desktop' and desktop is not None)
            if _plain(path) in claimed:raise ClientSkip('shared_target','shared file already planned')
            claimed.add(_plain(path))
            before=read(path,limit=connection().GROK_SPEC_LIMIT if kind=='grok' else None)
            if refresh and before is None:raise ClientSkip('missing_file','No existing client file to refresh.')
            if kind not in ('protocol','skill'):
                data,content=_parse_client_config(before,kind,path,root,refresh)
            if kind=='json':
                servers=data.get('mcpServers',{})
                prior=_update_entry(servers,root,path,refresh)
                data['mcpServers']={**servers,'second-brain':{**prior,**specs}}
                after=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
            elif kind=='toml':
                servers=data.get('mcp_servers',{})
                _update_entry(servers,root,path,refresh)
                after=toml_plan(content,specs)
            elif kind in ('protocol','skill'):
                content=(before or b'').decode('utf-8-sig');starts=content.count(BEGIN);ends=content.count(END)
                if starts!=ends or starts>1:raise ClientSkip('bad_markers','Ambiguous/incomplete managed protocol markers: '+str(path))
                if starts and content.index(END)<content.index(BEGIN):
                    raise ClientSkip('bad_markers','Managed protocol marker order is invalid: '+str(path))
                if refresh and not starts:raise ClientSkip('missing_owned_block','No existing managed protocol block.')
                if kind=='skill':
                    after=skill
                else:
                    block=BEGIN+'\n'+protocol.rstrip()+'\n'+END
                    if starts:
                        first=content.index(BEGIN);last=content.index(END)
                        after=content[:first]+block+content[last+len(END):]
                    else:after=content.rstrip()+'\n\n'+block+'\n'
            elif kind=='grok':
                servers=data.get('mcpServers',{})
                if servers in ({},None):servers={}
                if not isinstance(servers,dict):raise ValueError('Invalid Grok MCP registration: '+str(path))
                extra=[name for name in servers if name!='second-brain']
                if extra:raise ClientSkip('multiple_servers','Grok registration must contain only the second-brain server; remove the other names and run this again: '+', '.join(extra))
                _update_entry(servers,root,path,refresh)
                after=json.dumps(connection().grok_spec(root,python),indent=2,ensure_ascii=False)+'\n'
            else:
                hooks=data.get('hooks',{})
                if not isinstance(hooks,dict):raise ValueError('Invalid hooks object: '+str(path))
                found=False
                create=not refresh
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
                    if create and not event_owned:replacement.append({'hooks':[{'type':'command','command':command,'timeout':10}]})
                    if event in hooks or create:hooks[event]=replacement
                if refresh and not found:raise ClientSkip('missing_owned_hook','no exact owned hook')
                data['hooks']=hooks;after=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
            encoded=after.encode('utf-8')
            records.append({'client':client,'path':str(path.absolute()),'before_sha256':digest(before),'after_sha256':digest(encoded),'after':after})
        except ClientPlanError:raise
        except ClientSkip as error:skip(client,path,error.code,error)
        except (OSError,ConcurrentWriteError) as error:skip(client,path,'inaccessible',error)
        except (ValueError,RecursionError) as error:skip(client,path,'malformed',error)
    if any(row['client']=='claude-desktop' for row in records) and desktop_busy():raise ValueError('Claude Desktop is open. Close it or explicitly skip its refresh before continuing.')
    return {'format':1,'root':str(root),'python':str(python),'refresh':refresh,'files':records,'skipped':skipped}


def validate_plan(value,root,python=None):
    if not isinstance(value,dict) or value.get('format')!=1 or value.get('root')!=str(Path(root).resolve()) or not isinstance(value.get('files'),list):raise ValueError('Invalid sealed client plan identity.')
    if python is not None and value.get('python')!=str(python):raise ValueError('Sealed plan selects another interpreter.')
    seen=set()
    for row in value['files']:
        path=Path(row['path'])
        if not path.is_absolute() or '..' in path.parts or _plain(path) in seen or digest(row['after'].encode('utf-8'))!=row['after_sha256']:raise ValueError('Invalid sealed client plan payload.')
        if row.get('client') not in CLIENTS:raise ValueError('Invalid sealed client identity.')
        seen.add(_plain(path))
    return value


def load_plan(path,root,python=None):
    return validate_plan(json_object(read(Path(path))),root,python)


def authorize_plan(value,*,selected=None,desktop=None,skip_desktop=False):
    """Public plan bytes cannot grant themselves filesystem write authority."""
    home=Path.home();selected=set(CLIENTS if selected is None else selected)
    allowed={}
    if not selected<=set(CLIENTS):raise ValueError('Unrecognized client selection.')
    def inside(locate):
        try:return {_plain(locate())}
        except (ValueError,OSError):return set()
    def target(path,explicit=False):return inside(lambda:_target(path,home,explicit))
    codex_block=inside(lambda:block_file(home/'.codex/AGENTS.md',home)) if 'codex' in selected else set()
    if 'claude-code' in selected:
        allowed['claude-code']=(target(home/'.claude.json')|inside(lambda:block_file(home/'.claude/CLAUDE.md',home,True,codex_block))
                                |target(home/'.claude/skills/second-brain/SKILL.md'))
    if 'codex' in selected:allowed['codex']=target(home/'.codex/config.toml')|codex_block|target(codex_skill(home))
    if 'hooks' in selected:allowed['hooks']=target(home/'.claude/settings.json')
    if 'claude-desktop' in selected:
        desktop_target=desktop_path(home,desktop,skip_desktop)
        allowed['claude-desktop']=target(desktop_target,desktop is not None) if desktop_target is not None else set()
    if 'grok' in selected:
        folder=grok_directory(home)
        allowed['grok']=target(folder/'SKILL.md')|target(folder/'mcp-registration.json')
    for row in value['files']:
        if _plain(row['path']) not in allowed.get(row['client'],set()):
            raise ValueError('Sealed client plan target is outside the selected client scope; no writes made.')
    return value


def current(value):
    if any(row['client']=='claude-desktop' for row in value['files']) and desktop_busy():raise ValueError('Claude Desktop reopened; re-plan while the old service is healthy.')
    changed=[]
    for row in value['files']:
        if not _matches(row['path'],row['before_sha256']):changed.append(row['path'])
    if changed:raise ValueError('Client settings changed since planning; re-plan while the old service is healthy: '+', '.join(changed))


def apply(value,atomic,backup,root):
    validate_plan(value,root)
    changed=[];deferred=[]
    for row in value['files']:
        path=Path(row['path'])
        if row['client']=='claude-desktop' and desktop_busy():reason='Desktop reopened'
        elif not _matches(path,row['before_sha256']):reason='concurrent edit; preserved'
        else:
            try:
                backup(path,root)
                if not _matches(path,row['before_sha256']):raise ConcurrentWriteError('Client changed during backup.')
                atomic(path,row['after'],expected_sha256=row['before_sha256'])
            except (ConcurrentWriteError,ClientSkip,FileNotFoundError,NotADirectoryError):
                reason='concurrent edit during backup or staging; preserved'
            except OSError as error:
                if error.errno not in (errno.ELOOP,errno.EACCES,errno.EPERM):raise
                reason='client path became inaccessible during backup or staging; preserved'
            else:changed.append(str(path));continue
        deferred.append({'path':str(path),'client':row['client'],'reason':reason})
    return {'changed':changed,'deferred':deferred,'skipped':value.get('skipped',[]),'client_acceptance':'pending' if deferred else 'configuration written; named-client check pending'}


def after_check(value):
    return [row['path'] for row in value['files'] if not _matches(row['path'],row['after_sha256'])]


def _matches(path,expected):
    try:return digest(read(path))==expected
    except (ValueError,OSError):return False
