"""Real disposable Git repositories exercise durable publication admission."""
import json
from pathlib import Path
import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git,_init,_raw

def pair(brain,tmp_path,hooks,diverged):
    vault,state=brain;remote=tmp_path/'remote.git';peer=tmp_path/'peer'
    _init(remote,hooks,bare=True);_init(vault,hooks)
    note=vault/'01-Projects/Source.md';note.parent.mkdir();note.write_text('baseline')
    _raw(vault,'add','.');_raw(vault,'commit','-m','baseline');_raw(vault,'remote','add','origin',str(remote));_raw(vault,'push','-u','origin','HEAD')
    _raw(tmp_path,'clone','--branch','main',str(remote),str(peer))
    _raw(peer,'config','user.name','Synthetic');_raw(peer,'config','user.email','synthetic@example.invalid');_raw(peer,'config','commit.gpgsign','false')
    note2=peer/'01-Projects/Peer.md';note2.write_text('peer source');_raw(peer,'add','.');_raw(peer,'commit','-m','peer update');_raw(peer,'push','origin','HEAD')
    if diverged:
        (vault/'01-Projects/Local.md').write_text('local source');_raw(vault,'add','.');_raw(vault,'commit','-m','local update')
    return vault,state,remote

@pytest.mark.parametrize('diverged,kind',[(False,'message'),(False,'content'),(True,'content')])
def test_hook_mismatch_remains_held_across_sync_and_attended_review(brain,tmp_path,isolated_git,monkeypatch,diverged,kind):
    vault,state,remote=pair(brain,tmp_path,isolated_git,diverged)
    hook=isolated_git/'post-merge'
    content="printf 'hook changed source' > 01-Projects/Peer.md\ngit add 01-Projects/Peer.md\n" if kind=='content' else ''
    amendment=("new=$(printf 'synthetic hook amendment' | git commit-tree \"$(git write-tree)\" -p \"$(git rev-parse HEAD^1)\" -p \"$(git rev-parse HEAD^2)\")\ngit update-ref HEAD \"$new\"\n"
               if diverged else "git -c core.hooksPath=/dev/null commit --amend -m 'synthetic hook amendment'\n")
    hook.write_bytes(('#!/bin/sh\n'+content+amendment).encode());hook.chmod(0o700)
    remote_before=_raw(remote,'rev-parse','refs/heads/main')
    original=g._run_git;merge_output=[]
    def observed(command,**kwargs):
        result=original(command,**kwargs)
        if 'merge' in command:merge_output.append((result.returncode,result.stdout,result.stderr))
        return result
    monkeypatch.setattr(g,'_run_git',observed)
    try:g.sync(vault)
    except g.GitSyncError as exc:assert 'durable Git review hold' in str(exc)
    else:pytest.fail('Synthetic hook diagnostic: '+repr(merge_output))
    hold=state/'git-sync-hold.json';raw=hold.read_bytes();head=_raw(vault,'rev-parse','HEAD');notes={p.name:p.read_bytes() for p in (vault/'01-Projects').glob('*.md')}
    for _ in range(2):
        with pytest.raises(g.GitSyncError,match='attended review'):g.sync(vault)
        assert hold.read_bytes()==raw and _raw(remote,'rev-parse','refs/heads/main')==remote_before
    # Attended remediation is separate from the product recovery command.
    # Disable the offending synthetic hook and deliberately reconcile the FF
    # sibling created by amend, preserving the inspected local tree.
    hook.unlink()
    if not diverged:_raw(vault,'merge','--strategy=ours','--no-edit','refs/remotes/origin/main')
    head=_raw(vault,'rev-parse','HEAD')
    info=g.review(vault)
    for changes in ({'head':'a'*40},{'tree':'b'*40},{'hold_sha256':'c'*64}):
        args={k:info[k] for k in ('head','tree','hold_sha256')};args.update(changes)
        with pytest.raises(g.GitSyncError,match='fingerprint'):g.review(vault,**args)
        assert hold.read_bytes()==raw
    dirty=vault/'Untracked.md';dirty.write_text('preserve')
    with pytest.raises(g.GitSyncError):g.review(vault,**{k:info[k] for k in ('head','tree','hold_sha256')})
    dirty.unlink()
    config=(vault/'.git/config').read_bytes();index=_raw(vault,'ls-files','--stage');index_bytes=(vault/'.git/index').read_bytes()
    receipt=g.review(vault,**{k:info[k] for k in ('head','tree','hold_sha256')})
    assert not hold.exists() and receipt['published'] is False and Path(receipt['receipt']).is_file()
    assert _raw(vault,'rev-parse','HEAD')==head and _raw(vault,'ls-files','--stage')==index and (vault/'.git/config').read_bytes()==config
    assert (vault/'.git/index').read_bytes()==index_bytes
    assert {p.name:p.read_bytes() for p in (vault/'01-Projects').glob('*.md')}==notes
    assert g.sync(vault)=='synced' and _raw(remote,'rev-parse','refs/heads/main')==head

