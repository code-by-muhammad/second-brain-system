"""Final independent-review regressions; no external sockets or owner state."""
import asyncio,json,os,socket,stat
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
import brain_markdown as md
import brain_store as store
import brain_wiki as wiki
import brain_git as git
from test_health_cache_boundary import health_function
from test_initial_install_resume import initial
from test_sync_fsmonitor_policy import isolated_git,_init,_raw

def test_composition_budget_refuses_before_delegating_every_node(monkeypatch):
    monkeypatch.setattr(md,'_YAML_NODE_LIMIT',10);calls=[]
    original=yaml.composer.Composer.compose_node
    def compose(self,*args):calls.append(1);return original(self,*args)
    monkeypatch.setattr(yaml.composer.Composer,'compose_node',compose)
    text='---\nitems: ['+','.join(['x']*1000)+']\n---\nBody'
    with pytest.raises(ValueError):md.frontmatter(text,strict=True)
    assert len(calls)==10
    calls.clear();assert md.frontmatter(text)=={} and len(calls)==10

def test_composition_exact_node_and_depth_boundaries(monkeypatch):
    monkeypatch.setattr(md,'_YAML_NODE_LIMIT',5)
    assert md.frontmatter('---\na: [x,y]\n---',strict=True)=={'a':['x','y']}
    monkeypatch.setattr(md,'_YAML_NODE_LIMIT',4)
    with pytest.raises(ValueError):md.frontmatter('---\na: [x,y]\n---',strict=True)
    monkeypatch.setattr(md,'_YAML_NODE_LIMIT',100_000)
    assert md.frontmatter('---\na: '+'['*127+'x'+']'*127+'\n---',strict=True)
    with pytest.raises(ValueError):md.frontmatter('---\na: '+'['*128+'x'+']'*128+'\n---',strict=True)

@pytest.mark.parametrize('header',[
    'a: &A {x: 1, date: 2026-11-02}\nb: {<<: *A, y: 2}',
    'a: &A {x: 1}\nb: &B {x: 2}\nc: {<<: [*A,*B], z: true}',
    'binary: !!binary YWJj\nset: !!set {a: null, b: null}\nvalue: !!pairs [a: 1, b: 2]',
])
def test_composition_controls_retain_upstream_metadata_semantics(header):
    assert md.frontmatter('---\n'+header+'\n---',strict=True)==yaml.safe_load(header)

def test_nonregular_note_refused_before_open(brain,monkeypatch):
    path=brain[0]/'Pipe.md';path.write_bytes(b'original');original=Path.stat
    def info(self,*args,**kwargs):
        return SimpleNamespace(st_mode=stat.S_IFIFO,st_size=0) if self==path else original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',info)
    monkeypatch.setattr(Path,'open',lambda *a,**k:pytest.fail('Non-regular source must not be opened'))
    with pytest.raises(ValueError,match='Non-regular'):store._note_bytes(path)

@pytest.mark.skipif(os.name=='nt',reason='Actual POSIX FIFO control; Windows negative seam is separate.')
def test_actual_fifo_note_is_never_opened(brain):
    path=brain[0]/'Pipe.md';os.mkfifo(path)
    # O_NONBLOCK/fstat in the actual read helper additionally closes the FIFO swap race.
    with pytest.raises(ValueError,match='Non-regular'):store.read('Pipe.md')
    result=store.fresh_search('synthetic')
    assert result.scope['status']=='partial' and result.scope['skipped']==1

def test_health_never_inventories_or_hydrates_wiki(brain,monkeypatch):
    state=brain[1];(state/'wiki-jobs.db').write_bytes(b'Synthetic sentinel, not a SQLite database')
    source=brain[0]/'Original.md';source.write_bytes(b'original')
    before={p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}
    def forbidden(*a,**k):pytest.fail('Health must use cached metadata, never scan or hydrate wiki')
    monkeypatch.setattr(wiki,'status',forbidden);monkeypatch.setattr(wiki,'inventory',forbidden);monkeypatch.setattr(wiki,'db',forbidden)
    health=health_function()()
    assert health['wiki']['worker']['status']=='idle' and health['wiki']['coverage'].startswith('not checked')
    assert before=={p.name:p.read_bytes() for p in state.iterdir() if p.is_file()} and source.read_bytes()==b'original'

