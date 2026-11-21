"""Synthetic handles only; public lifecycle oracles, no real process creation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import brain_settings as cfg
import brain_supervisor as supervisor


class EndObservation(BaseException):
    pass


@pytest.fixture
def lifecycle(brain, monkeypatch):
    from supervisor_fixture import install
    install(monkeypatch,supervisor)
    _, state = brain
    (state / 'index-bootstrap-status.json').write_text('{"status":"pending"}', encoding='utf-8')
    h = SimpleNamespace(owned={}, processes=[], snapshots=[], now=1000,
                        listening=True, sleeps=0, stop_after=1,
                        on_sleep=lambda: None, on_spawn=lambda name: None,
                        on_publish=lambda pids: None)
    import os
    replace = os.replace

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect_ex(self, address):
            assert address == ('127.0.0.1', cfg.PORT)
            return 0 if h.listening else 1

    class Process:
        def __init__(self, command, **kwargs):
            from supervisor_fixture import script
            self.name = Path(script(command)[0]).name
            self.arguments = script(command)[1]
            assert self.name in ('brain_server.py', 'brain_index_bootstrap.py', 'brain_maintenance.py')
            assert kwargs['env'] == {} and 'timeout' not in kwargs
            from supervisor_fixture import bootstrap_pipe
            self.stdin = bootstrap_pipe(command, kwargs)
            h.on_spawn(self.name)
            self.pid = 4100 + len(h.processes)
            self.returncode = None
            h.processes.append(self)

        def poll(self):
            return self.returncode

        def kill(self):
            pytest.fail('supervisor killed an owned child')

        def terminate(self):
            pytest.fail('supervisor terminated an owned child')

        def wait(self, *args, **kwargs):
            pytest.fail('supervisor blocked waiting for an owned child')

    def run(*args, **kwargs):
        pytest.fail('blocking maintenance subprocess.run used')

    def publish(source, target):
        assert Path(target) == state / 'service-pids.json'
        pids = json.loads(Path(source).read_text(encoding='utf-8'))
        h.on_publish(pids)
        result = replace(source, target)
        h.snapshots.append(pids)
        return result

    def sleep(seconds):
        assert seconds == 20
        h.sleeps += 1
        h.on_sleep()
        if h.sleeps >= h.stop_after:
            raise EndObservation

    def observe(cycles=1):
        h.sleeps = 0
        h.stop_after = cycles
        with pytest.raises(EndObservation):
            supervisor.main(h.owned)

    h.observe = observe
    h.registry = lambda: json.loads((state / 'service-pids.json').read_text(encoding='utf-8'))
    h.named = lambda name: [p for p in h.processes if p.name == name]
    h.state = state
    monkeypatch.setattr(supervisor.socket, 'socket', Socket)
    monkeypatch.setattr(supervisor.subprocess, 'Popen', Process)
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: h.now)
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    monkeypatch.setattr(cfg, 'environment', lambda: {})
    monkeypatch.setattr(cfg, 'child_options', lambda: {})
    monkeypatch.setattr(os, 'replace', publish)
    return h


@pytest.mark.parametrize('elapsed', [241, 901, 3601, 86400])
def test_live_maintenance_never_overlaps_or_blocks_server_recovery(lifecycle, elapsed):
    h = lifecycle

    def step():
        maintenance = h.named('brain_maintenance.py')[0]
        assert h.registry()['maintenance'] == maintenance.pid
        assert maintenance.poll() is None
        if h.sleeps == 1:
            h.now += elapsed
            h.listening = False
        else:
            h.listening = True

    h.on_sleep = step
    h.observe(3)
    assert len(h.named('brain_maintenance.py')) == 1
    assert len(h.named('brain_server.py')) == 1
    assert len(h.named('brain_index_bootstrap.py')) == 1
    assert all(p.poll() is None for p in h.processes)


@pytest.mark.parametrize('role', ['server', 'bootstrap', 'maintenance'])
def test_successful_spawn_is_owned_before_registry_failure_and_retry(lifecycle, role):
    h = lifecycle
    if role == 'server':
        h.listening = False
    if role == 'maintenance':
        (h.state / 'index-bootstrap-status.json').write_text('{"status":"verified"}', encoding='utf-8')
    failed = []

    def reject(pids):
        if pids[role] is not None and not failed:
            failed.append(pids[role])
            raise OSError('synthetic registry replacement failure')

    h.on_publish = reject
    with pytest.raises(OSError, match='synthetic registry'):
        supervisor.main(h.owned)
    process = h.owned[role]
    assert process.poll() is None and process.pid == failed[0]
    h.listening = True
    h.on_publish = lambda pids: None
    h.observe()
    assert h.owned[role] is process
    assert h.registry()[role] == process.pid
    assert len(h.named(process.name)) == 1


@pytest.mark.parametrize('returncode', [0, 1])
def test_next_maintenance_waits_after_observed_completion_even_across_retry(lifecycle, returncode):
    h = lifecycle
    h.observe()
    first = h.named('brain_maintenance.py')[0]
    first.returncode = returncode
    h.now = 1100
    h.observe()
    assert h.registry()['maintenance'] is None
    # Each observe call is a fresh main invocation with the same owned handles.
    h.now = 1999
    h.observe()
    assert len(h.named('brain_maintenance.py')) == 1
    h.now = 2001
    h.observe()
    assert len(h.named('brain_maintenance.py')) == 2
    assert h.registry()['maintenance'] == h.named('brain_maintenance.py')[1].pid


@pytest.mark.parametrize('returncode', [0, 1])
def test_index_reconciliation_uses_completion_cadence_and_respects_hold(lifecycle, returncode):
    h = lifecycle
    (h.state / 'index-bootstrap-status.json').write_text('{"status":"verified"}', encoding='utf-8')
    script = 'brain_index_bootstrap.py'
    hold = h.state / 'service-transaction.json'
    held = {'hold_id': 'a' * 32, 'root': str(cfg.ROOT), 'stage': 'stopped'}
    hold.write_text(json.dumps(held), encoding='utf-8')
    h.observe()
    assert not h.named(script)

    hold.unlink()
    h.listening = False
    h.observe()
    assert not h.named(script)

    h.listening = True
    h.observe()
    # The maintenance pass that is due goes first; the repair waits for it and then takes its turn.
    upkeep, = h.named('brain_maintenance.py')
    assert not h.named(script)
    upkeep.returncode = 0
    h.observe()  # The cycle that records the end of the pass also starts the repair.
    first, = h.named(script)
    assert first.arguments == ['--reconcile']
    assert h.registry()['bootstrap'] == first.pid

    h.now += 3600
    h.observe()
    # A maintenance pass that became due meanwhile is not started beside a running repair.
    assert h.named(script) == [first] and h.named('brain_maintenance.py') == [upkeep]
    assert h.registry()['bootstrap'] == first.pid
    first.returncode = returncode
    h.observe()
    assert h.registry()['bootstrap'] is None
    _, later = h.named('brain_maintenance.py')
    later.returncode = 0
    h.observe()

    h.now += 899
    h.observe()
    assert h.named(script) == [first]
    hold.write_text(json.dumps(held), encoding='utf-8')
    h.now += 1
    h.observe()
    assert h.named(script) == [first]

    hold.unlink()
    h.observe()
    _, second = h.named(script)
    assert second.arguments == ['--reconcile']
    assert h.registry()['bootstrap'] == second.pid


def test_spawn_failure_retries_without_inventing_maintenance_handle(lifecycle):
    h = lifecycle
    failed = []

    def reject(name):
        if name == 'brain_maintenance.py' and not failed:
            failed.append(name)
            raise OSError('synthetic maintenance spawn failure')

    h.on_spawn = reject
    with pytest.raises(OSError, match='synthetic maintenance spawn'):
        supervisor.main(h.owned)
    assert h.owned.get('maintenance') is None
    assert not h.named('brain_maintenance.py')
    h.observe()
    assert len(h.named('brain_maintenance.py')) == 1


def test_new_handle_is_published_before_any_next_launch_or_sleep(lifecycle):
    h = lifecycle
    h.listening = False
    def step():
        for process in h.processes:
            role = {'brain_server.py': 'server', 'brain_index_bootstrap.py': 'bootstrap',
                    'brain_maintenance.py': 'maintenance'}[process.name]
            assert h.registry()[role] == process.pid
        h.listening = True

    # A spawn sees the registry for every preceding successful live handle.
    def on_spawn(name):
        for process in h.processes:
            role = {'brain_server.py': 'server', 'brain_index_bootstrap.py': 'bootstrap',
                    'brain_maintenance.py': 'maintenance'}[process.name]
            assert h.registry()[role] == process.pid

    h.on_spawn = on_spawn
    h.on_sleep = step
    h.observe(2)
    assert {p.name for p in h.processes} == {'brain_server.py', 'brain_index_bootstrap.py', 'brain_maintenance.py'}


def test_cached_registry_pids_are_not_adopted_and_dead_handles_are_not_published(lifecycle):
    h = lifecycle
    (h.state / 'service-pids.json').write_text('{"supervisor":90001,"server":90002,"bootstrap":90003,"maintenance":90004}', encoding='utf-8')
    h.observe()
    bootstrap = h.named('brain_index_bootstrap.py')[0]
    bootstrap.returncode = 0
    (h.state / 'index-bootstrap-status.json').write_text('{"status":"verified"}', encoding='utf-8')
    h.observe()
    pids = h.registry()
    assert pids['server'] is None and pids['bootstrap'] is None
    assert pids['maintenance'] == h.named('brain_maintenance.py')[0].pid
    assert not {90001, 90002, 90003, 90004}.intersection(pids.values())


@pytest.mark.parametrize('ending', [KeyboardInterrupt, SystemExit, EndObservation])
def test_observation_cancellation_does_not_kill_owned_maintenance(lifecycle, ending):
    h = lifecycle

    def cancel():
        raise ending

    h.on_sleep = cancel
    with pytest.raises(ending):
        supervisor.main(h.owned)
    maintenance = h.named('brain_maintenance.py')[0]
    assert maintenance.poll() is None
    assert h.owned['maintenance'] is maintenance
    assert h.registry()['maintenance'] == maintenance.pid
    # No native service-stop or abrupt OS-exit containment is inferred here.


def test_repair_that_cannot_be_started_waits_an_interval_and_the_cycle_goes_on(lifecycle, monkeypatch):
    h = lifecycle
    (h.state / 'index-bootstrap-status.json').write_text('{"status":"verified"}', encoding='utf-8')
    h.observe()
    upkeep, = h.named('brain_maintenance.py')
    upkeep.returncode = 0  # The next cycle records that the pass ended and tries to start the repair.
    spawn = supervisor.subprocess.Popen
    attempts = []

    def refuse(command, **kwargs):
        from supervisor_fixture import script
        if Path(script(command)[0]).name == 'brain_index_bootstrap.py':
            attempts.append(h.now)
            raise OSError('synthetic launch failure')
        return spawn(command, **kwargs)

    beats = []
    monkeypatch.setattr(supervisor.subprocess, 'Popen', refuse)
    monkeypatch.setattr(supervisor.service, 'write_heartbeat', lambda state: beats.append(state))
    h.observe(3)
    # One attempt, not one per cycle; the heartbeat was still written in every cycle.
    assert attempts == [h.now] and beats == ['ready'] * 3 and h.owned.get('bootstrap') is None
    assert h.owned['_reconciling'] is False
    h.now += 899
    h.observe()
    assert len(attempts) == 1
    h.now += 1
    monkeypatch.setattr(supervisor.subprocess, 'Popen', spawn)
    h.observe()
    retried, = h.named('brain_index_bootstrap.py')
    assert retried.arguments == ['--reconcile']
    from supervisor_fixture import released
    assert released(retried)
