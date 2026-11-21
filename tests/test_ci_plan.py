"""Exercise shard evidence and both shutdown paths through the CI launcher."""

from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from textwrap import dedent
from threading import Event, Thread
import time

import psutil
import pytest

import ci_plan


TESTS = Path(__file__).resolve().parent
GENERAL = 'general-1of1'
NATIVE = 'native'
FIRST = 'tests/test_general.py::TestCase::test_case[value with spaces Ω]'
SECOND = 'tests/test_native.py::test_case'
COLLECTED = [FIRST, SECOND]
MAP = {'version': 1, 'general_shards': 1, 'lanes': {NATIVE: ['tests/test_native.py']},
       'weights': {'tests/test_general.py': 2}}


def _row(path, key, *, collected=COLLECTED, selected=None, started=None, ended=None,
         exit_code=0, finish=0, watchdog='', junit=True):
    path.mkdir()
    if selected is None:
        selected = [FIRST] if key == GENERAL else [SECOND]
    if started is None:
        started = selected
    if ended is None:
        ended = [(node, 'passed') for node in selected]
    events = [dict(event='session', shard=key, map_sha256=ci_plan.map_digest(MAP)),
              dict(event='collected', ids=collected), dict(event='selected', ids=selected)]
    events.extend(dict(event='start', id=node) for node in started)
    events.extend(dict(event='end', id=node, outcome=outcome, seconds=0.1, reason='', longrepr='')
                  for node, outcome in ended)
    if finish is not None:
        events.append(dict(event='finish', exitstatus=finish))
    (path / 'ledger.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
    (path / 'watchdog.txt').write_text(watchdog, encoding='utf-8')
    if exit_code is not None:
        (path / 'exit-code.txt').write_text(str(exit_code), encoding='ascii')
    if junit:
        (path / 'junit.xml').write_text('<testsuites><testsuite errors="0" failures="0"/></testsuites>',
                                      encoding='utf-8')
    return path


@pytest.mark.parametrize(('changes', 'classification', 'detail'), [
    pytest.param({}, 'ok', '', id='clean'),
    pytest.param({'started': COLLECTED, 'ended': [(node, 'passed') for node in COLLECTED]},
                 'failed', SECOND, id='unselected-passing-node'),
    pytest.param({'started': [FIRST, FIRST]}, 'failed', FIRST, id='duplicate-start'),
    pytest.param({'exit_code': 1}, 'failed', '', id='finish-zero-exit-one'),
    pytest.param({'exit_code': None}, 'killed-without-dump', '', id='no-exit-code'),
    pytest.param({'junit': False}, 'failed', 'junit.xml', id='clean-missing-junit'),
    pytest.param({'junit': False, 'ended': [(FIRST, 'failed')], 'finish': 1, 'exit_code': 1},
                 'failed', '', id='failed-missing-junit'),
])
def test_check_row(tmp_path, changes, classification, detail):
    result = ci_plan.check_row(_row(tmp_path / GENERAL, GENERAL, **changes), GENERAL, MAP)
    assert result['class'] == classification
    errors = ' '.join(result['errors'])
    if detail:
        assert detail in errors
    if changes.get('exit_code') == 1:
        assert 'junit.xml' not in errors


def test_check_row_recovers_timeout_from_partial_ledger(tmp_path):
    watchdog = f'CHECKPOINT 0.000 node 3 {FIRST}\nPHASE call {FIRST}\nTIMEOUT node {FIRST}\n'
    path = _row(tmp_path / GENERAL, GENERAL, started=[FIRST], ended=[], finish=None,
                exit_code=None, watchdog=watchdog, junit=False)
    with (path / 'ledger.jsonl').open('a', encoding='utf-8') as ledger:
        ledger.write('{"event":"end"')
    row = ci_plan.check_row(path, GENERAL, MAP)
    assert row['class'] == 'timeout'
    assert row['timeout'] == {'scope': 'node', 'node': FIRST, 'phase': 'call', 'limit': 3}
    for log, limit in [
        (watchdog.replace(f'TIMEOUT node {FIRST}', 'TIMEOUT node'), 3),
        (watchdog.replace('node 3 ', 'node e ').replace(f'TIMEOUT node {FIRST}', 'Timeout (0:00:33)!'), None),
    ]:
        (path / 'watchdog.txt').write_text(log, encoding='utf-8')
        row = ci_plan.check_row(path, GENERAL, MAP)
        assert row['class'] == 'timeout'
        assert row['timeout'] == {'scope': 'node', 'node': FIRST, 'phase': 'call', 'limit': limit}


@pytest.mark.parametrize('defect', [None, 'missing', 'extra', 'collection', 'partition'],
                         ids=['clean', 'missing', 'extra', 'collection', 'partition'])
def test_gate_requires_complete_partition(tmp_path, defect):
    _row(tmp_path / GENERAL, GENERAL, selected=COLLECTED if defect == 'partition' else None)
    if defect != 'missing':
        collected = list(reversed(COLLECTED)) if defect == 'collection' else COLLECTED
        _row(tmp_path / NATIVE, NATIVE, collected=collected)
    if defect == 'extra':
        (tmp_path / 'unexpected').mkdir()
    result = ci_plan.gate(tmp_path, MAP)
    assert result['ok'] is (defect is None)
    if defect == 'missing':
        assert result['rows'][NATIVE]['class'] == 'missing'
    elif defect is not None:
        assert {'extra': 'Extra row', 'collection': 'Collected digests differ',
                'partition': 'do not partition'}[defect] in ' '.join(result['errors'])


def test_assign_uses_median_for_unweighted_files():
    mapping = {'version': 1, 'general_shards': 2, 'lanes': {},
               'weights': {'heavy.py': 8, 'middle.py': 5, 'light.py': 2}}
    files = ['unweighted.py', 'heavy.py', 'light.py', 'middle.py']
    assert ci_plan.assign(mapping, files) == {
        'heavy.py': 'general-1of2', 'light.py': 'general-1of2',
        'middle.py': 'general-2of2', 'unweighted.py': 'general-2of2',
    }


def test_assign_covers_committed_map_and_lanes():
    mapping = ci_plan.load_map()
    lanes = {file: key for key, files in mapping['lanes'].items() for file in files}
    files = set(lanes) | mapping['weights'].keys()
    assigned = ci_plan.assign(mapping, sorted(files))
    assert assigned.keys() == files
    for file, key in lanes.items():
        assert (TESTS.parent / file).is_file()
        assert assigned[file] == key


HOLDER = '''\
import os
from pathlib import Path
import sys
import time

print('holder stdout', flush=True)
print('holder stderr', file=sys.stderr, flush=True)
pending = Path('holder.pid.tmp')
pending.write_text(str(os.getpid()), encoding='ascii')
pending.replace('holder.pid')
deadline = time.monotonic() + 95
while not Path('release').exists() and time.monotonic() < deadline:
    time.sleep(0.05)
'''

HANG_IMPORTS = '''\
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time
from time import sleep as real_sleep

import psutil

def start_holder():
    child = subprocess.Popen([sys.executable, '-B', 'holder.py'], stdout=sys.stdout, stderr=sys.stderr)
    deadline = time.monotonic() + 15
    while not Path('holder.pid').exists():
        assert time.monotonic() < deadline, 'stdout holder failed to start'
        time.sleep(0.02)
    return child
'''


def _running(process):
    with suppress(psutil.NoSuchProcess):
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    return False


def _holder_running(path):
    pid = path / 'holder.pid'
    if pid.exists():
        with suppress(psutil.NoSuchProcess):
            return _running(psutil.Process(int(pid.read_text(encoding='ascii'))))
    return False


def _wait_until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


@contextmanager
def _launch_hang(path, source, name, *, seconds, observe_live=False):
    report = path / 'report'
    report.mkdir()
    (path / 'conftest.py').write_text(
        f'import sys\nsys.path.insert(0, {str(TESTS)!r})\nfrom ci_plugin import Plugin\n'
        "def pytest_configure(config):\n    config.pluginmanager.register(Plugin(config), 'sb-ci')\n",
        encoding='utf-8',
    )
    (path / 'holder.py').write_text(HOLDER, encoding='utf-8')
    (path / 'test_hangs.py').write_text(HANG_IMPORTS + dedent(source), encoding='utf-8')
    env = dict(os.environ, SB_TEST_CI_DIR=str(report), SB_TEST_SHARD='all', SB_TEST_NODE_TIMEOUT='3',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='', PYTEST_PLUGINS='')
    deadline = time.monotonic() + seconds
    launcher = subprocess.Popen(
        [sys.executable, '-B', str(TESTS / 'ci_launch.py'), str(report), '-v', '-s',
         '-p', 'no:cacheprovider', '-p', 'no:faulthandler', '--rootdir', str(path), 'test_hangs.py'],
        cwd=path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, encoding='utf-8', errors='replace',
    )
    node = f'test_hangs.py::{name}'
    lines = []
    start_seen = Event()

    def read_output():
        for line in launcher.stdout:
            lines.append(line)
            if line == f'START {node}\n':
                start_seen.set()

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    try:
        if observe_live:
            assert start_seen.wait(15), ''.join(lines)
            assert _wait_until((path / 'entering-c-sleep').is_file, 15), ''.join(lines)
            assert any(_running(child) for child in psutil.Process(launcher.pid).children())
        assert launcher.wait(timeout=max(0, deadline - time.monotonic())) == 1, ''.join(lines)
        reader.join(timeout=min(5, max(0, deadline - time.monotonic())))
        assert not reader.is_alive(), 'launcher stdout did not reach EOF'
        assert start_seen.is_set(), ''.join(lines)
        assert (report / 'exit-code.txt').read_text(encoding='ascii').strip() == '1'
        yield report, node
    finally:
        (path / 'release').touch()
        if launcher.poll() is None:
            with suppress(psutil.NoSuchProcess):
                for pytest_process in reversed(psutil.Process(launcher.pid).children(recursive=True)):
                    with suppress(psutil.NoSuchProcess):
                        pytest_process.kill()
            launcher.kill()
            launcher.wait(timeout=5)
        reader.join(timeout=5)
        if not reader.is_alive():
            launcher.stdout.close()
        assert _wait_until(lambda: not _holder_running(path), 5), 'stdout holder did not stop'


def _timeout_trace(report, node):
    watchdog = (report / 'watchdog.txt').read_text(encoding='utf-8')
    assert f'PHASE call {node}' in watchdog.splitlines()
    name = node.split('::')[1]
    assert re.search(rf'test_hangs\.py", line \d+ in {name}\b', watchdog)
    row = ci_plan.check_row(report, 'all', ci_plan.load_map())
    assert row['class'] == 'timeout'
    assert row['timeout'] == {'scope': 'node', 'node': node, 'phase': 'call', 'limit': 3}
    return watchdog, row


def test_hang_fails_fast_with_stdout_held(tmp_path):
    source = '''
        def test_first():
            pass

        def test_python_hang(monkeypatch):
            child = start_holder()

            def broken(*args, **kwargs):
                raise SystemExit('patched by synthetic test')

            monkeypatch.setattr(time, 'sleep', broken)
            monkeypatch.setattr(time, 'monotonic', lambda: -1)
            monkeypatch.setattr(os, '_exit', broken)
            monkeypatch.setattr(psutil, 'Process', broken)
            real_sleep(90)

        def test_third():
            pass
    '''
    with _launch_hang(tmp_path, source, 'test_python_hang', seconds=40) as (report, node):
        watchdog, row = _timeout_trace(report, node)
        assert f'TIMEOUT node {node}' in watchdog.splitlines()
        pid = (tmp_path / 'holder.pid').read_text(encoding='ascii')
        assert f'KILLED {pid}' in watchdog.splitlines()
        assert _wait_until(lambda: not _holder_running(tmp_path), 5)
        assert row['not-run'] == ['test_hangs.py::test_third']


def test_gil_held_hang_hits_c_backstop(tmp_path):
    source = '''
        def test_gil_hang():
            child = start_holder()
            sleep = ctypes.PyDLL('kernel32.dll').Sleep if os.name == 'nt' else ctypes.PyDLL(None).sleep
            Path('entering-c-sleep').touch()
            sleep(90_000 if os.name == 'nt' else 90)
    '''
    with _launch_hang(tmp_path, source, 'test_gil_hang', seconds=75, observe_live=True) as (report, node):
        watchdog, _ = _timeout_trace(report, node)
        checkpoint = rf'^CHECKPOINT \S+ node \S+ {re.escape(node)}\n'
        assert re.search(checkpoint + r'.*?^Timeout \(', watchdog, flags=re.MULTILINE | re.DOTALL)
        assert _holder_running(tmp_path), 'holder exited before launcher and pipe EOF'
        output = (report / 'pytest-output.log').read_bytes()
        assert b'holder stdout' in output and b'holder stderr' in output
