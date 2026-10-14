import importlib.util, json, hashlib
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('updater',Path(__file__).resolve().parents[1]/'update.py')
updater=importlib.util.module_from_spec(spec); spec.loader.exec_module(updater)

def test_release_hash_mismatch_blocks_execution(tmp_path):
    content=tmp_path/'install.py'; content.write_text('original')
    for name in ('update.py','pyproject.toml','uv.lock'): (tmp_path/name).write_text('synthetic fixture')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    (tmp_path/'release-manifest.json').write_text(json.dumps({'version':'0.1.0','sha256':hashes}))
    assert updater.verify(tmp_path)['version']=='0.1.0'
    content.write_text('modified')
    with pytest.raises(ValueError,match='integrity mismatch'): updater.verify(tmp_path)

def test_manifest_cannot_leave_release_directory(tmp_path):
    hashes={name:'bad' for name in ('install.py','update.py','pyproject.toml','uv.lock','../secret')}
    for name in ('install.py','update.py','pyproject.toml','uv.lock'): (tmp_path/name).write_text('fixture')
    hashes.update({name:hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() for name in hashes if name!='../secret'})
    (tmp_path/'release-manifest.json').write_text(json.dumps({'version':'0.1.0','sha256':hashes}))
    with pytest.raises(ValueError,match='Unsafe'): updater.verify(tmp_path)
