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

def test_independent_offline_captures_merge(brain,tmp_path):
    a,b=pair(tmp_path)
    for repo,name in ((a,'one'),(b,'two')):
        folder=repo/'00-Inbox/AI-Captures'; folder.mkdir(parents=True)
        (folder/(name+'.md')).write_text('Synthetic immutable event '+name+'\n')
    assert brain_git.sync(a)=='synced'
    assert brain_git.sync(b)=='synced'
    assert brain_git.sync(a)=='synced'
    for repo in (a,b): assert len(list((repo/'00-Inbox/AI-Captures').glob('*.md')))==2

def test_conflicting_offline_edits_pause_without_conflict_files(brain,tmp_path):
    a,b=pair(tmp_path)
    (a/'01-Projects/Initial.md').write_text('Device A edited the same line.\n')
    (b/'01-Projects/Initial.md').write_text('Device B edited the same line.\n')
    assert brain_git.sync(a)=='synced'
    assert brain_git.sync(b).startswith('paused: conflicting')
    assert (b/'01-Projects/Initial.md').read_text()=='Device B edited the same line.\n'
    assert not git(b,'status','--porcelain').strip()

def test_user_staging_is_preserved(brain,tmp_path):
    a,b=pair(tmp_path); (a/'01-Projects/User.md').write_text('User staged note.\n'); git(a,'add','.')
    assert brain_git.sync(a).startswith('paused: existing staged')
    assert 'User.md' in git(a,'diff','--cached','--name-only')


def test_notes_without_a_remote_are_off_not_a_fault(brain,tmp_path):
    """A single-device brain: a plain folder, or a local repository with no origin."""
    vault,_=brain
    assert brain_git.sync(vault).startswith('off: ')
    local=tmp_path/'local';local.mkdir()
    subprocess.run(['git','init','-q','-b','main',str(local)],check=True)
    (local/'01-Projects').mkdir();(local/'01-Projects/Note.md').write_text('Synthetic note.\n',encoding='utf-8')
    (local/'unrelated.txt').write_text('Synthetic other file.\n',encoding='utf-8')
    subprocess.run(['git','-C',str(local),'add','unrelated.txt'],check=True)
    # Off means untouched: nothing is committed, the owner's staging stays, and no Git identity is needed.
    assert brain_git.sync(local).startswith('off: ')
    assert subprocess.run(['git','-C',str(local),'rev-parse','--verify','-q','HEAD'],capture_output=True).returncode!=0
    assert git(local,'diff','--cached','--name-only').split()==['unrelated.txt']


def test_notes_inside_another_repository_are_never_synchronized_into_it(brain,tmp_path):
    """A notes folder without its own repository must not be published through an enclosing one."""
    a,_=pair(tmp_path)
    nested=a/'Second-Brain';(nested/'01-Projects').mkdir(parents=True)
    (nested/'01-Projects/Private.md').write_text('Synthetic private note.\n',encoding='utf-8')
    head=git(a,'rev-parse','HEAD')
    assert brain_git.sync(nested).startswith('off: the notes folder is inside another Git repository')
    assert git(a,'rev-parse','HEAD')==head and not git(a,'diff','--cached','--name-only')
