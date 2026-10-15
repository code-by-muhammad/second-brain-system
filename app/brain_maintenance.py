"""Deterministic maintenance: link captures, flag stale memory, serialize safe Git sync."""
import codecs, hashlib, html, json, os, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from filelock import FileLock
import brain_store as s
import brain_settings as cfg
from brain_wiki import meta as parse_meta, _read_derived, _current_hash, BASE as WIKI_BASE
from brain_markdown import frontmatter

def save_status(status):
    path=s.STATE/'maintenance-status.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(status,indent=2),encoding='utf-8')
    os.replace(temp,path)

LEGACY_WITHHELD_PATH='[withheld; inspect private maintenance status]'
LEGACY_WITHHELD_DETAIL='Review detail withheld; inspect private maintenance status.'
WITHHELD_PATH='[withheld; see DEVICE-SETUP local path-SHA lookup]'
WITHHELD_DETAIL='Review detail withheld; use the documented local path-SHA lookup in DEVICE-SETUP and inspect matching notes locally.'

def _diagnostic_text(value,limit=2000):
    if not isinstance(value,str): raise ValueError('Invalid diagnostic text.')
    value.encode('utf-8'); return s.clean_text(value,limit)

def _diagnostic_path(value):
    _diagnostic_text(value,400); return s.safe_path(value)

def _review_path(row):
    value=row.get('path')
    # A prior safe withheld locator keeps its useful opaque hash. Neither this
    # hash nor cached attributes authorize a read or a write.
    if value in (WITHHELD_PATH,LEGACY_WITHHELD_PATH) and row.get('path_withheld') is True and isinstance(row.get('path_sha256'),str) and re.fullmatch('[a-f0-9]{64}',row['path_sha256']):
        return {'path':WITHHELD_PATH,'path_withheld':True,'path_sha256':row['path_sha256']}
    try: _diagnostic_path(value); return {'path':value}
    except (ValueError,TypeError,UnicodeError,OSError):
        result={'path':WITHHELD_PATH,'path_withheld':True}
        if isinstance(value,str): result['path_sha256']=s.digest(value.encode('utf-8',errors='surrogatepass'))
        return result

def _review_reasons(reasons,limit=None):
    if not isinstance(reasons,list): return [WITHHELD_DETAIL]
    result=[]
    for reason in reasons if limit is None else reasons[:limit]:
        try: result.append(_diagnostic_text(WITHHELD_DETAIL if reason==LEGACY_WITHHELD_DETAIL else reason))
        except (ValueError,UnicodeError): result.append(WITHHELD_DETAIL)
    return result

def health_status(path,review_limit=20):
    """Screen even old cached fields; keep the full local receipt unchanged."""
    try: status=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError): return {'status':'needs attention','error':'Maintenance status is unavailable or invalid.'}
    if not isinstance(status,dict): return {'status':'needs attention','error':'Maintenance status is invalid.'}
    public={}
    for key in ('at','status','stage','git','aggregation','error_type','error'):
        if key not in status: continue
        try: public[key]=_diagnostic_text(WITHHELD_DETAIL if status[key]==LEGACY_WITHHELD_DETAIL else status[key])
        except (ValueError,UnicodeError): public[key]='needs attention' if key=='status' else WITHHELD_DETAIL
    for key in ('captures_linked','completed_private_artifacts_removed'):
        value=status.get(key)
        if type(value) is int and 0<=value<=2**63-1: public[key]=value
    review=status.get('review',[])
    if not isinstance(review,list): review=[]
    public['review_count']=len(review); public['review_truncated']=len(review)>review_limit
    bounded=[]
    for row in review[:review_limit]:
        if not isinstance(row,dict): continue
        item=_review_path(row); reasons=row.get('reasons',[])
        item.update(reason_count=len(reasons) if isinstance(reasons,list) else None,
                    reasons_truncated=isinstance(reasons,list) and len(reasons)>5,
                    reasons=_review_reasons(reasons,5))
        bounded.append(item)
    public['review']=bounded
    return public

def add_review(status,path,reasons):
    try: relative=path.relative_to(s.VAULT).as_posix()
    except ValueError: relative=path.as_posix()
    row=_review_path({'path':relative}); row['reasons']=_review_reasons(reasons)
    status['review'].append(row)

def _source_representations(path):
    """Compiler-equivalent newline/BOM digests, with constant-sized buffers."""
    hashes={(newline,bom):hashlib.sha256(b'\xef\xbb\xbf' if bom else b'')
            for newline in ('\n','\r\n','\r') for bom in (False,True)}
    decoder=codecs.getincrementaldecoder('utf-8-sig')('strict'); pending=''
    def update(text):
        normalized=text.replace('\r\n','\n').replace('\r','\n')
        for newline in ('\n','\r\n','\r'):
            raw=normalized.replace('\n',newline).encode('utf-8')
            hashes[newline,False].update(raw); hashes[newline,True].update(raw)
    with path.open('rb') as stream:
        expected=os.fstat(stream.fileno()).st_size; remaining=expected
        while remaining:
            chunk=stream.read(min(65536,remaining))
            if not chunk: raise ValueError('Source changed during a bounded streaming read.')
            remaining-=len(chunk)
            text=pending+decoder.decode(chunk,final=False); pending=''
            if text.endswith('\r'): text,pending=text[:-1],'\r'
            update(text)
        if stream.read(1) or os.fstat(stream.fileno()).st_size!=expected:
            raise ValueError('Source grew during a bounded streaming read.')
        update(pending+decoder.decode(b'',final=True))
    return {digest.hexdigest() for digest in hashes.values()}

