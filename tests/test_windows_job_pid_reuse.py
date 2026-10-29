"""No-process API controls: gone/reused PIDs cannot inflate lifetime proof."""
import ctypes as c
from ctypes import wintypes as w
import sys
import pytest
import brain_windows_process as runner

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Windows ctypes error-state contract')

class Api:
    def __init__(self):
        self.mode='gone';self.opened=[];self.closed=[];self.total=2;self.active=0;self.member_live=False
    def CreateIoCompletionPort(self,*args):return 99
    def SetInformationJobObject(self,*args):return True
    def GetProcessId(self,handle):assert handle==10;return 100
    def IsProcessInJob(self,handle,job,belongs):
        assert job==1 and handle in (10,20,30)
        if self.mode=='query-fails' and handle==20:c.set_last_error(5);return False
        c.cast(belongs,c.POINTER(w.BOOL)).contents.value=handle!=30;return True
    def GetProcessTimes(self,handle,*times):
        assert handle in (10,20),'A foreign handle must never be timed.'
        for pointer in times:
            row=c.cast(pointer,c.POINTER(w.FILETIME)).contents;row.dwHighDateTime=0;row.dwLowDateTime=handle
        return True
    def OpenProcess(self,access,inherit,pid):
        assert access==0x101000 and inherit is False and pid==200
        self.opened.append(pid)
        if self.mode in ('gone','denied'):c.set_last_error(87 if self.mode=='gone' else 5);return None
        return 30 if self.mode=='foreign' else 20
    def WaitForSingleObject(self,handle,timeout):
        assert handle in (10,20),'A foreign handle must never be waited on.'
        return 258 if handle==20 and self.member_live else 0
    def GetQueuedCompletionStatus(self,*args):c.set_last_error(258);return False
    def QueryInformationJobObject(self,job,kind,buffer,size,returned):
        assert job==1
        if kind==3:
            row=c.cast(buffer,c.POINTER(runner._ProcessIds)).contents
            row.NumberOfAssignedProcesses=row.NumberOfProcessIdsInList=0
        else:
            assert kind==1;row=c.cast(buffer,c.POINTER(runner._Accounting)).contents
            row.TotalProcesses=self.total;row.ActiveProcesses=self.active
        return True
    def CloseHandle(self,handle):assert handle in (20,30,99);self.closed.append(handle);return True

def fixture():
    api=Api();tracker=runner._LifetimeTracker(api,1);tracker.associate();tracker._attach_root(10);return api,tracker

def test_trusted_birth_then_foreign_pid_is_gone_without_foreign_use():
    api,tracker=fixture();api.mode='foreign'
    try:
        tracker._observe(200,True)
        assert tracker._members[200]['gone'] and not tracker._pending
        assert tracker.complete(10) and api.closed==[30]
        tracker._observe(200,True)
        assert len(tracker._members)==2 and tracker.complete(10)
        assert api.closed==[30,30]
    finally:tracker.close()
    assert api.closed==[30,30,99]

@pytest.mark.parametrize('mode,trusted',[('foreign',False),('denied',True),('query-fails',True)])
def test_missing_notice_or_failed_api_stays_uncertain(mode,trusted):
    api,tracker=fixture();api.mode=mode
    try:
        tracker._attempt(200,trusted)
        assert tracker._uncertain and 200 in tracker._pending and 200 not in tracker._members
        assert not tracker.complete(10)
    finally:tracker.close()
    assert api.closed==([30,30,99] if mode=='foreign' else [20,20,99] if mode=='query-fails' else [99])

@pytest.mark.parametrize('first',['gone','foreign'])
@pytest.mark.parametrize('later_notice',[False,True],ids=['snapshot-only','notice'])
def test_gone_then_positive_member_counts_distinct_birth_once(first,later_notice):
    api,tracker=fixture();api.mode=first
    try:
        tracker._observe(200,True);api.mode='member';api.total=3
        tracker._observe(200,later_notice)
        assert tracker._members[200]['prior_gone_slot'] is True
        opened=len(api.opened)
        tracker._observe(200,True);tracker._observe(200,False)
        assert len(api.opened)==opened and tracker.complete(10)
        tracker._observe(200,True) # Signaled retained member still pins this PID.
        assert len(api.opened)==opened and tracker.complete(10)
    finally:tracker.close()
    assert api.closed.count(20)==1

def test_unsignaled_reused_member_cannot_complete_from_zero_accounting():
    api,tracker=fixture()
    try:
        tracker._observe(200,True);api.mode='member';api.total=3;api.member_live=True;tracker._observe(200,False)
        assert not tracker.complete(10)
        api.member_live=False;assert tracker.complete(10)
    finally:tracker.close()

@pytest.mark.parametrize('total',[2,4])
def test_over_or_under_coverage_cannot_complete(total):
    api,tracker=fixture()
    try:
        tracker._observe(200,True);api.mode='member';tracker._observe(200,False);api.total=total
        assert not tracker.complete(10)
    finally:tracker.close()

def test_decreasing_total_is_permanently_invalid():
    api,tracker=fixture()
    try:
        tracker._observe(200,True);api.mode='member';tracker._observe(200,False);api.total=3;assert tracker.complete(10)
        api.total=2;assert not tracker.complete(10) and tracker._coverage_invalid
        api.total=3;assert not tracker.complete(10)
    finally:tracker.close()

def test_unobserved_birth_still_holds_with_empty_accounting():
    api,tracker=fixture();api.total=3
    try:
        tracker._observe(200,True)
        assert not tracker.complete(10) and not tracker._pending and api.active==0
    finally:tracker.close()
