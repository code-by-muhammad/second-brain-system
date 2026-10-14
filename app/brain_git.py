"""Serialize locally; merge independent offline edits only after a conflict-free preflight."""
import subprocess
import os
from pathlib import Path
import brain_store as s

FOLDERS={'00-Inbox','01-Projects','02-Areas','03-Resources','04-Archive','05-Daily','06-People','07-Meetings','Maps','Templates'}

def sync(vault=None):
    vault=Path(vault or s.VAULT)
    def git(*args,check=True):
        p=subprocess.run(['git','-C',str(vault),*args],capture_output=True,text=True,encoding='utf-8',timeout=90)
        if check and p.returncode: raise RuntimeError('Git '+args[0]+' failed; local changes preserved. Inspect repository status.')
        return p
    if git('diff','--cached','--name-only').stdout.strip(): return 'paused: existing staged changes preserved'
    candidates=git('ls-files','--modified','--others','--exclude-standard','-z').stdout.split('\x00')
    eligible=[]
    for rel in candidates:
        if not rel or rel.split('/')[0] not in FOLDERS or not rel.lower().endswith('.md'): continue
        target=vault/rel
        for parent in (target,*target.parents):
            if parent==vault.parent: break
            if parent.is_symlink() or os.path.isjunction(parent): raise ValueError('Linked note paths are not synchronized automatically.')
        if not target.resolve().is_relative_to(vault.resolve()): raise ValueError('Note path leaves vault.')
        if not target.is_file(): continue
        s.clean_text(target.read_text(encoding='utf-8'),2_000_000); eligible.append(rel)
    if eligible:
        git('add','--',*eligible)
        try:
            for rel in eligible: s.clean_text(git('show',':'+rel).stdout,2_000_000)
        except Exception:
            git('reset','--',*eligible); raise
        if git('diff','--cached','--name-only').stdout.strip(): git('commit','-m','brain: sync source notes and derived memory')
    if git('status','--porcelain').stdout.strip(): return 'paused: other local changes or source deletions require review'
    git('fetch','origin')
    upstream=git('rev-parse','@{upstream}').stdout.strip()
    ahead,behind=map(int,git('rev-list','--left-right','--count','HEAD...'+upstream).stdout.split())
    if ahead and behind:
        # merge-tree does not modify the working tree or index.
        if git('merge-tree','--write-tree','HEAD',upstream,check=False).returncode: return 'paused: conflicting device edits; originals preserved for review'
        git('merge','--no-edit',upstream)
    elif behind: git('merge','--ff-only',upstream)
    git('push','origin','HEAD')
    return 'synced'
