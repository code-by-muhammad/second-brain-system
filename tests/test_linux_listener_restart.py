"""A killed server must not look like a second listener. No sockets are opened."""
from types import SimpleNamespace

import pytest

import brain_process as processes
import brain_settings as cfg


def _row(pid):
    return SimpleNamespace(pid=pid, status=processes.psutil.CONN_LISTEN,
                           laddr=SimpleNamespace(ip='127.0.0.1', port=cfg.PORT))


def _patch(monkeypatch, rows, dead):
    monkeypatch.setattr(processes.psutil, 'net_connections', lambda **_: rows)

    def Process(pid):
        if pid in dead:
            return SimpleNamespace(is_running=lambda: False, status=lambda: processes.psutil.STATUS_ZOMBIE)
        return SimpleNamespace(is_running=lambda: True, status=lambda: processes.psutil.STATUS_RUNNING)

    monkeypatch.setattr(processes.psutil, 'Process', Process)


@pytest.fixture(autouse=True)
def linux_scan(monkeypatch):
    if processes.sys.platform == 'darwin':
        pytest.skip('Darwin uses the separate inventory')


def test_a_dead_listener_and_a_duplicate_row_leave_one_owner(monkeypatch):
    _patch(monkeypatch, [_row(10), _row(11), _row(10)], {11})
    assert [row.pid for row in processes._listeners()] == [10]


def test_two_live_listeners_stay_visible(monkeypatch):
    _patch(monkeypatch, [_row(10), _row(12)], set())
    assert [row.pid for row in processes._listeners()] == [10, 12]