@pytest.mark.parametrize('when',['before','after'])
def test_interrupted_merge_keeps_pending_hold(brain,tmp_path,isolated_git,monkeypatch,when):
    vault,state,remote=pair(brain,tmp_path,isolated_git,True);original=g._run_git
    remote_before=_raw(remote,'rev-parse','refs/heads/main')
    def run(command,**kwargs):
        if 'merge' in command:
            if when=='after':original(command,**kwargs)
            raise KeyboardInterrupt('synthetic interruption')
        return original(command,**kwargs)
    monkeypatch.setattr(g,'_run_git',run)
    with pytest.raises(KeyboardInterrupt):g.sync(vault)
    monkeypatch.setattr(g,'_run_git',original)
    assert (state/'git-sync-hold.json').exists()
    with pytest.raises(g.GitSyncError,match='attended review'):g.sync(vault)
    assert _raw(remote,'rev-parse','refs/heads/main')==remote_before

@pytest.mark.parametrize('kind',['corrupt','oversized','directory','wrong-root','wrong-vault'])
def test_invalid_hold_blocks_before_git_mutation(brain,tmp_path,isolated_git,monkeypatch,kind):
    vault,state,remote=pair(brain,tmp_path,isolated_git,False);path=state/'git-sync-hold.json'
    if kind=='directory':path.mkdir()
    elif kind=='oversized':path.write_bytes(b'x'*65537)
    elif kind in ('wrong-root','wrong-vault'):
        value=intent(vault)
        value['root' if kind=='wrong-root' else 'vault']='unowned'
        path.write_text(json.dumps(value))
    else:path.write_bytes(b'bad json')
    original=g._run_git;calls=[]
    def run(command,**kwargs):calls.append(command);return original(command,**kwargs)
    monkeypatch.setattr(g,'_run_git',run)
    with pytest.raises(g.GitSyncError):g.sync(vault)
    assert len(calls)==1 and '--version' in calls[0]

def test_failed_pending_persistence_never_merges(brain,tmp_path,isolated_git,monkeypatch):
    vault,state,remote=pair(brain,tmp_path,isolated_git,False);head=_raw(vault,'rev-parse','HEAD');upstream=_raw(remote,'rev-parse','refs/heads/main');replace=g.os.replace
    def refuse(source,destination):
        if Path(destination)==state/'git-sync-hold.json':raise OSError('synthetic persistence failure')
        return replace(source,destination)
    monkeypatch.setattr(g.os,'replace',refuse)
    with pytest.raises(g.GitSyncError,match='persisted'):g.sync(vault)
    assert _raw(vault,'rev-parse','HEAD')==head
    assert _raw(remote,'rev-parse','refs/heads/main')==upstream and not (vault/'.git/ORIG_HEAD').exists()

def test_review_accepts_explicit_repair_descendant(brain,tmp_path,isolated_git,monkeypatch):
    vault,state,_=pair(brain,tmp_path,isolated_git,False);original=g._run_git
    def run(command,**kwargs):
        result=original(command,**kwargs)
        if 'merge' in command:raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(g,'_run_git',run)
    with pytest.raises(KeyboardInterrupt):g.sync(vault)
    monkeypatch.setattr(g,'_run_git',original)
    (vault/'01-Projects/Repair.md').write_text('attended repair');_raw(vault,'add','.');_raw(vault,'commit','-m','reviewed repair')
    info=g.review(vault);assert g.review(vault,**{k:info[k] for k in ('head','tree','hold_sha256')})['status']=='review accepted'

