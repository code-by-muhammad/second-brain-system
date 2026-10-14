"""Portable stdio adapter to the authenticated device-local MCP service."""
import json, socket, time
import brain_settings as cfg

def ensure_service():
    def listening():
        with socket.socket() as sock:
            sock.settimeout(1)
            return sock.connect_ex(('127.0.0.1', cfg.PORT)) == 0
    if listening(): return
    from brain_service import start
    start()
    until=time.monotonic()+60
    while time.monotonic()<until:
        if listening(): return
        time.sleep(.5)
    raise RuntimeError(f'Second Brain service did not start. Inspect {cfg.ROOT / "logs"}.')

def main():
    from fastmcp import Client
    from fastmcp.server import create_proxy
    from fastmcp.client.transports import StreamableHttpTransport
    ensure_service()
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer '+token})
    create_proxy(Client(transport),name='Second Brain').run(transport='stdio',show_banner=False)

if __name__=='__main__': main()
