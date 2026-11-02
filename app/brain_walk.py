"""Allocation-bounded, caller-prunable filesystem inventory. No runtime imports."""
import os
from pathlib import Path

class WalkLimit(ValueError):pass

def walk(root,*,check=lambda:None,onerror=None,max_entries=500_000,max_path_bytes=64*1024*1024,max_depth=128):
    pending=[(Path(root),0)];count=0;path_bytes=0
    while pending:
        directory,depth=pending.pop();check()
        if depth>max_depth:raise WalkLimit('inventory_depth_limit')
        dirs=[];files=[]
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    check();count+=1;path_bytes+=len(os.fsencode(entry.path))
                    if count>max_entries or path_bytes>max_path_bytes:raise WalkLimit('inventory_entry_limit')
                    (dirs if entry.is_dir(follow_symlinks=False) or entry.is_symlink() and entry.is_dir() else files).append(entry.name)
        except OSError as error:
            if onerror is None:raise
            onerror(error);continue
        yield str(directory),dirs,files
        # Consumers remove ignored/offline/linked directories before this resumes.
        for name in reversed(dirs):pending.append((directory/name,depth+1))
