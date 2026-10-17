"""Synthetic process handles only; exercise the actual supervisor entrypoint."""
import json
import runpy
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
            name = Path(command[1]).name
            assert name in ('brain_server.py', 'brain_index_bootstrap.py')
            assert kwargs['env'] == {}
            self.pid = 1001 + len(processes)
            self.name = name
            processes.append(self)

        def poll(self):
            return None

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
    expected = ['brain_server.py'] + ([] if terminal else ['brain_index_bootstrap.py'])
    assert [process.name for process in processes] == expected
    registry = json.loads((state / 'service-pids.json').read_text(encoding='utf-8'))
    assert registry['server'] == processes[0].pid
    assert registry['bootstrap'] == (None if terminal else processes[1].pid)
    assert (progress.read_bytes() if progress.exists() else None) == original
    assert failures == ([fault] if fault else [])
    assert len(connections) == 3 and len(sleeps) == 3
    assert all(process.poll() is None for process in processes)
