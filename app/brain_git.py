"""Serialize index/worktree changes; let captures proceed during Git network I/O."""
import codecs
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from filelock import FileLock
import brain_store as s

FOLDERS={'00-Inbox','01-Projects','02-Areas','03-Resources','04-Archive','05-Daily','06-People','07-Meetings','Maps','Templates'}
MIN_GIT=(2,38)
ARCHIVE_BLOCK=65536
ARCHIVE_BYTES_LIMIT=64*1024*1024
_ARCHIVE=re.compile(r'02-Areas/AI-Memory/Wiki/Log-Archives/([a-f0-9]{64})\.md\Z')
_WHITESPACE=re.compile(r'\s+')

class GitSyncError(RuntimeError):
    """Controlled diagnostics: never include subprocess output or note contents."""

def _archive_path(relative):
    return bool(isinstance(relative,str) and _ARCHIVE.fullmatch(relative))

def _scan_archive(stream,expected_size,expected_sha=None):
    """Screen exact bytes with fixed memory, including across chunk boundaries.

    SECRET contains unbounded whitespace runs. Collapse those runs only in the
    scan input, carrying their state between blocks; never alter stored bytes.
    Every current SECRET alternative then has a positive witness shorter than
    128 characters (including its minimum token length). Retest this invariant
    whenever SECRET changes. Conservative additional spacing matches are safe.
    """
    if type(expected_size) is not int or not 0<=expected_size<=ARCHIVE_BYTES_LIMIT:
        raise ValueError('size')
    raw_hash=hashlib.sha256(); lf_hash=hashlib.sha256()
    decoder=codecs.getincrementaldecoder('utf-8')('strict')
    remaining=expected_size; carry=''; whitespace=False; final_cr=b''
    def screen(text):
        nonlocal carry,whitespace
        if '\x00' in text: raise ValueError('characters')
        if not text: return
        collapsed=_WHITESPACE.sub(' ',text)
        if whitespace and collapsed.startswith(' '): collapsed=collapsed[1:]
        whitespace=text[-1].isspace()
        scanned=carry+collapsed
        if s.SECRET.search(scanned): raise ValueError('credentials')
        carry=scanned[-128:]
    try:
        while remaining:
            chunk=stream.read(min(ARCHIVE_BLOCK,remaining))
            if not chunk or len(chunk)>min(ARCHIVE_BLOCK,remaining): raise ValueError('changed')
            remaining-=len(chunk); raw_hash.update(chunk)
            screen(decoder.decode(chunk,final=False))
            joined=final_cr+chunk; final_cr=b''
            if joined.endswith(b'\r'): joined=joined[:-1]; final_cr=b'\r'
            lf_hash.update(joined.replace(b'\r\n',b'\n'))
        if stream.read(1): raise ValueError('changed')
        screen(decoder.decode(b'',final=True)); lf_hash.update(final_cr)
    except UnicodeError: raise ValueError('encoding') from None
    sha=raw_hash.hexdigest()
    if expected_sha is not None and sha!=expected_sha: raise ValueError('hash')
    return {'raw_sha256':sha,'lf_sha256':lf_hash.hexdigest(),'bytes':expected_size}

def _archive_failure(relative,reason):
    descriptions={'credentials':'possible credential; redact before syncing',
                  'characters':'invalid characters',
                  'encoding':'invalid UTF-8',
                  'hash':'archive bytes do not match its SHA256 filename',
                  'changed':'archive changed during validation',
                  'size':'archive exceeds the 64 MiB sync limit',
                  'staged':'staged archive differs beyond permitted CRLF-to-LF conversion',
                  'read':'archive could not be read',
                  'blob':'staged archive could not be verified locally'}
    return GitSyncError('Note '+json.dumps(relative,ensure_ascii=True)+' blocks sync: '+descriptions.get(reason,'archive validation failed')+'. Original preserved.')

def _archive_file(relative,target):
    try:
        with target.open('rb') as stream:
            size=os.fstat(stream.fileno()).st_size
            result=_scan_archive(stream,size,_ARCHIVE.fullmatch(relative).group(1))
            if os.fstat(stream.fileno()).st_size!=size: raise ValueError('changed')
            return result
    except ValueError as exc: raise _archive_failure(relative,str(exc)) from None
    except OSError: raise _archive_failure(relative,'read') from None

def _archive_blob(vault,relative,entry,raw):
    """Read the pinned owned index blob, never a filter or a lazily fetched object."""
    try:
        mode,oid,stage=entry.split()
        if mode not in ('100644','100755') or stage!='0' or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',oid):
            raise ValueError('blob')
        env=os.environ.copy()
        env.update(GIT_ALLOW_PROTOCOL='',GIT_NO_LAZY_FETCH='1',GIT_TERMINAL_PROMPT='0',GIT_NO_REPLACE_OBJECTS='1')
        command=['git','--literal-pathspecs','-C',str(vault),'cat-file']
        result=subprocess.run([*command,'-s',oid],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=90)
        if result.returncode or len(result.stdout)>32 or not re.fullmatch(rb'[0-9]+\n?',result.stdout): raise ValueError('blob')
        size=int(result.stdout)
        if size>raw['bytes'] or size>ARCHIVE_BYTES_LIMIT: raise ValueError('staged')
        with tempfile.TemporaryFile(mode='w+b') as stream:
            result=subprocess.run([*command,'blob',oid],stdout=stream,stderr=subprocess.DEVNULL,env=env,timeout=90)
            if result.returncode or os.fstat(stream.fileno()).st_size!=size: raise ValueError('blob')
            stream.seek(0); staged=_scan_archive(stream,size)
        if staged['raw_sha256'] not in (raw['raw_sha256'],raw['lf_sha256']): raise ValueError('staged')
    except ValueError as exc: raise _archive_failure(relative,str(exc)) from None
    except (OSError,subprocess.TimeoutExpired): raise _archive_failure(relative,'blob') from None

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
            eligible=[]; archives={}
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
                if _archive_path(rel): archives[rel]=_archive_file(rel,target)
                else:
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
                    for rel in eligible:
                        if rel in archives:
                            if rel not in owned: raise _archive_failure(rel,'blob')
                            _archive_blob(vault,rel,owned[rel],archives[rel])
                        else: validate_note(rel,git('show',':'+rel).stdout)
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
