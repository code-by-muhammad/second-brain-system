"""Deterministic maintenance: link captures, flag stale memory, serialize safe Git sync."""
import json, os, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from filelock import FileLock
import brain_store as s
import brain_settings as cfg
from brain_wiki import meta as parse_meta
from brain_markdown import frontmatter

def save_status(status):
    path=s.STATE/'maintenance-status.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(status,indent=2),encoding='utf-8')
    os.replace(temp,path)

def health_status(path,review_limit=20):
    """Keep the full diagnostic file local while bounding the MCP response."""
    try: status=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError): return {'status':'needs attention','error':'Maintenance status is unavailable or invalid.'}
    if not isinstance(status,dict): return {'status':'needs attention','error':'Maintenance status is invalid.'}
    review=status.get('review',[])
    if not isinstance(review,list): review=[]
    status['review_count']=len(review)
    status['review_truncated']=len(review)>review_limit
    bounded=[]
    for row in review[:review_limit]:
        if not isinstance(row,dict): continue
        item=dict(row); reasons=item.get('reasons',[])
        if isinstance(reasons,list):
            item['reason_count']=len(reasons)
            item['reasons_truncated']=len(reasons)>5
            item['reasons']=reasons[:5]
        bounded.append(item)
    status['review']=bounded
    return status

def add_review(status,path,reasons):
    status['review'].append({'path':path.relative_to(s.VAULT).as_posix(),'reasons':reasons})

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
        try: source=s.safe_path(src)
        except (ValueError,TypeError):
            stale.append('invalid source path metadata; original preserved')
            continue
        recorded=hashes.get(src)
        if recorded is not None and (not isinstance(recorded,str) or not re.fullmatch('[a-fA-F0-9]{64}',recorded)):
            stale.append('invalid SHA256 metadata for source: '+src)
            continue
        if not source.is_file(): stale.append('missing source: '+src)
        elif recorded and s.digest(source.read_bytes())!=recorded.lower(): stale.append('source changed: '+src)
        elif not recorded and source.stat().st_mtime>path.stat().st_mtime:
            stale.append('source may have changed; no recorded hash: '+src)
    if time.time()-path.stat().st_mtime>30*86400: stale.append('review older than 30 days')
    return stale

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
            try: raw=p.read_text(encoding='utf-8'); meta=parse_meta(raw)
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
            day=when.strftime('%Y-%m-%d-%A')
            target=s.safe_path('05-Daily/'+day+'.md'); marker='<!-- brain-capture:'+meta['event_id']+' -->'
            if target.exists(): body=target.read_text(encoding='utf-8')
            else:
                body=(s.VAULT/'Templates/daily-note.md').read_text(encoding='utf-8')
                replacements={
                    '<% tp.file.creation_date("YYYY-MM-DD") %>':when.strftime('%Y-%m-%d'),
                    '<% moment(tp.file.title,\'YYYY-MM-DD\').format("dddd, MMMM DD, YYYY") %>':when.strftime('%A, %B %d, %Y'),
                    '<% moment(tp.file.title,\'YYYY-MM-DD\').subtract(1,\'d\').format("YYYY-MM-DD-dddd") %>':(when-timedelta(days=1)).strftime('%Y-%m-%d-%A'),
                    '<% moment(tp.file.title,\'YYYY-MM-DD\').add(1,\'d\').format("YYYY-MM-DD-dddd") %>':(when+timedelta(days=1)).strftime('%Y-%m-%d-%A'),
                    '<% tp.file.title %>':when.strftime('%Y-%m-%d')}
                for a,b in replacements.items(): body=body.replace(a,b)
            if marker in body: continue
            title=next((line[2:] for line in raw.splitlines() if line.startswith('# ')),'AI milestone')
            link=p.relative_to(s.VAULT).as_posix()[:-3]
            entry=f'\n### {when:%H:%M} — {title}\n{marker}\n- **What / Why / How / Result / Next:** [[{link}|Saved milestone and evidence]]\n- **Source:** {meta["source_app"]}\n- **Links:** [[Maps/AI-Context]]\n'
            if '## Work Log' not in body: body+='\n## Work Log\n'
            start=body.index('## Work Log')+len('## Work Log'); end=body.find('\n## ',start)
            if end<0: body=body.rstrip()+'\n'+entry
            else: body=body[:end].rstrip()+'\n'+entry+'\n'+body[end:]
            s._write(target,body,'daily_capture_link'); status['captures_linked']+=1
        for p in (s.VAULT/'02-Areas/AI-Memory').rglob('*.md'):
            try: raw=p.read_text(encoding='utf-8'); meta=frontmatter(raw,strict=True)
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
                status['git']='needs attention: '+str(e)
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
