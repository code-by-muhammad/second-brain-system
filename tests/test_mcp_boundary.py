"""End-to-end MCP boundary in a disposable vault; no cloud model or private notes."""
import asyncio, hashlib, json, os, socket, subprocess, sys, time
import threading
from pathlib import Path
import pytest
import brain_settings as cfg

@pytest.mark.asyncio
async def test_authenticated_mcp_and_safe_writes(tmp_path):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    import httpx
    import httpx2
    # Synthetic transport acceptance; not production supervisor/Task acceptance.
    # Keep its actual SDK transport isolated from env/registry proxies anyway.
    def local_http_client(headers=None,timeout=None,**options):
        return httpx2.AsyncClient(headers=headers,timeout=timeout,trust_env=False,follow_redirects=False)
    root=tmp_path/'isolated-runtime'; state=root/'state'; vault=tmp_path/'isolated-vault'
    state.mkdir(parents=True); vault.mkdir()
    token='synthetic-test-only-authorization'
    with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    (root/'device.json').write_text(json.dumps({'vault':str(vault),'role':'maintainer','device_id':'synthetic-test','port':port}))
    (state/'access.json').write_text(json.dumps({'token_sha256':hashlib.sha256(token.encode()).hexdigest()}))
    (state/'config.json').write_text(json.dumps({'projects':{'brain':{'path':str(vault),'mode':'local'}},'default_project':'brain',
                                               'semantic_search_enabled':False,'logfire_enabled':False,'disable_permalinks':True,
                                               'ensure_frontmatter_on_sync':False,'format_on_save':False}))
    env=os.environ.copy(); env.update(BRAIN_SYSTEM_ROOT=str(root),BRAIN_VAULT=str(vault),BRAIN_STATE=str(state),BRAIN_PORT=str(port))
    app=Path(__file__).resolve().parents[1]/'app/brain_server.py'
    with (root/'server.log').open('wb') as log:
        command=[sys.executable,'-I','-B','-c','import runpy,sys;sys.path.insert(0,'+repr(str(app.parent))+');runpy.run_path('+repr(str(app))+',run_name="__main__")']
        stop=threading.Event();failure=[]
        class RequestedStop(Exception):pass
        def tick():
            if stop.is_set():raise RequestedStop()
        def serve():
            from brain_windows_process import run
            try:
                result=run(command,env=env,stdout=log,stderr=log,direct_output=True,timeout=180,on_tick=tick)
                failure.append(AssertionError('Synthetic MCP exited early: '+str(result.returncode)))
            except RequestedStop:pass
            except BaseException as error:failure.append(error)
        if os.name=='nt':
            worker=threading.Thread(target=serve,daemon=True);worker.start()
        else:proc=subprocess.Popen(command,env=env,stdout=log,stderr=log)
        try:
            until=time.monotonic()+60
            while True:
                if (not worker.is_alive() if os.name=='nt' else proc.poll() is not None):raise AssertionError('Synthetic MCP exited before readiness; inspect private log.')
                with socket.socket() as sock: ready=sock.connect_ex(('127.0.0.1',port))==0
                if ready: break
                if time.monotonic()>until: raise AssertionError('Isolated MCP startup timed out')
                await asyncio.sleep(.25)
            url=f'http://127.0.0.1:{port}/mcp'
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False) as h:
                rejected=await h.post(url,json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
                assert rejected.status_code in (401,403)
            async with Client(StreamableHttpTransport(url,headers={'Authorization':'Bearer '+token},httpx_client_factory=local_http_client),timeout=90) as c:
                assert {tool.name for tool in await c.list_tools()}=={'brain_context','brain_search','brain_read','brain_capture','brain_update_current','brain_health'}
                context=(await c.call_tool('brain_context',{})).data
                assert Path(context['vault'])==vault
                # R141: exercise the registered/authenticated health tool itself.
                health=(await c.call_tool('brain_health',{})).data
                assert health['status']=='running' and health['version']==cfg.VERSION
                for filename,key in [('maintenance-status.json','maintenance'),('index-bootstrap-status.json','initial_index_validation')]:
                    cache=state/filename
                    for raw in (b'{',b'[]'):
                        cache.write_bytes(raw)
                        health=(await c.call_tool('brain_health',{})).data
                        assert health['status']=='running' and health[key]['status']=='needs attention'
                        assert cache.read_bytes()==raw
                    cache.write_text(json.dumps({'status':'completed' if key=='maintenance' else 'verified'}),encoding='utf-8')
                    health=(await c.call_tool('brain_health',{})).data
                    assert health[key]['status']==('completed' if key=='maintenance' else 'verified')
                args={'event_id':'isolated-mcp-milestone','title':'Synthetic test','outcome':'Synthetic marker QUARTZ-57321.',
                      'source_app':'CI','source_reference':'synthetic disposable fixture'}
                first=(await c.call_tool('brain_capture',args)).data
                assert (await c.call_tool('brain_capture',args)).data['status']=='duplicate'
                assert 'QUARTZ-57321' in (await c.call_tool('brain_read',{'path':first['path']})).data['content']
                linked=(await c.call_tool('brain_read',{'path':'[['+first['path'][:-3]+'|Saved evidence]]'})).data
                assert linked['path']==first['path'] and linked['sha256']==first['sha256']
                search=(await c.call_tool('brain_search',{'query':'ye synthetic marker kahan hai',
                              'english_query':'QUARTZ-57321','limit':4})).data
                assert first['path'] in {row['path'] for row in search['results']}
                assert first['path'] in {row['path'] for row in search['fresh_file_matches']}
                assert len({row['path'] for row in search['results']})==len(search['results'])
                for path in ('../outside.md','.obsidian/secret.md','C:/Windows/private.md'):
                    assert (await c.call_tool('brain_read',{'path':path},raise_on_error=False)).is_error
                update={'path':'02-Areas/AI-Memory/Synthetic.md','title':'Synthetic derived page','content':'Supported synthetic marker.', 'source_paths':[first['path']]}
                receipt=(await c.call_tool('brain_update_current',update)).data
                assert (await c.call_tool('brain_update_current',update,raise_on_error=False)).is_error
                update.update(expected_sha256=receipt['sha256'],content='Updated supported synthetic marker.')
                assert (await c.call_tool('brain_update_current',update)).data['status']=='saved'
                assert (state/'write-backups'/receipt['sha256']).exists()
                bad={**update,'path':'01-Projects/Original.md','expected_sha256':None}
                assert (await c.call_tool('brain_update_current',bad,raise_on_error=False)).is_error
                compiler_owned={**update,'path':'02-Areas/AI-Memory/Wiki/Control.md','expected_sha256':None}
                assert (await c.call_tool('brain_update_current',compiler_owned,raise_on_error=False)).is_error
                assert not (vault/'02-Areas/AI-Memory/Wiki/Control.md').exists()
        finally:
            primary=sys.exc_info()[1]
            try:
                if os.name=='nt':
                    stop.set();worker.join(200)
                    if worker.is_alive():raise AssertionError('Owned synthetic Job cleanup remains pending; do not clear its state.')
                    if failure:raise failure[0]
                else:proc.terminate();proc.wait(timeout=20)
            except BaseException as error:
                if primary is None:raise
                primary.add_note('Synthetic server cleanup also failed: '+type(error).__name__)