def _source_hash_matches(path,recorded):
    return _current_hash(path)==recorded or recorded in _source_representations(path)

def review_sources(path,meta):
    stale=[]
    sources=meta.get('sources')
    if sources is None: sources=[]
    if not isinstance(sources,list):
        stale.append('invalid sources metadata; expected a list of vault-relative Markdown paths')
        sources=[]
    hashes=meta.get('source_hashes')
    if hashes is None: hashes={}
    if not isinstance(hashes,dict):
        stale.append('invalid source_hashes metadata; expected a path-to-SHA256 mapping')
        hashes={}
    for src in sources:
        try: source=_diagnostic_path(src)
        except (ValueError,TypeError,UnicodeError,OSError):
            stale.append('invalid source path metadata; original preserved')
            continue
        recorded=hashes.get(src)
        if recorded is not None and (not isinstance(recorded,str) or not re.fullmatch('[a-fA-F0-9]{64}',recorded)):
            stale.append('invalid SHA256 metadata for source: '+src)
            continue
        if not source.is_file(): stale.append('missing source: '+src)
        elif recorded and not _source_hash_matches(source,recorded.lower()): stale.append('source changed: '+src)
        elif not recorded and source.stat().st_mtime>path.stat().st_mtime:
            stale.append('source may have changed; no recorded hash: '+src)
    if time.time()-path.stat().st_mtime>30*86400: stale.append('review older than 30 days')
    return stale

def _compiler_history(path):
    relative=path.relative_to(s.VAULT).as_posix().casefold()
    base=WIKI_BASE.casefold()
    return relative==base+'log.md' or relative.startswith(base+'log-archives/')

def _capture_link(path):
    relative=path.relative_to(s.VAULT).as_posix()
    _diagnostic_path(relative)
    if any(c in relative for c in '[]#^') or any(ord(c)<32 or c in '\x7f\u2028\u2029' for c in relative):
        raise ValueError('Capture path cannot be represented as a safe Markdown link.')
    return relative[:-3]

def _display_text(value,limit):
    _diagnostic_text(value,limit)
    return html.escape(' '.join(value.split()),quote=True)

class _DailyIdentityReview(ValueError):
    """An owner-edited or ambiguous marker must not cause another entry."""

def _daily_link(p,raw,meta,when):
    link=_capture_link(p)
    day=when.strftime('%Y-%m-%d-%A')
    target=s.safe_path('05-Daily/'+day+'.md'); marker='<!-- brain-capture:'+meta['event_id']+' -->'
    before=None
    if target.exists():
        raw_daily,body=_read_derived(target); before=s.digest(raw_daily)
    else:
        _,body=_read_derived(s.safe_path('Templates/daily-note.md'))
        replacements={
            '<% tp.file.creation_date("YYYY-MM-DD") %>':when.strftime('%Y-%m-%d'),
            '<% moment(tp.file.title,\'YYYY-MM-DD\').format("dddd, MMMM DD, YYYY") %>':when.strftime('%A, %B %d, %Y'),
            '<% moment(tp.file.title,\'YYYY-MM-DD\').subtract(1,\'d\').format("YYYY-MM-DD-dddd") %>':(when-timedelta(days=1)).strftime('%Y-%m-%d-%A'),
            '<% moment(tp.file.title,\'YYYY-MM-DD\').add(1,\'d\').format("YYYY-MM-DD-dddd") %>':(when+timedelta(days=1)).strftime('%Y-%m-%d-%A'),
            '<% tp.file.title %>':when.strftime('%Y-%m-%d')}
        for a,b in replacements.items(): body=body.replace(a,b)
    identity='- **What / Why / How / Result / Next:** [['+link+'|Saved milestone and evidence]]'
    lines=body.split('\n')
    markers=[i for i,line in enumerate(lines) if line==marker]
    if markers:
        if len(markers)==1 and markers[0]+1<len(lines) and lines[markers[0]+1]==identity: return False
        raise _DailyIdentityReview('Daily capture identity needs owner reconciliation.')
    title=_display_text(next((line[2:] for line in raw.splitlines() if line.startswith('# ')),'AI milestone'),180)
    source=_display_text(meta['source_app'],2000)
    entry=f'\n### {when:%H:%M} — {title}\n{marker}\n{identity}\n- **Source:** {source}\n- **Links:** [[Maps/AI-Context]]\n'
    if '## Work Log' not in body: body+='\n## Work Log\n'
    start=body.index('## Work Log')+len('## Work Log'); end=body.find('\n## ',start)
    if end<0: body=body.rstrip()+'\n'+entry
    else: body=body[:end].rstrip()+'\n'+entry+'\n'+body[end:]
    if len(body.encode('utf-8'))>2_000_000: raise ValueError('Daily note exceeds the bounded note-size contract.')
    if _current_hash(target)!=before: raise ValueError('Daily note changed during linking; owner content preserved.')
    s._write(target,body,'daily_capture_link',expected_sha256=before)
    return True

