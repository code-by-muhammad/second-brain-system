"""Synthetic process handles only; exercise the actual supervisor entrypoint."""
import json
import runpy
import sys
from pathlib import Path

import pytest
import brain_settings as cfg
import brain_supervisor as supervisor


class EndObservation(BaseException):
    pass


CASES = [
    ('null', 'null', None),
    ('list', '[]', None),
    ('nonempty-list', '["pending"]', None),
    ('string', '"verified"', None),
    ('number', '7', None),
    ('boolean', 'true', None),
    ('verified-control', '{"status":"verified"}', None),
    ('review-control', '{"status":"needs review"}', None),
    ('attention-control', '{"status":"needs attention"}', None),
    ('object-control', '{}', None),
    ('pending-control', '{"status":"pending"}', None),
    ('invalid-json-control', '{', None),
    ('missing-control', None, None),
    ('maintenance-fault', '{}', 'maintenance'),
    ('registry-fault', '{}', 'registry'),
]


@pytest.mark.parametrize('label,raw,fault', CASES, ids=[row[0] for row in CASES])
def test_actual_supervisor_recovery_keeps_owned_children(brain, monkeypatch, label, raw, fault):
    from supervisor_fixture import install
    install(monkeypatch,supervisor)
    _, state = brain
    progress = state / 'index-bootstrap-status.json'
    if raw is not None:
        progress.write_bytes(raw.encode('utf-8'))
    original = progress.read_bytes() if progress.exists() else None
    processes, sleeps, connections, failures = [], [], [], []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect_ex(self, address):
            assert address == ('127.0.0.1', cfg.PORT)
            connections.append(address)
            return 1 if len(connections) == 1 else 0

    class Process:
        def __init__(self, command, **kwargs):
            from supervisor_fixture import script
            name = Path(script(command)[0]).name
            # R138 transport qualification only: preserve every old oracle.
            if name == 'brain_maintenance.py':
                maintenance(command, **kwargs)
                self.pid = 9001
                self.name = name
                return
            assert name in ('brain_server.py', 'brain_index_bootstrap.py')
            assert kwargs['env'] == {}
            from supervisor_fixture import bootstrap_pipe
            self.stdin = bootstrap_pipe(command, kwargs)
            self.pid = 1001 + len(processes)
            self.name = name
            self.arguments = script(command)[1]
            processes.append(self)

        def poll(self):
            return 0 if self.name == 'brain_maintenance.py' else None

    def sleep(seconds):
        assert seconds in (10, 20)
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise EndObservation

    def maintenance(command, **kwargs):
        assert Path(command[1]).name == 'brain_maintenance.py'
        if fault == 'maintenance' and not failures:
            failures.append(fault)
            raise RuntimeError('synthetic maintenance observation')
        return supervisor.subprocess.CompletedProcess(command, 0)

    import os
    replace = os.replace

    def replace_registry(source, target):
        assert Path(target) == state / 'service-pids.json'
        if fault == 'registry' and not failures:
            failures.append(fault)
            raise OSError('synthetic registry observation')
        return replace(source, target)

    monkeypatch.setattr(supervisor.socket, 'socket', Socket)
    monkeypatch.setattr(supervisor.subprocess, 'Popen', Process)
    monkeypatch.setattr(supervisor.subprocess, 'run', maintenance)
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: 1000)
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    monkeypatch.setattr(cfg, 'environment', lambda: {})
    monkeypatch.setattr(cfg, 'child_options', lambda: {})
    monkeypatch.setattr(os, 'replace', replace_registry)
    with pytest.raises(EndObservation):
        runpy.run_path(supervisor.__file__, run_name='__main__')

    terminal = label in ('verified-control', 'review-control', 'attention-control')
    assert [process.name for process in processes] == ['brain_server.py', 'brain_index_bootstrap.py']
    assert processes[1].arguments == (['--reconcile'] if terminal else [])
    # Either entry form is started behind a pipe and released with one line.
    from supervisor_fixture import released
    assert processes[0].stdin is None and released(processes[1])
    registry = json.loads((state / 'service-pids.json').read_text(encoding='utf-8'))
    assert registry['server'] == processes[0].pid
    assert registry['bootstrap'] == processes[1].pid
    assert (progress.read_bytes() if progress.exists() else None) == original
    assert failures == ([fault] if fault else [])
    # Ownership now requires publishing this supervisor before looking at a
    # listener. A registry failure therefore precedes that iteration's query.
    assert len(connections) == (2 if fault=='registry' else 3) and len(sleeps) == 3
    assert all(process.poll() is None for process in processes)


@pytest.mark.skipif(sys.platform!='linux',reason='Linux user-space generation enrolment.')
@pytest.mark.parametrize('role',['server','maintenance'])
def test_real_supervisor_child_is_reaped_when_enrolment_fails(brain,monkeypatch,role):
    _,state=brain
    app=cfg.ROOT/'app';app.mkdir()
    (app/f'brain_{role}.py').write_text('import time\ntime.sleep(60)\n',encoding='utf-8')
    spawned=[]

    def refuse(given,child):
        assert given==role
        spawned.append(child)
        raise RuntimeError('synthetic enrolment failure')

    monkeypatch.setattr(supervisor.service,'supervisor_spawn_allowed',lambda:True)
    monkeypatch.setattr(supervisor.service,'enrol_supervisor_child',refuse)
    monkeypatch.setattr(supervisor.processes,'listener_identity',lambda:{'synthetic':True} if role=='maintenance' else None)
    monkeypatch.setattr(supervisor,'_bootstrap_admitted',lambda:None)
    monkeypatch.setattr(supervisor,'_maintenance_due',lambda owned:True)
    owned={}
    try:
        with pytest.raises(RuntimeError,match='synthetic enrolment failure'):
            supervisor.main(owned)
        assert len(spawned)==1 and spawned[0].returncode is not None
        assert role not in owned and json.loads((state/'service-pids.json').read_text())[role] is None
    finally:
        for child in spawned:
            if child.poll() is None:child.kill()
            child.wait(timeout=5)
