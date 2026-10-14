"""Common commands on Windows, macOS and Linux."""
import argparse, asyncio, json, os
import brain_settings as cfg

async def mcp_call(name,args):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer '+token})
    async with Client(transport,timeout=120) as client: return (await client.call_tool(name,args)).data

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['health','search','read','wiki','service'])
    parser.add_argument('action',nargs='?',default='status'); parser.add_argument('--limit',type=int,default=4)
    args=parser.parse_args()
    if args.command=='health': result=asyncio.run(mcp_call('brain_health',{}))
    elif args.command=='search': result=asyncio.run(mcp_call('brain_search',{'query':args.action,'limit':args.limit}))
    elif args.command=='read': result=asyncio.run(mcp_call('brain_read',{'path':args.action}))
    elif args.command=='wiki':
        import brain_wiki as wiki
        if args.action=='status': result=wiki.status()
        elif args.action=='run': result=wiki.run(args.limit)
        elif args.action=='prepare': result=wiki.prepare(args.limit)
        elif args.action=='lint': result=wiki.lint()
        elif args.action=='retry':
            cfg.require_maintainer()
            with wiki.db() as conn: conn.execute('UPDATE sources SET error=NULL,attempts=0')
            result={'status':'retry queue reset'}
        else: parser.error('wiki action: status, run, prepare, lint, retry')
    else:
        import brain_service as service
        if args.action=='install': result=service.install()
        elif args.action in ('start','stop'): getattr(service,args.action)(); result={'status':args.action}
        else: result={'definition':service.definition()}
    print(json.dumps(result,indent=2,ensure_ascii=False,default=str))

if __name__=='__main__': main()
