"""Portable stdio adapter to the authenticated device-local MCP service."""
import asyncio, json, time
from functools import partial
import brain_settings as cfg
import brain_process as processes
REQUIRED={'brain_context','brain_search','brain_read','brain_capture','brain_update_current','brain_health'}


async def verify_request(request, *, deadline=None):
    url=request.url
    if url.scheme!='http' or url.host!='127.0.0.1' or url.port!=cfg.PORT or url.path.rstrip('/')!='/mcp':
        raise processes.OwnershipError('Refusing an unexpected MCP destination before sending credentials.')
    processes.validate_environment()
    if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Second Brain startup exceeded its deadline.')
    if processes.listener_identity(deadline=deadline) is None:raise processes.OwnershipError('Owned listener disappeared; no credentials sent on reconnect.')
    if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Second Brain startup exceeded its deadline.')


def owned_http_client(headers=None,timeout=None,auth=None,*,deadline=None,**options):
    if auth is not None:
        raise processes.OwnershipError('OAuth transports are unsupported; only the owned local static-token service is allowed.')
    import httpx2
    return httpx2.AsyncClient(headers=headers,auth=auth,timeout=timeout or httpx2.Timeout(30,read=300),
                             follow_redirects=False,trust_env=False,event_hooks={'request':[partial(verify_request,deadline=deadline)]})

def ensure_service(deadline=None):
    if deadline is None:
        deadline = time.monotonic() + 50
    processes.validate_environment()
    if processes.listener_identity(deadline=deadline) is not None: return
    from brain_service import start
    start(deadline=deadline)
    if processes.listener_identity(deadline=deadline) is None:raise processes.OwnershipError('Service ownership changed after startup.')

async def wait_ready(transport,deadline):
    from fastmcp import Client
    while time.monotonic()<deadline:
        if processes.listener_identity(deadline=deadline) is None:raise processes.OwnershipError('Service listener disappeared before authentication.')
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        try:
            attempt = min(5, remaining)
            async with asyncio.timeout(attempt):
                async with Client(transport,timeout=attempt,init_timeout=attempt) as client:
                    if REQUIRED <= {tool.name for tool in await client.list_tools()}: return
        except Exception: pass
        await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
    raise RuntimeError('Authenticated Second Brain tools are not ready; inspect local service health and retry.')

def main():
    deadline=time.monotonic()+50
    from fastmcp import Client
    from fastmcp.server import create_proxy
    from fastmcp.client.transports import StreamableHttpTransport
    ensure_service(deadline)
    if time.monotonic() >= deadline:raise RuntimeError('Second Brain startup exceeded its deadline.')
    if processes.listener_identity(deadline=deadline) is None:raise processes.OwnershipError('Service listener disappeared before credential access.')
    if time.monotonic() >= deadline:raise RuntimeError('Second Brain startup exceeded its deadline.')
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    url=f'http://127.0.0.1:{cfg.PORT}/mcp'
    headers={'Authorization':'Bearer '+token}
    ready=StreamableHttpTransport(url,headers=headers,httpx_client_factory=partial(owned_http_client,deadline=deadline))
    asyncio.run(wait_ready(ready,deadline))
    if processes.listener_identity(deadline=deadline) is None:raise processes.OwnershipError('Service ownership changed during readiness.')
    if time.monotonic() >= deadline:raise RuntimeError('Second Brain startup exceeded its deadline.')
    transport=StreamableHttpTransport(url,headers=headers,httpx_client_factory=owned_http_client)
    # warning hides FastMCP's "Starting MCP server ... stdio" line. Errors still print.
    create_proxy(Client(transport),name='Second Brain').run(transport='stdio',show_banner=False,log_level='warning')

if __name__=='__main__': main()
