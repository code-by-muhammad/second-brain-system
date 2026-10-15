"""Serialize index/worktree changes; let captures proceed during Git network I/O."""
import json
import os
import re
import subprocess
from pathlib import Path
from filelock import FileLock
import brain_store as s

FOLDERS={'00-Inbox','01-Projects','02-Areas','03-Resources','04-Archive','05-Daily','06-People','07-Meetings','Maps','Templates'}
MIN_GIT=(2,38)

class GitSyncError(RuntimeError):
    """Controlled diagnostics: never include subprocess output or note contents."""

def sync(vault=None):
    vault=Path(vault or s.VAULT)
    def git(*args,check=True,input=None):
        try:
            p=subprocess.run(['git','--literal-pathspecs','-C',str(vault),*args],input=input,
                             capture_output=True,text=True,encoding='utf-8',timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise GitSyncError('Git '+args[0]+' timed out; local changes preserved. Retry when the service is available.') from exc
        except OSError as exc:
            raise GitSyncError('Git executable unavailable; install Git >= 2.38 and inspect the local service environment.') from exc
        if check and p.returncode:
            raise GitSyncError('Git '+args[0]+' failed (exit '+str(p.returncode)+'); local changes preserved. Inspect repository status.')
        return p

    def staged_paths():
        return set(filter(None,git('diff','--cached','--ita-visible-in-index','--name-only','-z').stdout.split('\0')))

    def index_entries():
        result={}
        for row in git('ls-files','--stage','-z').stdout.split('\0'):
            if row:
                entry,path=row.split('\t',1)
                result[path]=entry
        return result

    def paths_input(paths):
        return ''.join(path+'\0' for path in paths)

    def undo_owned_staging(owned):
        # A hook or external Git client may stage other files (or a new version of
        # the same file). Only remove entries still identical to our own snapshot.
        current=index_entries()
        unchanged=[path for path,entry in owned.items() if current.get(path)==entry]
        if unchanged:
            git('reset','--pathspec-from-file=-','--pathspec-file-nul',input=paths_input(unchanged))

    def validate_note(rel,text):
        try: s.clean_text(text,2_000_000)
        except ValueError as exc:
            reason='possible credential; redact before syncing' if s.SECRET.search(text) else 'invalid characters or note exceeds the allowed size'
            raise GitSyncError('Note '+json.dumps(rel,ensure_ascii=True)+' blocks sync: '+reason+'. Original preserved.') from exc

    s.STATE.mkdir(parents=True,exist_ok=True)
    # Network stages are serialized with other syncs, but not with local captures.
    with FileLock(str(s.STATE/'git-sync.lock'),timeout=1):
        version=git('--version').stdout
        match=re.search(r'\b(\d+)\.(\d+)',version)
        if not match or tuple(map(int,match.groups()))<MIN_GIT:
            raise GitSyncError('Git >= 2.38 is required for safe merge-tree preflight; upgrade Git before synchronization.')
        with FileLock(str(s.STATE/'writer.lock'),timeout=30):
            if staged_paths(): return 'paused: existing staged changes preserved'
            candidates=git('ls-files','--modified','--others','--exclude-standard','-z').stdout.split('\0')
            eligible=[]
            for rel in dict.fromkeys(candidates):
                if not rel or rel.split('/')[0] not in FOLDERS or not rel.lower().endswith('.md'): continue
                target=vault/rel
                for parent in (target,*target.parents):
                    if parent==vault.parent: break
                    if parent.is_symlink() or os.path.isjunction(parent):
                        raise GitSyncError('Linked note '+json.dumps(rel,ensure_ascii=True)+' is not synchronized automatically.')
                if not target.resolve().is_relative_to(vault.resolve()):
                    raise GitSyncError('Note path leaves vault; synchronization refused.')
                if not target.is_file(): continue
                try: text=target.read_text(encoding='utf-8')
                except (OSError,UnicodeError) as exc:
                    raise GitSyncError('Note '+json.dumps(rel,ensure_ascii=True)+' cannot be read as UTF-8; original preserved.') from exc
                validate_note(rel,text)
                eligible.append(rel)
            if eligible:
                # NUL-delimited stdin avoids Windows' command-line size limit and
                # preserves literal Unicode, spaces, brackets and newlines.
                git('add','--pathspec-from-file=-','--pathspec-file-nul',input=paths_input(eligible))
                entries=index_entries()
                owned={rel:entries[rel] for rel in eligible if rel in entries}
                try:
                    for rel in eligible: validate_note(rel,git('show',':'+rel).stdout)
                    changed=staged_paths()
                    if changed-set(owned):
                        raise GitSyncError('Other staged changes arrived during sync; user staging preserved for review.')
                    current=index_entries()
                    if any(current.get(rel)!=entry for rel,entry in owned.items()):
                        raise GitSyncError('Staged note changed during sync; user staging preserved for review.')
                    if changed: git('commit','-m','brain: sync source notes and derived memory')
                except Exception:
                    undo_owned_staging(owned)
                    raise
            if git('status','--porcelain').stdout.strip():
                return 'paused: other local changes or source deletions require review'

        git('fetch','origin')
        with FileLock(str(s.STATE/'writer.lock'),timeout=30):
            # Captures can arrive while fetch waits. Never merge over new files or
            # user staging; leave them for the next normal maintenance cycle.
            if git('status','--porcelain').stdout.strip():
                return 'paused: local changes arrived during fetch; retry next cycle'
            upstream_ref=git('rev-parse','--symbolic-full-name','@{upstream}').stdout.strip()
            if not upstream_ref.startswith('refs/remotes/origin/'):
                raise GitSyncError('Configure an origin tracking branch before automatic synchronization; local changes preserved.')
            upstream=git('rev-parse',upstream_ref).stdout.strip()
            ahead,behind=map(int,git('rev-list','--left-right','--count','HEAD...'+upstream).stdout.split())
            if ahead and behind:
                result=git('merge-tree','--write-tree','HEAD',upstream,check=False)
                if result.returncode==1:
                    return 'paused: conflicting device edits; originals preserved for review'
                if result.returncode:
                    raise GitSyncError('Git merge-tree preflight failed (exit '+str(result.returncode)+'); Git >= 2.38 is required. Inspect repository health; originals preserved.')
                git('merge','--no-edit',upstream)
            elif behind: git('merge','--ff-only',upstream)
            head=git('rev-parse','HEAD').stdout.strip()
            branch=upstream_ref.removeprefix('refs/remotes/origin/')
        # Pin the verified commit; unrelated external commits made during network
        # waits cannot accidentally be pushed by this cycle.
        git('push','origin',head+':refs/heads/'+branch)
        return 'synced'