@pytest.mark.parametrize('kind',['replacement','dirty','staged','graft','parents'])
def test_real_true_merge_hook_cannot_hide_unverified_result(brain,tmp_path,isolated_git,monkeypatch,kind):
    vault,state,remote=pair(brain,tmp_path,isolated_git,True)
    start='#!/bin/sh\nold=$(git rev-parse HEAD)\n'
    if kind in ('replacement','parents'):
        content="printf 'changed by hook' > 01-Projects/Peer.md\ngit add 01-Projects/Peer.md\n" if kind=='replacement' else ''
        parent='HEAD^1' if kind=='replacement' else 'HEAD^1^'
        body=content+'new=$(printf synthetic | git commit-tree "$(git write-tree)" -p "$(git rev-parse '+parent+')" -p "$(git rev-parse HEAD^2)")\ngit update-ref HEAD "$new"\n'
        if kind=='replacement':body+='git replace "$new" "$old"\n'
    elif kind=='graft':body='printf "%s\\n" "$old" > "$(git rev-parse --git-path info/grafts)"\n'
    else:
        body="printf 'uncommitted hook source' > 01-Projects/Hook.md\n"
        if kind=='staged':body+='git add 01-Projects/Hook.md\n'
    hook=isolated_git/'post-merge';hook.write_bytes((start+body).encode());hook.chmod(0o700)
    remote_before=_raw(remote,'rev-parse','refs/heads/main')
    with pytest.raises(g.GitSyncError):g.sync(vault)
    hold=state/'git-sync-hold.json';raw=hold.read_bytes()
    for _ in range(2):
        with pytest.raises(g.GitSyncError,match='attended review'):g.sync(vault)
        assert hold.read_bytes()==raw and _raw(remote,'rev-parse','refs/heads/main')==remote_before
    if kind=='replacement':
        info=g.review(vault);expected=json.loads(raw)['expected_tree']
        assert info['tree']!=expected
        ref='refs/replace/'+info['head'];assert _raw(vault,'rev-parse',ref).strip()
        monkeypatch.setenv('GIT_NO_REPLACE_OBJECTS','1')
        assert _raw(vault,'rev-parse',info['head']+'^{tree}').decode().strip()==info['tree']
        hook.unlink();receipt=g.review(vault,**{k:info[k] for k in ('head','tree','hold_sha256')})
        assert receipt['published'] is False and _raw(vault,'rev-parse',ref).strip()
        assert g.sync(vault)=='synced' and _raw(remote,'rev-parse','refs/heads/main').decode().strip()==info['head']
    elif kind=='graft':
        with pytest.raises(g.GitSyncError,match='grafts'):g.review(vault)
        assert (vault/'.git/info/grafts').exists() and hold.read_bytes()==raw

@pytest.mark.parametrize('seam',['parents','tree'])
def test_external_head_advance_does_not_change_verified_push_source(brain,tmp_path,isolated_git,monkeypatch,seam):
    vault,_,remote=pair(brain,tmp_path,isolated_git,True);original=g._run_git;observed={}
    def run(command,**kwargs):
        result=original(command,**kwargs)
        trigger=(seam=='parents' and 'rev-list' in command and '--parents' in command) or (seam=='tree' and 'rev-parse' in command and command[-1].endswith('^{tree}') and len(command[-1].split('^')[0])==40)
        if trigger and not observed:
            observed['verified']=_raw(vault,'rev-parse','HEAD')
            (vault/'01-Projects/External.md').write_text('external owner commit')
            _raw(vault,'add','.');_raw(vault,'commit','-m','external owner commit');observed['later']=_raw(vault,'rev-parse','HEAD')
        return result
    monkeypatch.setattr(g,'_run_git',run)
    assert g.sync(vault)=='synced'
    assert _raw(remote,'rev-parse','refs/heads/main')==observed['verified']
    assert _raw(vault,'rev-parse','HEAD')==observed['later']!=observed['verified']

