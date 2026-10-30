import time
from types import SimpleNamespace
import pytest
import fastmcp
import brain_client as client

@pytest.mark.asyncio
async def test_proxy_waits_until_all_tools_are_exposed(monkeypatch):
    monkeypatch.setattr(client.processes,'listener_identity',lambda:{'synthetic':True})
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
    await client.wait_ready(None,time.monotonic()+3)
    assert calls==2

@pytest.mark.asyncio
async def test_proxy_fails_instead_of_exposing_empty_tool_list():
    with pytest.raises(RuntimeError,match='tools are not ready'): await client.wait_ready(None,time.monotonic()-1)
