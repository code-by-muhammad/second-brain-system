"""Private Windows Git runner: atomic Job association and file-backed stdio.

This is the small subprocess.run subset used by brain_git, not a shell runner.
Windows 10+ is required. No product imports and no Win32 calls occur on import.
"""
import ctypes
from ctypes import wintypes as wt
import io
import math
import os
import shutil
import subprocess
import tempfile
import time


class _StartupInfo(ctypes.Structure):
    _fields_=[('cb',wt.DWORD),('lpReserved',wt.LPWSTR),('lpDesktop',wt.LPWSTR),
              ('lpTitle',wt.LPWSTR),('dwX',wt.DWORD),('dwY',wt.DWORD),
              ('dwXSize',wt.DWORD),('dwYSize',wt.DWORD),('dwXCountChars',wt.DWORD),
              ('dwYCountChars',wt.DWORD),('dwFillAttribute',wt.DWORD),
              ('dwFlags',wt.DWORD),('wShowWindow',wt.WORD),('cbReserved2',wt.WORD),
              ('lpReserved2',ctypes.POINTER(wt.BYTE)),('hStdInput',wt.HANDLE),
              ('hStdOutput',wt.HANDLE),('hStdError',wt.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_=[('StartupInfo',_StartupInfo),('lpAttributeList',ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_=[('hProcess',wt.HANDLE),('hThread',wt.HANDLE),
              ('dwProcessId',wt.DWORD),('dwThreadId',wt.DWORD)]


class _BasicLimit(ctypes.Structure):
    _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),
              ('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',wt.DWORD),
              ('MinimumWorkingSetSize',ctypes.c_size_t),
              ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wt.DWORD),
              ('Affinity',ctypes.c_size_t),('PriorityClass',wt.DWORD),
              ('SchedulingClass',wt.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount',
              'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class _ExtendedLimit(ctypes.Structure):
    _fields_=[('BasicLimitInformation',_BasicLimit),('IoInfo',_IoCounters),
              ('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),
              ('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_=[('TotalUserTime',ctypes.c_int64),('TotalKernelTime',ctypes.c_int64),
              ('ThisPeriodTotalUserTime',ctypes.c_int64),('ThisPeriodTotalKernelTime',ctypes.c_int64),
              ('TotalPageFaultCount',wt.DWORD),('TotalProcesses',wt.DWORD),
              ('ActiveProcesses',wt.DWORD),('TotalTerminatedProcesses',wt.DWORD)]


class _CompletionAssociation(ctypes.Structure):
    _fields_=[('CompletionKey',ctypes.c_void_p),('CompletionPort',wt.HANDLE)]


class _ProcessIds(ctypes.Structure):
    _fields_=[('NumberOfAssignedProcesses',wt.DWORD),('NumberOfProcessIdsInList',wt.DWORD)]


def _kernel():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    definitions={
        'CreateJobObjectW':([ctypes.c_void_p,wt.LPCWSTR],wt.HANDLE),
        'SetInformationJobObject':([wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD],wt.BOOL),
        'QueryInformationJobObject':([wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD)],wt.BOOL),
        'TerminateJobObject':([wt.HANDLE,wt.UINT],wt.BOOL),
        'GetCurrentProcess':([],wt.HANDLE),
        'DuplicateHandle':([wt.HANDLE,wt.HANDLE,wt.HANDLE,ctypes.POINTER(wt.HANDLE),wt.DWORD,wt.BOOL,wt.DWORD],wt.BOOL),
        'InitializeProcThreadAttributeList':([ctypes.c_void_p,wt.DWORD,wt.DWORD,ctypes.POINTER(ctypes.c_size_t)],wt.BOOL),
        'UpdateProcThreadAttribute':([ctypes.c_void_p,wt.DWORD,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_void_p],wt.BOOL),
        'DeleteProcThreadAttributeList':([ctypes.c_void_p],None),
        'CreateProcessW':([wt.LPCWSTR,wt.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,wt.BOOL,wt.DWORD,ctypes.c_void_p,wt.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(_ProcessInformation)],wt.BOOL),
        'WaitForSingleObject':([wt.HANDLE,wt.DWORD],wt.DWORD),
        'GetExitCodeProcess':([wt.HANDLE,ctypes.POINTER(wt.DWORD)],wt.BOOL),
        'CloseHandle':([wt.HANDLE],wt.BOOL),
        'CreateIoCompletionPort':([wt.HANDLE,wt.HANDLE,ctypes.c_size_t,wt.DWORD],wt.HANDLE),
        'GetQueuedCompletionStatus':([wt.HANDLE,ctypes.POINTER(wt.DWORD),ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_void_p),wt.DWORD],wt.BOOL),
        'OpenProcess':([wt.DWORD,wt.BOOL,wt.DWORD],wt.HANDLE),
        'IsProcessInJob':([wt.HANDLE,wt.HANDLE,ctypes.POINTER(wt.BOOL)],wt.BOOL),
        'GetProcessTimes':([wt.HANDLE,ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME)],wt.BOOL),
        'GetProcessId':([wt.HANDLE],wt.DWORD),
    }
    for name,(arguments,result) in definitions.items():
        function=getattr(api,name);function.argtypes=arguments;function.restype=result
    return api


def _checked(result):
    if not result:raise ctypes.WinError(ctypes.get_last_error())
    return result


class _LifetimeTracker:
    """Require object-lifetime evidence, not only Job accounting, for release.

    One coverage slot per PID deliberately undercounts ambiguous PID reuse.
    Positive member handles remain open until completion, preventing their PID
    from being recycled. A trusted NEW_PROCESS notice whose valid OpenProcess
    lookup returns87 may fill one already-gone slot; snapshots alone may not.
    Missing notifications or uncertain identity reduce availability, never the
    amount of evidence required before caller locks can be released.
    """
    _BATCH=64
    def __init__(self,api,job):
        self.api=api;self.job=job;self.port=None
        self._members={};self._pending={};self._seen_new=set()
        self._root_handle=None;self._root_pid=None
        self._capacity=16;self._snapshot_cursor=0;self._pending_cursor=0;self._wait_cursor=0
        self._uncertain=False;self._maximum_total=0;self._coverage_invalid=False

    def associate(self):
        # Unnamed, noninherited and exclusive to this still-empty private Job.
        self.port=_checked(self.api.CreateIoCompletionPort(wt.HANDLE(-1),None,0,1))
        association=_CompletionAssociation();association.CompletionKey=1;association.CompletionPort=self.port
        _checked(self.api.SetInformationJobObject(self.job,7,ctypes.byref(association),ctypes.sizeof(association)))

    @staticmethod
    def _pid(value):
        if type(value) is not int or not 0<value<=0xffffffff:
            raise OSError('Invalid owned Job process identifier.')
        return value

    def _birth(self,handle):
        created,exited,kernel,user=(wt.FILETIME() for _ in range(4))
        _checked(self.api.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user)))
        return int(created.dwLowDateTime)+(int(created.dwHighDateTime)<<32)

    def _member(self,handle):
        belongs=wt.BOOL()
        _checked(self.api.IsProcessInJob(handle,self.job,ctypes.byref(belongs)))
        if not belongs.value:raise OSError('Owned Job process identity is uncertain.')

    def _attach_root(self,handle):
        if self._root_handle is not None:
            if self._root_handle!=handle:raise OSError('Owned root process handle changed.')
            row=self._members[self._root_pid]
            if row['creation_time'] is None:row['creation_time']=self._birth(handle)
            return
        pid=self._pid(int(_checked(self.api.GetProcessId(handle))))
        self._member(handle)
        # This handle is borrowed from PROCESS_INFORMATION, never opened or
        # closed by the tracker, even after post-CreateProcess interruption.
        self._members[pid]={'handle':handle,'owned':False,'creation_time':None,
                            'signaled':False,'gone':False,'proof':'owned-root-handle'}
        self._root_handle=handle;self._root_pid=pid;self._pending.pop(pid,None)
        self._members[pid]['creation_time']=self._birth(handle)

    def _observe(self,pid,from_new=False):
        pid=self._pid(pid)
        if from_new:self._seen_new.add(pid)
        self._pending[pid]=pid in self._seen_new
        prior=self._members.get(pid)
        if prior is not None and prior['handle'] is not None:
            if prior['creation_time'] is None:prior['creation_time']=self._birth(prior['handle'])
            self._pending.pop(pid,None)
            return
        handle=self.api.OpenProcess(0x00101000,False,pid)
        if not handle:
            error=ctypes.get_last_error()
            if error==87 and pid in self._seen_new:
                # Under valid access flags/nonzero DWORD PID on supported
                # Windows, this infers absence of a process object at lookup.
                # It is not an exit-code or ActiveProcesses inference. Count
                # this trusted Job birth at most once, including repeat notices.
                self._members.setdefault(pid,{'handle':None,'owned':False,'creation_time':None,
                    'signaled':True,'gone':True,'proof':'job-new-process-and-OpenProcess87'})
                self._pending.pop(pid,None)
                return
            raise ctypes.WinError(error)
        accepted=False
        try:
            self._member(handle)
            # A later live member after a gone slot is retained, but its PID
            # slot never inflates coverage: TotalProcesses mismatch holds safely.
            self._members[pid]={'handle':handle,'owned':True,'creation_time':None,
                                'signaled':False,'gone':False,'proof':'positive-job-member-handle',
                                'prior_gone_slot':prior is not None}
            accepted=True
            self._members[pid]['creation_time']=self._birth(handle)
            self._pending.pop(pid,None)
        finally:
            if not accepted:self.api.CloseHandle(handle)

    def _attempt(self,pid,from_new=False):
        try:self._observe(pid,from_new)
        except OSError:self._uncertain=True

    def _notices(self):
        for _ in range(self._BATCH):
            message=wt.DWORD();key=ctypes.c_size_t();value=ctypes.c_void_p()
            result=self.api.GetQueuedCompletionStatus(self.port,ctypes.byref(message),ctypes.byref(key),ctypes.byref(value),0)
            if not result:
                error=ctypes.get_last_error()
                if value.value is None and error==258:return
                raise ctypes.WinError(error)
            if key.value!=1:raise OSError('Unexpected private Job completion key.')
            if message.value==6:self._attempt(self._pid(int(value.value or 0)),True)
            # EXIT_PROCESS and ACTIVE_PROCESS_ZERO are hints, never lifetime
            # completion evidence; only a signaled retained handle proves exit.

    def _snapshot(self):
        size=ctypes.sizeof(_ProcessIds)+self._capacity*ctypes.sizeof(ctypes.c_size_t)
        buffer=ctypes.create_string_buffer(size);header=_ProcessIds.from_buffer(buffer)
        result=self.api.QueryInformationJobObject(self.job,3,buffer,size,None)
        if not result:
            error=ctypes.get_last_error()
            if error!=234:raise ctypes.WinError(error)
            self._uncertain=True
        count=int(header.NumberOfProcessIdsInList)
        if count>self._capacity:raise OSError('Invalid Job process snapshot length.')
        self._capacity=max(16,int(header.NumberOfAssignedProcesses))
        if count:
            values=(ctypes.c_size_t*count).from_buffer(buffer,ctypes.sizeof(_ProcessIds))
            start=self._snapshot_cursor%count
            for index in range(min(count,self._BATCH)):
                self._attempt(int(values[(start+index)%count]))
            self._snapshot_cursor=start+self._BATCH

    def _retry(self):
        pending=list(self._pending)
        if pending:
            start=self._pending_cursor%len(pending)
            for index in range(min(len(pending),self._BATCH)):
                pid=pending[(start+index)%len(pending)]
                self._attempt(pid,pid in self._seen_new)
            self._pending_cursor=start+self._BATCH

    def _wait_members(self):
        outstanding=[row for row in self._members.values() if row['handle'] is not None and not row['signaled']]
        if outstanding:
            start=self._wait_cursor%len(outstanding)
            for index in range(min(len(outstanding),self._BATCH)):
                row=outstanding[(start+index)%len(outstanding)]
                state=self.api.WaitForSingleObject(row['handle'],0)
                if state==0:row['signaled']=True
                elif state==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
                elif state!=258:raise OSError('Unexpected owned member wait result.')
            self._wait_cursor=start+self._BATCH

    def collect(self,process):
        self._uncertain=False
        try:self._attach_root(process)
        except OSError:
            self._uncertain=True
            return
        for operation in (self._notices,self._snapshot,self._retry,self._wait_members):
            try:operation()
            except OSError:self._uncertain=True

    def complete(self,process):
        self.collect(process)
        if self._uncertain or self._pending or self._coverage_invalid or self._root_handle is None:
            return False
        if any(not row['signaled'] or (row['creation_time'] is None and not row['gone'])
               for row in self._members.values()):return False
        # All retained creators are now signaled. Only THEN take the fresh
        # accounting used to rule out a late, unobserved process birth.
        accounting=_Accounting()
        _checked(self.api.QueryInformationJobObject(self.job,1,ctypes.byref(accounting),ctypes.sizeof(accounting),None))
        total=int(accounting.TotalProcesses)
        if total<self._maximum_total:self._coverage_invalid=True
        self._maximum_total=max(self._maximum_total,total)
        return not self._coverage_invalid and accounting.ActiveProcesses==0 and total==len(self._members)

    def close(self):
        for row in self._members.values():
            if row['owned'] and row['handle'] is not None:self.api.CloseHandle(row['handle'])
        if self.port:self.api.CloseHandle(self.port)


