import subprocess
from pathlib import Path
import brain_git

def git(path,*args):
    return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True).stdout

def pair(tmp_path):
    remote=tmp_path/'remote.git'; subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    a=tmp_path/'a'; b=tmp_path/'b'
    subprocess.run(['git','clone',str(remote),str(a)],check=True,capture_output=True)
    for repo in (a,):
        git(repo,'config','user.email','synthetic@example.invalid'); git(repo,'config','user.name','Synthetic Test')
    (a/'01-Projects').mkdir(); (a/'01-Projects/Initial.md').write_text('Initial source.\n')
    git(a,'add','.'); git(a,'commit','-m','initial'); git(a,'push','-u','origin','HEAD')
    subprocess.run(['git','clone',str(remote),str(b)],check=True,capture_output=True)
    git(b,'config','user.email','synthetic@example.invalid'); git(b,'config','user.name','Synthetic Test')
    return a,b

def test_independent_offline_captures_merge(tmp_path):
    a,b=pair(tmp_path)
    for repo,name in ((a,'one'),(b,'two')):
        folder=repo/'00-Inbox/AI-Captures'; folder.mkdir(parents=True)
        (folder/(name+'.md')).write_text('Synthetic immutable event '+name+'\n')
    assert brain_git.sync(a)=='synced'
    assert brain_git.sync(b)=='synced'
    assert brain_git.sync(a)=='synced'
    for repo in (a,b): assert len(list((repo/'00-Inbox/AI-Captures').glob('*.md')))==2

def test_conflicting_offline_edits_pause_without_conflict_files(tmp_path):
    a,b=pair(tmp_path)
    (a/'01-Projects/Initial.md').write_text('Device A edited the same line.\n')
    (b/'01-Projects/Initial.md').write_text('Device B edited the same line.\n')
    assert brain_git.sync(a)=='synced'
    assert brain_git.sync(b).startswith('paused: conflicting')
    assert (b/'01-Projects/Initial.md').read_text()=='Device B edited the same line.\n'
    assert not git(b,'status','--porcelain').strip()

def test_user_staging_is_preserved(tmp_path):
    a,b=pair(tmp_path); (a/'01-Projects/User.md').write_text('User staged note.\n'); git(a,'add','.')
    assert brain_git.sync(a).startswith('paused: existing staged')
    assert 'User.md' in git(a,'diff','--cached','--name-only')