@pytest.mark.parametrize('explicit_env',[False,True])
def test_transport_forces_raw_object_view_without_mutating_inputs(brain,tmp_path,isolated_git,monkeypatch,explicit_env):
    import os
    vault,_,_=pair(brain,tmp_path,isolated_git,False)
    old=_raw(vault,'rev-parse','HEAD').decode().strip()
    (vault/'01-Projects/Source.md').write_text('replacement source');_raw(vault,'add','.');_raw(vault,'commit','-m','replacement')
    new=_raw(vault,'rev-parse','HEAD').decode().strip();_raw(vault,'replace',new,old)
    alternate=tmp_path/'alternate-grafts';alternate.write_bytes(b'preserve alternate graft evidence')
    monkeypatch.setenv('GIT_GRAFT_FILE',str(alternate));monkeypatch.setenv('GIT_NO_REPLACE_OBJECTS','0');before=dict(os.environ);env=dict(before)
    command=['git','-C',str(vault),'cat-file','commit',new];args=list(command)
    result=g._run_git(command,capture_output=True,timeout=10,**({'env':env} if explicit_env else {}))
    assert result.returncode==0 and b'replacement' in result.stdout and command==args and env==before and dict(os.environ)==before
    assert alternate.read_bytes()==b'preserve alternate graft evidence'

def test_deeply_nested_hold_has_controlled_diagnostic(brain):
    vault,state=brain;(state/'git-sync-hold.json').write_bytes(b'['*5000)
    with pytest.raises(g.GitSyncError,match='invalid or unreadable'):g.review(vault)

def intent(vault):
    head=_raw(vault,'rev-parse','HEAD').decode().strip()
    return {'format':1,'root':str(g.s.ROOT.resolve()),'vault':str(vault.resolve()),'branch':'refs/remotes/origin/main',
            'upstream':head,'pre_head':head,'expected_head':head,'expected_tree':_raw(vault,'rev-parse','HEAD^{tree}').decode().strip(),
            'reason':'checkout-pending-verification','created':0}

@pytest.mark.parametrize('kind',['staged','merge','unrelated','receipt-failure','linked'])
def test_attended_review_refusals_preserve_exact_hold(brain,tmp_path,isolated_git,monkeypatch,kind):
    vault,state,_=pair(brain,tmp_path,isolated_git,False);hold=state/'git-sync-hold.json';g._atomic_hold(hold,intent(vault));raw=hold.read_bytes()
    if kind=='staged':
        (vault/'01-Projects/Source.md').write_text('staged owner change');_raw(vault,'add','.');info=g.review(vault)
    elif kind=='merge':
        (vault/'.git/MERGE_HEAD').write_bytes(_raw(vault,'rev-parse','HEAD'));info=g.review(vault)
    elif kind=='unrelated':
        tree=_raw(vault,'rev-parse','HEAD^{tree}').decode().strip();new=_raw(vault,'commit-tree',tree,'-m','unrelated').decode().strip();_raw(vault,'update-ref','HEAD',new);info=g.review(vault)
    else:info=g.review(vault)
    if kind=='receipt-failure':
        def refuse(*args):raise g.GitSyncError('synthetic receipt failure')
        monkeypatch.setattr(g,'_atomic_hold',refuse)
    if kind=='linked':
        original=Path.is_symlink
        monkeypatch.setattr(Path,'is_symlink',lambda self:self==hold or original(self))
    with pytest.raises(g.GitSyncError):g.review(vault,**{k:info[k] for k in ('head','tree','hold_sha256')})
    assert hold.read_bytes()==raw

@pytest.mark.parametrize('when',['before','after-fetch'])
def test_legacy_grafts_are_preserved_and_block_receive(brain,tmp_path,isolated_git,monkeypatch,when):
    vault,state,remote=pair(brain,tmp_path,isolated_git,False);path=vault/'.git/info/grafts';head=_raw(vault,'rev-parse','HEAD');original=g._run_git
    def create():path.write_bytes(head)
    if when=='before':create()
    else:
        def run(command,**kwargs):
            result=original(command,**kwargs)
            if 'fetch' in command:create()
            return result
        monkeypatch.setattr(g,'_run_git',run)
    with pytest.raises(g.GitSyncError,match='grafts'):g.sync(vault)
    assert path.read_bytes()==head and _raw(vault,'rev-parse','HEAD')==head and not (state/'git-sync-hold.json').exists()

def test_hidden_noncolliding_untracked_file_pauses_before_checkout(brain,tmp_path,isolated_git):
    vault,state,_=pair(brain,tmp_path,isolated_git,False);_raw(vault,'config','status.showUntrackedFiles','no')
    local=vault/'Local.bin';local.write_bytes(b'owner original');head=_raw(vault,'rev-parse','HEAD')
    assert g.sync(vault).startswith('paused: other local changes')
    assert not (state/'git-sync-hold.json').exists() and _raw(vault,'rev-parse','HEAD')==head and local.read_bytes()==b'owner original'
