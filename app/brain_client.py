"""Portable stdio adapter to the authenticated device-local MCP service."""
import asyncio, json, socket, time
import brain_settings as cfg
import brain_process as processes
REQUIRED={'brain_context','brain_search','brain_read','brain_capture','brain_update_current','brain_health'}


async def verify_request(request):
    url=request.url
    if url.scheme!='http' or url.host!='127.0.0.1' or url.port!=cfg.PORT or url.path.rstrip('/')!='/mcp':
        raise processes.OwnershipError('Refusing an unexpected MCP destination before sending credentials.')
    processes.validate_environment()
    if processes.listener_identity() is None:raise processes.OwnershipError('Owned listener disappeared; no credentials sent on reconnect.')


def owned_http_client(headers=None,timeout=None,auth=None,**options):
    if auth is not None:
        raise processes.OwnershipError('OAuth transports are unsupported; only the owned local static-token service is allowed.')
    import httpx2
    return httpx2.AsyncClient(headers=headers,auth=auth,timeout=timeout or httpx2.Timeout(30,read=300),
                             follow_redirects=False,trust_env=False,event_hooks={'request':[verify_request]})

def ensure_service(deadline=None):
    processes.validate_environment()
    if processes.listener_identity() is not None: return
    from brain_service import start
    start(deadline=deadline)
    if processes.listener_identity() is None:raise processes.OwnershipError('Service ownership changed after startup.')

async def wait_ready(transport,deadline):
    from fastmcp import Client
    while time.monotonic()<deadline:
        if processes.listener_identity() is None:raise processes.OwnershipError('Service listener disappeared before authentication.')
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
    if processes.listener_identity() is None:raise processes.OwnershipError('Service listener disappeared before credential access.')
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer '+token},httpx_client_factory=owned_http_client)
    asyncio.run(wait_ready(transport,deadline))
    if processes.listener_identity() is None:raise processes.OwnershipError('Service ownership changed during readiness.')
    create_proxy(Client(transport),name='Second Brain').run(transport='stdio',show_banner=False)

if __name__=='__main__': main()
