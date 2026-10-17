import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_integrity import updater

def test_upgrade_waits_for_the_expected_running_version(tmp_path,monkeypatch):
    replies=iter([SimpleNamespace(returncode=1,stdout=''),SimpleNamespace(returncode=0,stdout=json.dumps({'status':'running','version':'old'})),
                  SimpleNamespace(returncode=0,stdout=json.dumps({'status':'running','version':'new'}))])
    monkeypatch.setattr(updater.subprocess,'run',lambda *a,**kw:next(replies))
    monkeypatch.setattr(updater.time,'sleep',lambda *a:None)
    assert updater.wait_health('python',tmp_path,'new')['version']=='new'

def test_upgrade_readiness_has_a_bounded_deadline(tmp_path,monkeypatch):
    with pytest.raises(RuntimeError,match='startup deadline'): updater.wait_health('python',tmp_path,'new',timeout=0)
    root=tmp_path/'runtime'; (root/'state').mkdir(parents=True)
    home=tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setenv('APPDATA',str(home/'AppData/Roaming'))
    release=root/'releases/v0.1.1'; release.mkdir(parents=True)
    previous=tmp_path/'old-release'; previous.mkdir()
    (root/'device.json').write_text(json.dumps({'release_root':str(previous),'python':'old-python','version':'0.1.0','vault':str(tmp_path/'vault'),'role':'maintainer'}))
    monkeypatch.setattr(updater.sys,'argv',['update.py','--root',str(root),'--version','v0.1.1'])
    monkeypatch.setattr(updater,'verify',lambda *a,**kwargs:{'version':'0.1.1'})
    calls=[]
    def run(args,**kwargs):
        calls.append(args); return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(updater.subprocess,'run',run)
    health_calls=[]
    def health(python,root,version,**kwargs):
        health_calls.append(version)
        if version=='0.1.1': raise RuntimeError('synthetic new-service failure')
        return {'status':'running','version':version}
    monkeypatch.setattr(updater,'wait_health',health)
    with pytest.raises(RuntimeError,match='synthetic new-service failure'): updater.main()
    new_stop=next(i for i,c in enumerate(calls) if c[-2:]==['service','stop'] and c[0]!='old-python')
    restore=next(i for i,c in enumerate(calls) if c[-2:]==['service','install'] and c[0]=='old-python')
    old_start=next(i for i,c in enumerate(calls) if c[-2:]==['service','start'] and c[0]=='old-python')
    assert new_stop<restore<old_start
    assert health_calls==['0.1.1','0.1.0']
    assert not any(len(c)>1 and c[1]==str(previous/'install.py') for c in calls)
