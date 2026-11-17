"""End-to-end self-test of one device, through the same stdio adapter an assistant starts.

It lists the tools, reads health and context, saves one test milestone, reads it back and finds it by search.
The milestone has a fixed identity and text per device, so repeating the test never adds a second note."""
import asyncio, json
import brain_settings as cfg

TITLE='Second Brain self-test'
OUTCOME=('Written by the self-test of this device to prove that saving, reading and searching work end to end. '
         'It is written once per device and may be kept or deleted.')


def milestone():
    return {'event_id':'second-brain-selftest-'+cfg.DEVICE_ID,'title':TITLE+' '+cfg.DEVICE_ID,'outcome':OUTCOME,
            'source_app':'second-brain-selftest','source_reference':'brain_cli.py selftest on device '+cfg.DEVICE_ID}


async def _steps(add):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from brain_client import REQUIRED
    # Exactly what an assistant's connection entry runs: the recorded Python and the adapter, nothing else.
    transport=StdioTransport(command=str(cfg.PYTHON),args=[str(cfg.ROOT/'app/brain_client.py')],keep_alive=False)
    async with Client(transport,timeout=180) as client:
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
            return milestone()['title'] in json.dumps(value,ensure_ascii=False),'the saved milestone was read back'
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
    try:asyncio.run(_steps(add))
    except Exception as error:
        add(False,type(error).__name__+': '+str(error),name='connection')
    failed=sum(row['status']=='FAIL' for row in rows)
    return {'status':'failed' if failed else 'passed','failed':failed,'checks':rows}
