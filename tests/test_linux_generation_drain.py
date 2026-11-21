"""Real child lifetimes and locks in an isolated non-systemd Linux runtime."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from filelock import FileLock
import pytest

import brain_linux_generation as generation
import brain_linux_user_service as user
import brain_process as processes
import brain_service as service
import brain_settings as cfg


pytestmark=pytest.mark.skipif(sys.platform!='linux' or not hasattr(os,'pidfd_open'),
                              reason='Linux process lifetime handles required')

SUPERVISOR="""
import os, subprocess, time
from filelock import FileLock
import brain_service as controller
import brain_settings as cfg
from brain_supervisor import _start_bootstrap
while True:
    with FileLock(str(cfg.STATE/'service-control.lock'),timeout=5):
        if controller.supervisor_spawn_allowed():
            with (cfg.ROOT/'logs/bootstrap.log').open('ab') as log:
                child=_start_bootstrap([],log)
            break
    time.sleep(.01)
child.wait()
while True:time.sleep(.1)
"""

BOOTSTRAP="""
import os, signal, sys, time
from filelock import FileLock
import brain_service as controller
import brain_settings as cfg
from brain_index_bootstrap_impl import _run_reindex, _released
if not _released():sys.exit(75)
launch=controller.index_launch(manual=False)
with FileLock(str(cfg.STATE/'index-bootstrap.lock')):
    (cfg.STATE/'preparing').write_text(str(os.getpid()))
    if (cfg.STATE/'prepare-only').exists():
        signal.signal(signal.SIGTERM,lambda number,frame:sys.exit(0))
        while True:time.sleep(.1)
    command=[str(cfg.PYTHON),'-I','-B',str(cfg.ROOT/'app/brain_index_worker.py'),'reindex','-p','brain']
    with (cfg.ROOT/'logs/worker.log').open('ab') as output:
        try:
            _run_reindex(command,output,time.monotonic()+60,lambda:None,lambda:None,
                         on_spawn=launch.publish,admission=launch.admission,managed=True)
        except InterruptedError:
            (cfg.STATE/'managed-drain-seen').write_text(str(launch.drained()))
