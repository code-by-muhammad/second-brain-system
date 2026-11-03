"""Finite two-level setup.run cancellation observation; no actual installer/service work."""
import argparse,ast,hashlib,json,os,signal,subprocess,sys,tempfile,threading,time,traceback,types
from pathlib import Path


def read_source(path,expected):
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Plain absolute pinned setup source required')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Setup source hash mismatch')
    return raw


def observe(args,root,raw,initial):
    ready=root/'leaf-ready.json';exited=root/'leaf-exit.json';lock=root/'synthetic-install.lock'
    outer_events=[];children=[];sender_errors=[];observed={};started=time.monotonic();active=threading.Event()
    leaf=root/'leaf.py';middle=root/'install.py';(root/'app').mkdir(mode=0o700)
    leaf.write_text("""import os,signal,sys,time,json
from pathlib import Path
import psutil
signal.signal(signal.SIGTERM,signal.SIG_IGN)
ready,exited=sys.argv[1:]
path=Path(ready);temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps({'pid':os.getpid(),'birth':psutil.Process().create_time(),'pgid':os.getpgrp(),'sid':os.getsid(0),'ppid':os.getppid()}));temporary.replace(path)
time.sleep(7)
Path(exited).write_text(json.dumps({'pid':os.getpid(),'normal_self_exit':True}))
""",encoding='utf-8')
    middle.write_text("""import sys;sys.dont_write_bytecode=True
import hashlib,json,os,signal,subprocess,time,types
from pathlib import Path
from filelock import FileLock
source,expected,leaf,ready,exited,lock,events=sys.argv[1:8]
path=Path(source);raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==expected
setup=types.ModuleType('observed_inner_setup');setup.__file__=source;exec(compile(raw,source,'exec'),setup.__dict__)
native=os.killpg;native_popen=subprocess.Popen;owned=[]
def launch(*a,**kw):
    child=native_popen(*a,**kw);owned.append(child);return child
# A declared finite scheduling pause makes the two equal-grace race deterministic.
def observe(pid,signum):
    assert len(owned)==1 and pid==owned[0].pid
    if signum:assert owned[0].returncode is None,'inner signal after reap'
    with Path(events).open('a') as stream:stream.write(json.dumps({'group':pid,'signal':int(signum),'at':time.monotonic(),'returncode':owned[0].returncode})+'\\n')
    result=native(pid,signum)
    if signum==signal.SIGTERM:time.sleep(.35)
    return result
setup.os=types.SimpleNamespace(name='posix',killpg=observe)
setup.subprocess=types.SimpleNamespace(Popen=launch,PIPE=subprocess.PIPE,CompletedProcess=subprocess.CompletedProcess,TimeoutExpired=subprocess.TimeoutExpired)
with FileLock(lock,timeout=0):
    try:setup.run([sys.executable,'-I','-B',leaf,ready,exited],source_app='unused',capture_output=True,text=True,timeout=12)
    except InterruptedError:pass
""",encoding='utf-8')
    for p in (leaf,middle):ast.parse(p.read_bytes());os.chmod(p,0o600)
    setup=types.ModuleType('observed_outer_setup');setup.__file__=str(args.setup_source);exec(compile(raw,str(args.setup_source),'exec'),setup.__dict__)
    native_popen=subprocess.Popen;native_killpg=os.killpg
    def launch(*a,**kw):
        child=native_popen(*a,**kw);children.append(child);return child
    def group_signal(pid,signum):
        assert len(children)==1 and pid==children[0].pid
        outer_events.append({'group':pid,'signal':int(signum),'returncode':children[0].returncode,'at':time.monotonic()})
        if signum:assert children[0].returncode is None,'source signal after reap'
        return native_killpg(pid,signum)
    setup.os=types.SimpleNamespace(name='posix',killpg=group_signal)
    setup.subprocess=types.SimpleNamespace(Popen=launch,PIPE=subprocess.PIPE,CompletedProcess=subprocess.CompletedProcess,TimeoutExpired=subprocess.TimeoutExpired)
    def cancel():
        deadline=time.monotonic()+6
        while not ready.exists():
            if time.monotonic()>deadline:sender_errors.append('bounded readiness deadline');return
            time.sleep(.01)
        if active.is_set() and children and children[0].returncode is None:
            os.kill(os.getpid(),signal.SIGTERM)
    sender=threading.Thread(target=cancel);sender.start();error=None
    environment={'PATH':'/usr/bin:/bin','HOME':str(root),'TMPDIR':str(root),'LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
    command=[sys.executable,'-I','-B',str(middle),str(args.setup_source),args.setup_sha256,str(leaf),str(ready),str(exited),str(lock),str(root/'inner-signals.jsonl'),'--reuse-python',sys.executable]
    active.set()
    try:setup.run(command,source_app=root/'app',capture_output=True,text=True,timeout=15,env=environment)
    except InterruptedError as caught:error=type(caught).__name__
    finally:active.clear();sender.join(timeout=7)
    observed['outer_return_elapsed']=time.monotonic()-started
    if sender.is_alive() or sender_errors or len(children)!=1:raise RuntimeError('Probe setup/cancellation incomplete')
    import psutil
    data=json.loads(ready.read_bytes())
    births=[];probe_errors=[]
    def same_live_leaf():
        try:
            p=psutil.Process(data['pid']);birth=p.create_time()
            if birth not in births:births.append(birth)
            if birth!=data['birth']:return None
            return p.status()!=psutil.STATUS_ZOMBIE and p.is_running()
        except psutil.NoSuchProcess:return False
        except psutil.Error as error:probe_errors.append(type(error).__name__);return None
    observed['leaf_alive_after_outer_return']=same_live_leaf()
    observed['leaf_normal_exit_at_outer_return']=exited.exists()
    from filelock import FileLock,Timeout
    try:
        with FileLock(str(lock),timeout=0):observed['synthetic_admission_released']=True
    except Timeout:observed['synthetic_admission_released']=False
    observed['distinct_native_sessions']=(data['pgid']==data['pid'] and data['sid']==data['pid'] and data['pid']!=children[0].pid)
    observed['middle_reaped']=children[0].returncode is not None
    # No cleanup signal uses the leaf PID. Its finite single-thread script self-exits.
    deadline=time.monotonic()+10
    while same_live_leaf() is not False and time.monotonic()<deadline:time.sleep(.05)
    observed['leaf_not_executing_at_end']=same_live_leaf() is False
    observed.update(leaf_reported_birth=data['birth'],driver_observed_births=births,lifetime_probe_errors=probe_errors)
    observed['leaf_self_exit_marker']=exited.exists()
    inner_path=root/'inner-signals.jsonl';inner=[json.loads(line) for line in inner_path.read_text().splitlines()] if inner_path.exists() else []
    mechanism=(children[0].returncode==-signal.SIGKILL and [e['signal'] for e in outer_events if e['signal']]==[signal.SIGTERM,signal.SIGKILL]
        and [e['signal'] for e in inner]==[signal.SIGTERM] and observed['leaf_normal_exit_at_outer_return'] is False)
    observed.update(mechanism_verified=mechanism,middle_returncode=children[0].returncode,
        inner_term_after_outer_term_seconds=(inner[0]['at']-next(e['at'] for e in outer_events if e['signal']==signal.SIGTERM)) if inner else None)
    result={'status':'nested cleanup orphan observed' if mechanism and error=='InterruptedError' and observed['leaf_alive_after_outer_return'] is True and observed['synthetic_admission_released'] and observed['distinct_native_sessions'] and observed['leaf_not_executing_at_end'] else 'needs inspection',
        'scope':'Actual unchanged POSIX setup.run at two levels with synthetic scripts only; declared .35second inner TERM observer scheduling pause; finite leaf ignoresTERM/self-exits7seconds. No actual install/service/model/network/manager operation, no leaf-PID signal.',
        'source_sha256':args.setup_sha256,'source_unchanged':read_source(args.setup_source,args.setup_sha256)==raw,
        'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'platform':sys.platform,'python':sys.executable,'private_fixture':str(root),
        'error':error,'outer_signals':outer_events,'inner_signals':inner,'observation':observed,'elapsed':time.monotonic()-started,
        'initial_signal_state':initial,'limits':['One controlled scheduling interleaving, not an uncontrolled occurrence-frequency or whole product lifecycle claim. The pause widens an equal-grace race whose inner grace begins after the outer grace.',
        'Actual install.py nesting must be confirmed separately; this probe executes synthetic middle/leaf scripts only. Trailing reuse flags are inert parity data.','Leaf lifetime matched by actual PID+creation time; no cached-PID signal authority.','Leaf is single-threaded; zombie is counted non-executing without claiming group absence or deleting a process.']}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-reviewed-native',action='store_true');parser.add_argument('--setup-source',type=Path,required=True)
    parser.add_argument('--setup-sha256',required=True);parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_reviewed_native or sys.platform not in ('darwin','linux') or not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError('Explicit reviewed POSIX Python -I -B required')
    if args.receipt.exists():raise ValueError('Preserve prior receipt')
    import psutil,filelock
    initial={'SIGTERM':int(signal.getsignal(signal.SIGTERM)),'SIGCHLD':int(signal.getsignal(signal.SIGCHLD))}
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL or signal.getsignal(signal.SIGCHLD) is not signal.SIG_DFL:
        raise ValueError('Probe requires original default TERM/CHLD dispositions')
    raw=read_source(args.setup_source,args.setup_sha256)
    parent=Path('/private/tmp') if sys.platform=='darwin' else Path('/tmp')
    root=Path(tempfile.mkdtemp(prefix='sb-nested-cancel-',dir=parent));os.chmod(root,0o700)
    try:result=observe(args,root,raw,initial)
    except BaseException as error:
        trace=root/'probe-error.txt';trace.write_text(traceback.format_exc(),encoding='utf-8')
        result={'status':'probe-incomplete','error_type':type(error).__name__,'private_traceback':str(trace),'synthetic_traceback_tail':trace.read_text(encoding='utf-8')[-8192:],'private_fixture':str(root),
            'source_sha256':args.setup_sha256,'initial_signal_state':initial,
            'scope':'Finite synthetic scripts only; no product action; incomplete observation never acceptance.'}
    try:result['source_unchanged']=read_source(args.setup_source,args.setup_sha256)==raw
    except BaseException:result['source_unchanged']=False;result['status']='probe-incomplete'
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    print(json.dumps({'status':result['status'],'receipt':str(args.receipt),'observation':result.get('observation'),'private_fixture':str(root),'source_unchanged':result['source_unchanged']}))
    return 0 if result['status']=='nested cleanup orphan observed' and result['source_unchanged'] else 1


if __name__=='__main__':raise SystemExit(main())
