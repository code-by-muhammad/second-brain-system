import json
from types import SimpleNamespace
import pytest
from test_integrity import updater

def test_upgrade_waits_for_the_expected_running_version(tmp_path,monkeypatch):
    replies=iter([SimpleNamespace(returncode=1,stdout=''),SimpleNamespace(returncode=0,stdout=json.dumps({'status':'running','version':'old'})),
                  SimpleNamespace(returncode=0,stdout=json.dumps({'status':'running','version':'new'}))])
    monkeypatch.setattr(updater.subprocess,'run',lambda *a,**kw:next(replies))
    monkeypatch.setattr(updater.time,'sleep',lambda *a:None)
    assert updater.wait_health('python',tmp_path,'new')['version']=='new'

def test_upgrade_readiness_has_a_bounded_deadline(tmp_path):
    with pytest.raises(RuntimeError,match='startup deadline'): updater.wait_health('python',tmp_path,'new',timeout=0)
