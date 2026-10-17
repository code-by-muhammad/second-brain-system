"""Regression tests use real disposable repositories, never the shared vault."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from filelock import FileLock, Timeout
import pytest
import brain_git as syncer
import brain_store as store
from test_sync import pair, git

def failing_hook(repo,body='exit 1\n'):
    path=repo/'.git/hooks/pre-commit'
    path.write_text('#!/bin/sh\n'+body,encoding='utf-8')
    path.chmod(0o755)
    return path

def test_failed_commit_unstages_only_automatic_notes_and_retries(brain,tmp_path):
    a,b=pair(tmp_path)
    note=a/'01-Projects/Automatic.md'; note.write_text('Synthetic automatic note.\n')
    hook=failing_hook(a)
    with pytest.raises(syncer.GitSyncError,match='commit failed'): syncer.sync(a)
    assert git(a,'diff','--cached','--name-only')==''
    assert note.read_text()=='Synthetic automatic note.\n'
    hook.unlink()
    assert syncer.sync(a)=='synced'

def test_failed_hook_preserves_unrelated_user_staging(brain,tmp_path):
    a,b=pair(tmp_path)
    (a/'01-Projects/Automatic.md').write_text('Synthetic automatic note.\n')
    user=a/'user-staged.txt'; user.write_text('Synthetic user staging.\n')
    failing_hook(a,'git add -- user-staged.txt\nexit 1\n')
    with pytest.raises(syncer.GitSyncError): syncer.sync(a)
    assert git(a,'diff','--cached','--name-only').strip()=='user-staged.txt'
    assert user.read_text()=='Synthetic user staging.\n'

def test_failed_hook_preserves_a_restaged_version_of_the_same_note(brain,tmp_path):
    a,b=pair(tmp_path)
    path=a/'01-Projects/Automatic.md'; path.write_text('Version before hook.\n')
    failing_hook(a,"printf 'Version explicitly restaged by hook.\\n' > 01-Projects/Automatic.md\ngit add -- 01-Projects/Automatic.md\nexit 1\n")
    with pytest.raises(syncer.GitSyncError): syncer.sync(a)
    assert git(a,'show',':01-Projects/Automatic.md')=='Version explicitly restaged by hook.\n'
    assert path.read_text()=='Version explicitly restaged by hook.\n'

def test_intent_to_add_is_user_staging_and_is_preserved(brain,tmp_path):
    a,b=pair(tmp_path)
    (a/'01-Projects/User.md').write_text('User intent to stage.\n')
    git(a,'add','--intent-to-add','01-Projects/User.md')
    before=git(a,'ls-files','--stage')
    assert syncer.sync(a).startswith('paused: existing staged')
    assert git(a,'ls-files','--stage')==before

def test_operational_merge_error_is_not_reported_as_a_conflict(brain,tmp_path,monkeypatch):
    a,b=pair(tmp_path)
    (a/'01-Projects/A.md').write_text('Independent source A.\n')
    assert syncer.sync(a)=='synced'
    (b/'01-Projects/B.md').write_text('Independent source B.\n')
    native=subprocess.run
    def injected(cmd,**kwargs):
        if 'merge-tree' in cmd: return subprocess.CompletedProcess(cmd,128,'','synthetic operational failure')
        return native(cmd,**kwargs)
    monkeypatch.setattr(syncer, '_run_git', injected)
    with pytest.raises(syncer.GitSyncError,match='preflight failed.*128'):
        syncer.sync(b)
    assert not git(b,'status','--porcelain').strip()
    assert not (b/'01-Projects/A.md').exists()

def test_unsupported_git_has_a_clear_diagnostic_before_mutation(brain,tmp_path,monkeypatch):
    a,b=pair(tmp_path); calls=[]
    def old_git(cmd,**kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd,0,'git version 2.34.1\n','')
    monkeypatch.setattr(syncer, '_run_git', old_git)
    with pytest.raises(syncer.GitSyncError,match=r'Git >= 2\.38'): syncer.sync(a)
    assert len(calls)==1 and calls[0][-1]=='--version'

@pytest.mark.parametrize('payload,reason', [('Synthetic\x00bad note.','invalid characters'),
                                          ('password = syntheticabcdefghijk123456789','possible credential')])
def test_blocking_note_path_is_reported_without_its_contents(brain,tmp_path,payload,reason):
    a,b=pair(tmp_path)
    note=a/'01-Projects/Blocked.md'; note.write_text(payload)
    with pytest.raises(syncer.GitSyncError) as error: syncer.sync(a)
    message=str(error.value)
    assert '01-Projects/Blocked.md' in message and reason in message
    assert payload not in message and note.read_text()==payload
    assert not git(a,'diff','--cached','--name-only')

def test_large_windows_path_list_uses_nul_stdin(brain,tmp_path,monkeypatch):
    a,b=pair(tmp_path)
    for i in range(340):
        (a/'01-Projects'/('note-'+str(i).zfill(3)+'-'+'x'*105+'.md')).write_text('Synthetic batch note.\n')
    native=subprocess.run; additions=[]
    def traced(cmd,**kwargs):
        if 'add' in cmd:
            additions.append((cmd,kwargs.get('input')))
            assert '--pathspec-from-file=-' in cmd and '--pathspec-file-nul' in cmd
            assert len(subprocess.list2cmdline(cmd))<1000
        return native(cmd,**kwargs)
    monkeypatch.setattr(syncer, '_run_git', traced)
    assert syncer.sync(a)=='synced'
    assert len(additions)==1 and len(additions[0][1])>32767
    assert len(git(a,'ls-files','01-Projects').splitlines())==341

def writer_available(state):
    try:
        with FileLock(str(state/'writer.lock'),timeout=.05): return True
    except Timeout: return False

def test_network_does_not_hold_writer_but_merge_and_index_stages_do(brain,tmp_path,monkeypatch):
    a,b=pair(tmp_path); state=brain[1]
    (a/'01-Projects/A.md').write_text('Independent source A.\n')
    assert syncer.sync(a)=='synced'
    (b/'01-Projects/B.md').write_text('Independent source B.\n')
    native=subprocess.run; observed={}
    with ThreadPoolExecutor(max_workers=1) as pool:
        def traced(cmd,**kwargs):
            action=next((name for name in ('add','commit','fetch','merge','push') if name in cmd),None)
            if action: observed[action]=pool.submit(writer_available,state).result(timeout=2)
            return native(cmd,**kwargs)
        monkeypatch.setattr(syncer, '_run_git', traced)
        assert syncer.sync(b)=='synced'
    assert observed=={'add':False,'commit':False,'fetch':True,'merge':False,'push':True}

@pytest.mark.parametrize('network_stage',['fetch','push'])
def test_real_capture_succeeds_during_network_wait(brain,tmp_path,monkeypatch,network_stage):
    a,b=pair(tmp_path)
    monkeypatch.setattr(store,'VAULT',a)
    native=subprocess.run; captured=[]
    def traced(cmd,**kwargs):
        if network_stage in cmd and not captured:
            captured.append(store.capture('during-'+network_stage,'Synthetic capture','Saved during network I/O.','test','synthetic fixture'))
        return native(cmd,**kwargs)
    monkeypatch.setattr(syncer, '_run_git', traced)
    outcome=syncer.sync(a)
    assert captured[0]['status']=='saved'
    assert (a/captured[0]['path']).is_file()
    if network_stage=='fetch': assert outcome.startswith('paused: local changes arrived during fetch')
    else: assert outcome=='synced'
    assert syncer.sync(a)=='synced'
