"""Independent-review failure scenarios; disposable notes, clients and Git only."""
import json
import os
from pathlib import Path
import shutil
import sys
import time
import pytest
import brain_markdown as md
import brain_store as store
import brain_wiki as wiki
import brain_index as index
import brain_index_bootstrap as bootstrap
from brain_walk import walk,WalkLimit
from test_initial_install_resume import initial
from test_setup_sealed_plans import owner,plan,helpers
from test_upgrade_transactions import installation,updater
from test_sync_fsmonitor_policy import isolated_git,_init,_raw

@pytest.mark.parametrize('client,kind',[('claude-code','json'),('codex','toml'),('hooks','hooks')])
def test_refresh_uses_decoded_ownership_with_unicode_and_escaped_slashes(owner,client,kind):
    home,base,python=owner;root=base/'ü'
    if kind=='toml':
        path=home/'.codex/config.toml'
        raw=('[mcp_servers.second-brain]\ncommand="old"\nargs=['+json.dumps(str(root/'app/brain_client.py'))+']\n').encode()
    elif kind=='json':
        path=home/'.claude.json';raw=json.dumps({'mcpServers':{'second-brain':{'command':'old','args':[str(root/'app/brain_client.py')]}}}).replace('/',r'\/').encode()
    else:
        path=home/'.claude/settings.json';path.parent.mkdir()
        command='"'+str(python)+'" "'+str(root/'app/brain_hooks.py')+'"' if os.name=='nt' else __import__('shlex').join([str(python),str(root/'app/brain_hooks.py')])
        raw=json.dumps({'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':command}]}]}}).encode()
    path.write_bytes(raw)
    value=helpers.plan(root,python,'protocol',__import__('test_setup_sealed_plans').installer._codex_toml_plan,home=home,selected=[client],refresh=True)
    assert any(Path(row['path'])==path for row in value['files'])
    assert path.read_bytes()==raw

def test_public_client_authority_comes_from_actual_selection(owner,monkeypatch):
    home,root,python=owner;monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    value=plan(owner,selected=['codex']);helpers.authorize_plan(value,selected=['codex'])
    value['files'][0]['path']=str(home/'Original.md')
    with pytest.raises(ValueError,match='outside'):helpers.authorize_plan(value,selected=['codex'])

@pytest.mark.parametrize('stage',['deployed','model','service-intent','service-done'])
@pytest.mark.parametrize('artifact',['device.json','state/client-access.json','state/access.json','state/config.json','state/.bmignore','app'])
def test_missing_published_install_artifact_never_completes(initial,monkeypatch,stage,artifact):
    module,root,_,run=initial;support=module.setup_helpers()
    def fail(*args,**kwargs):raise OSError('Synthetic model interruption')
    monkeypatch.setattr(support,'run',fail)
    with pytest.raises(OSError):run('--download-model')
    journal_path=root/'state/install-transaction.json';value=json.loads(journal_path.read_text());value['stage']=stage
    journal_path.write_text(json.dumps(value))
    target=root/artifact
    if target.is_dir():shutil.rmtree(target)
    else:target.unlink()
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):module._fresh_check(root,value)
    assert not target.exists() and before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert json.loads(journal_path.read_text())['writes']

def test_metadata_aliases_do_not_expand_representations(brain):
    class Hostile:
        def __str__(self):raise AssertionError('str called')
        def __repr__(self):raise AssertionError('repr called')
    assert md.bounded_date_label(Hostile())=='unknown'
    with pytest.raises(ValueError):md.bounded_capture_datetime(Hostile())
    lines=['---','a0: &a0 [x, x]']+[f'a{i}: &a{i} [*a{i-1}, *a{i-1}]' for i in range(1,31)]
    raw=('\n'.join(lines)+'\ndate: *a30\ncaptured_at: *a30\n---\n# Small source with aliased metadata\nEvidence remains historical.\n').encode()
    path=brain[0]/'01-Projects/Aliased.md';path.parent.mkdir();path.write_bytes(raw)
    started=time.monotonic();rows=wiki.inventory()
    assert time.monotonic()-started<3 and any(row['path']=='01-Projects/Aliased.md' and row['date']=='unknown' for row in rows)
    assert path.read_bytes()==raw
    assert md.bounded_date_label(10**5000)=='unknown'
    assert md.bounded_date_label(md.date(2026,11,2))=='2026-11-02'

