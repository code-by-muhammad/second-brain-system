"""Host-local service ownership. Never infer ownership from a PID/name alone."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import json
import os
import stat
import socket
import sys
import time
from types import SimpleNamespace
from pathlib import Path
import psutil
import brain_settings as cfg

SCRIPTS={name:'brain_'+name+'.py' for name in ('supervisor','server','index_bootstrap','maintenance')}
_JOB=None


class OwnershipError(RuntimeError): pass


class _MacInventoryChanged(OwnershipError): pass


def _path(value): return os.path.normcase(str(Path(value).resolve()))


def _api():
    from brain_windows_process import _kernel
    api=_kernel()
    for name,arguments,result in (
        ('TerminateProcess',[wt.HANDLE,wt.UINT],wt.BOOL),
        ('AssignProcessToJobObject',[wt.HANDLE,wt.HANDLE],wt.BOOL),
        ('GetSystemTimeAsFileTime',[ctypes.POINTER(wt.FILETIME)],None),
    ):
        function=getattr(api,name);function.argtypes=arguments;function.restype=result
    return api


def _birth(api,handle):
    created,exited,kernel,user=(wt.FILETIME() for _ in range(4))
    if not api.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user)):
        raise OwnershipError('Process birth identity is unavailable.')
    return int(created.dwLowDateTime)+(int(created.dwHighDateTime)<<32)


def filetime_now():
    if os.name!='nt':
        import time
        return time.time_ns()//100+116444736000000000
    api=_api();value=wt.FILETIME();api.GetSystemTimeAsFileTime(ctypes.byref(value))
    return int(value.dwLowDateTime)+(int(value.dwHighDateTime)<<32)


def _linux_lifetime(pid):
    from brain_linux_identity import IdentityError,process
    try:return process(pid)
    except (IdentityError,OSError,UnicodeError):
        raise OwnershipError('Linux process lifetime could not be verified.') from None


def linux_marker():
    from brain_linux_identity import IdentityError,marker
    try:return marker()
    except (IdentityError,OSError,UnicodeError):
        raise OwnershipError('Linux boot-relative boundary could not be verified.') from None


def newer_than_empty(identity,boundary):
    from brain_linux_identity import IdentityError,fresh
    try:return fresh(identity,boundary)
    except IdentityError:
        raise OwnershipError('Linux quiescence boundary could not be verified.') from None


def same_runtime(left,right):
    if sys.platform!='linux':return left==right
    if not isinstance(left,dict) or not isinstance(right,dict):return False
    for record in (left,right):
        if (any(type(record.get(key)) is not int or record[key]<=0 for key in
                ('pid','supervisor_pid','start_ticks','supervisor_start_ticks'))
            or not isinstance(record.get('boot_id'),str)
            or record.get('supervisor_boot_id')!=record['boot_id']):return False
    return ({key:value for key,value in left.items() if key not in ('birth','supervisor_birth')}
            =={key:value for key,value in right.items() if key not in ('birth','supervisor_birth')})


def _parent_precedes(parent,child):
    if sys.platform=='linux':
        return (parent.lifetime is not None and child.lifetime is not None
                and parent.lifetime['boot_id']==child.lifetime['boot_id']
                and parent.lifetime['start_ticks']<=child.lifetime['start_ticks'])
    return parent.birth<child.birth


def validate_environment(*,entrypoint=False):
    """Service locks/port must refer to the same device as the scheduled task."""
    state=cfg.ROOT/'state'
    device=cfg.DEVICE
    if not isinstance(device,dict) or not device.get('vault') or not device.get('python'):
        raise OwnershipError('Configure a device before controlling its service.')
    for name in ('app','state'):
        target=cfg.ROOT/name
        if target.is_symlink() or target.is_junction() or target.resolve()!=target:raise OwnershipError('Linked runtime app/state is refused.')
    expected={'BRAIN_SYSTEM_ROOT':cfg.ROOT,'BRAIN_STATE':state,'BRAIN_VAULT':Path(device['vault']).resolve()}
    if cfg.STATE!=state or cfg.VAULT!=expected['BRAIN_VAULT'] or cfg.PORT!=int(device.get('port',8765)):
        raise OwnershipError('Runtime state/vault/port overrides disagree with device.json; remove ambient BRAIN overrides.')
    for name,path in expected.items():
        if name in os.environ and _path(os.environ[name])!=_path(path):
            raise OwnershipError('Ambient BRAIN paths disagree with this device; use a clean host shell.')
    if 'BRAIN_PORT' in os.environ and os.environ['BRAIN_PORT']!=str(cfg.PORT):
        raise OwnershipError('Ambient BRAIN_PORT disagrees with this device.')
    if entrypoint and Path(__file__).resolve().parents[1]!=cfg.ROOT:
        raise OwnershipError('Supervisor source and runtime root disagree; no service work started.')


def _framework_image(interpreter,home):
    """Only the measured CPython framework layout, derived from a selected path."""
    version=interpreter.parent.parent
    if (interpreter.parent.name!='bin' or version.parent.name!='Versions'
            or version.parent.parent.name!='Python.framework'):
        return None
    if home is not None:
        # Either the framework's own bin, or a prefix bin (Homebrew) whose same-named
        # interpreter is a link to this exact framework binary.
        try:matching=home.is_absolute() and (home.resolve(strict=True)==version/'bin'
                or (home/interpreter.name).resolve(strict=True)==interpreter)
        except OSError:matching=False
        if not matching:raise OwnershipError('Python framework home disagrees with the selected interpreter.')
    image=version/'Resources/Python.app/Contents/MacOS/Python'
    try:
        if not stat.S_ISREG(image.lstat().st_mode) or image.resolve(strict=True)!=image:
            raise OwnershipError('Python framework image is missing or linked.')
    except OSError:
        raise OwnershipError('Python framework image is missing or linked.') from None
    return image


def _identity_metadata(path,limit):
    """Read optional POSIX authority metadata without links/FIFOs or unbounded IO."""
    import os as native_os
    if native_os.name=='nt':
        if not path.exists():return None
        if path.stat().st_size>limit:raise OwnershipError('Interpreter metadata exceeds its bound.')
        return path.read_text(encoding='utf-8')
    flags=native_os.O_RDONLY|native_os.O_NOFOLLOW|native_os.O_NONBLOCK
    try:descriptor=native_os.open(path,flags)
    except FileNotFoundError:return None
    except OSError:raise OwnershipError('Interpreter metadata cannot be safely opened.') from None
    try:
        info=native_os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=native_os.geteuid() or info.st_size>limit:
            raise OwnershipError('Interpreter metadata ownership/type/size is invalid.')
        payload=native_os.read(descriptor,limit+1)
        after=native_os.fstat(descriptor)
        if len(payload)!=info.st_size or len(payload)>limit or (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise OwnershipError('Interpreter metadata changed or exceeds its bound.')
        try:return payload.decode('utf-8')
        except UnicodeError:raise OwnershipError('Interpreter metadata is not valid UTF-8.') from None
    finally:native_os.close(descriptor)


def _selected_python():
    selected={str(cfg.PYTHON)}
    previous=cfg.ROOT/'state/previous-release.json'
    previous_text=_identity_metadata(previous,65536)
    if previous_text is not None:
        try:value=json.loads(previous_text)
        except ValueError:raise OwnershipError('Previous release metadata is invalid.') from None
        if isinstance(value,dict) and isinstance(value.get('python'),str):selected.add(value['python'])
        if isinstance(value,dict) and isinstance(value.get('controller_python'),str):selected.add(value['controller_python'])
    if any(not Path(value).is_absolute() for value in selected):
        raise OwnershipError('Selected Python interpreter must be absolute.')
    return selected


def _bm_paths():
    if os.name=='nt':
        return {_path(Path(value).parent/'bm.exe') for value in _launchers()}
    result=set()
    for value in _selected_python():
        path=Path(value).parent/'bm'
        try:info=path.lstat()
        except FileNotFoundError:continue
        except OSError:raise OwnershipError('Index executable metadata is unavailable.') from None
        if not stat.S_ISREG(info.st_mode):
            raise OwnershipError('Linked or nonregular index executable is refused.')
        result.add(_path(path))
    return result


def _launchers():
    result={}
    selected=_selected_python()
    for value in selected:
        path=Path(value)
        if path.name.lower() not in ('python.exe','pythonw.exe','python','python3'):continue
        config=path.parent.parent/'pyvenv.cfg'
        base=None
        text=_identity_metadata(config,16384)
        if text is not None:
            homes=[line.split('=',1)[1].strip() for line in text.splitlines() if line.partition('=')[0].strip()=='home']
            if len(homes)!=1:raise OwnershipError('Python base interpreter cannot be verified.')
            base=Path(homes[0])
        if os.name!='nt':
            # POSIX aliases/framework stubs replace the same process, unlike
            # Windows launcher/base parent pairs.
            try:interpreter=path.resolve(strict=True)
            except OSError:raise OwnershipError('Selected Python interpreter is unavailable.') from None
            result[_path(interpreter)]=_path(interpreter)
            if sys.platform=='darwin':
                image=_framework_image(interpreter,base)
                if image is not None:result[_path(image)]=_path(image)
            continue
        names=('python.exe','pythonw.exe')
        for name in names:
            launcher=path.with_name(name)
            result[_path(launcher)]=_path(base/name) if base is not None else _path(launcher)
    return result


class Process:
    def __init__(self,pid,api,handle,birth,role,exe,script,parent,kind,*,lifetime=None):
        self.pid=pid;self.api=api;self.handle=handle;self.birth=birth
        self.lifetime=lifetime
        self.role=role;self.exe=exe;self.script=script;self.parent_pid=parent;self.kind=kind;self.parents=[]
    def alive(self):
        if self.api is not None:
            value=self.api.WaitForSingleObject(self.handle,0)
            if value not in (0,258):raise OwnershipError('Process exit state is unavailable.')
            return value==258
        if sys.platform=='linux':
            current=_linux_lifetime(self.pid)
            return self.lifetime is not None and current==self.lifetime
        try:return int(psutil.Process(self.pid).create_time()*10_000_000)+116444736000000000==self.birth and psutil.Process(self.pid).is_running()
        except psutil.NoSuchProcess:return False
    def terminate(self):
        if not self.alive():return
        if self.api is not None:
            if _birth(self.api,self.handle)!=self.birth:raise OwnershipError('Retained process identity changed.')
            if not self.api.TerminateProcess(self.handle,1) and self.alive():raise OwnershipError('Owned process did not terminate.')
        else:
            if sys.platform in ('linux','darwin'):
                raise OwnershipError('POSIX process signals require the verified native unit or manager; cached PIDs cannot authorize termination.')
            process=psutil.Process(self.pid)
            if int(process.create_time()*10_000_000)+116444736000000000!=self.birth:raise OwnershipError('Process identity changed.')
            process.terminate()
    def record(self):
        return {'pid':self.pid,'birth':self.birth,'role':self.role,'executable':self.exe,'script':self.script,'parent_pid':self.parent_pid,'kind':self.kind,
                **({key:self.lifetime[key] for key in ('start_ticks','boot_id')} if self.lifetime is not None else {})}
    def close(self):
        for parent in self.parents:parent.close()
        self.parents=[]
        if self.handle is not None:
            self.api.CloseHandle(self.handle);self.handle=None


def inspect(pid,*,required=False):
    """Return a retained exact role identity, or None for absent/foreign roles."""
    if type(pid) is not int or not 0<pid<=0xffffffff:raise OwnershipError('Invalid process identifier.')
    api=_api() if os.name=='nt' else None;handle=None;parents=[]
    try:
        lifetime=_linux_lifetime(pid) if sys.platform=='linux' else None
        if sys.platform=='linux' and lifetime is None:return None
        if api is not None:
            handle=api.OpenProcess(0x00101001,False,pid)
            if not handle:
                if ctypes.get_last_error()==87:return None
                raise OwnershipError('Process ownership cannot be inspected.')
            if api.WaitForSingleObject(handle,0)==0:return None
            birth=_birth(api,handle)
        process=psutil.Process(pid)
        if api is None:birth=int(process.create_time()*10_000_000)+116444736000000000
        args=process.cmdline();exe=_path(process.exe());parent=process.ppid();launchers=_launchers()
        tokens=args[1:]
        if sys.platform=='darwin':
            from brain_mac_registration import SERVICE_ENTRY
            supervisor=str(cfg.ROOT/'app/brain_supervisor.py')
            if tokens==['-I','-B','-c',SERVICE_ENTRY,supervisor]:tokens=[supervisor]
        while tokens and tokens[0] in ('-I','-B'):tokens=tokens[1:]
        role=None;script=None
        if len(tokens)==1:
            for name,filename in SCRIPTS.items():
                expected=cfg.ROOT/'app'/filename
                if expected.is_symlink() or expected.is_junction():raise OwnershipError('Linked runtime script cannot establish ownership.')
                if _path(tokens[0])==_path(expected):role=name;script=_path(tokens[0]);break
        # The index entry point is beside the selected venv alias, not the
        # canonical base interpreter or its framework exec-replacement image.
        bm_paths=_bm_paths()
        bm_arguments=(['reindex','-p','brain'],['reindex','-p','brain','--full'])
        bm=exe in bm_paths and args[1:] in bm_arguments
        child_bm=len(tokens) in (4,5) and _path(tokens[0]) in bm_paths and tokens[1:] in bm_arguments
        if bm or child_bm:role='index_worker';script=exe if bm else _path(tokens[0])
        if role is None:
            if required:raise OwnershipError('Listener is not this runtime\'s exact server process.')
            return None
        if bm:kind='launcher'
        elif exe in launchers:kind='launcher' if launchers[exe]!=exe else 'direct'
        elif exe in launchers.values():
            owner=inspect(parent,required=True)
            if owner is None or owner.kind!='launcher' or owner.birth>=birth or owner.script!=script or owner.role!=role:
                if owner is not None:owner.close()
                raise OwnershipError('Base interpreter has no verified matching launcher parent.')
            expected=launchers.get(owner.exe)
            if owner.role=='index_worker':expected=launchers.get(_path(Path(owner.exe).with_name('python.exe')))
            if expected!=exe:owner.close();raise OwnershipError('Python launcher/base pair disagrees.')
            parents.append(owner);kind='interpreter'
        else:raise OwnershipError('Runtime script uses an unrecognized interpreter.')
        if sys.platform=='linux':
            current_lifetime=_linux_lifetime(pid)
            if current_lifetime is None:return None
            if current_lifetime!=lifetime:
                raise OwnershipError('Linux process lifetime changed during ownership inspection.')
        result=Process(pid,api,handle,birth,role,exe,script,parent,kind,lifetime=lifetime);result.parents=parents
        handle=None;parents=[]
        return result
    except psutil.NoSuchProcess:return None
    except (psutil.AccessDenied,psutil.ZombieProcess) as error:
        if sys.platform=='linux' and _linux_lifetime(pid) is None:return None
        raise OwnershipError('Runtime process metadata could not be verified.') from error
    finally:
        for parent in parents:parent.close()
        if handle is not None:api.CloseHandle(handle)


def roots():
    """Discover exact role candidates; never adopt or signal cached PIDs."""
    result=[]
    try:
        for count,process in enumerate(psutil.process_iter(['pid','name','cmdline'])):
            if count>=32768:raise OwnershipError('Process inventory exceeds its safety bound.')
            known_name=(process.info.get('name') or '').lower() in ('python.exe','pythonw.exe','python','python3','bm.exe','bm')
            original_name=known_name
            if os.name=='nt' and not known_name:continue
            args=process.info.get('cmdline') or []
            candidates=[value for value in args[1:] if value and not value.startswith('-') and Path(value).name.lower() in set(SCRIPTS.values())]
            exact_script=any(_path(value)==_path(cfg.ROOT/'app'/filename) for value in candidates for filename in SCRIPTS.values())
            index_args=[value for value in args if Path(value).name.lower() in ('bm.exe','bm')]
            if not exact_script and not index_args:continue
            if os.name!='nt' and not original_name and not exact_script:
                if not any(_path(value) in _bm_paths() for value in index_args):continue
            if sys.platform=='linux' and not known_name:
                launchers=_launchers()
                selected_names={Path(value).name.lower() for value in set(launchers)|set(launchers.values())}
                known_name=(process.info.get('name') or '').lower() in selected_names
            if not known_name:
                # Editors and other interpreters can mention our script without
                # being owned. Only a selected executable merits deeper proof.
                try:executable=_path(psutil.Process(process.pid).exe())
                except psutil.NoSuchProcess:continue
                except psutil.Error:raise OwnershipError('Exact runtime candidate executable is unreadable.') from None
                launchers=_launchers()
                if executable not in set(launchers)|set(launchers.values())|_bm_paths():continue
            owned=inspect(process.pid)
            if owned is not None:
                if not known_name:
                    owned.close()
                    raise OwnershipError('A selected runtime process uses an unqualified image name; pending work is preserved, not empty.')
                result.append(owned)
        return result
    except BaseException:
        for process in result:process.close()
        raise


def descendants(owned):
    """Retain children only through a live, birth-ordered parent chain.

    This is a stop-time snapshot, taken before ending the scheduler action.
    An unreadable candidate prevents cleanup; cached PPIDs alone never authorize it.
    """
    selected={process.pid:process for process in owned};result=[]
    try:
        rows=list(psutil.process_iter(['pid','ppid']))
        if len(rows)>32768:raise OwnershipError('Process inventory exceeds its safety bound.')
        for _ in range(64):
            added=False
            for row in rows:
                if row.pid in selected or row.info.get('ppid') not in selected:continue
                parent=selected[row.info['ppid']]
                if not parent.alive():continue
                api=_api() if os.name=='nt' else None;handle=None;query=None
                try:
                    lifetime=_linux_lifetime(row.pid) if sys.platform=='linux' else None
                    if sys.platform=='linux' and lifetime is None:continue
                    if api is not None:
                        # A stale PPID can point at a reused owned PID. Query
                        # birth with least privilege before requesting authority
                        # to terminate a genuinely possible descendant.
                        query=api.OpenProcess(0x1000,False,row.pid)
                        if not query:
                            if ctypes.get_last_error()==87:continue
                            raise OwnershipError('A possible descendant birth cannot be inspected; no stop receipt issued.')
                        queried_birth=_birth(api,query)
                        if queried_birth<parent.birth:continue
                        # Keep the query handle open through the second open:
                        # it pins this process object against PID reuse.
                        handle=api.OpenProcess(0x00101001,False,row.pid)
                        if not handle:
                            raise OwnershipError('An owned descendant cannot be inspected; no stop receipt issued.')
                        exit_state=api.WaitForSingleObject(handle,0)
                        if exit_state==0:continue
                        if exit_state!=258:raise OwnershipError('Descendant exit state is unavailable.')
                        birth=_birth(api,handle)
                        if birth!=queried_birth:raise OwnershipError('Descendant identity changed during stop inspection.')
                    current=psutil.Process(row.pid)
                    if api is None:birth=int(current.create_time()*10_000_000)+116444736000000000
                    # A stale PPID may name a reused PID. A child older than its
                    # alleged parent is positively unrelated, never a stop target.
                    if sys.platform=='linux':
                        if (parent.lifetime is None or lifetime['boot_id']!=parent.lifetime['boot_id']
                            or lifetime['start_ticks']<parent.lifetime['start_ticks']):continue
                    elif birth<parent.birth:continue
                    if current.ppid()!=parent.pid or not parent.alive():
                        raise OwnershipError('Descendant ancestry changed during stop inspection.')
                    child=Process(row.pid,api,handle,birth,'descendant',_path(current.exe()),None,parent.pid,'descendant',lifetime=lifetime)
                    if sys.platform=='linux':
                        current_lifetime=_linux_lifetime(row.pid)
                        if current_lifetime is None:continue
                        if current_lifetime!=lifetime:
                            raise OwnershipError('Linux descendant lifetime changed during inspection.')
                    if current.ppid()!=parent.pid or not parent.alive():raise OwnershipError('Descendant ancestry changed during stop inspection.')
                    handle=None;result.append(child);selected[child.pid]=child;added=True
                except psutil.NoSuchProcess:continue
                except psutil.Error as error:
                    if sys.platform=='linux' and _linux_lifetime(row.pid) is None:continue
                    raise OwnershipError('Descendant metadata could not be verified.') from error
                finally:
                    if handle is not None:api.CloseHandle(handle)
                    if query is not None:api.CloseHandle(query)
            if not added:return result
        raise OwnershipError('Process ancestry exceeds its safety bound.')
    except BaseException:
        for process in result:process.close()
        raise


def _mac_socket_snapshot():
    """Inspect each owned process lifetime once, never signal.

    psutil rows describe file descriptors; they are not proven distinct sockets.
    Multiple descriptor rows remain a conservative refusal pending native proof.
    """
    owned = []
    children = []
    result = []
    visited = {}
    try:
        owned = roots()
        servers = [process for process in owned if process.role == 'server']
        for process in owned:
            if process.role != 'server':
                continue
            related = descendants([process])
            children.extend(related)
            for candidate in (process, *related):
                # A server interpreter can be both an exact role root and a
                # launcher's descendant. Inspect each lifetime once, preserving
                # multiple sockets returned by that one process.
                if candidate.pid in visited:
                    if visited[candidate.pid] != candidate.birth:
                        raise _MacInventoryChanged('Server identity changed between ownership paths.')
                    continue
                visited[candidate.pid] = candidate.birth
                if not candidate.alive():
                    raise _MacInventoryChanged('Server lifetime changed before socket inspection.')
                rows = psutil.Process(candidate.pid).net_connections(kind='tcp')
                if len(rows) > 65536:
                    raise OwnershipError('Server socket inventory exceeds its bound.')
                if not candidate.alive():
                    raise _MacInventoryChanged('Server lifetime changed during socket inspection.')
                for row in rows:
                    if not row.laddr:
                        continue
                    if row.status == psutil.CONN_LISTEN and row.laddr.port == cfg.PORT:
                        result.append(SimpleNamespace(pid=candidate.pid, birth=candidate.birth,
                                                      laddr=row.laddr, status=row.status))
        return result, bool(servers)
    except psutil.NoSuchProcess:
        failed = 'changed'
    except (psutil.Error, OSError):
        failed = 'unreadable'
    finally:
        for process in children + owned:
            process.close()
    if failed == 'changed':
        raise _MacInventoryChanged('Server lifetime changed during socket inspection.') from None
    raise OwnershipError('Socket inventory unreadable; listener absence is not established.') from None


def _mac_listeners():
    """Cross-check global presence with positive owned sockets, never connect.

    This candidate still needs native product/lifecycle/identity qualification.
    Global rows alone never identify an owner or authorize a signal.
    """
    import brain_mac_inventory as inventory
    deadline = time.monotonic()+1.5
    unverified = False
    for attempt in range(3):
        if attempt and time.monotonic() >= deadline:
            break
        changed = False
        unreadable = False
        unverified = False
        try:
            first = inventory.read(cfg.PORT, timeout=5)
            result, _starting = _mac_socket_snapshot()
            again = inventory.read(cfg.PORT, timeout=5)
        except _MacInventoryChanged:
            changed = True
        except inventory.InventoryError:
            unreadable = True
        if unreadable:
            raise OwnershipError('Socket inventory unreadable; listener absence is not established.') from None
        if not changed:
            if first == again:
                if not first and not result:
                    return []
                if first and (len(first) != 1 or first[0] != ('tcp4', '127.0.0.1', cfg.PORT)):
                    raise OwnershipError('Port has multiple, foreign or unverified listeners; no credentials sent.')
                if (len(first) == 1 and len(result) == 1
                        and result[0].laddr.ip == '127.0.0.1' and result[0].laddr.port == cfg.PORT):
                    return result
                if len(result) > 1:
                    raise OwnershipError('Port has multiple, foreign or unverified listeners; no credentials sent.')
                unverified = bool(first) and not result
            # Includes G=1/S=0, G=0/S=1, and a changing global table.
        if attempt == 2 or time.monotonic() >= deadline:
            break
        time.sleep(.02)
    if unverified:
        raise OwnershipError('Configured IPv4 destination has no verified socket owner; no credentials sent.')
    raise OwnershipError('Socket inventory changed during inspection; no credentials sent.')


def _listeners():
    try:
        if sys.platform == 'darwin':
            return _mac_listeners()
        return [row for row in psutil.net_connections(kind='tcp') if row.status==psutil.CONN_LISTEN and row.laddr.port==cfg.PORT]
    except (psutil.Error,OSError) as error:raise OwnershipError('Local listener ownership is unavailable.') from error


def listener_identity():
    """Verify listener -> server launcher -> live recorded supervisor."""
    rows=_listeners()
    if not rows:return None
    if len(rows)!=1 or rows[0].pid is None or rows[0].laddr.ip!='127.0.0.1':
        raise OwnershipError('Port has multiple, foreign or unverified listeners; no credentials sent.')
    owned=inspect(rows[0].pid,required=True)
    if owned is None:raise OwnershipError('Listener changed during ownership inspection.')
    supervisor=None
    try:
        if owned.role!='server':raise OwnershipError('Listener is not the runtime server.')
        if getattr(rows[0],'birth',owned.birth)!=owned.birth:
            raise OwnershipError('Listener lifetime changed during ownership inspection.')
        launcher=owned.parents[0] if owned.kind=='interpreter' else owned
        registry=cfg.ROOT/'state/service-pids.json'
        if not registry.exists() or registry.stat().st_size>65536:raise OwnershipError('Supervisor registry is absent or invalid.')
        value=json.loads(registry.read_text(encoding='utf-8'))
        pid=value.get('supervisor') if isinstance(value,dict) else None
        if launcher.parent_pid!=pid:raise OwnershipError('Owned orphan found; run service stop then explicit service start.')
        supervisor=inspect(pid,required=True)
        if supervisor is None or supervisor.role!='supervisor' or not _parent_precedes(supervisor,launcher):
            raise OwnershipError('Live supervisor ownership could not be established.')
        again=_listeners()
        if (len(again)!=1 or again[0].pid!=owned.pid
                or again[0].laddr.ip!=rows[0].laddr.ip
                or again[0].laddr.port!=rows[0].laddr.port
                or getattr(again[0],'birth',owned.birth)!=owned.birth):
            raise OwnershipError('Listener changed during ownership verification.')
        if sys.platform=='linux' and (not owned.alive() or not supervisor.alive()):
            raise OwnershipError('Linux listener or supervisor lifetime changed during verification.')
        return {**owned.record(),'supervisor_pid':supervisor.pid,'supervisor_birth':supervisor.birth,
                **({'supervisor_start_ticks':supervisor.lifetime['start_ticks'],
                    'supervisor_boot_id':supervisor.lifetime['boot_id']} if sys.platform=='linux' else {})}
    finally:
        owned.close()
        if supervisor is not None:supervisor.close()


def enter_supervisor_job():
    global _JOB
    if os.name!='nt' or _JOB is not None:return
    from brain_windows_process import _ExtendedLimit
    api=_api();job=api.CreateJobObjectW(None,None)
    if not job:raise OwnershipError('Supervisor containment could not be created.')
    limits=_ExtendedLimit();limits.BasicLimitInformation.LimitFlags=0x2000
    if not api.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(job,api.GetCurrentProcess()):
        api.CloseHandle(job);raise OwnershipError('Supervisor containment could not be established; no children started.')
    _JOB=job  # The sole non-inherited handle closes when this interpreter exits.