def test_oversized_health_cache_refuses_before_json_parser(tmp_path,monkeypatch):
    path=tmp_path/'cache.json';path.write_bytes(b'x'*101)
    monkeypatch.setattr(md.json,'loads',lambda *a,**k:pytest.fail('Oversized cache must not reach decoder'))
    with pytest.raises(ValueError,match='byte limit'):md.bounded_json_file(path,100)

@pytest.mark.skipif(os.name!='nt',reason='Qualified Windows case-alias privacy guard.')
@pytest.mark.parametrize('prefix',[False,True])
def test_case_alias_exclusion_blocks_before_model_without_rewriting_policy(brain,monkeypatch,prefix):
    vault,state=brain;source=vault/'01-Projects/Private/Evidence.md';source.parent.mkdir(parents=True);source.write_text('Historical synthetic evidence with enough content.')
    target=vault/wiki.SOURCE_POLICY;target.parent.mkdir(parents=True,exist_ok=True)
    rules={'version':1,'exclude_paths':[] if prefix else ['01-Projects/private/evidence.md'],
           'exclude_prefixes':['01-Projects/private/'] if prefix else []}
    payload=('```json\n'+json.dumps(rules)+'\n```\n').encode();target.write_bytes(payload)
    monkeypatch.setattr(wiki,'_run_model',lambda *a,**k:pytest.fail('No model for ambiguous exclusion'))
    outcome=wiki.run();status=wiki.status()
    assert outcome['status']=='attention' and 'differs only by case' in outcome['reason']
    assert status['status']=='attention' and 'differs only by case' in status['reason']
    assert target.read_bytes()==payload and source.read_text()=='Historical synthetic evidence with enough content.'
    assert not list((state/'wiki/batches').glob('*/input.json'))

@pytest.mark.asyncio
@pytest.mark.skipif(os.name!='nt',reason='Windows IOCP numeric-address route.')
async def test_actual_async_numeric_connect_is_refused_before_iocp(monkeypatch):
    from asyncio.windows_events import IocpProactor
    loop=asyncio.get_running_loop()
    if not isinstance(getattr(loop,'_proactor',None),IocpProactor):pytest.skip('IOCP policy not active; plain socket path covered separately')
    monkeypatch.setattr(IocpProactor,'_register_with_iocp',lambda *a,**k:pytest.fail('No native connection may be registered'))
    guarded=IocpProactor.connect;seen=[]
    def observed(self,connection,address):seen.append(address);return guarded(self,connection,address)
    monkeypatch.setattr(IocpProactor,'connect',observed)
    with pytest.raises(AssertionError,match='non-loopback'):
        await asyncio.wait_for(asyncio.open_connection('192.0.2.1',443),1)
    assert seen and seen[0][0]=='192.0.2.1'

@pytest.mark.asyncio
@pytest.mark.skipif(os.name!='nt',reason='Windows IOCP datagram event-loop route.')
async def test_actual_async_numeric_udp_is_refused_before_iocp(monkeypatch):
    from asyncio.windows_events import IocpProactor
    loop=asyncio.get_running_loop()
    if not isinstance(getattr(loop,'_proactor',None),IocpProactor):pytest.skip('IOCP policy not active')
    monkeypatch.setattr(IocpProactor,'_register_with_iocp',lambda *a,**k:pytest.fail('No native datagram may be registered'))
    guarded=IocpProactor.sendto;seen=[]
    def observed(self,connection,buffer,flags=0,addr=None):seen.append(addr);return guarded(self,connection,buffer,flags,addr)
    monkeypatch.setattr(IocpProactor,'sendto',observed)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as connection:
        connection.setblocking(False);connection.bind(('127.0.0.1',0))
        with pytest.raises(AssertionError,match='non-loopback'):
            await loop.sock_sendto(connection,b'synthetic',('192.0.2.1',9))
    assert seen==[('192.0.2.1',9)]

