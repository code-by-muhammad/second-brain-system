"""Supported local runtime composition and credential-routing regressions."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types
import pytest
import brain_settings as cfg
import brain_client as client


@pytest.mark.parametrize('change',[{'semantic_embedding_provider':'litellm'}, {'semantic_embedding_provider':'openai'},
                                   {'reranker_enabled':True}, {'reranker_enabled':None}])
def test_server_refuses_unsupported_provider_or_reranker_before_upstream_imports(brain,monkeypatch,change):
    value={'semantic_embedding_provider':'fastembed','reranker_enabled':False,**change}
    (cfg.STATE/'config.json').write_text(json.dumps(value))
    module=types.ModuleType('synthetic_server_start');module.__file__=str(Path(__file__).resolve().parents[1]/'app/brain_server.py')
    original=__import__;upstream=[]
    def guarded(name,*args,**kwargs):
        if name.startswith('basic_memory'):
            upstream.append(name);pytest.fail('Unsupported configuration reached Basic Memory imports')
        return original(name,*args,**kwargs)
    monkeypatch.setattr('builtins.__import__',guarded)
    with pytest.raises(ValueError,match='Only local FastEmbed'):
        exec(compile(Path(module.__file__).read_bytes(),module.__file__,'exec'),module.__dict__)
    assert not upstream


def test_locked_environment_excludes_litellm_package_and_proxy_entrypoints():
    assert importlib.util.find_spec('litellm') is None
    assert 'litellm' not in sys.modules
    assert not any(Path(sys.executable).with_name(name).exists() for name in ('litellm.exe','litellm-proxy.exe','litellm','litellm-proxy'))


def test_startup_refuses_a_stale_environment_containing_the_excluded_sdk(brain,monkeypatch):
    (cfg.STATE/'config.json').write_text(json.dumps({'semantic_embedding_provider':'fastembed','reranker_enabled':False}))
    actual=importlib.util.find_spec
    monkeypatch.setattr(importlib.util,'find_spec',lambda name:object() if name=='litellm' else actual(name))
    with pytest.raises(ValueError,match='excludes LiteLLM'):cfg.require_local_search_runtime()


def test_both_product_transport_constructors_have_no_oauth_auth(brain,monkeypatch):
    from types import SimpleNamespace
    import fastmcp
    import fastmcp.server
    import brain_cli
    seen=[]
    class FakeClient:
        def __init__(self,transport,**options):seen.append(transport);assert transport.auth is None
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def call_tool(self,*args):return SimpleNamespace(data={'synthetic':True})
    monkeypatch.setattr(client.processes,'validate_environment',lambda:None)
    monkeypatch.setattr(client.processes,'listener_identity',lambda:{'pid':1})
    (cfg.ROOT/'state/client-access.json').write_text(json.dumps({'token':'synthetic-local-token'}))
    monkeypatch.setattr(fastmcp,'Client',FakeClient)
    assert asyncio.run(brain_cli.mcp_call('brain_health',{}))=={'synthetic':True}
    monkeypatch.setattr(client,'ensure_service',lambda *args,**options:None)
    async def ready(transport,deadline):assert transport.auth is None
    monkeypatch.setattr(client,'wait_ready',ready)
    monkeypatch.setattr(fastmcp.server,'create_proxy',lambda *args,**options:SimpleNamespace(run=lambda **kw:None))
    client.main()
    assert len(seen)==2


def test_oauth_auth_object_is_rejected_before_http_client_creation(brain):
    with pytest.raises(client.processes.OwnershipError,match='OAuth transports are unsupported'):
        client.owned_http_client(auth=object())


@pytest.mark.asyncio
async def test_401_with_advertised_oauth_does_not_follow_metadata_or_send_credentials_elsewhere(brain,monkeypatch):
    import httpx2
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from mcp.shared.exceptions import MCPError
    import socket
    seen=[];attempted=[];dns_attempts=[];actual_send=httpx2.AsyncClient.send
    async def send(self,request,*args,**kwargs):
        attempted.append((request.url.host,request.url.port,request.url.path))
        if attempted[-1]!=('127.0.0.1',cfg.PORT,'/mcp'):pytest.fail('OAuth metadata or credentials attempted a different destination')
        return await actual_send(self,request,*args,**kwargs)
    monkeypatch.setattr(httpx2.AsyncClient,'send',send)
    def network(*args,**kwargs):
        dns_attempts.append(args)
        pytest.fail('Mocked OAuth-negative probe must not perform DNS or real network traffic')
    monkeypatch.setattr(socket,'getaddrinfo',network)
    monkeypatch.setattr(client.processes,'validate_environment',lambda:None)
    monkeypatch.setattr(client.processes,'listener_identity',lambda:{'pid':1})
    async def respond(request):
        seen.append((request.url.host,request.url.port,request.url.path))
        return httpx2.Response(401,headers={'WWW-Authenticate':'Bearer resource_metadata="https://untrusted.invalid/oauth-protected-resource"'},request=request)
    def factory(headers=None,timeout=None,auth=None,**options):
        result=client.owned_http_client(headers,timeout,auth,**options)
        result._transport=httpx2.MockTransport(respond)
        return result
    transport=StreamableHttpTransport(f'http://127.0.0.1:{cfg.PORT}/mcp',headers={'Authorization':'Bearer synthetic-only'},httpx_client_factory=factory)
    assert transport.auth is None
    with pytest.raises(MCPError):
        async with Client(transport,timeout=2) as connection:await connection.list_tools()
    assert not dns_attempts
    assert attempted and seen and all(item==('127.0.0.1',cfg.PORT,'/mcp') for item in attempted+seen)


def test_owned_http_factory_disables_redirects_and_ambient_proxies():
    transport=client.owned_http_client()
    try:assert transport.follow_redirects is False and transport._trust_env is False
    finally:asyncio.run(transport.aclose())


@pytest.mark.skipif(os.name!='nt',reason='Native Windows Job qualification; POSIX release remains v0.1.7')
@pytest.mark.parametrize('seconds',[0,1,20])
def test_two_process_writer_lock_contention_and_release(brain,tmp_path,seconds):
    from filelock import FileLock
    from brain_windows_process import run
    script=tmp_path/'synthetic_lock.py';ready=tmp_path/'ready'
    script.write_text('''import json,sys
from pathlib import Path
from filelock import FileLock,Timeout
Path(sys.argv[2]).write_text('ready')
try:
 with FileLock(sys.argv[1],timeout=int(sys.argv[3])):print(json.dumps({'status':'acquired'}))
except Timeout:print(json.dumps({'status':'timeout'}))
''',encoding='utf-8')
    lock=FileLock(str(cfg.STATE/'writer.lock'),timeout=0);lock.acquire();released=False
    def tick():
        nonlocal released
        if seconds==20 and ready.exists() and not released:lock.release();released=True
    try:
        result=run([sys.executable,'-I','-B',str(script),str(cfg.STATE/'writer.lock'),str(ready),str(seconds)],
                   capture_output=True,text=True,encoding='utf-8',timeout=25,on_tick=tick,env=cfg.environment())
    finally:lock.release()
    assert result.returncode==0 and json.loads(result.stdout)['status']==('acquired' if seconds==20 else 'timeout')
    assert seconds!=20 or released
