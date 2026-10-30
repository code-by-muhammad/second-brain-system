"""Common commands on Windows, macOS and Linux."""
import argparse, asyncio, json, os, sys
import brain_settings as cfg

async def mcp_call(name,args):
    import brain_process as processes
    processes.validate_environment()
    if processes.listener_identity() is None:raise processes.OwnershipError('No owned local service is available; no credentials sent.')
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from brain_client import owned_http_client
    token=json.loads((cfg.ROOT/'state/client-access.json').read_text())['token']
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer '+token},httpx_client_factory=owned_http_client)
    async with Client(transport,timeout=120) as client: return (await client.call_tool(name,args)).data

def main():
    # JSON is a UTF-8 wire response even when a Windows pipe defaults to ANSI.
    # Embedded callers may replace stdout with a text-only stream such as StringIO.
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['health','search','read','wiki','service','index'])
    parser.add_argument('action',nargs='?',default='status'); parser.add_argument('--limit',type=int,default=4)
    parser.add_argument('--apply',action='store_true',help='Apply an explicitly reviewed legacy migration.')
    parser.add_argument('--requeue-unverified',action='store_true',help='Keep unverified legacy parts pending instead of claiming completion.')
    parser.add_argument('--full',action='store_true',help='With index retry, force source scan and re-embedding (costs more local CPU).')
    parser.add_argument('--overall-seconds',type=int,default=28800)
    parser.add_argument('--stall-seconds',type=int,default=1800)
    parser.add_argument('--hold-id')
    parser.add_argument('--snapshot')
    parser.add_argument('--since',type=int)
    parser.add_argument('--allow-legacy',action='store_true')
    parser.add_argument('--expected-version')
    parser.add_argument('--wait-seconds',type=int,default=30,help='Service stop admission/exit wait budget, 1–300 seconds; busy writers are preserved.')
    args=parser.parse_args()
    if args.command=='health': result=asyncio.run(mcp_call('brain_health',{}))
    elif args.command=='search': result=asyncio.run(mcp_call('brain_search',{'query':args.action,'limit':args.limit}))
    elif args.command=='read': result=asyncio.run(mcp_call('brain_read',{'path':args.action}))
    elif args.command=='index':
        if args.action=='status':
            from brain_index import status
            result=status()
        elif args.action=='retry':
            from brain_index_bootstrap import main as retry
            result=retry(overall_seconds=args.overall_seconds,stall_seconds=args.stall_seconds,full=args.full,retry=True)
        else: parser.error('index action: status, retry')
    elif args.command=='wiki':
        import brain_wiki as wiki
        if args.action=='status': result=wiki.status()
        elif args.action=='run': result=wiki.run(args.limit)
        elif args.action=='prepare': result=wiki.prepare(args.limit)
        elif args.action=='lint': result=wiki.lint()
        elif args.action=='migrate':
            result=wiki.migrate_legacy_receipts(requeue_unverified=args.requeue_unverified,dry_run=not args.apply)
        elif args.action=='relink': result=wiki.rebuild_source_links()
        elif args.action=='retry':
            result=wiki.reset_retries()
        else: parser.error('wiki action: status, run, prepare, lint, retry, migrate, relink')
    else:
        import brain_service as service
        if args.action=='install': result=service.install()
        elif args.action=='start': result=service.start(explicit=True,hold_id=args.hold_id,since=args.since,allow_legacy=args.allow_legacy)
        elif args.action=='stop': result=service.stop(hold_id=args.hold_id,wait_seconds=args.wait_seconds)
        elif args.action=='hold': result=service.begin_transaction(args.hold_id,args.snapshot)
        elif args.action=='finish': result=service.finish_transaction(args.hold_id,args.expected_version,args.allow_legacy)
        elif args.action=='cancel': result=service.cancel_transaction(args.hold_id)
        elif args.action=='recover': result=service.recovery_status()
        elif args.action=='inspect': result=service.inspect()
        elif args.action=='definition': result={'definition':service.definition()}
        else: result=service.recovery_status()
    print(json.dumps(result,indent=2,ensure_ascii=False,default=str))

if __name__=='__main__': main()
