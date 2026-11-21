"""Listener inventory preserves uncertainty through ownership confirmation."""
import json
from types import SimpleNamespace

import pytest

import brain_process as processes
import brain_settings as cfg


@pytest.fixture(autouse=True)
def linux_scan(monkeypatch):
    monkeypatch.setattr(processes.sys,'platform','linux')


def row(pid,fd=8,ip='127.0.0.1'):
    return SimpleNamespace(pid=pid,fd=fd,status=processes.psutil.CONN_LISTEN,
                           laddr=SimpleNamespace(ip=ip,port=cfg.PORT))


def lifetime(pid,ticks=100):
    return {'pid':pid,'start_ticks':ticks,'boot_id':'test-boot'}


def scans(monkeypatch,*inventories,dead=()):
    calls=[]
    def inventory(**unused):
        values=inventories[min(len(calls),len(inventories)-1)]
        calls.append(values)
        return values
    monkeypatch.setattr(processes.psutil,'net_connections',inventory)
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:None if pid in dead else lifetime(pid))
    return calls


def owned_server(monkeypatch):
    (cfg.STATE/'service-pids.json').write_text(json.dumps({'supervisor':9}))
    server=SimpleNamespace(pid=10,role='server',birth=2,parent_pid=9,lifetime=lifetime(10),
                           alive=lambda:True,close=lambda:None,record=lambda:lifetime(10))
    supervisor=SimpleNamespace(pid=9,role='supervisor',birth=1,lifetime=lifetime(9),
                               alive=lambda:True,close=lambda:None)
    monkeypatch.setattr(processes,'inspect',lambda pid:server if pid==10 else supervisor)
    return server


def test_exact_duplicate_rows_require_a_live_lifetime_and_identical_socket(monkeypatch):
    scans(monkeypatch,[row(10),row(10),row(10,fd=9),row(11),row(11)],dead={11})
    assert [(item.pid,item.lifetime) for item in processes._listeners()]==[
        (10,lifetime(10)),(10,lifetime(10)),(11,None),(11,None)]


def test_dead_listener_requires_a_fresh_empty_inventory(monkeypatch):
    calls=scans(monkeypatch,[row(10)],[],dead={10})
    assert processes.listener_identity() is None and len(calls)==2


def test_unknown_can_become_owned_within_three_scans(monkeypatch):
    unknown=[row(None)]
    known=[row(10)]
    calls=scans(monkeypatch,unknown,known,known)
    owned_server(monkeypatch)
    identity=processes.listener_identity()
    assert identity['pid']==10 and identity['supervisor_pid']==9 and calls==[unknown,known,known]


@pytest.mark.parametrize('unreadable',[None,10])
def test_persistent_unknown_and_accessdenied_stay_occupied(monkeypatch,unreadable):
    calls=scans(monkeypatch,[row(unreadable)])
    if unreadable is not None:
        def inaccessible(pid):
            raise processes.psutil.AccessDenied(pid)
        monkeypatch.setattr(processes,'_linux_lifetime',inaccessible)
    assert len(processes._listeners())==1
    calls.clear()
    with pytest.raises(processes.ListenerUncertain,match='unverified'):
        processes.listener_identity()
    assert len(calls)==3


def test_same_pid_wildcard_is_foreign_even_with_a_loopback_listener(monkeypatch):
    calls=scans(monkeypatch,[row(10),row(10,fd=9,ip='0.0.0.0')])
    monkeypatch.setattr(processes,'inspect',lambda pid:pytest.fail('wildcard must refuse before ownership lookup'))
    with pytest.raises(processes.OwnershipError,match='non-loopback'):
        processes.listener_identity()
    assert len(calls)==1


def test_pid_reuse_during_verification_does_not_validate_the_old_socket(monkeypatch):
    calls=scans(monkeypatch,[row(10)])
    owned_server(monkeypatch)
    observed=iter([lifetime(10),lifetime(10,ticks=101)]*3)
    monkeypatch.setattr(processes,'_linux_lifetime',lambda pid:next(observed))
    with pytest.raises(processes.ListenerUncertain,match='lifetime changed'):
        processes.listener_identity()
    assert len(calls)==3