def _empty(api,job,process,tracker):
    if tracker.api is not api or tracker.job!=job:raise OSError('Wrong owned Job lifetime tracker.')
    return tracker.complete(process)


def _cleanup_owned(api,job,process,on_cleanup_wait,tracker):
    """Keep the live Job and caller's locks until termination is verified.

    Failed Win32 queries/termination do not authorize exception unwinding. The
    owning process retries, and reports only a fixed diagnostic once. This
    deliberately has no second deadline that could abandon live descendants.
    """
    next_report=time.monotonic()+0.5;notified=False
    while True:
        try:
            try:
                tracker.collect(process)
            except BaseException:pass
            try:
                api.TerminateJobObject(job,1)
                if _empty(api,job,process,tracker):return
            except BaseException:pass
            if not notified and time.monotonic()>=next_report:
                next_report=time.monotonic()+1
                try:
                    if on_cleanup_wait is not None:on_cleanup_wait()
                except BaseException:pass
                else:notified=True
            time.sleep(0.05)
        except BaseException:
            # Includes interruption: neither cancellation nor an API failure
            # may release writer/maintenance ownership while cleanup is unknown.
            pass


def _environment(values):
    pairs=[];seen=set()
    for key,value in values.items():
        if not isinstance(key,str) or not isinstance(value,str):raise TypeError('Windows environment must contain text.')
        if not key or '\x00' in key+value or '=' in key[1:]:raise ValueError('Invalid Windows environment.')
        folded=key.upper()
        if folded in seen:raise ValueError('Ambiguous Windows environment name.')
        seen.add(folded);pairs.append((key,value))
    pairs.sort(key=lambda item:item[0].upper())
    return ctypes.create_unicode_buffer('\x00'.join(key+'='+value for key,value in pairs)+'\x00\x00')


