"""Bounded real-child regression, UNEXECUTED draft; no service/Git/real vault.

Only the old supervisor's maintenance timeout is shortened. Both implementations
use the same readiness handshake. Every synthetic child expires naturally; this
test never kills a PID or claims native service-stop tree containment.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import filelock
from filelock import FileLock, Timeout
import pytest
import brain_settings as cfg
import brain_supervisor as supervisor


# Windows venv python.exe is a redirector with a different real child PID.
# Use the authentic interpreter; supply only its existing FileLock dependency
# path inside the wholly synthetic maintenance parent, never via global env.
SYNTHETIC_PYTHON = Path(sys.base_prefix) / 'python.exe' if os.name == 'nt' else Path(sys.executable).resolve()
FILELOCK_ORIGIN = Path(filelock.__file__).resolve()
FILELOCK_SITE = FILELOCK_ORIGIN.parent.parent
assert sys.version_info[:2] == (3, 12)
assert SYNTHETIC_PYTHON.is_file(), 'Authentic Python executable is missing.'
assert FILELOCK_ORIGIN == FILELOCK_SITE / 'filelock' / '__init__.py' and FILELOCK_ORIGIN.is_file()

class EndObservation(BaseException):
    pass


MAINTENANCE_SOURCE = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from filelock import FileLock

state = Path(sys.argv[0]).resolve().parents[1] / 'state'
grandchild_source = r"""
from pathlib import Path
import sys
import time
state = Path(sys.argv[1])
(state / 'grandchild-started').write_text('started', encoding='utf-8')
time.sleep(1.5)
(state / 'grandchild-done').write_text('done', encoding='utf-8')
"""
with FileLock(str(state / 'maintenance.lock'), timeout=0):
    child = subprocess.Popen([sys.executable, '-c', grandchild_source, str(state)])
    deadline = time.monotonic() + 2
    while not (state / 'grandchild-started').exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('synthetic grandchild did not become ready')
        time.sleep(0.005)
    (state / 'maintenance-ready.json').write_text(
        json.dumps({'parent': os.getpid(), 'grandchild': child.pid}), encoding='utf-8')
    child.wait(timeout=4)
'''


def test_maintenance_keeps_lock_while_its_bounded_descendant_is_live(brain, monkeypatch):
    _, state = brain
    app = cfg.ROOT / 'app'
    app.mkdir()
    marker = 'from filelock import FileLock\n'
    assert MAINTENANCE_SOURCE.count(marker) == 1
    synthetic_source = MAINTENANCE_SOURCE.replace(marker, 'sys.path.insert(0, ' + repr(str(FILELOCK_SITE)) + ')\n' + marker, 1)
    (app / 'brain_maintenance.py').write_text(synthetic_source, encoding='utf-8')
    (state / 'index-bootstrap-status.json').write_text('{"status":"verified"}', encoding='utf-8')
    ready = state / 'maintenance-ready.json'
    done = state / 'grandchild-done'
    parents = []
    owned = {}
    real_popen, real_run = subprocess.Popen, subprocess.run
    real_sleep, real_monotonic = time.sleep, time.monotonic

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect_ex(self, address):
            assert address == ('127.0.0.1', cfg.PORT)
            return 0

    def launch(command, **kwargs):
        assert Path(command[1]) == app / 'brain_maintenance.py'
        process = real_popen(command, **kwargs)
        parents.append(process)
        deadline = real_monotonic() + 3
        while not ready.exists():
            if process.poll() is not None or real_monotonic() >= deadline:
                pytest.fail('bounded maintenance child failed readiness handshake')
            real_sleep(0.005)
        return process

    def old_timeout_only(command, **kwargs):
        assert Path(command[1]) == app / 'brain_maintenance.py'
        assert kwargs['timeout'] == 240
        return real_run(command, **{**kwargs, 'timeout': 0.2})

    def sleep(seconds):
        if seconds == 20:
            raise EndObservation
        # CPython's POSIX Popen.wait(timeout) uses time.sleep too. Preserve it.
        return real_sleep(seconds)

    monkeypatch.setattr(cfg, 'PYTHON', SYNTHETIC_PYTHON)
    monkeypatch.setattr(cfg, 'environment', lambda: os.environ.copy())
    monkeypatch.setattr(supervisor.socket, 'socket', Socket)
    monkeypatch.setattr(supervisor.subprocess, 'Popen', launch)
    monkeypatch.setattr(supervisor.subprocess, 'run', old_timeout_only)
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: 1000)
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    try:
        with pytest.raises(EndObservation):
            supervisor.main(owned)
        assert len(parents) == 1
        parent = parents[0]
        pids = json.loads(ready.read_text(encoding='utf-8'))
        assert pids['parent'] == parent.pid and pids['grandchild'] != parent.pid
        held = False
        try:
            with FileLock(str(state / 'maintenance.lock'), timeout=0):
                pass
        except Timeout:
            held = True
        observations = {'parent_live': parent.poll() is None,
                        'owned_parent': owned.get('maintenance') is parent,
                        'grandchild_not_done': not done.exists(), 'lock_held': held}
        assert all(observations.values()), json.dumps(observations, sort_keys=True)
        registry = json.loads((state / 'service-pids.json').read_text(encoding='utf-8'))
        assert registry['maintenance'] == parent.pid
    finally:
        # Parents and descendants are synthetic and naturally bounded. Never
        # kill an arbitrary/stale PID, even when exercising the old failure.
        for parent in parents:
            parent.wait(timeout=7)
        if parents:
            deadline = real_monotonic() + 3
            while not done.exists() and real_monotonic() < deadline:
                real_sleep(0.005)
            assert done.exists(), 'synthetic descendant did not expire naturally'
