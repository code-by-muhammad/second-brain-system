"""Real synthetic files: staging must not overwrite intervening owner saves."""
import os
from pathlib import Path
import pytest
import brain_store as s

@pytest.mark.parametrize('exists',[False,True])
def test_owner_edit_during_staging_is_preserved(brain,monkeypatch,exists):
    vault,state=brain;path=s.safe_path('02-Areas/AI-Memory/Staging.md');path.parent.mkdir(parents=True)
    old=b'Original derived bytes.'
    if exists:path.write_bytes(old)
    owner=b'Owner save during staging.';tracked=set();create=s.tempfile.mkstemp;fsync=s.os.fsync;fired=[]
    def staging(*args,**kwargs):
        fd,name=create(*args,**kwargs)
        if kwargs.get('prefix')=='.brain-':tracked.add(fd)
        return fd,name
    def flush(fd):
        fsync(fd)
        if fd in tracked and not fired:fired.append(True);path.write_bytes(owner)
    monkeypatch.setattr(s.tempfile,'mkstemp',staging);monkeypatch.setattr(s.os,'fsync',flush)
    with pytest.raises(ValueError,match='Concurrent change'):s._write(path,'Planned replacement','test',s.digest(old) if exists else None)
    assert fired and path.read_bytes()==owner and not (state/'audit.jsonl').exists()
    if exists:assert (state/'write-backups'/s.digest(old)).read_bytes()==old

def test_truncated_existing_backup_is_reported_without_overwrite(brain):
    vault,state=brain;path=s.safe_path('02-Areas/AI-Memory/Backup.md');path.parent.mkdir(parents=True);old=b'Complete original.';path.write_bytes(old)
    backup=state/'write-backups'/s.digest(old);backup.parent.mkdir();backup.write_bytes(b'Incomplete')
    with pytest.raises(ValueError,match='backup integrity'):s._write(path,'New derived text','test',s.digest(old))
    assert path.read_bytes()==old and backup.read_bytes()==b'Incomplete' and not (state/'audit.jsonl').exists()