def _output(stream,destination,text,encoding):
    stream.seek(0)
    if destination==subprocess.DEVNULL:return None
    if destination==subprocess.PIPE:
        if text:
            # Match subprocess text mode's UTF-8 decoding/universal newlines.
            wrapper=io.TextIOWrapper(stream,encoding=encoding,newline=None)
            try:return wrapper.read()
            finally:wrapper.detach()
        return stream.read()
    shutil.copyfileobj(stream,destination,65536)
    destination.flush()
    return None


def run(command,*,input=None,capture_output=False,stdout=None,stderr=None,text=False,
        encoding=None,env=None,timeout=90,on_cleanup_wait=None):
    """Run current Git call shapes, with one deadline for direct + descendant work.

    Regular temporary files replace all pipe ends, including stdin. The only
    inherited handles are three explicit duplicates of those temporary files.
    JOB_LIST assigns a non-breakaway kill-on-close Job before the initial thread
    runs; the calling maintenance process is never added to that Job.
    A signaled root gets one second for remaining background work, then the
    same verified Job cleanup runs before its original exit code is returned.
    The operation deadline still wins if it arrives first.
    """
    if os.name!='nt':raise OSError('Windows process runner requires Windows 10 or newer.')
    import msvcrt
    if capture_output:
        if stdout is not None or stderr is not None:raise ValueError('capture_output conflicts with explicit streams.')
        stdout=stderr=subprocess.PIPE
    for destination in (stdout,stderr):
        if destination not in (subprocess.PIPE,subprocess.DEVNULL) and not (hasattr(destination,'write') and hasattr(destination,'fileno')):
            raise ValueError('Git output requires capture, discard or a binary file.')
    if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('A positive finite Git deadline is required.')
    arguments=[os.fsdecode(value) for value in command]
    if not arguments or any('\x00' in value for value in arguments):raise ValueError('Invalid command arguments.')
    environment=dict(os.environ if env is None else env)
    application=shutil.which(arguments[0],path=environment.get('PATH'))
    if not application:raise FileNotFoundError('Git executable is unavailable.')
    application=os.path.abspath(application)
    line=ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    block=_environment(environment)
    text=bool(text or encoding is not None);encoding=encoding or 'utf-8'
    if input is not None and text and not isinstance(input,str):raise TypeError('Text Git input must be text.')
    api=_kernel();process=_ProcessInformation();job=None;tracker=None;attributes=None;attributes_initialized=False
    inherited=[];verified_empty=False;deadline=time.monotonic()+timeout
    root_finished_at=None;root_exit_code=None
    with tempfile.TemporaryFile(mode='w+b') as incoming, tempfile.TemporaryFile(mode='w+b') as outgoing, tempfile.TemporaryFile(mode='w+b') as errors:
        if input is not None:
            if text:
                wrapper=io.TextIOWrapper(incoming,encoding=encoding,newline=None)
                try:wrapper.write(input);wrapper.flush()
                finally:wrapper.detach()
            else:incoming.write(input)
        incoming.flush();incoming.seek(0)
        try:
            job=_checked(api.CreateJobObjectW(None,None))
            limits=_ExtendedLimit();limits.BasicLimitInformation.LimitFlags=0x2000
            _checked(api.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)))
            tracker=_LifetimeTracker(api,job)
            tracker.associate()
            owner=api.GetCurrentProcess()
            for stream in (incoming,outgoing,errors):
                handle=wt.HANDLE()
                _checked(api.DuplicateHandle(owner,wt.HANDLE(msvcrt.get_osfhandle(stream.fileno())),owner,ctypes.byref(handle),0,True,2))
                inherited.append(handle)
            size=ctypes.c_size_t()
            initialized=api.InitializeProcThreadAttributeList(None,2,0,ctypes.byref(size))
            if initialized or ctypes.get_last_error()!=122 or not size.value:
                raise OSError('Windows process attribute sizing failed.')
            attributes=ctypes.create_string_buffer(size.value)
            _checked(api.InitializeProcThreadAttributeList(attributes,2,0,ctypes.byref(size)))
            attributes_initialized=True
            handles=(wt.HANDLE*3)(*(handle.value for handle in inherited))
            jobs=(wt.HANDLE*1)(job)
            _checked(api.UpdateProcThreadAttribute(attributes,0,0x00020002,handles,ctypes.sizeof(handles),None,None))
            _checked(api.UpdateProcThreadAttribute(attributes,0,0x0002000d,jobs,ctypes.sizeof(jobs),None,None))
            startup=_StartupInfoEx();startup.StartupInfo.cb=ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags=0x100
            startup.StartupInfo.hStdInput,startup.StartupInfo.hStdOutput,startup.StartupInfo.hStdError=handles
            startup.lpAttributeList=ctypes.cast(attributes,ctypes.c_void_p)
            # No suspended/assign/resume window. Unsupported Job nesting fails
            # before any child can run; there is no uncontained fallback.
            if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(arguments,timeout)
            _checked(api.CreateProcessW(application,line,None,None,True,0x00080000|0x00000400|0x08000000,
                                        block,None,ctypes.byref(startup),ctypes.byref(process)))
            api.DeleteProcThreadAttributeList(attributes);attributes_initialized=False
            for handle in inherited:api.CloseHandle(handle)
            inherited.clear()
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(arguments,timeout)
                if _empty(api,job,process.hProcess,tracker):break
                # Wake promptly when ordinary Git exits. Sleep only while a
                # completed root still has active owned descendants.
                state=api.WaitForSingleObject(process.hProcess,max(1,min(50,math.ceil(remaining*1000))))
                if state==0:
                    if root_finished_at is None:
                        code=wt.DWORD();_checked(api.GetExitCodeProcess(process.hProcess,ctypes.byref(code)))
                        root_exit_code=int(code.value);root_finished_at=time.monotonic()
                    if _empty(api,job,process.hProcess,tracker):break
                    now=time.monotonic()
                    if now>=deadline:raise subprocess.TimeoutExpired(arguments,timeout)
                    if now-root_finished_at>=1.0:
                        _cleanup_owned(api,job,process.hProcess,on_cleanup_wait,tracker)
                        verified_empty=True
                        if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(arguments,timeout)
                        break
                    time.sleep(min(0.005,max(0.0,deadline-time.monotonic())))
                elif state==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
                elif state!=258:raise OSError('Unexpected owned process wait result.')
            verified_empty=True
            if root_exit_code is None:
                code=wt.DWORD();_checked(api.GetExitCodeProcess(process.hProcess,ctypes.byref(code)))
                root_exit_code=int(code.value)
            return subprocess.CompletedProcess(arguments,root_exit_code,
                _output(outgoing,stdout,text,encoding),_output(errors,stderr,text,encoding))
        finally:
            # PROCESS_INFORMATION is filled atomically by CreateProcess. Inspect
            # its live handle even if interruption arrived immediately afterward.
            if process.hProcess and not verified_empty:
                _cleanup_owned(api,job,process.hProcess,on_cleanup_wait,tracker)
            if attributes_initialized:api.DeleteProcThreadAttributeList(attributes)
            for handle in inherited:api.CloseHandle(handle)
            if tracker is not None:tracker.close()
            for handle in (process.hThread,process.hProcess,job):
                if handle:api.CloseHandle(handle)
