import importlib.util, json, hashlib
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('updater',Path(__file__).resolve().parents[1]/'update.py')
updater=importlib.util.module_from_spec(spec); spec.loader.exec_module(updater)

def test_release_hash_mismatch_blocks_execution(tmp_path):
    content=tmp_path/'install.py'; content.write_text('original')
    (tmp_path/'release-manifest.json').write_text(json.dumps({'version':'0.1.0','sha256':{'install.py':hashlib.sha256(content.read_bytes()).hexdigest()}}))
    assert updater.verify(tmp_path)['version']=='0.1.0'
    content.write_text('modified')
    with pytest.raises(ValueError,match='integrity mismatch'): updater.verify(tmp_path)

def test_manifest_cannot_leave_release_directory(tmp_path):
    (tmp_path/'release-manifest.json').write_text(json.dumps({'version':'0.1.0','sha256':{'../secret':'bad'}}))
    with pytest.raises(ValueError,match='Unsafe'): updater.verify(tmp_path)
