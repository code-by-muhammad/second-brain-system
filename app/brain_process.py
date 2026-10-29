"""Host-local service ownership. Never infer ownership from a PID/name alone."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import psutil
import brain_settings as cfg

SCRIPTS={name:'brain_'+name+'.py' for name in ('supervisor','server','index_bootstrap','maintenance')}
_JOB=None


class OwnershipError(RuntimeError): pass


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


def _launchers():
    selected={str(cfg.PYTHON)}
    previous=cfg.ROOT/'state/previous-release.json'
    if previous.exists():
        if previous.stat().st_size>65536:raise OwnershipError('Previous release metadata exceeds its bound.')
        value=json.loads(previous.read_text(encoding='utf-8'))
        if isinstance(value,dict) and isinstance(value.get('python'),str):selected.add(value['python'])
        if isinstance(value,dict) and isinstance(value.get('controller_python'),str):selected.add(value['controller_python'])
    result={}
    for value in selected:
        path=Path(value)
        if path.name.lower() not in ('python.exe','pythonw.exe','python','python3'):continue
        config=path.parent.parent/'pyvenv.cfg'
        base=None
        if config.exists():
            with config.open('r',encoding='utf-8') as stream:text=stream.read(16385)
            if len(text)>16384:raise OwnershipError('Python environment metadata exceeds its bound.')
            homes=[line.split('=',1)[1].strip() for line in text.splitlines() if line.partition('=')[0].strip()=='home']
            if len(homes)!=1:raise OwnershipError('Python base interpreter cannot be verified.')
            base=Path(homes[0])
        names=('python.exe','pythonw.exe') if os.name=='nt' else (path.name,)
        for name in names:
            launcher=path.with_name(name)
            result[_path(launcher)]=_path(base/name) if base is not None else _path(launcher)
    return result


class Process:
    def __init__(self,pid,api,handle,birth,role,exe,script,parent,kind):
        self.pid=pid;self.api=api;self.handle=handle;self.birth=birth
        self.role=role;self.exe=exe;self.script=script;self.parent_pid=parent;self.kind=kind;self.parents=[]
    def alive(self):
        if self.api is not None:
            value=self.api.WaitForSingleObject(self.handle,0)
            if value not in (0,258):raise OwnershipError('Process exit state is unavailable.')
            return value==258
        try:return int(psutil.Process(self.pid).create_time()*10_000_000)+116444736000000000==self.birth and psutil.Process(self.pid).is_running()
        except psutil.NoSuchProcess:return False
    def terminate(self):
        if not self.alive():return
        if self.api is not None:
            if _birth(self.api,self.handle)!=self.birth:raise OwnershipError('Retained process identity changed.')
            if not self.api.TerminateProcess(self.handle,1) and self.alive():raise OwnershipError('Owned process did not terminate.')
        else:
            process=psutil.Process(self.pid)
            if int(process.create_time()*10_000_000)+116444736000000000!=self.birth:raise OwnershipError('Process identity changed.')
            process.terminate()
    def record(self):
        return {'pid':self.pid,'birth':self.birth,'role':self.role,'executable':self.exe,'script':self.script,'parent_pid':self.parent_pid,'kind':self.kind}
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
        while tokens and tokens[0] in ('-I','-B'):tokens=tokens[1:]
        role=None;script=None
        if len(tokens)==1:
            for name,filename in SCRIPTS.items():
                expected=cfg.ROOT/'app'/filename
                if expected.is_symlink() or expected.is_junction():raise OwnershipError('Linked runtime script cannot establish ownership.')
                if _path(tokens[0])==_path(expected):role=name;script=_path(tokens[0]);break
        bm_paths={_path(Path(value).parent/('bm.exe' if os.name=='nt' else 'bm')) for value in launchers}
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
        result=Process(pid,api,handle,birth,role,exe,script,parent,kind);result.parents=parents
        handle=None;parents=[]
        return result
    except psutil.NoSuchProcess:return None
    except (psutil.AccessDenied,psutil.ZombieProcess) as error:
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
            if (process.info.get('name') or '').lower() not in ('python.exe','pythonw.exe','python','python3','bm.exe','bm'):continue
            args=process.info.get('cmdline') or []
            candidates=[value for value in args[1:] if value and not value.startswith('-') and Path(value).name.lower() in set(SCRIPTS.values())]
            if not any(_path(value)==_path(cfg.ROOT/'app'/filename) for value in candidates for filename in SCRIPTS.values()) and not any(Path(value).name.lower() in ('bm.exe','bm') for value in args):continue
            owned=inspect(process.pid)
            if owned is not None:result.append(owned)
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
                api=_api() if os.name=='nt' else None;handle=None
                try:
                    if api is not None:
                        handle=api.OpenProcess(0x00101001,False,row.pid)
                        if not handle:
                            if ctypes.get_last_error()==87:continue
                            raise OwnershipError('An owned descendant cannot be inspected; no stop receipt issued.')
                        if api.WaitForSingleObject(handle,0)==0:continue
                        birth=_birth(api,handle)
                    current=psutil.Process(row.pid)
                    if api is None:birth=int(current.create_time()*10_000_000)+116444736000000000
                    # A stale PPID may name a reused PID. A child older than its
                    # alleged parent is positively unrelated, never a stop target.
                    if birth<parent.birth:continue
                    if current.ppid()!=parent.pid or not parent.alive():
                        raise OwnershipError('Descendant ancestry changed during stop inspection.')
                    child=Process(row.pid,api,handle,birth,'descendant',_path(current.exe()),None,parent.pid,'descendant')
                    if current.ppid()!=parent.pid or not parent.alive():raise OwnershipError('Descendant ancestry changed during stop inspection.')
                    handle=None;result.append(child);selected[child.pid]=child;added=True
                except psutil.NoSuchProcess:continue
                except psutil.Error as error:raise OwnershipError('Descendant metadata could not be verified.') from error
                finally:
                    if handle is not None:api.CloseHandle(handle)
            if not added:return result
        raise OwnershipError('Process ancestry exceeds its safety bound.')
    except BaseException:
        for process in result:process.close()
        raise


def _listeners():
    try:
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
        launcher=owned.parents[0] if owned.kind=='interpreter' else owned
        registry=cfg.ROOT/'state/service-pids.json'
        if not registry.exists() or registry.stat().st_size>65536:raise OwnershipError('Supervisor registry is absent or invalid.')
        value=json.loads(registry.read_text(encoding='utf-8'))
        pid=value.get('supervisor') if isinstance(value,dict) else None
        if launcher.parent_pid!=pid:raise OwnershipError('Owned orphan found; run service stop then explicit service start.')
        supervisor=inspect(pid,required=True)
        if supervisor is None or supervisor.role!='supervisor' or supervisor.birth>=launcher.birth:
            raise OwnershipError('Live supervisor ownership could not be established.')
        again=_listeners()
        if len(again)!=1 or again[0].pid!=owned.pid:raise OwnershipError('Listener changed during ownership verification.')
        return {**owned.record(),'supervisor_pid':supervisor.pid,'supervisor_birth':supervisor.birth}
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
