"""Portable stdio adapter to the authenticated device-local MCP service."""
import asyncio, json, socket, time
import brain_settings as cfg
REQUIRED={'brain_context','brain_search','brain_read','brain_capture','brain_update_current','brain_health'}

def ensure_service(deadline=None):
    def listening():
        with socket.socket() as sock:
            sock.settimeout(1)
            return sock.connect_ex(('127.0.0.1', cfg.PORT)) == 0
    if listening(): return
    from brain_service import start
    start()
    until=deadline or time.monotonic()+50
    while time.monotonic()<until:
        if listening(): return
        time.sleep(.5)
    raise RuntimeError(f'Second Brain service did not start. Inspect {cfg.ROOT / "logs"}.')

async def wait_ready(transport,deadline):
    from fastmcp import Client
    while time.monotonic()<deadline:
        try:
            async with Client(transport,timeout=5) as client:
                if REQUIRED <= {tool.name for tool in await client.list_tools()}: return
        except Exception: pass
        await asyncio.sleep(.25)
    raise RuntimeError('Authenticated Second Brain tools are not ready; inspect local service health and retry.')

def main():
    from fastmcp import Client
    from fastmcp.server import create_proxy
    from fastmcp.client.transports import StreamableHttpTransport
    deadline=time.monotonic()+50
    ensure_service(deadline)
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer '+token})
    asyncio.run(wait_ready(transport,deadline))
    create_proxy(Client(transport),name='Second Brain').run(transport='stdio',show_banner=False)

if __name__=='__main__': main()
