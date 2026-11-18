"""End-to-end self-test of one device, through the same stdio adapter an assistant starts.

It lists the tools, reads health and context, saves one test milestone, reads it back and finds it by search.
The milestone has a fixed identity and text per device, so repeating the test never adds a second note.
No assistant program and no remote model is called; the search step uses the device's own local embedding
model, as every search does."""
import asyncio
from tempfile import TemporaryFile
import brain_settings as cfg

TITLE='Second Brain self-test'
STEPS=('tools','health','context','save','read','search')
OUTCOME=('Written by the self-test of this device to prove that saving, reading and searching work end to end. '
         'It is written once per device and may be kept or deleted.')


def milestone():
    return {'event_id':'second-brain-selftest-'+cfg.DEVICE_ID,'title':TITLE+' '+cfg.DEVICE_ID,'outcome':OUTCOME,
            'source_app':'second-brain-selftest','source_reference':'brain_cli.py selftest on device '+cfg.DEVICE_ID}


async def _steps(add, adapter_stderr):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from brain_client import REQUIRED
    # Exactly what an assistant's connection entry runs: the recorded Python and the adapter, nothing else.
    transport=StdioTransport(command=str(cfg.PYTHON),args=[str(cfg.ROOT/'app/brain_client.py')],
                             keep_alive=False,log_file=adapter_stderr)
    async with Client(transport,timeout=180,init_timeout=65) as client:
        async def step(name,work):
            try:add(*await work(),name=name);return True
            except Exception as error:
                add(False,type(error).__name__+': '+str(error),name=name);return False
        async def tools():
            missing=REQUIRED-{tool.name for tool in await client.list_tools()}
            return not missing,'all six tools offered' if not missing else 'missing: '+', '.join(sorted(missing))
        async def health():
            value=(await client.call_tool('brain_health',{})).data
            good=value.get('status')=='running' and value.get('version')==cfg.VERSION
            return good,f"status {value.get('status')}, version {value.get('version')}"
        async def context():
            value=(await client.call_tool('brain_context',{})).data
            return isinstance(value,dict) and bool(value),'context returned' if value else 'context was empty'
        saved={}
        async def save():
            value=(await client.call_tool('brain_capture',milestone())).data
            saved.update(value if isinstance(value,dict) else {})
            return saved.get('status') in ('saved','duplicate') and bool(saved.get('path')),f"{saved.get('status')}: {saved.get('path')}"
        async def read():
            value=(await client.call_tool('brain_read',{'path':saved['path']})).data
            # The note itself, at the path that was saved, with this device's identity, title and text.
            text=value.get('content') if isinstance(value,dict) else None
            wanted=milestone()
            good=(isinstance(text,str) and value.get('path')==saved['path']
                  and all(part in text for part in ('event_id: '+wanted['event_id'],'# '+wanted['title'],wanted['outcome'])))
            return good,'the saved milestone was read back' if good else 'the note at that path is not the saved milestone'
        async def search():
            value=(await client.call_tool('brain_search',{'query':milestone()['title'],'limit':15})).data
            found=any(hit.get('path')==saved['path'] for hit in value.get('results',[]))
            return found,'the saved milestone was found' if found else 'the saved milestone is not among the results'
        await step('tools',tools);await step('health',health);await step('context',context)
        if await step('save',save):
            await step('read',read);await step('search',search)
        else:
            add(False,'not run: nothing was saved',name='read');add(False,'not run: nothing was saved',name='search')


def run():
    rows=[]
    def add(ok,detail,*,name):rows.append({'status':'PASS' if ok else 'FAIL','check':'self-test '+name,'detail':str(detail)[:300]})
    with TemporaryFile(mode='w+',encoding='utf-8',errors='replace') as adapter_stderr:
        try:
            # Basic Memory's migration dependency can patch asyncio.run to reuse the ambient loop.
            with asyncio.Runner() as runner:
                runner.run(_steps(add,adapter_stderr))
        except Exception as error:
            # Classify known startup failures without copying arbitrary child stderr into the report.
            adapter_stderr.seek(0)
            lines=adapter_stderr.read(65536).splitlines()
            if any(line.startswith('brain_process.OwnershipError:') for line in lines):
                detail=' (adapter could not verify the local service; run brain_cli.py service inspect)'
            elif any(line.startswith('RuntimeError: Authenticated Second Brain tools are not ready;') for line in lines):
                detail=' (the local service did not expose the authenticated tools in time)'
            else:
                detail=''
            reason='not run: '+type(error).__name__+': '+str(error)+detail
            done={row['check'] for row in rows}
            for name in STEPS:
                if 'self-test '+name not in done:add(False,reason,name=name)
    failed=sum(row['status']=='FAIL' for row in rows)
    return {'status':'failed' if failed else 'passed','failed':failed,'checks':rows}