def maintenance(sync=True):
    status={'at':s.stamp(),'status':'running','stage':'starting','captures_linked':0,'review':[],'git':'pending' if sync else 'not requested'}
    with FileLock(str(s.STATE/'maintenance.lock'),timeout=1):
      save_status(status)
      try:
       with FileLock(str(s.STATE/'writer.lock'),timeout=30):
        status['stage']='local maintenance'
        can_maintain=cfg.ROLE=='maintainer'
        if can_maintain:
            try: cfg.require_maintainer()
            except PermissionError:
                can_maintain=False
                status['aggregation']='skipped: shared ownership belongs to another device; Git synchronization remains enabled'
        else: status['aggregation']='skipped: capture/search device'
        captures=sorted((s.VAULT/'00-Inbox/AI-Captures').glob('*.md')) if can_maintain else []
        for p in captures:
            try: _capture_link(p)
            except (OSError,UnicodeError,ValueError):
                add_review(status,p,['capture path cannot be safely linked; original preserved']); continue
            try: _,raw=_read_derived(p); meta=parse_meta(raw)
            except (OSError,UnicodeError,ValueError):
                add_review(status,p,['capture could not be read; original preserved']); continue
            if not isinstance(meta,dict) or not all(meta.get(key) for key in ('captured_at','event_id','source_app')):
                add_review(status,p,['capture metadata missing; original preserved']); continue
            if not all(isinstance(meta[key],str) for key in ('event_id','source_app')):
                add_review(status,p,['invalid capture metadata types; original preserved']); continue
            try:
                s.clean_text(meta['event_id'],180); s.clean_text(meta['source_app'],2000)
                if any(x in meta['event_id'] for x in ('\r','\n','-->')): raise ValueError('Invalid capture marker.')
            except ValueError:
                add_review(status,p,['invalid capture metadata; original preserved']); continue
            try: when=datetime.fromisoformat(str(meta['captured_at']))
            except ValueError:
                add_review(status,p,['invalid capture date; original preserved']); continue
            try:
                if _daily_link(p,raw,meta,when): status['captures_linked']+=1
            except _DailyIdentityReview:
                add_review(status,p,['daily capture marker has an edited, missing or ambiguous identity; owner must reconcile the existing daily entry before retry; owner content preserved'])
            except (OSError,UnicodeError,ValueError):
                add_review(status,p,['daily note or template could not be linked; inspect the daily/template path, UTF-8, write access and concurrent changes; original preserved'])
        for p in (s.VAULT/'02-Areas/AI-Memory').rglob('*.md'):
            # Compiler history has no derived-header review contract. In
            # particular, a legacy raw archive may exceed the note-size cap.
            if _compiler_history(p): continue
            try:
                s.safe_path(p.relative_to(s.VAULT).as_posix())
                _,raw=_read_derived(p); meta=frontmatter(raw,strict=True)
            except (OSError,UnicodeError,ValueError):
                add_review(status,p,['derived metadata could not be read; original preserved']); continue
            if not isinstance(meta,dict) or not meta: continue
            try: stale=review_sources(p,meta)
            except (OSError,UnicodeError,ValueError,TypeError): stale=['source metadata could not be validated; original preserved']
            if stale: add_review(status,p,stale)
        # Collapse redundant operational logs only; evidence notes and revision backups stay intact.
        for p in (s.ROOT/'logs').glob('service-*.log'):
            if p.stat().st_size>5_000_000:
                import gzip
                dest=p.with_suffix(p.suffix+'.'+datetime.now().strftime('%Y%m%d%H%M%S')+'.gz')
                with gzip.open(dest,'wb') as f: f.write(p.read_bytes())
                p.write_bytes(b'')
        from brain_wiki import cleanup
        status['completed_private_artifacts_removed']=cleanup()
       if sync:
            status['stage']='Git synchronization'
            save_status(status)
            try:
                from brain_git import sync as sync_git, GitSyncError
                status['git']=sync_git()
            except GitSyncError as e:
                try: status['git']='needs attention: '+_diagnostic_text(str(e))
                except (ValueError,UnicodeError): status['git']='needs attention: Git synchronization requires local review; diagnostic withheld'
                status['status']='needs attention'
            except Exception as e:
                status['git']='needs attention: Git synchronization failed; inspect local logs ('+type(e).__name__+')'
                status['status']='needs attention'
       if status['status']=='running': status['status']='completed'
       status['stage']='finished'
       save_status(status)
      except Exception as exc:
        status.update(status='needs attention',error_type=type(exc).__name__,error='Maintenance aborted; inspect local logs.')
        if sync: status['git']='not completed: maintenance aborted'
        save_status(status)
        raise
    return status

if __name__=='__main__': print(json.dumps(maintenance('--no-sync' not in sys.argv)))
