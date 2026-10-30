"""R142 ownership regressions: private fixtures, real children, no real services.

Native Windows lifetime cases use the actual runner/API. Startup-failure,
cleanup-reporter and tracker controls deliberately inject API states/faults.
The standalone children expire naturally so a failed assertion cannot leave a
permanent test process. Product hashes belong in the preparation receipt, not
in this shipping module.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

from filelock import FileLock, Timeout
import pytest

import brain_git as g


WINDOWS = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows Job Object contract')
POSIX = pytest.mark.skipif(sys.platform == 'win32', reason='Actual POSIX import/dispatch contract')
CHILD_LIFETIME = 6.0
COMMAND_DEADLINE = 1.8
RETURN_BOUND = 4.5
PUBLIC_COMMIT_DEADLINE = 0.5


@pytest.fixture
def windows_runner():
    # Helper controls are AFTER-only. The public-sync regression must collect
    # and execute against the old source that does not contain this module.
    import brain_windows_process
    return brain_windows_process

CHILD_SOURCE = r'''import json,os,subprocess,sys,time
from pathlib import Path
role=sys.argv[1];folder=Path(sys.argv[2])
def publish(name,value):
    temporary=folder/(name+'.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.replace(folder/name)
def identity():
    value={'pid':os.getpid(),'parent':os.getppid()}
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as wt
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.GetCurrentProcess.restype=wt.HANDLE
        api.GetProcessTimes.argtypes=[wt.HANDLE,ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME),ctypes.POINTER(wt.FILETIME)]
        api.GetProcessTimes.restype=wt.BOOL
        created,exited,kernel,user=(wt.FILETIME() for _ in range(4))
        assert api.GetProcessTimes(api.GetCurrentProcess(),ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user))
        value['created']=created.dwLowDateTime+(created.dwHighDateTime<<32)
    return value
if role=='leaf':
    publish('child-ready.json',identity())
    time.sleep(6.0)
    (folder/'late-write').write_text('natural child completion',encoding='utf-8')
elif role=='tree':
    executable=Path(sys.base_prefix)/('python.exe' if os.name=='nt' else 'bin/python')
    child=subprocess.Popen([str(executable),'-I','-B',str(Path(__file__).resolve()),'leaf',str(folder)],close_fds=False)
    publish('parent-ready.json',identity()|{'child':child.pid})
    if sys.argv[3]!='parent-exits':time.sleep(6.5)
elif role=='echo':
    data=sys.stdin.buffer.read()
    sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
    sys.stderr.buffer.write(b'e'*262144);sys.stderr.buffer.flush()
    raise SystemExit(7)
elif role=='text':
    sys.stdout.buffer.write(('argument='+sys.argv[3]+'\r\nenvironment='+os.environ['R142_TEXT']+'\rfinal\n').encode('utf-8'))
else:raise SystemExit('Unknown synthetic role')
'''


def _child(tmp_path):
    folder=tmp_path/'owned child fixture';folder.mkdir()
    source=folder/'synthetic child.py';source.write_text(CHILD_SOURCE,encoding='utf-8',newline='\n')
    return folder,source


def _base_python():
    executable=Path(sys.base_prefix)/('python.exe' if os.name=='nt' else 'bin/python')
    assert executable.is_file(), 'An authentic base Python is required for PID evidence.'
    return str(executable.resolve())


def _start(function):
    result={};done=threading.Event()
    def execute():
        result['started']=time.monotonic()
        try:result['value']=function()
        except BaseException as error:result['error']=error
        finally:
            result['returned']=time.monotonic();done.set()
    worker=threading.Thread(target=execute,daemon=True);worker.start()
    return worker,done,result


def _ready(folder,done,parent=False):
    deadline=time.monotonic()+4.0
    def published():return (folder/'child-ready.json').exists() and (not parent or (folder/'parent-ready.json').exists())
    while not published() and not done.is_set() and time.monotonic()<deadline:
        time.sleep(0.01)
    assert published(), 'No child readiness evidence; this is not a lifetime observation.'
    return json.loads((folder/'child-ready.json').read_text('utf-8'))


def _available(path):
    try:
        with FileLock(str(path),timeout=0):return True
    except Timeout:return False


def _observer_api():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
    api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.WaitForSingleObject.restype=wintypes.DWORD
    api.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)];api.IsProcessInJob.restype=wintypes.BOOL
    api.GetHandleInformation.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];api.GetHandleInformation.restype=wintypes.BOOL
    api.GetProcessTimes.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME)];api.GetProcessTimes.restype=wintypes.BOOL
    api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
    return api


def _live(identity,job=None):
    api=_observer_api();process=api.OpenProcess(0x00100000|0x1000,False,identity['pid'])
    if not process:
        assert ctypes.get_last_error()==87, 'Cannot establish the synthetic PID lifetime.'
        return False
    try:
        created,exited,kernel,user=(wintypes.FILETIME() for _ in range(4))
        assert api.GetProcessTimes(process,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user))
        if created.dwLowDateTime+(created.dwHighDateTime<<32)!=identity['created']:return False
        state=api.WaitForSingleObject(process,0)
        assert state in (0,258), 'Independent process observation failed.'
        if job is not None and state==258:
            member=wintypes.BOOL()
            assert api.IsProcessInJob(process,job,ctypes.byref(member)) and member.value
        return state==258
    finally:assert api.CloseHandle(process)


def _handle_value(handle):
    return handle.value if hasattr(handle,'value') else handle


class _CompletionAssociation(ctypes.Structure):
    _fields_=[('CompletionKey',ctypes.c_void_p),('CompletionPort',wintypes.HANDLE)]


class _ObservedKernel:
    """Delegate to actual Win32; record the creation/handle contract."""
    def __init__(self,actual,module,failure=None):
        self.actual=actual;self.module=module;self.failure=failure;self.job=None;self.acquired=[];self.closed=[]
        self.attributes={};self.flags=None;self.inherit=None;self.deleted=0;self.process_calls=0
        self.port=None;self.associated=False
    def __getattr__(self,name):return getattr(self.actual,name)
    def _fail(self):
        ctypes.set_last_error(5);return False
    def CreateJobObjectW(self,*args):
        if self.failure=='job':return self._fail()
        self.job=self.actual.CreateJobObjectW(*args)
        if self.job:self.acquired.append(_handle_value(self.job))
        return self.job
    def SetInformationJobObject(self,job,kind,value,size):
        if kind==9:
            limits=ctypes.cast(value,ctypes.POINTER(self.module._ExtendedLimit)).contents
            assert limits.BasicLimitInformation.LimitFlags==0x2000
            information=wintypes.DWORD()
            assert _observer_api().GetHandleInformation(job,ctypes.byref(information)) and not information.value&1
        else:
            assert kind==7 and size==ctypes.sizeof(_CompletionAssociation)
            association=ctypes.cast(value,ctypes.POINTER(_CompletionAssociation)).contents
            assert self.port and association.CompletionPort==self.port and self.process_calls==0
            self.associated=True
        return self.actual.SetInformationJobObject(job,kind,value,size)
    def CreateIoCompletionPort(self,*args):
        result=self.actual.CreateIoCompletionPort(*args)
        if result:
            self.port=_handle_value(result);self.acquired.append(self.port)
            information=wintypes.DWORD()
            assert _observer_api().GetHandleInformation(result,ctypes.byref(information)) and not information.value&1
        return result
    def OpenProcess(self,access,inherit,pid):
        assert not inherit and not access&1, 'Tracking opens an observation handle, never PID kill authority.'
        result=self.actual.OpenProcess(access,inherit,pid)
        if result:self.acquired.append(_handle_value(result))
        return result
    def DuplicateHandle(self,*args):
        result=self.actual.DuplicateHandle(*args)
        if result:self.acquired.append(ctypes.cast(args[3],ctypes.POINTER(wintypes.HANDLE)).contents.value)
        return result
    def UpdateProcThreadAttribute(self,attributes,flags,key,value,size,previous,length):
        count=size//ctypes.sizeof(wintypes.HANDLE)
        self.attributes[key]=tuple(ctypes.cast(value,ctypes.POINTER(wintypes.HANDLE))[i] for i in range(count))
        if self.failure=='attributes' and key==0x0002000d:return self._fail()
        return self.actual.UpdateProcThreadAttribute(attributes,flags,key,value,size,previous,length)
    def CreateProcessW(self,*args):
        self.process_calls+=1;self.inherit=args[4];self.flags=args[5]
        assert self.associated, 'The completion port must be associated before any owned process starts.'
        assert args[0] and args[2] is None and args[3] is None
        assert self.attributes[0x0002000d]==(self.job,)
        io=self.attributes[0x00020002]
        assert len(io)==len(set(io))==3 and self.job not in io
        assert self.inherit and self.flags&0x00080000 and self.flags&0x00000400
        assert not self.flags&0x01000000, 'No CREATE_BREAKAWAY_FROM_JOB.'
        if self.failure=='process':return self._fail()
        return self.actual.CreateProcessW(*args)
    def DeleteProcThreadAttributeList(self,*args):
        self.deleted+=1;return self.actual.DeleteProcThreadAttributeList(*args)
    def CloseHandle(self,handle):
        result=self.actual.CloseHandle(handle)
        if result:self.closed.append(_handle_value(handle))
        return result


@WINDOWS
@pytest.mark.parametrize('mode',['parent-exits','parent-lingers','venv-chain'])
def test_actual_native_job_owns_root_and_descendant_until_verified_cleanup(tmp_path,monkeypatch,mode,windows_runner):
    wp=windows_runner
    folder,source=_child(tmp_path);observed=_ObservedKernel(wp._kernel(),wp)
    monkeypatch.setattr(wp,'_kernel',lambda:observed)
    writer=tmp_path/'writer.lock';sync=tmp_path/'git-sync.lock'
    executable=sys.executable if mode=='venv-chain' else _base_python()
    behavior='parent-exits' if mode=='parent-exits' else 'parent-lingers'
    def operation():
        with FileLock(str(writer)),FileLock(str(sync)):
            return wp.run([executable,'-I','-B',str(source),'tree',str(folder),behavior],capture_output=True,timeout=COMMAND_DEADLINE)
    worker,done,result=_start(operation)
    try:
        child=_ready(folder,done,parent=True);parent=json.loads((folder/'parent-ready.json').read_text('utf-8'))
        assert parent['child']==child['pid']
        assert _live(child,observed.job)
        if behavior!='parent-exits':assert _live(parent,observed.job)
        assert not done.is_set() and not _available(writer) and not _available(sync)
        assert done.wait(RETURN_BOUND), 'Owned timeout waited for natural child expiry.'
        if behavior=='parent-exits':
            assert 'error' not in result and result['value'].returncode==0
        else:
            assert isinstance(result.get('error'),subprocess.TimeoutExpired)
        assert result['returned']-result['started']<RETURN_BOUND<CHILD_LIFETIME
        assert not _live(child) and not _live(parent)
        assert not (folder/'late-write').exists()
        assert _available(writer) and _available(sync)
    finally:worker.join(CHILD_LIFETIME+2)


@WINDOWS
def test_unread_inherited_stdin_cannot_trap_timeout_cleanup(tmp_path,windows_runner):
    wp=windows_runner
    folder,source=_child(tmp_path)
    worker,done,result=_start(lambda:wp.run([_base_python(),'-I','-B',str(source),'tree',str(folder),'parent-exits'],input=b'x'*(4*1024*1024),capture_output=True,timeout=COMMAND_DEADLINE))
    try:
        child=_ready(folder,done,parent=True)
        assert done.wait(RETURN_BOUND), 'A descendant retaining unread stdin trapped cleanup.'
        assert 'error' not in result and result['value'].returncode==0
        assert result['returned']-result['started']<RETURN_BOUND
        assert not _live(child) and not (folder/'late-write').exists()
    finally:worker.join(CHILD_LIFETIME+2)


@WINDOWS
def test_interruption_after_successful_create_keeps_ownership_until_tree_is_gone(tmp_path,monkeypatch,windows_runner):
    wp=windows_runner
    folder,source=_child(tmp_path);observed=_ObservedKernel(wp._kernel(),wp)
    real_create=observed.CreateProcessW;identities={}
    def interrupted(*args):
        created=real_create(*args)
        if created:
            identities['child']=_ready(folder,threading.Event(),parent=True)
            identities['parent']=json.loads((folder/'parent-ready.json').read_text('utf-8'))
            assert _live(identities['child'],observed.job)
            raise KeyboardInterrupt('Synthetic interruption after returned PROCESS_INFORMATION')
        return created
    observed.CreateProcessW=interrupted
    monkeypatch.setattr(wp,'_kernel',lambda:observed)
    writer=tmp_path/'writer.lock'
    def operation():
        with FileLock(str(writer)):
            wp.run([_base_python(),'-I','-B',str(source),'tree',str(folder),'parent-lingers'],capture_output=True,timeout=COMMAND_DEADLINE)
    worker,done,result=_start(operation)
    try:
        assert done.wait(RETURN_BOUND)
        assert isinstance(result.get('error'),KeyboardInterrupt) and 'child' in identities
        assert not _live(identities['child']) and not _live(identities['parent']) and not (folder/'late-write').exists()
        assert _available(writer)
    finally:worker.join(CHILD_LIFETIME+2)


@WINDOWS
def test_native_file_stdio_preserves_large_binary_text_and_caller_file(tmp_path,windows_runner):
    wp=windows_runner
    folder,source=_child(tmp_path);command=[_base_python(),'-I','-B',str(source)]
    payload=(b'\x00ordinary\xff\r\n'*32768)
    result=wp.run([*command,'echo',str(folder)],input=payload,capture_output=True,timeout=10)
    assert result.returncode==7 and result.stdout==payload and result.stderr==b'e'*262144
    env=os.environ|{'R142_TEXT':'harmless اردو é'}
    result=wp.run([*command,'text',str(folder),'space [bracket] é'],capture_output=True,text=True,encoding='utf-8',env=env,timeout=10)
    assert result.returncode==0 and result.stdout=='argument=space [bracket] é\nenvironment=harmless اردو é\nfinal\n'
    with (tmp_path/'caller output.bin').open('w+b') as target:
        result=wp.run([*command,'echo',str(folder)],input=payload,stdout=target,stderr=subprocess.DEVNULL,timeout=10)
        assert result.returncode==7 and result.stdout is result.stderr is None
        target.seek(0);assert target.read()==payload
        target.write(b'owner still has the file')


@WINDOWS
@pytest.mark.parametrize('failure',['job','attributes','process'])
def test_startup_failure_never_launches_an_uncontained_fallback(tmp_path,monkeypatch,failure,windows_runner):
    wp=windows_runner
    folder,source=_child(tmp_path);observed=_ObservedKernel(wp._kernel(),wp,failure)
    monkeypatch.setattr(wp,'_kernel',lambda:observed)
    def forbidden(*args,**kwargs):raise AssertionError('No subprocess fallback may launch after ownership setup fails.')
    monkeypatch.setattr(wp.subprocess,'run',forbidden)
    with pytest.raises(OSError):
        wp.run([_base_python(),'-I','-B',str(source),'leaf',str(folder)],capture_output=True,timeout=COMMAND_DEADLINE)
    assert not (folder/'child-ready.json').exists()
    assert sorted(observed.acquired)==sorted(observed.closed)
    assert observed.process_calls==(1 if failure=='process' else 0)
    assert observed.deleted==(0 if failure=='job' else 1)


def test_cleanup_faults_keep_caller_locks_and_retry_failed_report_until_safety_is_known(tmp_path,windows_runner):
    wp=windows_runner
    release=threading.Event();notice=threading.Event();notices=[]
    class FaultingCleanup:
        calls=0
        def TerminateJobObject(self,*args):return False
        def QueryInformationJobObject(self,job,kind,value,size,length):
            self.calls+=1
            if not release.is_set():
                if self.calls==1:raise KeyboardInterrupt('synthetic interruption')
                raise OSError('synthetic unavailable cleanup query')
            ctypes.cast(value,ctypes.POINTER(wp._Accounting)).contents.ActiveProcesses=0
            return True
        def WaitForSingleObject(self,*args):return 0
    api=FaultingCleanup();writer=tmp_path/'writer.lock';sync=tmp_path/'git-sync.lock'
    class FaultingTracker:
        # This existing unit control exercises cleanup/report ownership. The
        # two tracker controls below exercise actual lifetime completeness.
        def collect(self,process):pass
        def complete(self,process):
            accounting=wp._Accounting()
            assert api.QueryInformationJobObject(101,1,ctypes.byref(accounting),ctypes.sizeof(accounting),None)
            return accounting.ActiveProcesses==0 and api.WaitForSingleObject(process,0)==0
    tracker=FaultingTracker();tracker.api=api;tracker.job=101
    def report():
        notices.append('fixed cleanup attention')
        if len(notices)==1:
            raise RuntimeError('A failed attention callback cannot release process ownership.')
        notice.set()
    def operation():
        with FileLock(str(writer)),FileLock(str(sync)):
            wp._cleanup_owned(api,101,202,report,tracker)
        return 'quiescent'
    worker,done,result=_start(operation)
    try:
        assert notice.wait(4), 'A failed cleanup reporter must be retried while ownership is retained.'
        assert not done.is_set() and not _available(writer) and not _available(sync)
        assert notices==['fixed cleanup attention']*2 and api.calls>1
    finally:
        release.set();worker.join(3)
    assert done.is_set() and result.get('value')=='quiescent' and 'error' not in result
    assert notices==['fixed cleanup attention']*2 and _available(writer) and _available(sync)


class _LifetimeControlApi:
    """Independent Win32 state model: inactive accounting is not a wait proof.

    Both scenarios have root PID401/handle202 and two lifetime births. The
    retained scenario exposes PID402/handle303 with exit code1 but WAIT_TIMEOUT.
    The missing scenario withholds the second birth until a trusted NEW packet
    names an already-gone PID499; only that lookup returns ERROR_INVALID_PARAMETER.
    """
    def __init__(self,module,scenario):
        self.module=module;self.scenario=scenario;self.release=threading.Event()
        self.birth_delivered=threading.Event();self.sent=False;self.associated=False
        self.closed=[];self.opened=[];self.closed_pending=False;self.queries=0
    def CreateIoCompletionPort(self,*args):return 404
    def SetInformationJobObject(self,job,kind,value,size):
        association=ctypes.cast(value,ctypes.POINTER(_CompletionAssociation)).contents
        assert job==101 and kind==7 and size==ctypes.sizeof(_CompletionAssociation)
        assert association.CompletionKey==1 and association.CompletionPort==404
        self.associated=True;return True
    def GetQueuedCompletionStatus(self,port,code,key,pid,timeout):
        assert port==404 and self.associated
        deliver=not self.sent and (self.scenario=='retained' or self.release.is_set())
        if not deliver:
            ctypes.cast(pid,ctypes.POINTER(ctypes.c_void_p)).contents.value=None
            ctypes.set_last_error(258);return False
        ctypes.cast(code,ctypes.POINTER(wintypes.DWORD)).contents.value=6
        ctypes.cast(key,ctypes.POINTER(ctypes.c_size_t)).contents.value=1
        ctypes.cast(pid,ctypes.POINTER(ctypes.c_void_p)).contents.value=402 if self.scenario=='retained' else 499
        self.sent=True;self.birth_delivered.set();return True
    def QueryInformationJobObject(self,job,kind,value,size,length):
        assert job==101;self.queries+=1
        if kind==1:
            accounting=ctypes.cast(value,ctypes.POINTER(self.module._Accounting)).contents
            accounting.ActiveProcesses=0;accounting.TotalProcesses=2
        else:
            assert kind==3 and size>=2*ctypes.sizeof(wintypes.DWORD)
            header=ctypes.cast(value,ctypes.POINTER(wintypes.DWORD))
            header[0]=header[1]=0
            if length is not None:
                ctypes.cast(length,ctypes.POINTER(wintypes.DWORD)).contents.value=2*ctypes.sizeof(wintypes.DWORD)
        return True
    def TerminateJobObject(self,job,code):
        assert job==101;return True
    def GetProcessId(self,process):
        handle=_handle_value(process);assert handle in (202,303)
        return 401 if handle==202 else 402
    def GetProcessTimes(self,process,created,exited,kernel,user):
        handle=_handle_value(process);assert handle in (202,303)
        for target in (created,exited,kernel,user):
            value=ctypes.cast(target,ctypes.POINTER(wintypes.FILETIME)).contents
            value.dwLowDateTime=value.dwHighDateTime=0
        ctypes.cast(created,ctypes.POINTER(wintypes.FILETIME)).contents.dwLowDateTime=1001 if handle==202 else 1002
        return True
    def IsProcessInJob(self,process,job,member):
        assert _handle_value(process) in (202,303) and job==101
        ctypes.cast(member,ctypes.POINTER(wintypes.BOOL)).contents.value=True
        return True
    def OpenProcess(self,access,inherit,pid):
        assert not inherit and access&0x00100000 and not access&1
        self.opened.append(pid)
        if self.scenario=='retained':
            assert pid==402;return 303
        assert pid==499 and self.sent and self.release.is_set()
        ctypes.set_last_error(87);return None
    def WaitForSingleObject(self,process,timeout):
        handle=_handle_value(process);assert handle in (202,303)
        return 258 if handle==303 and not self.release.is_set() else 0
    def GetExitCodeProcess(self,process,code):
        assert _handle_value(process) in (202,303)
        ctypes.cast(code,ctypes.POINTER(wintypes.DWORD)).contents.value=1
        return True
    def CloseHandle(self,handle):
        value=_handle_value(handle);assert value in (303,404), 'The root process handle is borrowed.'
        if value==303 and not self.release.is_set():self.closed_pending=True
        self.closed.append(value);return True


def _lifetime_control(tmp_path,module,scenario):
    api=_LifetimeControlApi(module,scenario);tracker=module._LifetimeTracker(api,101);tracker.associate()
    writer=tmp_path/'writer.lock';sync=tmp_path/'git-sync.lock';notice=threading.Event()
    def operation():
        with FileLock(str(writer)),FileLock(str(sync)):
            try:module._cleanup_owned(api,101,202,notice.set,tracker)
            finally:tracker.close()
        return 'verified quiescence'
    worker,done,result=_start(operation)
    return api,writer,sync,notice,worker,done,result


@WINDOWS
def test_active_zero_cannot_release_locks_while_retained_child_handle_is_unsignaled(tmp_path,windows_runner):
    api,writer,sync,notice,worker,done,result=_lifetime_control(tmp_path,windows_runner,'retained')
    try:
        assert notice.wait(4), 'Accounting zero incorrectly bypassed a retained unsignaled child.'
        assert api.birth_delivered.is_set() and 402 in api.opened and api.queries>0
        assert not done.is_set() and not _available(writer) and not _available(sync)
        assert not api.closed_pending and 303 not in api.closed
    finally:
        api.release.set();worker.join(3)
    assert done.is_set() and result.get('value')=='verified quiescence' and 'error' not in result
    assert not api.closed_pending and sorted(api.closed)==[303,404]
    assert _available(writer) and _available(sync)


@WINDOWS
def test_missing_birth_count_requires_trusted_gone_process_evidence_before_unwind(tmp_path,windows_runner):
    api,writer,sync,notice,worker,done,result=_lifetime_control(tmp_path,windows_runner,'missing')
    try:
        assert notice.wait(4), 'Incomplete birth history was accepted from empty accounting/list alone.'
        assert not api.birth_delivered.is_set() and not api.opened and api.queries>0
        assert not done.is_set() and not _available(writer) and not _available(sync)
        assert not api.closed
    finally:
        api.release.set();worker.join(3)
    assert done.is_set() and result.get('value')=='verified quiescence' and 'error' not in result
    assert api.birth_delivered.is_set() and 499 in api.opened and api.closed==[404]
    assert _available(writer) and _available(sync)


@WINDOWS
def test_cleanup_reporting_is_scoped_and_dispatch_does_not_mutate_owner_environment(monkeypatch,windows_runner):
    wp=windows_runner
    calls=[];original={'PATH':os.environ['PATH'],'R142_OWNER':'unchanged'}
    def fake(command,**kwargs):
        calls.append(kwargs);return subprocess.CompletedProcess(command,7,b'output',b'diagnostic')
    monkeypatch.setattr(wp,'run',fake)
    first=lambda:None;second=lambda:None
    with g.cleanup_reporting(first):
        assert g._run_git(['git'],env=original,capture_output=True).returncode==7
        with pytest.raises(RuntimeError):
            with g.cleanup_reporting(second):
                g._run_git(['git'],env=original,capture_output=True)
                raise RuntimeError('synthetic scoped exit')
        g._run_git(['git'],env=original,capture_output=True)
    g._run_git(['git'],env=original,capture_output=True)
    assert [row['on_cleanup_wait'] for row in calls]==[first,second,first,None]
    assert original=={'PATH':os.environ['PATH'],'R142_OWNER':'unchanged'}
    assert all(row['env']['GIT_TERMINAL_PROMPT']=='0' and row['env']['GCM_INTERACTIVE']=='0' for row in calls)


def _git(path,*args):
    return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True,encoding='utf-8',timeout=20).stdout


def _repository(brain,tmp_path,monkeypatch):
    vault,state=brain
    for key in list(os.environ):
        if key.startswith('GIT_'):monkeypatch.delenv(key,raising=False)
    for key,value in {'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':'file'}.items():monkeypatch.setenv(key,value)
    remote=tmp_path/'synthetic remote.git'
    subprocess.run(['git','init','--bare','--initial-branch=main',str(remote)],check=True,capture_output=True,timeout=20)
    subprocess.run(['git','clone',str(remote),str(vault)],check=True,capture_output=True,timeout=20)
    _git(vault,'config','user.email','synthetic@example.invalid');_git(vault,'config','user.name','Synthetic Test')
    _git(vault,'config','core.autocrlf','false')
    (vault/'01-Projects').mkdir();(vault/'01-Projects/Initial.md').write_text('Initial harmless source.\n',encoding='utf-8')
    _git(vault,'add','.');_git(vault,'commit','-m','synthetic baseline');_git(vault,'push','-u','origin','HEAD')
    return vault,state,remote


@WINDOWS
def test_actual_git_post_commit_descendant_cannot_hold_writer_beyond_deadline(brain,tmp_path,monkeypatch):
    vault,state,remote=_repository(brain,tmp_path,monkeypatch);folder,source=_child(tmp_path)
    command=' '.join(shlex.quote(str(value).replace('\\','/')) for value in (_base_python(),'-I','-B',source,'leaf',folder))
    (vault/'.git/hooks/post-commit').write_text('#!/bin/sh\n'+command+' &\n',encoding='utf-8',newline='\n')
    note=vault/'01-Projects/Initial.md';raw=b'Updated harmless source.\n';note.write_bytes(raw)
    before=_git(vault,'rev-parse','HEAD').strip();real=getattr(g,'_run_git',subprocess.run);shortened=[]
    def selected(command,**kwargs):
        if 'commit' in command:
            assert kwargs['timeout']==90;kwargs['timeout']=PUBLIC_COMMIT_DEADLINE;shortened.append(True)
        return real(command,**kwargs)
    if hasattr(g,'_run_git'):monkeypatch.setattr(g,'_run_git',selected)
    else:monkeypatch.setattr(g.subprocess,'run',selected)
    releases=[];owner_thread={};real_release=FileLock.release
    def observed_release(lock,*args,**kwargs):
        try:
            if (threading.get_ident()==owner_thread.get('ident') and lock.is_locked
                    and Path(lock.lock_file) in (state/'writer.lock',state/'git-sync.lock')
                    and (folder/'child-ready.json').exists()):
                identity=json.loads((folder/'child-ready.json').read_text('utf-8'))
                releases.append({'lock':Path(lock.lock_file).name,'descendant_live':_live(identity),'at':time.monotonic()})
        finally:
            # Even a failed observation must delegate the actual lock release.
            real_release(lock,*args,**kwargs)
    monkeypatch.setattr(FileLock,'release',observed_release)
    def operation():
        owner_thread['ident']=threading.get_ident()
        return g.sync(vault)
    worker,done,result=_start(operation)
    try:
        child=_ready(folder,done)
        assert _live(child) and not _available(state/'writer.lock') and not _available(state/'git-sync.lock')
        assert done.wait(RETURN_BOUND), 'Actual Git inherited handles outlived its command deadline.'
        assert isinstance(result.get('error'),g.GitSyncError) and 'commit timed out' in str(result['error'])
        assert not _live(child) and not (folder/'late-write').exists()
        assert len(releases)==2 and {row['lock']:row['descendant_live'] for row in releases}=={'writer.lock':False,'git-sync.lock':False}
        assert _available(state/'writer.lock') and _available(state/'git-sync.lock')
        assert note.read_bytes()==raw and shortened==[True]
        # A post-commit hook runs after the commit has landed; timeout is not a
        # claim that Git rolled back that immutable commit.
        assert _git(vault,'rev-parse','HEAD').strip()!=before
    finally:
        worker.join(CHILD_LIFETIME+2)
        (tmp_path/'lock-release-observation.json').write_text(json.dumps({'releases':releases,'worker_done':done.is_set()},indent=2)+'\n',encoding='utf-8')


@POSIX
def test_actual_posix_import_never_loads_windows_api_and_git_dispatch_still_works(brain,tmp_path):
    vault,state=brain
    source="""import ctypes,sys
