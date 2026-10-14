"""Deterministic maintenance: link captures, flag stale memory, serialize safe Git sync."""
import json, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from filelock import FileLock
import yaml
import brain_store as s
import brain_settings as cfg

def git(*args):
    p=subprocess.run(['git','-C',str(s.VAULT),*args],capture_output=True,text=True,encoding='utf-8',timeout=90)
    if p.returncode: raise RuntimeError('Git '+args[0]+' failed; inspect repository manually. '+p.stderr[:250])
    return p.stdout

def maintenance(sync=True):
    status={'at':s.stamp(),'captures_linked':0,'review':[],'git':'not requested'}
    with FileLock(str(s.STATE/'maintenance.lock'),timeout=1):
      with FileLock(str(s.STATE/'writer.lock'),timeout=30):
        captures=sorted((s.VAULT/'00-Inbox/AI-Captures').glob('*.md')) if cfg.ROLE=='maintainer' else []
        if captures: cfg.require_maintainer()
        for p in captures:
            raw=p.read_text(encoding='utf-8'); meta=yaml.safe_load(raw.split('---',2)[1])
            when=datetime.fromisoformat(meta['captured_at']); day=when.strftime('%Y-%m-%d-%A')
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
            entry=f'\n### {when:%H:%M} â€” {title}\n{marker}\n- **What / Why / How / Result / Next:** [[{link}|Saved milestone and evidence]]\n- **Source:** {meta["source_app"]}\n- **Links:** [[Maps/AI-Context]]\n'
            if '## Work Log' not in body: body+='\n## Work Log\n'
            start=body.index('## Work Log')+len('## Work Log'); end=body.find('\n## ',start)
            if end<0: body=body.rstrip()+'\n'+entry
            else: body=body[:end].rstrip()+'\n'+entry+'\n'+body[end:]
            s._write(target,body,'daily_capture_link'); status['captures_linked']+=1
        for p in (s.VAULT/'02-Areas/AI-Memory').rglob('*.md'):
            raw=p.read_text(encoding='utf-8'); meta=yaml.safe_load(raw.split('---',2)[1]) or {}
            stale=[]
            for src in meta.get('sources',[]):
                source=s.safe_path(src)
                if not source.exists(): stale.append('missing source: '+src)
                elif meta.get('source_hashes',{}).get(src) and s.digest(source.read_bytes())!=meta['source_hashes'][src]: stale.append('source changed: '+src)
                elif not meta.get('source_hashes') and source.stat().st_mtime>p.stat().st_mtime: stale.append('source may have changed; no recorded hash: '+src)
            if time.time()-p.stat().st_mtime>30*86400: stale.append('review older than 30 days')
            if stale: status['review'].append({'path':p.relative_to(s.VAULT).as_posix(),'reasons':stale})
        # Collapse redundant operational logs only; evidence notes and revision backups stay intact.
        for p in (s.ROOT/'logs').glob('service-*.log'):
            if p.stat().st_size>5_000_000:
                import gzip
                dest=p.with_suffix(p.suffix+'.'+datetime.now().strftime('%Y%m%d%H%M%S')+'.gz')
                with gzip.open(dest,'wb') as f: f.write(p.read_bytes())
                p.write_bytes(b'')
        if sync:
            try:
                from brain_git import sync as sync_git
                status['git']=sync_git()
            except Exception as e: status['git']='needs attention: '+str(e)
        (s.STATE/'maintenance-status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    return status

if __name__=='__main__': print(json.dumps(maintenance('--no-sync' not in sys.argv)))