@pytest.mark.parametrize('kind',['directory','oversize','linked'])
def test_worker_cache_refuses_unsafe_file_without_opening(brain,monkeypatch,kind):
    path=brain[1]/'wiki-worker-status.json'
    if kind=='directory':path.mkdir()
    else:path.write_bytes(b'x'*65537 if kind=='oversize' else b'{"status":"idle"}')
    original=Path.open;linked=Path.is_symlink;native_open=os.open
    def no_open(self,*args,**kwargs):
        if self==path:pytest.fail('Unsafe worker cache must not be opened')
        return original(self,*args,**kwargs)
    if kind=='linked':monkeypatch.setattr(Path,'is_symlink',lambda self:True if self==path else linked(self))
    monkeypatch.setattr(Path,'open',no_open)
    if os.name!='nt':
        def no_native_open(file,*args,**kwargs):
            if Path(file)==path:pytest.fail('Unsafe worker cache must not be opened')
            return native_open(file,*args,**kwargs)
        monkeypatch.setattr(os,'open',no_native_open)
    value=wiki._read_worker_state()
    assert value['status']=='attention' and value['attention_required'] is True

def test_udp_routes_refuse_before_touching_a_native_socket():
    with pytest.raises(AssertionError,match='non-loopback'):
        socket.socket.sendto(object(),b'synthetic',('192.0.2.1',9))
    if os.name=='nt':
        from asyncio.windows_events import IocpProactor
        with pytest.raises(AssertionError,match='non-loopback'):
            IocpProactor.sendto(object(),object(),b'synthetic',addr=('192.0.2.1',9))

def test_nonwindows_abandon_is_refused_before_any_recovery_write(initial,monkeypatch):
    module,root,_,run=initial;(root/'state').mkdir(parents=True);journal=root/'state/install-transaction.json';journal.write_bytes(b'Preserve incomplete private recovery evidence')
    monkeypatch.setattr(module.sys,'platform','linux')
    monkeypatch.setattr(module,'atomic',lambda *a,**k:pytest.fail('No mutation on unqualified platform'))
    with pytest.raises(ValueError,match='Windows only'):run('--abandon-install','a'*32)
    assert journal.read_bytes()==b'Preserve incomplete private recovery evidence'

@pytest.mark.parametrize('diverged',[False,True])
@pytest.mark.parametrize('case',['direct','file-to-directory','directory-to-file','case-alias','ignore-change','hidden-untracked','nested-repo'])
def test_actual_git_preserves_ignored_device_local_file_during_receive(brain,tmp_path,isolated_git,diverged,case):
    vault,_=brain;remote=tmp_path/'remote.git';peer=tmp_path/'peer'
    _init(remote,isolated_git,bare=True);_init(vault,isolated_git)
    (vault/'.gitignore').write_text('' if case=='hidden-untracked' else 'Attachments/\n');note=vault/'01-Projects/Source.md';note.parent.mkdir();note.write_text('baseline')
    _raw(vault,'add','.');_raw(vault,'commit','-m','baseline');_raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','-u','origin','HEAD')
    _raw(tmp_path,'clone','--branch','main',str(remote),str(peer))
    _raw(peer,'config','user.name','Synthetic');_raw(peer,'config','user.email','synthetic@example.invalid');_raw(peer,'config','commit.gpgsign','false')
    incoming_name={'file-to-directory':'Attachments/item/child.md','directory-to-file':'Attachments/item','case-alias':'attachments/A.PNG','nested-repo':'Attachments/nested/new.md'}.get(case,'Attachments/a.png')
    local_name={'file-to-directory':'Attachments/item','directory-to-file':'Attachments/item/child.md','nested-repo':'Attachments/nested/Original.bin'}.get(case,'Attachments/a.png')
    incoming=peer/incoming_name;incoming.parent.mkdir(parents=True);incoming.write_bytes(b'peer original')
    _raw(peer,'add','-f',incoming_name)
    if case=='ignore-change':(peer/'.gitignore').write_text('');_raw(peer,'add','.gitignore')
    _raw(peer,'commit','-m','external attachment');_raw(peer,'push','origin','HEAD:refs/heads/main')
    local=vault/local_name;local.parent.mkdir(parents=True);local.write_bytes(b'device-local original')
    if case=='nested-repo':_raw(local.parent,'init')
    if case=='hidden-untracked':_raw(vault,'config','status.showUntrackedFiles','no')
    _raw(vault,'config','pull.twohead','resolve') # auto merge must explicitly match the screened ort tree
    if diverged:note.write_text('local committed update');_raw(vault,'add','01-Projects/Source.md');_raw(vault,'commit','-m','local diverged')
    head=_raw(vault,'rev-parse','HEAD');index=_raw(vault,'ls-files','--stage');upstream=_raw(remote,'rev-parse','refs/heads/main')
    status=_raw(vault,'status','--porcelain');other=(note.read_bytes(),(vault/'.gitignore').read_bytes())
    if case=='hidden-untracked':assert git.sync(vault).startswith('paused: other local changes')
    else:
        with pytest.raises(git.GitSyncError,match='Untracked local path'):git.sync(vault)
    assert local.read_bytes()==b'device-local original' and _raw(vault,'rev-parse','HEAD')==head
    assert _raw(vault,'ls-files','--stage')==index and _raw(remote,'rev-parse','refs/heads/main')==upstream
    assert _raw(vault,'status','--porcelain')==status and (note.read_bytes(),(vault/'.gitignore').read_bytes())==other
    assert not (vault/'.git/MERGE_HEAD').exists() and not (vault/'.git/ORIG_HEAD').exists()

