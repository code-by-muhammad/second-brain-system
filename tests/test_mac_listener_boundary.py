"""Mac listener refusal contracts; no manager mutation or owner credentials."""
from types import SimpleNamespace
import socket

import pytest

import brain_client as client
import brain_mac_inventory as inventory
import brain_process as processes
import brain_settings as cfg
_original_listener_route = processes._listeners


@pytest.fixture
def mac_inventory(brain, monkeypatch):
    monkeypatch.setattr(processes, '_listeners', processes._mac_listeners)
    monkeypatch.setattr(processes.psutil, 'net_connections', lambda **_: pytest.fail('Privileged global socket scan'))
    monkeypatch.setattr(processes, 'descendants', lambda _: [])
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: ())


def test_foreign_loopback_listener_refuses_before_adapter_start_or_credentials(mac_inventory, monkeypatch):
    monkeypatch.setattr(processes, 'roots', lambda: [])
    monkeypatch.setattr(processes, 'validate_environment', lambda: None)
    import brain_service
    monkeypatch.setattr(brain_service, 'start', lambda **_: pytest.fail('Foreign port must prevent service start'))
    with socket.socket() as foreign:
        foreign.bind(('127.0.0.1', cfg.PORT))
        foreign.listen(1)
        monkeypatch.setattr(inventory, 'read', lambda *_, **__: (('tcp4', '127.0.0.1', cfg.PORT),))
        with pytest.raises(processes.OwnershipError, match='no verified socket owner'):
            client.ensure_service()
        foreign.settimeout(.01)
        with pytest.raises(TimeoutError):
            foreign.accept()  # No connection/request reached the unknown server.


def test_empty_loopback_destination_returns_absent_without_global_scan(mac_inventory, monkeypatch):
    monkeypatch.setattr(processes, 'roots', lambda: [])
    assert processes.listener_identity() is None


def test_launcher_and_root_paths_inspect_same_lifetime_once(mac_inventory, monkeypatch):
    scanned=[];closed=[]
    def owned(pid,birth):
        return SimpleNamespace(role='server',pid=pid,birth=birth,alive=lambda:True,
                               close=lambda:closed.append((pid,birth)))
    launcher=owned(77,100);child=owned(78,200);related_child=owned(78,200)
    monkeypatch.setattr(processes,'roots',lambda:[launcher,child])
    monkeypatch.setattr(processes,'descendants',lambda values:[related_child] if values[0] is launcher else [])
    def connections(pid):
        scanned.append(pid)
        return [] if pid==77 else [SimpleNamespace(status=processes.psutil.CONN_LISTEN,
            laddr=SimpleNamespace(ip='127.0.0.1',port=cfg.PORT))]
    monkeypatch.setattr(processes.psutil,'Process',lambda pid:SimpleNamespace(net_connections=lambda **_:connections(pid)))
    rows,_=processes._mac_socket_snapshot()
    assert len(rows)==1 and rows[0].pid==78 and scanned==[77,78]
    assert sorted(closed)==[(77,100),(78,200),(78,200)]


def test_duplicate_ownership_paths_never_merge_distinct_lifetimes(mac_inventory, monkeypatch):
    closed=[]
    def owned(birth):return SimpleNamespace(role='server',pid=77,birth=birth,
                                           alive=lambda:True,close=lambda:closed.append(birth))
    monkeypatch.setattr(processes,'roots',lambda:[owned(100),owned(200)])
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(net_connections=lambda **_:[]))
    with pytest.raises(processes.OwnershipError,match='identity changed'):
        processes._mac_socket_snapshot()
    assert sorted(closed)==[100,200]


def test_multiple_sockets_from_one_process_remain_distinct(mac_inventory, monkeypatch):
    owned=SimpleNamespace(role='server',pid=77,birth=100,alive=lambda:True,close=lambda:None)
    monkeypatch.setattr(processes,'roots',lambda:[owned])
    row=SimpleNamespace(status=processes.psutil.CONN_LISTEN,laddr=SimpleNamespace(ip='127.0.0.1',port=cfg.PORT))
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(net_connections=lambda **_:[row,row]))
    rows,_=processes._mac_socket_snapshot()
    assert len(rows)==2


def candidate(monkeypatch, *, address='127.0.0.1', states=(True, True), denied=False):
    remaining = iter(states)
    closed = []
    owned = SimpleNamespace(role='server', pid=77, birth=100, alive=lambda: next(remaining, False), close=lambda: closed.append(77))
    monkeypatch.setattr(processes, 'roots', lambda: [owned])
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: (('tcp4', address, cfg.PORT),))
    def connections(**_):
        if denied:
            raise processes.psutil.AccessDenied(77)
        return [SimpleNamespace(status=processes.psutil.CONN_LISTEN,
                                laddr=SimpleNamespace(ip=address, port=cfg.PORT))]
    monkeypatch.setattr(processes.psutil, 'Process', lambda _: SimpleNamespace(net_connections=connections))
    return closed


@pytest.mark.parametrize('states', [(False,), (True, False)])
def test_changed_candidate_lifetime_never_authorizes_a_socket(mac_inventory, monkeypatch, states):
    closed = candidate(monkeypatch, states=states)
    with pytest.raises(processes.OwnershipError, match='changed'):
        processes.listener_identity()
    assert closed and all(pid == 77 for pid in closed)


def test_unreadable_candidate_is_not_treated_as_absence(mac_inventory, monkeypatch):
    closed = candidate(monkeypatch, denied=True)
    with pytest.raises(processes.OwnershipError, match='Socket inventory unreadable'):
        processes.listener_identity()
    assert closed == [77]


def test_starting_server_does_not_compete_for_or_probe_its_port(mac_inventory, monkeypatch):
    closed = candidate(monkeypatch)
    monkeypatch.setattr(processes.psutil, 'Process', lambda _: SimpleNamespace(net_connections=lambda **_: []))
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: ())
    monkeypatch.setattr(processes.socket, 'socket', lambda *_, **__: pytest.fail('Starting server port was probed'))
    assert processes.listener_identity() is None
    assert closed == [77]


def test_descendant_inheriting_listener_causes_refusal(mac_inventory, monkeypatch):
    closed = candidate(monkeypatch)
    child = SimpleNamespace(pid=78, birth=110, alive=lambda: True, close=lambda: closed.append(78))
    monkeypatch.setattr(processes, 'descendants', lambda _: [child])
    monkeypatch.setattr(processes, 'inspect', lambda *_, **__: pytest.fail('Shared listener adopted'))
    with pytest.raises(processes.OwnershipError, match='unverified'):
        processes.listener_identity()
    assert sorted(closed) == [77, 78]


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'linux'])
def test_platform_route_keeps_global_scan_off_mac(brain, monkeypatch, platform):
    row = SimpleNamespace(status=processes.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=cfg.PORT))
    with monkeypatch.context() as patch:
        patch.setattr(processes.sys, 'platform', platform)
        patch.setattr(processes, '_mac_listeners', lambda: ['mac-result'])
        patch.setattr(processes.psutil, 'net_connections', lambda **_: [row])
        assert _original_listener_route() == (['mac-result'] if platform == 'darwin' else [row])


@pytest.mark.parametrize('address', ['0.0.0.0', '::1'])
def test_non_loopback_v4_candidate_refused_before_adoption(mac_inventory, monkeypatch, address):
    closed = candidate(monkeypatch, address=address)
    monkeypatch.setattr(processes, 'inspect', lambda *_, **__: pytest.fail('Unqualified address adopted'))
    with pytest.raises(processes.OwnershipError, match='unverified'):
        processes.listener_identity()
    assert closed == [77]
