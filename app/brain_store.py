"""Local, file-first write boundary. No shell/network execution or arbitrary writes."""
from __future__ import annotations
import hashlib, json, os, re, tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from filelock import FileLock
import yaml
from brain_markdown import frontmatter
import brain_settings as cfg

ROOT, VAULT, STATE = cfg.ROOT, cfg.VAULT, cfg.STATE
STATE.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo(cfg.DEVICE.get('timezone', 'Asia/Karachi'))
SECRET = re.compile(r'(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{15,})|(?:api[_ -]?key|password|access[_ -]?token|secret)\s*[:=]\s*["\x27]?[A-Za-z0-9_+/=-]{16,})')

def stamp(): return datetime.now(TZ).isoformat(timespec='seconds')
def digest(data): return hashlib.sha256(data).hexdigest()
def safe_path(relative: str) -> Path:
    if not isinstance(relative,str) or not relative or len(relative)>400:
        raise ValueError('Provide a vault-relative Markdown path.')
    rel=relative.replace('\\','/')
    parts=rel.split('/')
    if rel.startswith('/') or ':' in rel or any(p in ('','.','..') or p.startswith('.') or p.endswith((' ','.')) for p in parts):
        raise ValueError('Unsafe or hidden path refused.')
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))}
    if any(p.split('.')[0].upper() in reserved or any(c in p for c in '<>"|?*') for p in parts):
        raise ValueError('Windows device names and special paths are refused.')
    if not rel.lower().endswith('.md'):
        raise ValueError('Only Markdown notes are accessible.')
    p=VAULT
    for part in parts:
        p=p/part
        if p.is_symlink() or os.path.isjunction(p):
            raise ValueError('Linked/reparse paths are refused.')
    if not p.resolve().is_relative_to(VAULT):
        raise ValueError('Path leaves the vault.')
    return p

def clean_text(value: str, max_len=30000):
    if not isinstance(value,str) or len(value)>max_len or '\x00' in value:
        raise ValueError('Text exceeds allowed size or contains invalid characters.')
    if SECRET.search(value):
        raise ValueError('Possible credential detected; redact it before saving.')
    return value

def note_text(meta, body):
    return '---\n'+yaml.safe_dump(meta,sort_keys=False,allow_unicode=True)+'---\n\n'+body.rstrip()+'\n'

def read(relative: str, start_line=1, max_lines=160):
    p=safe_path(relative)
    raw=p.read_bytes()
    if len(raw)>2_000_000: raise ValueError('Note exceeds 2 MB; select a smaller source.')
    lines=raw.decode('utf-8-sig').splitlines()
    start_line=max(1,int(start_line)); max_lines=max(1,min(int(max_lines),250))
    return {'path':p.relative_to(VAULT).as_posix(),'sha256':digest(raw),'total_lines':len(lines),
            'content':'\n'.join(f'{i+start_line}: {v}' for i,v in enumerate(lines[start_line-1:start_line-1+max_lines]))}

