import asyncio
import time
from types import SimpleNamespace
import pytest
import fastmcp
import brain_client as client

@pytest.mark.asyncio
async def test_proxy_waits_until_all_tools_are_exposed(monkeypatch):
    seen=[]
    def lookup(*,deadline):
        seen.append(deadline)
        return {'synthetic':True}
    monkeypatch.setattr(client.processes,'listener_identity',lookup)
    calls=0
    class Probe:
        def __init__(self,*a,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def list_tools(self):
            nonlocal calls
            calls+=1
            return [] if calls==1 else [SimpleNamespace(name=n) for n in client.REQUIRED]
    monkeypatch.setattr(fastmcp,'Client',Probe)
    deadline=time.monotonic()+3
    await client.wait_ready(None,deadline)
    assert calls==2 and seen==[deadline,deadline]

@pytest.mark.asyncio
async def test_proxy_fails_instead_of_exposing_empty_tool_list():
    with pytest.raises(RuntimeError,match='tools are not ready'): await client.wait_ready(None,time.monotonic()-1)


@pytest.mark.asyncio
async def test_proxy_cancels_a_stalled_handshake_when_startup_deadline_expires(monkeypatch):
    attempts=[]
    cancelled=[]
    budget=.2

    class StalledHandshake:
        def __init__(self,transport,*,timeout,init_timeout):
            attempts.append((timeout,init_timeout))

        async def __aenter__(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        async def __aexit__(self,*args):
            pass

    monkeypatch.setattr(client.processes,'listener_identity',lambda **kwargs:{'synthetic':True})
    monkeypatch.setattr(fastmcp,'Client',StalledHandshake)
    with pytest.raises(RuntimeError,match='Authenticated Second Brain tools are not ready'):
        await asyncio.wait_for(client.wait_ready(None,time.monotonic()+budget),timeout=2)
    assert len(attempts)==1 and cancelled==[True]
    timeout,init_timeout=attempts[0]
    assert 0<timeout==init_timeout<=budget


def test_cold_start_passes_the_same_deadline_to_dispatch_and_ownership_checks(monkeypatch):
    import brain_service
    seen=[]
    owners=iter((None,{'synthetic':True}))
    def lookup(*,deadline):
        seen.append(('listener',deadline))
        return next(owners)
    monkeypatch.setattr(client.processes,'validate_environment',lambda:None)
    monkeypatch.setattr(client.processes,'listener_identity',lookup)
    monkeypatch.setattr(brain_service,'start',lambda *,deadline:seen.append(('start',deadline)))
    deadline=time.monotonic()+3
    client.ensure_service(deadline)
    assert seen==[('listener',deadline),('start',deadline),('listener',deadline)]
