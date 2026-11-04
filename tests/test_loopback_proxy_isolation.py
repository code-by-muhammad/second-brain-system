"""A working loopback proxy trap, never an external network request."""
import asyncio
import pytest
from brain_client import owned_http_client

@pytest.mark.asyncio
async def test_owned_client_ignores_ambient_and_registry_proxy_routing(monkeypatch):
    import httpx
    import httpx._utils
    import httpx2._utils
    import brain_client
    proxy_hits=[]
    async def respond(reader,writer,body,hits=None):
        await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
        if hits is not None:hits.append(True)
        writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 5\r\n\r\n'+body)
        await writer.drain();writer.close();await writer.wait_closed()
    owned=await asyncio.start_server(lambda r,w:respond(r,w,b'OWNED'),'127.0.0.1',0)
    proxy=await asyncio.start_server(lambda r,w:respond(r,w,b'PROXY',proxy_hits),'127.0.0.1',0)
    async with owned,proxy:
        port=owned.sockets[0].getsockname()[1]
        url='http://127.0.0.1:'+str(port)+'/mcp'
        # Only proxy routing is under test; existing ownership tests qualify
        # listener identity. Never label this raw synthetic HTTP fixture owned.
        monkeypatch.setattr(brain_client.cfg,'PORT',port)
        monkeypatch.setattr(brain_client.processes,'validate_environment',lambda:None)
        monkeypatch.setattr(brain_client.processes,'listener_identity',lambda:object())
        trap='http://127.0.0.1:'+str(proxy.sockets[0].getsockname()[1])
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'):monkeypatch.setenv(name,trap)
        monkeypatch.setenv('NO_PROXY','')
        monkeypatch.setattr(httpx._utils,'getproxies',lambda:{'http':trap,'https':trap})
        monkeypatch.setattr(httpx2._utils,'getproxies',lambda:{'http':trap,'https':trap})
        async with owned_http_client(timeout=2) as client:
            assert (await client.get(url)).text=='OWNED'
        assert proxy_hits==[]
        async with httpx.AsyncClient(timeout=2,trust_env=True) as control:
            assert (await control.get(url)).text=='PROXY'
        assert proxy_hits==[True]