"""

WORKER=r"""
import os, signal, sys,time
from pathlib import Path
state=Path(__file__).parents[1]/'state'
if sys.stdin.buffer.readline(7)!=b'start\n':sys.exit(75)
if (state/'slow-worker').exists():signal.signal(signal.SIGTERM,signal.SIG_IGN)
(state/'worker-ready').write_text(str(os.getpid()))
while True:time.sleep(.1)
"""


def eventually(check,seconds=12):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if result:=check():return result
        time.sleep(.025)
    pytest.fail('Isolated generation did not reach its expected state.')


@pytest.fixture
def runtime(brain,monkeypatch):
    monkeypatch.setattr(user,'account_home',lambda:Path.home().resolve())
    monkeypatch.setattr(user,'pid1_comm',lambda:'tini')
    monkeypatch.setattr(service,'LINUX_UNIT','second-brain.service')
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    device={'python':str(cfg.PYTHON),'port':cfg.PORT,'vault':str(cfg.VAULT),'version':cfg.VERSION}
    (cfg.ROOT/'device.json').write_text(json.dumps(device))
    monkeypatch.setattr(cfg,'DEVICE',device)
    for key, value in (('BRAIN_SYSTEM_ROOT',cfg.ROOT),('BRAIN_STATE',cfg.STATE),('BRAIN_VAULT',cfg.VAULT)):
        monkeypatch.setenv(key,str(value))
    app=cfg.ROOT/'app'
    shutil.copytree(Path(user.__file__).parent,app,ignore=shutil.ignore_patterns('__pycache__'))
    keeper=app/'brain_linux_user_service.py'
    contents=keeper.read_text()
    contents=contents.replace('return Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()','return Path.home().resolve()')
    contents=contents.replace("def pid1_comm():\n", "def pid1_comm():\n    return 'tini'\n")
    keeper.write_text(contents)
    (app/'brain_index_bootstrap_impl.py').write_bytes((app/'brain_index_bootstrap.py').read_bytes())
    bootstrap='from brain_index_bootstrap_impl import LaunchDeferred, settle_interrupted\n'
    bootstrap+="if __name__=='__main__':\n"+''.join('    '+line+'\n' for line in BOOTSTRAP.strip().splitlines())
    (app/'brain_index_bootstrap.py').write_text(bootstrap)
    (app/'brain_index_worker.py').write_text(WORKER)
    (app/'brain_supervisor.py').rename(app/'brain_supervisor_impl.py')
    (app/'brain_supervisor.py').write_text(SUPERVISOR.replace('from brain_supervisor import','from brain_supervisor_impl import'))
    # The copied entry is a synthetic holder of the real work lock. The real
    # controller and identity code still see each child's exact production argv.
    linux=service._linux_registration()
    linux._write(linux.path,linux.installable,0o644)
    linux._set_enabled(True)
    children=[]
    original=linux._spawn_keeper
    def start():
        with service._control():
            native=linux.inspect(prove_main=False)
            assert native['enabled']
            original(time.monotonic()+10)
            children.append(linux._keeper_process()['pid'])
    try:
        yield linux,start
    finally:
        # Cleanup only verified test-owned lifetimes, even on a failed assertion.
        with ExitStack() as stack:
            drain=generation.GenerationDrain(linux,stack)
            for handle in drain.inventory().values():
                handle.send(signal.SIGKILL)
        for pid in children:
            try:os.waitpid(pid,0)
            except ChildProcessError:pass


@pytest.mark.parametrize('phase',['prepare','worker','reparented'])
def test_managed_drain_precedes_work_lock_and_native_end(runtime,monkeypatch,phase):
    linux,start=runtime
    if phase=='prepare':
        (cfg.STATE/'prepare-only').touch()
    elif phase=='worker':
        (cfg.STATE/'slow-worker').touch()
    start()
    witness=cfg.STATE/('preparing' if phase=='prepare' else 'worker-ready')
    eventually(witness.exists)
    pid=int(witness.read_text())
    if phase=='reparented':
        before=generation.generation(linux)
        bootstrap=before['roles']['index_bootstrap']['pid']
        with ExitStack() as stack:
            generation._retain(stack,before['roles']['index_bootstrap']).send(signal.SIGKILL)
        eventually(lambda:processes._linux_lifetime(bootstrap) is None)
    original=linux.native_stop
    order=[]
    monkeypatch.setattr(service,'_linux_registration',lambda:linux)
    def end(**kwargs):
        assert linux.inspect(prove_main=False)['enabled'] is False
        assert processes._linux_lifetime(pid) is None
        assert generation.generation(linux)['drain']=='drained'
        order.append('native End')
        return original(**kwargs)
    monkeypatch.setattr(linux,'native_stop',end)
    result=service.stop(wait_seconds=20)
    assert order==['native End'] and result['status']=='stopped'
    assert not processes._listeners() and linux.inspect()['quiescent']
    if phase=='worker':
        assert (cfg.STATE/'managed-drain-seen').read_text()=='True'


def test_unenrolled_worker_refuses_before_waiting_for_work_lock(runtime,monkeypatch):
    linux,_=runtime
    command=[str(cfg.PYTHON),'-I','-B',str(cfg.ROOT/'app/brain_index_worker.py'),'reindex','-p','brain']
    with subprocess.Popen(command,stdin=subprocess.PIPE,start_new_session=True) as child:
        child.stdin.write(b'start\n')
        child.stdin.close()
        try:
            eventually((cfg.STATE/'worker-ready').exists)
            monkeypatch.setattr(service,'_linux_registration',lambda:linux)
            monkeypatch.setattr(linux,'native_stop',lambda **kwargs:pytest.fail('Unmanaged worker authorizes no native End'))
            with FileLock(str(cfg.STATE/'index-bootstrap.lock')):
                with pytest.raises(processes.OwnershipError,match='unverified reindex'):
                    service.stop(wait_seconds=5)
            assert child.poll() is None and linux.inspect()['enabled'] is True
            status=json.loads((cfg.STATE/'service-control-status.json').read_text())
            assert status['reason']=='unmanaged_index_worker' and status['end_attempted'] is False
        finally:
            child.terminate()


def test_worker_capture_retries_when_exec_arguments_are_not_yet_visible(runtime, monkeypatch):
    command = [str(cfg.PYTHON), '-I', '-B', str(cfg.ROOT/'app/brain_index_worker.py'), 'reindex', '-p', 'brain']
    arguments = generation._arguments
    with subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True) as child, ExitStack() as stack:
        observed = []

        def delayed_arguments(pid):
            if pid == child.pid and not observed:
                observed.append(pid)
                return None
            return arguments(pid)

        monkeypatch.setattr(generation, '_arguments', delayed_arguments)
        try:
            record, handle = generation.retain_worker(stack, child)
            assert observed == [child.pid]
            assert record['argv'] == command and handle.alive()
        finally:
            child.stdin.close()
        assert child.wait(timeout=5) == 75


def test_missing_keeper_can_be_repaired_on_the_next_eligible_boot(runtime,monkeypatch):
    linux,start=runtime
    start()
    eventually((cfg.STATE/'worker-ready').exists)
    old=generation.generation(linux)
    keeper=old['roles']['keeper']
    with ExitStack() as stack:
        generation._retain(stack,keeper).send(signal.SIGKILL)
    eventually(lambda:processes._linux_lifetime(keeper['pid']) is None)
    monkeypatch.setattr(service,'_linux_registration',lambda:linux)
    # An eligible boot asks the controller to start. The fixture supervisor
    # exercises launch and enrolment without opening a real service endpoint.
    def dispatch(*,explicit):
        assert explicit is False
        with service._control():
            return linux.start(explicit=explicit,deadline=time.monotonic()+20)
    monkeypatch.setattr(service,'start',dispatch)
    user.boot()
    new=eventually(lambda:(value if (value:=generation.generation(linux))['generation']!=old['generation'] else None))
    assert new['roles']['keeper']['pid']!=keeper['pid']
    assert all(processes._linux_lifetime(record['pid']) is None for record in old['roles'].values())


def test_uncertain_listener_after_native_end_blocks_stop_receipt(runtime,monkeypatch):
    linux,start=runtime
    (cfg.STATE/'prepare-only').touch()
    start()
    eventually((cfg.STATE/'preparing').exists)
    monkeypatch.setattr(service,'_linux_registration',lambda:linux)
    original=linux.native_stop
    def end(**kwargs):
        result=original(**kwargs)
        monkeypatch.setattr(processes,'_listeners',lambda:[object()])
        return result
    monkeypatch.setattr(linux,'native_stop',end)
    with pytest.raises(processes.OwnershipError,match='empty.runtime receipt'):
        service.stop(wait_seconds=2)
    status=json.loads((cfg.STATE/'service-control-status.json').read_text())
    assert status['end_attempted'] is True and 'empty_at_filetime' not in status
    assert linux.inspect()['enabled'] is False


def test_health_blocker_is_reported_without_a_probe(runtime,monkeypatch,capsys):
    import brain_cli as cli
    linux,_=runtime
    monkeypatch.setattr(service,'_linux_registration',lambda:linux)
    generation.set_blocked(linux,generation.LifecycleError('port_ownership_uncertain','Listener owner is uncertain.'))
    monkeypatch.setattr(sys,'argv',['brain_cli.py','health'])
    monkeypatch.setattr(cli,'mcp_call',lambda *a,**k:pytest.fail('A blocked generation may not authenticate.'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    result=json.loads(capsys.readouterr().out)
    assert error.value.code==1 and result=={'status':'blocked','reason':'port_ownership_uncertain','guidance':'Listener owner is uncertain.'}
    assert service.inspect()['blocked']==result