@pytest.mark.parametrize('diverged',[False,True])
def test_ignored_sibling_and_tracked_updates_do_not_block_auto_merge(brain,tmp_path,isolated_git,diverged):
    vault,_=brain;remote=tmp_path/'remote.git';peer=tmp_path/'peer'
    _init(remote,isolated_git,bare=True);_init(vault,isolated_git)
    (vault/'.gitignore').write_text('Attachments/\n');note=vault/'01-Projects/Source.md';note.parent.mkdir();note.write_text('baseline')
    _raw(vault,'add','.');_raw(vault,'commit','-m','baseline');_raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','-u','origin','HEAD')
    _raw(tmp_path,'clone','--branch','main',str(remote),str(peer));_raw(peer,'config','user.name','Synthetic');_raw(peer,'config','user.email','synthetic@example.invalid');_raw(peer,'config','commit.gpgsign','false')
    (peer/'01-Projects/Source.md').write_text('peer tracked update')
    added=peer/'Attachments/b.png';added.parent.mkdir();added.write_bytes(b'peer b')
    _raw(peer,'add','-f','01-Projects/Source.md','Attachments/b.png');_raw(peer,'commit','-m','peer update');_raw(peer,'push','origin','HEAD:refs/heads/main')
    original=vault/'Attachments/a.png';original.parent.mkdir();original.write_bytes(b'local a')
    _raw(vault,'config','pull.twohead','resolve')
    if diverged:
        (vault/'01-Projects/Local.md').write_text('local committed evidence');_raw(vault,'add','01-Projects/Local.md');_raw(vault,'commit','-m','local diverged')
    assert git.sync(vault)=='synced'
    assert original.read_bytes()==b'local a' and (vault/'Attachments/b.png').read_bytes()==b'peer b'
    assert note.read_text()=='peer tracked update'

@pytest.mark.parametrize('mode,kind,allowed',[('100644','blob',True),('100755','blob',True),('120000','blob',False),('160000','commit',False)])
def test_actual_git_candidate_tree_modes_are_screened_before_checkout(tmp_path,isolated_git,mode,kind,allowed):
    from test_sync_committed_windows_paths import _input
    repo=tmp_path/'repo';_init(repo,isolated_git);(repo/'Original.md').write_text('original')
    _raw(repo,'add','.');_raw(repo,'commit','-m','baseline');head=_raw(repo,'rev-parse','HEAD').strip()
    oid=head if kind=='commit' else _input(repo,'hash-object','-w','--stdin',data=b'Synthetic harmless blob')
    tree=_input(repo,'mktree','-z',data=mode.encode()+b' '+kind.encode()+b' '+oid+b'\tIncoming.md\0').decode()
    if allowed:assert git._incoming_note_path(repo,tree) is None
    else:
        with pytest.raises(git.GitSyncError,match='linked or submodule'):git._incoming_note_path(repo,tree)
    assert _raw(repo,'rev-parse','HEAD').strip()==head and (repo/'Original.md').read_text()=='original'
    assert not (repo/'Incoming.md').exists()