def test_scandir_counts_non_notes_and_caller_prunes(tmp_path):
    (tmp_path/'hidden').mkdir();(tmp_path/'hidden/note.md').write_text('ignored')
    (tmp_path/'file.bin').write_bytes(b'x')
    with pytest.raises(WalkLimit,match='entry_limit'):list(walk(tmp_path,max_entries=1))
    seen=[]
    for directory,dirs,files in walk(tmp_path):dirs.clear();seen+=files
    assert seen==['file.bin']

def test_bootstrap_budget_includes_inventory(brain,monkeypatch):
    clock=[100.];monkeypatch.setattr(bootstrap.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(index,'configuration',lambda:None);monkeypatch.setattr(index,'ensure_history_scope',lambda:{})
    def inventory(check):clock[0]+=21;check()
    monkeypatch.setattr(index,'inventory',inventory)
    monkeypatch.setattr(bootstrap,'_run_reindex',lambda *args:pytest.fail('No runner after preflight timeout'))
    result=bootstrap.main(overall_seconds=20,stall_seconds=5)
    assert result['status']=='needs attention' and result['reason']=='overall_time_limit'

def test_fresh_search_reports_partial_budget(brain,monkeypatch):
    vault,_=brain;(vault/'A.md').write_text('quartz marker')
    clock=iter([0,11]);monkeypatch.setattr(store.time,'monotonic',lambda:next(clock,11))
    result=store.fresh_search('quartz')
    assert result==[] and result.scope['status']=='partial' and result.scope['reason']=='fresh_search_time_limit'

@pytest.mark.parametrize('kind',['oversize','directory'])
def test_corrupt_backup_refuses_before_note_replace(brain,kind):
    vault,state=brain;path=vault/'02-Areas/AI-Memory/A.md';path.parent.mkdir(parents=True);path.write_bytes(b'original')
    backup=state/'write-backups'/store.digest(b'original');backup.parent.mkdir()
    if kind=='directory':backup.mkdir()
    else:backup.write_bytes(b'x'*10000)
    with pytest.raises(ValueError,match='backup integrity'):store._write(path,'replacement','test',store.digest(b'original'))
    assert path.read_bytes()==b'original'

def test_device_restore_cas_failure_precedes_app_swap(installation,monkeypatch):
    h=installation;saved=updater.snapshot(h.root,sys.executable,h.plan)
    (h.root/'device.json').write_bytes((json.dumps(h.planned_device,indent=2)+'\n').encode())
    (h.root/'app/old.py').unlink();(h.root/'app/new.py').write_bytes(b'# candidate')
    def conflict(*args,**kwargs):raise h.support.ConcurrentWriteError('Injected staging conflict')
    monkeypatch.setattr(h.helper,'atomic',conflict)
    with pytest.raises(h.support.ConcurrentWriteError):updater.restore(h.root,saved)
    assert (h.root/'app/new.py').read_bytes()==b'# candidate' and not (h.root/'app/old.py').exists()

def test_structured_model_bytes_bounded_before_json_parse(tmp_path,monkeypatch):
    output=tmp_path/'output.json';output.write_bytes(b'x'*101)
    monkeypatch.setattr(wiki,'MODEL_OUTPUT_BYTES',100)
    with pytest.raises(wiki.WikiOutputError,match='byte budget'):wiki._read_model_output(output)

def test_cleanup_pending_is_visible_attention(brain):
    state=brain[1];path=state/'wiki-worker-status.json'
    path.write_text(json.dumps({'status':'cleanup pending'}))
    result=wiki._read_worker_state()
    assert result['status']=='cleanup pending' and result['attention_required'] is True

@pytest.mark.parametrize('names',[['Foo.md','foo.md'],['Foo/a.md','foo/b.md'],['ß.md','ss.md']])
def test_candidate_tree_case_collisions_refuse_before_checkout(tmp_path,monkeypatch,isolated_git,names):
    import brain_git as git
    repo=tmp_path/'repo';_init(repo,isolated_git)
    for name in ('seed.md',): (repo/name).write_text('seed')
    _raw(repo,'add','.');_raw(repo,'commit','-m','synthetic seed');oid=_raw(repo,'rev-parse','HEAD').decode().strip()
    original=git._run_git
    def run(command,**kwargs):
        if 'ls-tree' in command:
            kwargs['stdout'].write(b'\0'.join(b'100644 blob '+b'a'*40+b'\t'+name.encode() for name in names)+b'\0')
            return __import__('subprocess').CompletedProcess(command,0)
        return original(command,**kwargs)
    monkeypatch.setattr(git,'_run_git',run)
    with pytest.raises(git.GitSyncError,match='candidate-tree collision'):git._incoming_note_path(repo,oid)
    assert _raw(repo,'rev-parse','HEAD').decode().strip()==oid and (repo/'seed.md').read_text()=='seed'

@pytest.mark.parametrize('command',[['cmd','/c','codex exec'],['powershell','-Command','Start-Process claude'],['sh','-c','schtasks /run']])
def test_wrapped_child_commands_are_refused(command):
    from isolation_guard import check
    with pytest.raises(AssertionError,match='shell-wrapped'):check(command)

@pytest.mark.asyncio
async def test_async_inference_route_is_refused():
    import asyncio
    with pytest.raises(AssertionError,match='inference'):await asyncio.create_subprocess_exec('codex','exec','synthetic')

def test_python_network_and_owner_credential_routes_refused():
    import socket
    from isolation_guard import check,PRIVATE_OWNER
    with socket.socket() as connection:
        with pytest.raises(AssertionError,match='non-loopback'):connection.connect(('192.0.2.1',443))
    with pytest.raises(AssertionError,match='DNS'):socket.getaddrinfo('example.invalid',443)
    assert PRIVATE_OWNER
    with pytest.raises(AssertionError,match='credential'):check([sys.executable,PRIVATE_OWNER[0]])

def test_actual_git_combined_merge_tree_detects_case_collision_without_checkout(tmp_path,isolated_git):
    import subprocess
    import brain_git as git
    repo=tmp_path/'repo';_init(repo,isolated_git)
    (repo/'seed.md').write_text('seed');_raw(repo,'add','.');_raw(repo,'commit','-m','base')
    base=_raw(repo,'rev-parse','HEAD').decode().strip();seed=_raw(repo,'rev-parse','HEAD:seed.md').decode().strip()
    def run(*args,raw=None):
        return subprocess.run(['git','-c','core.fsmonitor=false','-C',str(repo),*args],input=raw,capture_output=True,check=True,timeout=10).stdout.decode().strip()
    def tree(names):return run('mktree',raw=''.join('100644 blob '+seed+'\t'+name+'\n' for name in sorted(names)).encode())
    left=run('commit-tree',tree(['seed.md','Foo.md']),'-p',base,'-m','left')
    right=run('commit-tree',tree(['seed.md','foo.md']),'-p',base,'-m','right')
    combined=run('merge-tree','--write-tree',left,right).splitlines()[0]
    with pytest.raises(git.GitSyncError,match='candidate-tree collision'):git._incoming_note_path(repo,combined)
    assert run('rev-parse','HEAD')==base and not (repo/'Foo.md').exists() and run('status','--porcelain')==''
    # A tree containing only the renamed spelling is a valid case-only rename.
    assert git._incoming_note_path(repo,tree(['seed.md','foo.md'])) is None