def forbidden(*args,**kwargs):raise AssertionError('POSIX import attempted WinDLL')
ctypes.WinDLL=forbidden
sys.path.insert(0,sys.argv[1])
import brain_windows_process,brain_git
result=brain_git._run_git(['git','--version'],capture_output=True,text=True,encoding='utf-8',timeout=10)
assert result.returncode==0 and result.stdout.startswith('git version ')
print('native POSIX import and dispatch verified')
"""
    env=os.environ|{'BRAIN_SYSTEM_ROOT':str(state.parent),'BRAIN_STATE':str(state),'BRAIN_VAULT':str(vault)}
    result=subprocess.run([sys.executable,'-I','-B','-c',source,str(Path(g.__file__).resolve().parent)],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='native POSIX import and dispatch verified'


def test_ordinary_git_sync_keeps_unicode_paths_and_owner_staging(brain,tmp_path,monkeypatch):
    vault,state,remote=_repository(brain,tmp_path,monkeypatch)
    note=vault/'01-Projects/Harmless space [bracket] é.md';raw='Ordinary Unicode source اردو.\n'.encode('utf-8');note.write_bytes(raw)
    assert g.sync(vault)=='synced'
    assert note.read_bytes()==raw
    assert _git(remote,'show','main:01-Projects/'+note.name).encode('utf-8')==raw
    owner=vault/'01-Projects/Owner staging.md';owner.write_text('Preserve explicit owner staging.\n',encoding='utf-8');_git(vault,'add','--',str(owner))
    before=_git(vault,'diff','--cached','--binary')
    assert g.sync(vault).startswith('paused: existing staged')
    assert _git(vault,'diff','--cached','--binary')==before and owner.read_text('utf-8')=='Preserve explicit owner staging.\n'


@WINDOWS
def test_actual_fast_descendants_and_ordinary_git_sync_finish_without_unknown_ownership(brain,tmp_path,monkeypatch,windows_runner):
    wp=windows_runner
    # These children finish normally before their parent. A safe tracker must
    # accommodate genuinely gone births, rather than permanently hold ownership.
    source="""import subprocess,sys
