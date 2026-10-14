import copy, hashlib, json
from pathlib import Path
import pytest
import brain_wiki as w
import brain_store as s
import brain_settings as cfg

def fixture(brain):
    vault,state=brain
    source=vault/'01-Projects/Example.md'; source.parent.mkdir(parents=True)
    source.write_text('---\ndate: 2026-10-01\n---\n# Example Project\n\nA prototype was demonstrated. Production deployment is still planned.\n',encoding='utf-8')
    packet=w.prepare(2)
    result={'pages':[{'category':'Projects','slug':'example','title':'Example Project','related':[],
                     'claims':[{'text':'The source records a demonstrated prototype, with production still planned.',
                                'source':'01-Projects/Example.md','quote':'A prototype was demonstrated. Production deployment is still planned.','status':'historical'}]}]}
    return source,packet,result

def test_compile_and_replay(brain):
    source,packet,result=fixture(brain); original=source.read_bytes()
    receipts=w.apply(packet,result)
    assert w.apply(packet,result)==receipts
    assert source.read_bytes()==original
    assert w.status()['pending_sources']==0
    assert not w.lint()['issues']
    text=(s.VAULT/receipts[0]['path']).read_text()
    assert 'source date 2026-10-01' in text and 'still planned' in text
    assert (s.VAULT/w.BASE/'Index.md').exists()

@pytest.mark.parametrize('mutation',['quote','path','category','empty','secret','link'])
def test_bad_model_output_cannot_write(brain,mutation):
    source,packet,result=fixture(brain)
    if mutation=='quote': result['pages'][0]['claims'][0]['quote']='Fabricated evidence that does not exist.'
    if mutation=='path': result['pages'][0]['slug']='../../Profile'
    if mutation=='category': result['pages'][0]['category']='Profile'
    if mutation=='empty': result['pages']=[]
    if mutation=='secret': result['pages'][0]['claims'][0]['text']='api_key=sk-abcdefghijklmnopqrstuvwxyz123456'
    if mutation=='link': result['pages'][0]['related']=[w.BASE+'Projects/missing.md']
    with pytest.raises(Exception): w.apply(packet,result)
    assert not (s.VAULT/w.BASE/'Projects').exists()

def test_source_change_prevents_publish(brain):
    source,packet,result=fixture(brain); source.write_text(source.read_text()+'Changed.\n')
    with pytest.raises(ValueError,match='Source changed'): w.apply(packet,result)
    assert not (s.VAULT/w.BASE/'Projects').exists()

def test_changed_source_requeues_and_lints(brain):
    source,packet,result=fixture(brain); w.apply(packet,result)
    source.write_text(source.read_text()+'New source evidence.\n')
    assert w.status()['pending_sources']==1
    assert any(i['issue']=='source missing or changed' for i in w.lint()['issues'])

def test_capture_device_cannot_compile(brain,monkeypatch):
    fixture(brain); monkeypatch.setattr(cfg,'ROLE','capture')
    with pytest.raises(PermissionError): w.prepare()
    with pytest.raises(PermissionError): s.update_current('02-Areas/AI-Memory/X.md','X','Claim',['01-Projects/Example.md'])
    receipt=s.capture('capture-device-1','Synthetic capture','An immutable event.','test','synthetic fixture')
    assert receipt['status']=='saved'

def test_shared_owner_enforced(brain):
    control=s.VAULT/w.BASE/'Control.md'; control.parent.mkdir(parents=True)
    control.write_text('maintainer_device_id: other-host\n')
    with pytest.raises(PermissionError): cfg.require_maintainer()

def test_partial_publish_recovery(brain,monkeypatch):
    source,packet,result=fixture(brain)
    second=copy.deepcopy(result['pages'][0]); second['slug']='second'; result['pages'].append(second)
    original=s._write; calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2: raise OSError('simulated interruption')
        return original(*args,**kwargs)
    monkeypatch.setattr(s,'_write',fail_second)
    with pytest.raises(OSError): w.apply(packet,result)
    monkeypatch.setattr(s,'_write',original)
    recovered=w.apply(packet,result)
    assert len(recovered)==2 and w.status()['pending_sources']==0
    assert (s.VAULT/w.BASE/'Log.md').read_text().count('<!-- wiki-batch:')==1

@pytest.mark.parametrize('path',['../secret.md','.obsidian/private.md','A/../B.md','C:/secret.md','NUL.md','A/file.md:stream','A/.hidden.md'])
def test_unsafe_paths(brain,path):
    with pytest.raises(ValueError): s.safe_path(path)

def test_capture_dedup_and_compare_and_swap(brain):
    args=('stable-event','Synthetic title','Synthetic milestone.','tests','test fixture')
    first=s.capture(*args); assert s.capture(*args)['status']=='duplicate'
    with pytest.raises(ValueError): s.capture('stable-event','Changed','Different','tests','fixture')
    update=s.update_current('02-Areas/AI-Memory/Test.md','Derived','Supported claim.',[first['path']])
    with pytest.raises(ValueError): s.update_current(update['path'],'Derived','Bad overwrite.',[first['path']])
    second=s.update_current(update['path'],'Derived','Updated supported claim.',[first['path']],update['sha256'])
    assert (s.STATE/'write-backups'/update['sha256']).exists()
    with pytest.raises(ValueError): s.update_current(second['path'],'Derived','Race.',[first['path']],update['sha256'])