def _write(p:Path,text:str,kind:str,expected_sha256:str|None=None):
    raw=text.encode('utf-8')
    old=p.read_bytes() if p.exists() else None
    before=digest(old) if old is not None else None
    if expected_sha256 is not None and before!=expected_sha256:
        raise ValueError('Concurrent change detected. Read the current note and merge before retrying.')
    if old==raw: return {'path':p.relative_to(VAULT).as_posix(),'status':'unchanged','sha256':before}
    if old is not None:
        archive=STATE/'write-backups'/before
        archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists(): archive.write_bytes(old)
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.brain-',suffix='.tmp',dir=p.parent)
    replaced=False
    try:
        try:
            with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,p)
            replaced=True
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        entry={'at':stamp(),'action':kind,'path':p.relative_to(VAULT).as_posix(),'before':before,'after':digest(raw)}
        with (STATE/'audit.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(entry)+'\n')
        return {'path':entry['path'],'status':'saved','sha256':entry['after'],'saved_at':entry['at']}
    except Exception as error:
        # Preserve the original exception and the known replacement outcome.
        # Callers may retain recovery intent; this does not authorize replay.
        if replaced: error._brain_note_replaced=True
        raise


def capture(event_id:str,title:str,outcome:str,source_app:str,source_reference:str,
            project:str='',decisions:list[str]|None=None,next_steps:list[str]|None=None,
            evidence_paths:list[str]|None=None):
    for x in (event_id,title,outcome,source_app,source_reference,project): clean_text(x)
    if not event_id or len(event_id)>180 or not title.strip() or not outcome.strip() or not source_reference.strip():
        raise ValueError('Stable event_id, title, outcome and source_reference are required.')
    if len(title)>180 or len(project)>180: raise ValueError('Title/project too long.')
    decisions=decisions or []; next_steps=next_steps or []; evidence_paths=evidence_paths or []
    if max(len(decisions),len(next_steps),len(evidence_paths))>30: raise ValueError('Too many list items.')
    for x in decisions+next_steps: clean_text(x,2000)
    for x in evidence_paths:
        if not safe_path(x).is_file(): raise ValueError('Evidence note does not exist: '+x)
    payload=dict(event_id=event_id,title=title,outcome=outcome,source_app=source_app,source_reference=source_reference,
                 project=project,decisions=decisions,next_steps=next_steps,evidence_paths=evidence_paths)
    fingerprint=digest(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode())
    key=digest(event_id.encode())[:24]
    p=safe_path(f'00-Inbox/AI-Captures/{key}.md')
    with FileLock(str(STATE/'writer.lock'),timeout=20):
        if p.exists():
            previous=frontmatter(p.read_text(encoding='utf-8'),strict=True)
            if previous.get('payload_sha256')==fingerprint:
                return {'path':p.relative_to(VAULT).as_posix(),'status':'duplicate','sha256':digest(p.read_bytes())}
            raise ValueError('event_id already saved with different content. Use a new milestone ID; evidence is immutable.')
        now=stamp()
        meta={'date':now[:10],'type':'work-log','tags':['ai-capture'],'event_id':event_id,
              'source_app':source_app,'source_reference':source_reference,'project':project,
              'captured_at':now,'payload_sha256':fingerprint,'status':'captured'}
        body=f'# {title}\n\n## Outcome\n{outcome}\n\n## Decisions\n'+('\n'.join('- '+x for x in decisions) or '- None recorded.')
        body+='\n\n## Next steps\n'+('\n'.join('- [ ] '+x for x in next_steps) or '- None recorded.')
        body+='\n\n## Evidence\n'+('\n'.join('- [['+x[:-3]+']]' for x in evidence_paths) or '- Session summary; verify claims against the source conversation.')
        body+='\n\n## Links\n- [[Maps/AI-Context]]'
        result=_write(p,note_text(meta,body),'capture')
        return result

def update_current(path:str,title:str,content:str,source_paths:list[str],expected_sha256:str|None=None):
    """Only derived pages, with source evidence and compare-and-swap for existing files."""
    cfg.require_maintainer()
    p=safe_path(path); rel=p.relative_to(VAULT).as_posix()
    if rel.casefold().startswith('02-areas/ai-memory/wiki/'):
        raise ValueError('Wiki pages and writer Control are compiler-owned; use the wiki worker or an explicit handoff/migration.')
    allowed=rel.startswith('02-Areas/AI-Memory/') or (rel.startswith('01-Projects/') and rel.endswith('/AI-Memory.md')) or rel=='Maps/AI-Context.md'
    if not allowed: raise ValueError('Only designated derived memory pages may be updated.')
    clean_text(title,180); clean_text(content)
    if not content.strip() or not source_paths or len(source_paths)>30: raise ValueError('Content and 1–30 source notes are required.')
    for src in source_paths:
        if src==rel or not safe_path(src).is_file(): raise ValueError('A distinct existing source is required.')
    with FileLock(str(STATE/'writer.lock'),timeout=20):
        if p.exists() and expected_sha256 is None: raise ValueError('Read current SHA256 before updating an existing page.')
        now=stamp()
        meta={'date':now[:10],'type':'note','tags':['ai-memory','derived'],'updated_at':now,
              'sources':source_paths,'source_hashes':{src:digest(safe_path(src).read_bytes()) for src in source_paths},
              'status':'current','maintained_by':'second-brain'}
        body=f'# {title}\n\n{content}\n\n## Sources\n'+'\n'.join('- [['+s[:-3]+']]' for s in source_paths)
        return _write(p,note_text(meta,body),'update_current',expected_sha256)

def fresh_search(query:str,limit:int=6):
    clean_text(query,1000)
    stop={'the','and','for','with','was','were','where','what','which','how','does','this','that','from','about','built','project'}
    terms=[t for t in re.findall(r'[\w]+',query.lower()) if len(t)>2 and t not in stop]
    found=[]
    for p in VAULT.rglob('*.md',case_sensitive=False):
        try:
            rel=p.relative_to(VAULT).as_posix(); safe_path(rel)
            if p.stat().st_size>2_000_000: continue
            text=p.read_text(encoding='utf-8-sig'); low=text.lower()
            title=p.stem.lower()
            score=sum(1 for t in terms if t in low)+sum(5 for t in terms if t in title)
            if score:
                line=next((i+1 for i,l in enumerate(text.splitlines()) if any(t in l.lower() for t in terms)),1)
                found.append({'path':rel,'line':line,'matches':score,'mtime':p.stat().st_mtime})
        except (OSError,ValueError,UnicodeError): continue
    return sorted(found,key=lambda x:(-x['matches'],-x['mtime']))[:max(1,min(limit,15))]
