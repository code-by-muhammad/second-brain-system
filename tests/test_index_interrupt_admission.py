"""Synthetic signal-dispatch boundary proof; never addresses a native PID."""
import ast,signal,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import inspect
import brain_index_bootstrap as subject
SOURCE=Path(inspect.getsourcefile(subject))

@pytest.mark.parametrize('pending',[2,15,1])
def test_dispatched_interrupt_at_finally_entry_cannot_release_before_cleanup(monkeypatch,pending):
    handlers={1:object(),2:signal.default_int_handler,15:object(),17:signal.SIG_DFL}
    original=dict(handlers);events=[]
    class Child:
        pid=43123
        returncode=None
        def poll(self):return self.returncode
        def wait(self,timeout):self.returncode=-9;events.append('reaped');return self.returncode
    child=Child()
    def set_handler(signum,handler):prior=handlers[signum];handlers[signum]=handler;return prior
    fake_signal=SimpleNamespace(SIGINT=2,SIGTERM=15,SIGHUP=1,SIGCHLD=17,SIGKILL=9,SIG_DFL=signal.SIG_DFL,SIG_IGN=signal.SIG_IGN,
        getsignal=handlers.__getitem__,signal=set_handler)
    def killpg(pid,signum):
        assert pid==43123
        assert all(handlers[s]==signal.SIG_IGN for s in (1,2,15))
        events.append(('signal',signum))
        if signum:assert child.returncode is None
        else:raise ProcessLookupError()
    monkeypatch.setattr(subject,'signal',fake_signal)
    monkeypatch.setattr(subject,'os',SimpleNamespace(name='posix',killpg=killpg))
    monkeypatch.setattr(subject,'time',SimpleNamespace(monotonic=lambda:0,sleep=lambda seconds:None))
    monkeypatch.setattr(subject.cfg,'environment',lambda:{})
    monkeypatch.setattr(subject,'subprocess',SimpleNamespace(Popen=lambda *a,**kw:child,DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired,CompletedProcess=subprocess.CompletedProcess))
    tree=ast.parse(SOURCE.read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_run_reindex')
    cleanup=next(n for n in function.body if isinstance(n,ast.Try)).finalbody[0].lineno
    dispatched=[]
    def trace(frame,event,arg):
        if frame.f_code is subject._run_reindex.__code__ and event=='line' and frame.f_lineno==cleanup and not dispatched:
            dispatched.append(pending);handlers[pending](pending,None)
        return trace
    def first_cancel():raise subject.IndexStalled('synthetic first cancellation')
    caught=None;previous=sys.gettrace()
    try:
        sys.settrace(trace)
        try:subject._run_reindex(['synthetic'],object(),10,first_cancel,lambda:None)
        except BaseException as error:caught=error
    finally:sys.settrace(previous)
    assert dispatched==[pending] and isinstance(caught,subject.IndexStalled)
    assert events==[('signal',15),('signal',9),'reaped',('signal',0)]
    assert handlers==original and child.returncode==-9
