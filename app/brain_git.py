"""Serialize index/worktree changes; let captures proceed during Git network I/O."""
import codecs
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from filelock import FileLock
import brain_store as s
import brain_settings as cfg
from brain_markdown import bounded_file_bytes

FOLDERS={'00-Inbox','01-Projects','02-Areas','03-Resources','04-Archive','05-Daily','06-People','07-Meetings','Maps','Templates'}
MIN_GIT=(2,38)
NOTE_CHARACTER_LIMIT=2_000_000
TREE_PATH_BYTES_LIMIT=64*1024*1024
ARCHIVE_BLOCK=65536
ARCHIVE_BYTES_LIMIT=64*1024*1024
_ARCHIVE=re.compile(r'02-Areas/AI-Memory/Wiki/Log-Archives/([a-f0-9]{64})\.md\Z')
_WHITESPACE=re.compile(r'\s+')

class GitSyncError(RuntimeError):
    """Controlled diagnostics: never include subprocess output or note contents."""

def _hold_path():
    path=s.STATE/'git-sync-hold.json'
    if any(p.is_symlink() or os.path.isjunction(p) for p in (path,*path.parents)):
        raise GitSyncError('paused: linked Git review state; preserve it for attended recovery.')
    return path

def _read_hold(vault):
    path=_hold_path()
    if not path.exists():return None
    try:
        raw=bounded_file_bytes(path,65536);value=json.loads(raw)
        fields={'format','root','vault','branch','upstream','pre_head','expected_head','expected_tree','reason','created'}
        if (not isinstance(value,dict) or set(value)!=fields or type(value['format']) is not int or value['format']!=1
                or value['root']!=str(s.ROOT.resolve()) or value['vault']!=str(Path(vault).resolve())
                or value['reason']!='checkout-pending-verification' or type(value['created']) not in (int,float)
                or not isinstance(value['branch'],str) or not value['branch'].startswith('refs/remotes/origin/')
                or len(value['branch'])>1024):raise ValueError()
        for key in ('upstream','pre_head','expected_tree'):
            if not isinstance(value[key],str) or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',value[key]):raise ValueError()
        if value['expected_head'] not in ('',value['upstream']):raise ValueError()
        return value,hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError,UnicodeError,RecursionError):
        raise GitSyncError('paused: Git review state is invalid or unreadable; preserve it for attended recovery.') from None

def _require_no_hold(vault):
    if _read_hold(vault) is not None:
        raise GitSyncError('paused: Git checkout requires attended review; use git status, inspect the commit and git review before retrying sync.')

def _require_no_grafts(git,vault):
    # Legacy grafts change ancestry traversal independently of replacement refs.
    # Preserve them, but do not qualify automatic publication through that view.
    value=git('rev-parse','--git-path','info/grafts').stdout.strip()
    path=Path(value)
    if not path.is_absolute():path=Path(vault)/path
    if path.exists() or any(p.is_symlink() or os.path.isjunction(p) for p in (path,*path.parents)):
        raise GitSyncError('paused: legacy Git grafts require attended repository recovery; original grafts preserved.')