for index in range(3):
    subprocess.run([sys.executable,'-I','-B','-c',"print('fast-child')"],check=True)
print('fast-parent',flush=True)
"""
    worker,done,result=_start(lambda:wp.run([_base_python(),'-I','-B','-c',source],capture_output=True,text=True,encoding='utf-8',timeout=10))
    try:
        assert done.wait(12), 'Completed fast descendants left ownership permanently unknown.'
        assert 'error' not in result,repr(result.get('error'))
        completed=result['value']
        assert completed.returncode==0 and completed.stderr==''
        assert completed.stdout=='fast-child\n'*3+'fast-parent\n'
    finally:worker.join(1)
    vault,state,remote=_repository(brain,tmp_path,monkeypatch)
    note=vault/'01-Projects/Fast-child progress.md';raw=b'Ordinary Git must still make progress.\n';note.write_bytes(raw)
    worker,done,result=_start(lambda:g.sync(vault))
    try:
        assert done.wait(12), 'Ordinary Git sync retained ownership after all processes completed.'
        assert 'error' not in result,repr(result.get('error'))
        assert result['value']=='synced' and note.read_bytes()==raw
        assert _git(remote,'show','main:01-Projects/'+note.name).encode('utf-8')==raw
        assert _available(state/'writer.lock') and _available(state/'git-sync.lock')
    finally:worker.join(1)
