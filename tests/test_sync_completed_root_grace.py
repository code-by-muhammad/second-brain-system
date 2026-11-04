"""Completed-root grace preserves root results only after native Job teardown."""
import ctypes
from ctypes import wintypes as wt
import json
import subprocess
import time

from filelock import FileLock
import pytest

import test_sync_windows_process_lifetime as lifetime


@lifetime.WINDOWS
@pytest.mark.parametrize('exit_code',[0,7],ids=['success','nonzero'])
def test_completed_root_background_is_gone_before_original_result_and_locks_release(tmp_path,monkeypatch,exit_code):
    import brain_windows_process as wp
    folder,source=lifetime._child(tmp_path)
    source.write_text(lifetime.CHILD_SOURCE+"\nif role=='tree':\n    sys.stdout.buffer.write(b'root output\\r\\n')\n    sys.stderr.buffer.write(b'root error\\n')\n    raise SystemExit(int(sys.argv[4]))\n",encoding='utf-8',newline='\n')
    observed=lifetime._ObservedKernel(wp._kernel(),wp)
    monkeypatch.setattr(wp,'_kernel',lambda:observed)
    writer=tmp_path/'writer.lock';sync=tmp_path/'git-sync.lock';cleanup=[]
    real_cleanup=wp._cleanup_owned
    def checked_cleanup(api,job,root,reporter,tracker):
        child=json.loads((folder/'child-ready.json').read_text(encoding='utf-8'))
        code=wt.DWORD()
        assert api.WaitForSingleObject(root,0)==0 and api.GetExitCodeProcess(root,ctypes.byref(code))
        assert code.value==exit_code and lifetime._live(child,job)
        assert not lifetime._available(writer) and not lifetime._available(sync)
        started=time.monotonic()
        real_cleanup(api,job,root,reporter,tracker)
        assert not lifetime._live(child) and not lifetime._available(writer) and not lifetime._available(sync)
        cleanup.append(time.monotonic()-started)
    monkeypatch.setattr(wp,'_cleanup_owned',checked_cleanup)
    def operation():
        with FileLock(str(writer)),FileLock(str(sync)):
            return wp.run([lifetime._base_python(),'-I','-B',str(source),'tree',str(folder),'parent-exits',str(exit_code)],
                          capture_output=True,timeout=4.0)
    worker,done,result=lifetime._start(operation)
    try:
        child=lifetime._ready(folder,done,parent=True)
        assert lifetime._live(child,observed.job)
        assert not lifetime._available(writer) and not lifetime._available(sync)
        assert done.wait(lifetime.RETURN_BOUND)
        assert 'error' not in result and result['value'].returncode==exit_code
        assert result['value'].stdout==b'root output\r\n' and result['value'].stderr==b'root error\n'
        assert len(cleanup)==1 and result['returned']-result['started']<lifetime.RETURN_BOUND<lifetime.CHILD_LIFETIME
        assert not lifetime._live(child) and not (folder/'late-write').exists()
        assert lifetime._available(writer) and lifetime._available(sync)
    finally:worker.join(lifetime.CHILD_LIFETIME+2)


@lifetime.WINDOWS
def test_exit_code_without_native_signal_cannot_start_root_completion_grace(tmp_path,monkeypatch):
    import brain_windows_process as wp
    class PendingRoot(lifetime._ObservedKernel):
        def __init__(self,actual):
            super().__init__(actual,wp);self.root=None;self.cleanup=False;self.early_queries=0;self.observed_exit=None
        def CreateProcessW(self,*args):
            result=super().CreateProcessW(*args)
            if result:self.root=ctypes.cast(args[-1],ctypes.POINTER(wp._ProcessInformation)).contents.hProcess
            return result
        def WaitForSingleObject(self,handle,timeout):
            if lifetime._handle_value(handle)==self.root and not self.cleanup:
                if self.actual.WaitForSingleObject(handle,0)==0:
                    code=wt.DWORD();assert self.actual.GetExitCodeProcess(handle,ctypes.byref(code))
                    self.observed_exit=int(code.value)
                return 258
            return self.actual.WaitForSingleObject(handle,timeout)
        def GetExitCodeProcess(self,*args):
            if not self.cleanup:self.early_queries+=1
            return self.actual.GetExitCodeProcess(*args)
        def TerminateJobObject(self,*args):
            self.cleanup=True
            return self.actual.TerminateJobObject(*args)
    observed=PendingRoot(wp._kernel());monkeypatch.setattr(wp,'_kernel',lambda:observed)
    started=time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        wp.run([lifetime._base_python(),'-I','-B','-c','pass'],capture_output=True,timeout=0.35)
    assert observed.observed_exit==0 and observed.early_queries==0 and observed.cleanup
    assert time.monotonic()-started<2.0