def _atomic_hold(path,value):
    # Private state, not notes. The completed rename is process-crash safe;
    # power-loss durability depends on the filesystem and Git ref durability.
    _hold_path()
    if path.parent!=s.STATE or path.is_symlink() or os.path.isjunction(path):
        raise GitSyncError('paused: unsafe Git review state path.')
    descriptor,name=tempfile.mkstemp(prefix='.git-review-',suffix='.tmp',dir=s.STATE)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            os.chmod(name,0o600)
            json.dump(value,stream,ensure_ascii=True,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    except OSError:
        raise GitSyncError('paused: Git review evidence could not be persisted; no automatic publication is permitted.') from None
    finally:Path(name).unlink(missing_ok=True)

def review(vault=None,*,head=None,tree=None,hold_sha256=None):
    """Inspect or explicitly accept a reviewed checkout; never merge or push."""
    vault=Path(vault or s.VAULT)
    def git(*args,check=True):
        result=_run_git(['git','--literal-pathspecs','-C',str(vault),*args],capture_output=True,text=True,encoding='utf-8',timeout=90,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
        if check and result.returncode:raise GitSyncError('Git review inspection failed; hold preserved.')
        return result
    s.STATE.mkdir(parents=True,exist_ok=True)
    with FileLock(str(s.STATE/'maintenance.lock'),timeout=1),FileLock(str(s.STATE/'git-sync.lock'),timeout=1),FileLock(str(s.STATE/'writer.lock'),timeout=30):
        held=_read_hold(vault)
        if held is None:
            if any(v is not None for v in (head,tree,hold_sha256)):raise GitSyncError('No Git review hold exists; nothing accepted.')
            return {'status':'clear'}
        value,digest=held
        _require_no_grafts(git,vault)
        live_head=git('rev-parse','HEAD').stdout.strip();live_tree=git('rev-parse',live_head+'^{tree}').stdout.strip()
        if all(v is None for v in (head,tree,hold_sha256)):
            return {'status':'needs review','hold_sha256':digest,'head':live_head,'tree':live_tree,'hold':value}
        cfg.write_admission()
        if (head!=live_head or tree!=live_tree or hold_sha256!=digest
                or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',head or '')
                or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',tree or '')
                or git('status','--porcelain','--untracked-files=all').stdout.strip()
                or git('rev-parse','--verify','-q','MERGE_HEAD',check=False).returncode==0
                or git('merge-base','--is-ancestor',value['pre_head'],head,check=False).returncode!=0):
            raise GitSyncError('Git review fingerprint, ancestry or clean-worktree check failed; hold preserved.')
        receipt={'format':1,'hold_sha256':digest,'hold':value,'reviewed_head':head,'reviewed_tree':tree,'reviewed_at':time.time()}
        destination=s.STATE/('git-reviewed-'+os.urandom(16).hex()+'.json')
        _atomic_hold(destination,receipt)
        if _read_hold(vault)!=held:raise GitSyncError('Git review state changed; hold preserved.')
        # Receipt is durable before removing the blocker. A crash before removal
        # leaves the hold; the next normal maintenance pass alone owns publishing.
        _hold_path().unlink()
        return {'status':'review accepted','head':head,'tree':tree,'receipt':str(destination),'published':False}

_CLEANUP_REPORTER=ContextVar('git_cleanup_reporter',default=None)

def git_executable(env=None):
    """Search absolute PATH entries only; never execute a vault/cwd shim."""
    environment=os.environ if env is None else env
    forbidden=(s.ROOT,s.STATE,s.VAULT,Path.cwd(),Path(sys.executable).parent)
    for entry in environment.get('PATH','').split(os.pathsep):
        folder=Path(entry.strip('"'))
        if not entry or not folder.is_absolute():continue
        candidate=folder/('git.exe' if os.name=='nt' else 'git')
        if not candidate.is_file() or any(item.is_symlink() or os.path.isjunction(item) for item in (candidate,*candidate.parents)):continue
        selected=candidate.resolve()
        if any(selected.is_relative_to(Path(parent).resolve()) for parent in forbidden):continue
        if os.name!='nt' and not os.access(selected,os.X_OK):continue
        return str(selected)
    raise FileNotFoundError('Install Git outside the vault, runtime, current directory and Python folder; use an absolute host PATH entry.')

@contextmanager
def cleanup_reporting(callback):
    """Keep cleanup attention scoped to the maintenance call that owns its state."""
    token=_CLEANUP_REPORTER.set(callback)
    try:yield
    finally:_CLEANUP_REPORTER.reset(token)

def _run_git(command,**kwargs):
    command=list(command)
    command[1:1]=['-c','core.fsmonitor=false']
    env=kwargs.get('env')
    kwargs['env']=dict(os.environ if env is None else env)
    kwargs['env'].update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='0',GIT_NO_REPLACE_OBJECTS='1')
    kwargs['env'].pop('GIT_GRAFT_FILE',None)
    command[0]=git_executable(kwargs['env'])
    if os.name=='nt':
        from brain_windows_process import run
        try:return run(command,on_cleanup_wait=_CLEANUP_REPORTER.get(),**kwargs)
        except FileNotFoundError:raise
        except OSError as exc:
            raise GitSyncError('Windows Git process containment could not be verified; local changes preserved. Inspect the local Windows process environment before retrying.') from exc
    return subprocess.run(command,**kwargs)

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
    remaining=expected_size; carry=''; whitespace=False; final_cr=b''; characters=0; decoded_cr=False
    def screen(text):
        nonlocal carry,whitespace,characters,decoded_cr
        if '\x00' in text: raise ValueError('characters')
        if not text: return
        # Match Path.read_text(encoding='utf-8')'s universal-newline character
        # count, including a BOM as one character. Carry CR across decoder blocks.
        characters+=len(text)-text.count('\r\n')-int(decoded_cr and text.startswith('\n'))
        decoded_cr=text.endswith('\r')
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
    return {'raw_sha256':sha,'lf_sha256':lf_hash.hexdigest(),'bytes':expected_size,'characters':characters}

def _note_locator(relative):
    """Screen the raw path before JSON escaping can obscure SECRET boundaries."""
    try:
        s.clean_text(relative,400)
        relative.encode('utf-8')
        return json.dumps(relative,ensure_ascii=True)
    except (ValueError,UnicodeError):
        digest=hashlib.sha256(relative.encode('utf-8','surrogatepass')).hexdigest()
        return '[withheld locator; path_sha256='+digest+']'

def _windows_note_path(relative):
    """Screen raw components for shared Windows checkout; never normalize them."""
    for part in relative.split('/'):
        if not part or part in ('.','..') or part.endswith((' ','.')) or any(ord(c)<32 or c in '<>:"|?*\\' for c in part):
            return False
        stem=part.split('.',1)[0].rstrip(' ').upper()
        if stem in {'CON','PRN','AUX','NUL','CONIN$','CONOUT$','COM0','LPT0'} or (
                len(stem)==4 and stem[:3] in {'COM','LPT'} and stem[3] in '123456789¹²³'):
            return False
    return True

def _windows_path_pause(relative):
    kind='Markdown path' if relative.split('/')[0] in FOLDERS and relative.lower().endswith('.md') else 'Repository path'
    return ('paused: '+kind+' is not compatible with Windows: '+_note_locator(relative)+
        '. Rename it explicitly on a device that can open the original before syncing; original and staging preserved.')

def _windows_checkout_path(vault,relative):
    # Native Git uses UTF-16 units; existing directories need no mkdir.
    root=Path(os.path.abspath(vault))
    if root.drive.startswith('\\\\?\\') and len(root.drive)==6 and root.drive[5]==':':
        root=Path(str(root)[4:])
    target=Path(os.path.abspath(root/relative))
    def units(path): return len(str(path).encode('utf-16-le'))//2
    if units(target)>=260: return False
    for parent in target.parents:
        if parent==root.parent: break
        if units(parent)>=248 and not parent.is_dir(): return False
    return True

def _windows_longpaths(vault):
    try:
        result=_run_git(['git','--literal-pathspecs','-C',str(vault),'config','--bool','--get','core.longpaths'],
                        capture_output=True,text=True,encoding='utf-8',timeout=90)
        value=result.stdout.strip()
        if result.returncode==1 and not value: return False
        if result.returncode==0 and value in ('true','false'): return value=='true'
        raise ValueError('configuration')
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise GitSyncError('Windows Git long-path setting could not be verified; local changes preserved.') from None

def _windows_checkout_pause(relative):
    return ("paused: Incoming path exceeds this Windows device's Git checkout limits: "+_note_locator(relative)+
        '. Use a shorter vault path or explicitly choose core.longpaths for this repository, then retry; local commits and staging preserved.')

def _incoming_note_path(vault,oid):
    """Screen a pinned fetched tree before checkout, with bounded decoded input."""
    try:
        deadline=time.monotonic()+90
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise GitSyncError('paused: Checkout preflight exceeded its work budget; originals preserved.')
            return value
        def collision_key(relative):return unicodedata.normalize('NFC',relative).casefold()
        if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',oid): raise ValueError('identity')
        env=os.environ.copy()
        env.update(GIT_ALLOW_PROTOCOL='',GIT_NO_LAZY_FETCH='1',GIT_TERMINAL_PROMPT='0',
                   GIT_NO_REPLACE_OBJECTS='1',GIT_OPTIONAL_LOCKS='0')
        command=['git','--literal-pathspecs','-C',str(vault),'ls-tree','-r','-z',oid]
        with tempfile.TemporaryFile(mode='w+b') as stream:
            result=_run_git(command,stdout=stream,stderr=subprocess.DEVNULL,env=env,timeout=remaining())
            if result.returncode or os.fstat(stream.fileno()).st_size>TREE_PATH_BYTES_LIMIT: raise ValueError('tree')
            stream.seek(0);pending=b'';longpaths=None;folded={};prefix_bytes=0;checkout_key_bytes=0;leaves={};directories={};checked=set()
            for chunk in iter(lambda:stream.read(65536),b''):
                rows=(pending+chunk).split(b'\0');pending=rows.pop()
                if len(pending)>1_048_576: raise ValueError('path')
                for raw in rows:
                    remaining()
                    if len(raw)>1_048_576: raise ValueError('path')
                    metadata,raw=raw.split(b'\t',1)
                    mode,kind,object_id=metadata.split()
                    if mode not in (b'100644',b'100755') or kind!=b'blob':
                        raise GitSyncError('paused: Incoming tree contains a linked or submodule entry; originals preserved, no checkout performed.')
                    if not re.fullmatch(rb'[a-f0-9]{40}|[a-f0-9]{64}',object_id):raise ValueError('tree object identity')
                    relative=raw.decode('utf-8')
                    parts=relative.split('/')
                    if len(parts)>128 or len(raw)>32768:raise ValueError('tree path work limit')
                    if not _windows_note_path(relative): return relative
                    for length in range(1,len(parts)+1):
                        prefix='/'.join(parts[:length])
                        if prefix not in checked:
                            checked.add(prefix)
                            target=vault.joinpath(*parts[:length])
                            if target.is_symlink() or os.path.isjunction(target):
                                raise GitSyncError('paused: Incoming checkout crosses a linked local path '+_note_locator(prefix)+'; originals preserved.')
                    canonical=relative
                    if os.name=='nt' and '~' in relative:
                        canonical=(vault/relative).resolve().relative_to(vault.resolve()).as_posix()
                    if len(canonical.encode('utf-8'))>32768 or len(canonical.split('/'))>128:raise ValueError('checkout alias work limit')
                    key=collision_key(canonical)
                    if key in leaves:
                        raise GitSyncError('paused: Conservative candidate-tree collision between '+_note_locator(leaves[key])+' and '+_note_locator(relative)+'; originals preserved.')
                    checkout_key_bytes+=len(key.encode('utf-8'))
                    leaves[key]=relative
                    canonical_parts=canonical.split('/')
                    for length in range(1,len(canonical_parts)):
                        parent_key=collision_key('/'.join(canonical_parts[:length]))
                        if parent_key not in directories:
                            checkout_key_bytes+=len(parent_key.encode('utf-8'));directories[parent_key]=relative
                    if checkout_key_bytes>TREE_PATH_BYTES_LIMIT or len(leaves)+len(directories)>500_000:raise ValueError('checkout key work limit')
                    for length in range(1,len(parts)+1):
                        prefix='/'.join(parts[:length]);key=prefix.casefold()
                        if key in folded and folded[key]!=prefix:
                            raise GitSyncError('paused: Conservative case-insensitive candidate-tree collision: '+_note_locator(folded[key])+' and '+_note_locator(prefix)+'. Originals and local staging preserved; no checkout performed.')
                        if key not in folded:
                            prefix_bytes+=len(prefix.encode('utf-8'))+len(key.encode('utf-8'))
                            if prefix_bytes>TREE_PATH_BYTES_LIMIT or len(folded)>=500_000:raise ValueError('case tree work limit')
                            folded[key]=prefix
                    if os.name=='nt' and longpaths is not True and not _windows_checkout_path(vault,relative):
                        if longpaths is None: longpaths=_windows_longpaths(vault)
                        if not longpaths: return relative
            if pending: raise ValueError('tree')
        # Git's true-merge path may overwrite ignored files even with
        # --no-overwrite-ignore. Screen every untracked path before checkout.
        command=['git','--literal-pathspecs','-C',str(vault),'ls-files','--others','-z']
        with tempfile.TemporaryFile(mode='w+b') as stream:
            result=_run_git(command,stdout=stream,stderr=subprocess.DEVNULL,env=env,timeout=remaining())
            if result.returncode or os.fstat(stream.fileno()).st_size>TREE_PATH_BYTES_LIMIT:
                raise GitSyncError('paused: Untracked checkout inventory exceeds its bound or is unavailable; originals preserved.')
            stream.seek(0);pending=b'';count=0
            for chunk in iter(lambda:stream.read(65536),b''):
                rows=(pending+chunk).split(b'\0');pending=rows.pop()
                if len(pending)>32768:raise GitSyncError('paused: Untracked checkout path exceeds its bound; originals preserved.')
                for raw in rows:
                    remaining();count+=1
                    if count>500_000 or len(raw)>32768:raise GitSyncError('paused: Untracked checkout inventory exceeds its bound; originals preserved.')
                    relative=raw.decode('utf-8').rstrip('/');parts=relative.split('/')
                    if not relative or len(parts)>128:raise ValueError('untracked path')
                    key=collision_key(relative);incoming=leaves.get(key) or directories.get(key)
                    if incoming is None:
                        incoming=next((leaves[collision_key('/'.join(parts[:length]))] for length in range(1,len(parts))
                                       if collision_key('/'.join(parts[:length])) in leaves),None)
                    if incoming is not None:
                        raise GitSyncError('paused: Untracked local path '+_note_locator(relative)+' blocks incoming checkout '+_note_locator(incoming)+
                                           '; originals, HEAD and index preserved. Review this collision explicitly.')
            if pending:raise ValueError('untracked framing')
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise GitSyncError('Incoming note paths could not be verified locally; local changes preserved. Inspect the local repository and its bounded tree-name output before retrying.') from None

def _worktree_case_path(vault,relative,directories):
    """Find case-only aliases from actual directory entries, without note reads.

    resolve() can retain an obsolete spelling on a case-insensitive filesystem.
    Inspect only parents of tracked notes, caching shared directory listings.
    Missing paths on a case-sensitive filesystem retain normal deletion review.
    Links are left to the existing linked-note guard, never traversed here.
    """
    directory=vault; actual=[]
    for part in relative.split('/'):
        if directory.is_symlink() or os.path.isjunction(directory): return None
        try:
            if directory not in directories:
                with os.scandir(directory) as entries:
                    directories[directory]=frozenset(entry.name for entry in entries)
            names=directories[directory]
        except OSError:
            raise GitSyncError('Tracked note path casing could not be inspected; local changes preserved for review.') from None
        name=part if part in names else None
        if name is None:
            expected=directory/part
            for candidate in names:
                if candidate.casefold()!=part.casefold(): continue
                target=directory/candidate
                if target.is_symlink() or os.path.isjunction(target): continue
                try:
                    if expected.samefile(target): name=candidate; break
                except OSError: continue
            if name is None: return None
        directory=directory/name
        if directory.is_symlink() or os.path.isjunction(directory): return None
        actual.append(name)
    spelling='/'.join(actual)
    return spelling if directory.is_file() and spelling!=relative else None

def _archive_failure(relative,reason):
    descriptions={'credentials':'possible credential; redact before syncing',
                  'characters':'invalid characters',
                  'encoding':'invalid UTF-8',
                  'hash':'oversized archive bytes do not match its SHA256 filename; review the edit and assign a content-matching SHA256 filename before syncing',
                  'changed':'archive changed during validation',
                  'size':'archive exceeds the 64 MiB sync limit',
                  'staged':'staged archive differs beyond permitted CRLF-to-LF conversion',
                  'read':'archive could not be read',
                  'blob':'staged archive could not be verified locally'}
    return GitSyncError('Note '+_note_locator(relative)+' blocks sync: '+descriptions.get(reason,'archive validation failed')+'. Original preserved.')

def _archive_file(relative,target):
    try:
        with target.open('rb') as stream:
            size=os.fstat(stream.fileno()).st_size
            result=_scan_archive(stream,size)
            if os.fstat(stream.fileno()).st_size!=size: raise ValueError('changed')
            if result['characters']>NOTE_CHARACTER_LIMIT and result['raw_sha256']!=_ARCHIVE.fullmatch(relative).group(1):
                raise ValueError('hash')
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
        result=_run_git([*command,'-s',oid],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=90)
        if result.returncode or len(result.stdout)>32 or not re.fullmatch(rb'[0-9]+\n?',result.stdout): raise ValueError('blob')
        size=int(result.stdout)
        if size>raw['bytes'] or size>ARCHIVE_BYTES_LIMIT: raise ValueError('staged')
        with tempfile.TemporaryFile(mode='w+b') as stream:
            result=_run_git([*command,'blob',oid],stdout=stream,stderr=subprocess.DEVNULL,env=env,timeout=90)
            if result.returncode or os.fstat(stream.fileno()).st_size!=size: raise ValueError('blob')
            stream.seek(0); staged=_scan_archive(stream,size)
        if staged['raw_sha256'] not in (raw['raw_sha256'],raw['lf_sha256']): raise ValueError('staged')
    except ValueError as exc: raise _archive_failure(relative,str(exc)) from None
    except (OSError,subprocess.TimeoutExpired): raise _archive_failure(relative,'blob') from None

def _ordinary_blob(vault,relative,entry):
    """Bound staged content before allocating decoded text; retain character semantics.

    A permitted UTF-8 character needs at most four raw bytes. Universal newline
    conversion cannot increase that bound (CRLF uses two bytes for one character).
    Size-check the pinned, local object before spooling at most that bounded raw
    content to disk; read only the decoded capacity plus one sentinel character.
    """
    try:
        mode,oid,stage=entry.split()
        if mode not in ('100644','100755') or stage!='0' or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',oid):
            raise ValueError('blob')
        env=os.environ.copy()
        env.update(GIT_ALLOW_PROTOCOL='',GIT_NO_LAZY_FETCH='1',GIT_TERMINAL_PROMPT='0',GIT_NO_REPLACE_OBJECTS='1')
        command=['git','--literal-pathspecs','-C',str(vault),'cat-file']
        result=_run_git([*command,'-s',oid],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=90)
        if result.returncode or len(result.stdout)>32 or not re.fullmatch(rb'[0-9]+\n?',result.stdout): raise ValueError('blob')
        size=int(result.stdout)
        if size>4*NOTE_CHARACTER_LIMIT: raise ValueError('size')
        with tempfile.TemporaryFile(mode='w+b') as stream:
            result=_run_git([*command,'blob',oid],stdout=stream,stderr=subprocess.DEVNULL,env=env,timeout=90)
            if result.returncode or os.fstat(stream.fileno()).st_size!=size: raise ValueError('blob')
            stream.seek(0)
            with io.TextIOWrapper(stream,encoding='utf-8',newline=None) as decoded:
                return decoded.read(NOTE_CHARACTER_LIMIT+1)
    except UnicodeError:
        raise GitSyncError('Note '+_note_locator(relative)+' cannot be read as UTF-8; original preserved.') from None
    except ValueError as exc:
        reason='invalid characters or note exceeds the allowed size' if str(exc)=='size' else 'staged note could not be verified locally'
        raise GitSyncError('Note '+_note_locator(relative)+' blocks sync: '+reason+'. Original preserved.') from None
    except (OSError,subprocess.TimeoutExpired):
        raise GitSyncError('Note '+_note_locator(relative)+' blocks sync: staged note could not be verified locally. Original preserved.') from None


def sync(vault=None):
    vault=Path(vault or s.VAULT)
    def git(*args,check=True,input=None):
        try:
            p=_run_git(['git','--literal-pathspecs','-C',str(vault),*args],input=input,
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

    def casing_pause(check_windows=True):
        directories={}
        for rel in index_entries():
            if check_windows and not _windows_note_path(rel): return _windows_path_pause(rel)
            if rel.split('/')[0] not in FOLDERS or not rel.lower().endswith('.md'): continue
            actual=_worktree_case_path(vault,rel,directories)
            if actual is not None:
                return ('paused: tracked Markdown path casing differs from disk: '+_note_locator(rel)+
                        ' -> '+_note_locator(actual)+'. Reconcile with an explicit Git rename and owner commit before syncing.')

    def undo_owned_staging(owned):
        # A hook or external Git client may stage other files (or a new version of
        # the same file). Only remove entries still identical to our own snapshot.
        current=index_entries()
        unchanged=[path for path,entry in owned.items() if current.get(path)==entry]
        if unchanged:
            git('reset','--pathspec-from-file=-','--pathspec-file-nul',input=paths_input(unchanged))

    def validate_note(rel,text):
        try: s.clean_text(text,NOTE_CHARACTER_LIMIT)
        except ValueError as exc:
            reason='possible credential; redact before syncing' if s.SECRET.search(text) else 'invalid characters or note exceeds the allowed size'
            raise GitSyncError('Note '+_note_locator(rel)+' blocks sync: '+reason+'. Original preserved.') from exc

    s.STATE.mkdir(parents=True,exist_ok=True)
    # Network stages are serialized with other syncs, but not with local captures.
    with FileLock(str(s.STATE/'git-sync.lock'),timeout=1):
        version=git('--version').stdout
        match=re.search(r'\b(\d+)\.(\d+)',version)
        if not match or tuple(map(int,match.groups()))<MIN_GIT:
            raise GitSyncError('Git >= 2.38 is required for safe merge-tree preflight; upgrade Git before synchronization.')
        with FileLock(str(s.STATE/'writer.lock'),timeout=30):
            _require_no_hold(vault)
            cfg.write_admission()
            _require_no_grafts(git,vault)
            if staged_paths(): return 'paused: existing staged changes preserved'
            if pause:=casing_pause(check_windows=False): return pause
            candidates=git('ls-files','--modified','--others','--exclude-standard','-z').stdout.split('\0')
            eligible=[]; archives={}
            for rel in dict.fromkeys(candidates):
                if not rel or rel.split('/')[0] not in FOLDERS or not rel.lower().endswith('.md'): continue
                if not _windows_note_path(rel):
                    if rel in index_entries():
                        return ('paused: tracked Markdown edit uses a Windows-incompatible path: '+_note_locator(rel)+
                            '. Preserve the edit and pending captures. If another device has committed the shared rename, explicitly commit this tracked edit under its current name, then sync. Otherwise make one explicit owner rename on a device that can open the original. Do not choose different replacement names on each peer; original and staging preserved.')
                    return ('paused: Markdown path is not compatible with Windows: '+_note_locator(rel)+
                        '. Rename it explicitly on a device that can open the original before syncing; original and staging preserved.')
                target=vault/rel
                for parent in (target,*target.parents):
                    if parent==vault.parent: break
                    if parent.is_symlink() or os.path.isjunction(parent):
                        raise GitSyncError('Linked note '+_note_locator(rel)+' is not synchronized automatically.')
                if not target.resolve().is_relative_to(vault.resolve()):
                    raise GitSyncError('Note path leaves vault; synchronization refused.')
                if not target.is_file(): continue
                if _archive_path(rel): archives[rel]=_archive_file(rel,target)
                else:
                    try:
                        with target.open('r',encoding='utf-8') as stream: text=stream.read(NOTE_CHARACTER_LIMIT+1)
                    except (OSError,UnicodeError) as exc:
                        raise GitSyncError('Note '+_note_locator(rel)+' cannot be read as UTF-8; original preserved.') from None
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
                        else:
                            if rel not in owned: raise GitSyncError('Note '+_note_locator(rel)+' blocks sync: staged note could not be verified locally. Original preserved.')
                            validate_note(rel,_ordinary_blob(vault,rel,owned[rel]))
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
            if git('status','--porcelain','--untracked-files=all').stdout.strip():
                return 'paused: other local changes or source deletions require review'

        git('fetch','origin')
        with FileLock(str(s.STATE/'writer.lock'),timeout=30):
            _require_no_hold(vault)
            cfg.write_admission()
            _require_no_grafts(git,vault)
            # Captures can arrive while fetch waits. Never merge over new files or
            # user staging; leave them for the next normal maintenance cycle.
            if git('status','--porcelain','--untracked-files=all').stdout.strip():
                return 'paused: local changes arrived during fetch; retry next cycle'
            upstream_ref=git('rev-parse','--symbolic-full-name','@{upstream}').stdout.strip()
            if not upstream_ref.startswith('refs/remotes/origin/'):
                raise GitSyncError('Configure an origin tracking branch before automatic synchronization; local changes preserved.')
            upstream=git('rev-parse',upstream_ref).stdout.strip()
            ahead,behind=map(int,git('rev-list','--left-right','--count','HEAD...'+upstream).stdout.split())
            if behind and (invalid:=_incoming_note_path(vault,upstream)) is not None:
                return _windows_path_pause(invalid) if not _windows_note_path(invalid) else _windows_checkout_pause(invalid)
            if ahead and behind:
                result=git('merge-tree','--write-tree','HEAD',upstream,check=False)
                if result.returncode==1:
                    return 'paused: conflicting device edits; originals preserved for review'
                if result.returncode:
                    raise GitSyncError('Git merge-tree preflight failed (exit '+str(result.returncode)+'); Git >= 2.38 is required. Inspect repository health; originals preserved.')
                tree=result.stdout.splitlines()[0].strip()
                if (invalid:=_incoming_note_path(vault,tree)) is not None:
                    return _windows_path_pause(invalid) if not _windows_note_path(invalid) else _windows_checkout_pause(invalid)
                expected_tree=tree
            elif behind:
                expected_tree=git('rev-parse',upstream+'^{tree}').stdout.strip()
            else:expected_tree=None
            if expected_tree:
                pre_head=git('rev-parse','HEAD').stdout.strip()
                intent={'format':1,'root':str(s.ROOT.resolve()),'vault':str(vault.resolve()),'branch':upstream_ref,
                        'upstream':upstream,'pre_head':pre_head,'expected_head':'' if ahead else upstream,
                        'expected_tree':expected_tree,'reason':'checkout-pending-verification','created':time.time()}
                _atomic_hold(_hold_path(),intent)
                held=_read_hold(vault)
                if ahead:git('merge','--strategy=ort','--no-overwrite-ignore','--no-edit',upstream)
                else:git('merge','--no-overwrite-ignore','--ff-only',upstream)
                actual_head=git('rev-parse','HEAD').stdout.strip()
                if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',actual_head):raise GitSyncError('paused: invalid checkout commit; review hold retained.')
                _require_no_grafts(git,vault)
                parents=git('rev-list','--parents','-n','1',actual_head).stdout.split()[1:]
                if (git('rev-parse',actual_head+'^{tree}').stdout.strip()!=expected_tree
                        or (not ahead and actual_head!=upstream)
                        or (ahead and parents!=[pre_head,upstream])
                        or git('status','--porcelain','--untracked-files=all').stdout.strip()
                        or git('rev-parse','--verify','-q','MERGE_HEAD',check=False).returncode==0):
                    raise GitSyncError('paused: checked-out merge differs from its screened result; durable Git review hold retained.')
                if _read_hold(vault)!=held:raise GitSyncError('paused: Git checkout evidence changed; review required.')
                _hold_path().unlink()
            if pause:=casing_pause(): return pause
            head=actual_head if expected_tree else git('rev-parse','HEAD').stdout.strip()
            branch=upstream_ref.removeprefix('refs/remotes/origin/')
        # Pin the verified commit; unrelated external commits made during network
        # waits cannot accidentally be pushed by this cycle.
        git('push','origin',head+':refs/heads/'+branch)
        return 'synced'
