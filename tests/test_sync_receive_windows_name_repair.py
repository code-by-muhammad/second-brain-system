"""A second POSIX device can receive one owner's Windows-name repair."""
import json,os
import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git,_raw
from test_sync_windows_path_portability import repository,snapshot,observe_transport,PREFIX,SUFFIX
from test_sync_committed_windows_paths import incoming

OLD='01-Projects/aux.md'
NEW='01-Projects/Auxiliary.md'
ORIGINAL=b'# Old cross-device source\nHarmless original evidence.\n'
CAPTURE='00-Inbox/Peer-B-Capture.md'
CAPTURE_BYTES=b'# Peer B capture\nHarmless local evidence stays intact.\n'


def peers(brain,tmp_path,hooks):
    a,state,remote,note=repository(brain,tmp_path,hooks)
    _raw(a,'checkout','--','01-Projects/Ordinary.md')
    (a/OLD).write_bytes(ORIGINAL);_raw(a,'add','--',OLD);_raw(a,'commit','-m','synthetic historically incompatible source');_raw(a,'push','origin','HEAD')
    b=tmp_path/'peer-b';_raw(tmp_path,'clone','--branch','main',str(remote),str(b))
    for key,value in [('gc.auto','0'),('maintenance.auto','false'),('core.fsmonitor','false'),('core.hooksPath',str(hooks)),('commit.gpgsign','false'),('core.autocrlf','false'),('user.name','Synthetic second peer'),('user.email','peer@example.invalid')]:_raw(b,'config','--local',key,value)
    assert (b/OLD).read_bytes()==ORIGINAL and _raw(b,'status','--porcelain')==b''
    return a,b,remote


@pytest.mark.skipif(os.name=='nt',reason='Actual two POSIX peers must already track the incompatible original.')
@pytest.mark.parametrize('capture',['none','committed','pending'])
def test_second_peer_receives_owner_repair_and_keeps_its_capture(brain,tmp_path,monkeypatch,isolated_git,capture):
    a,b,remote=peers(brain,tmp_path,isolated_git)
    if capture!='none':
        p=b/CAPTURE;p.parent.mkdir();p.write_bytes(CAPTURE_BYTES)
        if capture=='committed':_raw(b,'add','--',CAPTURE);_raw(b,'commit','-m','peer B previously committed its capture')
    _raw(a,'mv','--',OLD,NEW);_raw(a,'commit','-m','owner explicitly repairs one shared original')
    assert g.sync(a)=='synced' and _raw(remote,'show','refs/heads/main:'+NEW)==ORIGINAL
    commands=observe_transport(monkeypatch);result=g.sync(b)
    remaining=[name for name in (OLD,NEW) if (b/name).is_file()]
    assert len(remaining)==1 and (b/remaining[0]).read_bytes()==ORIGINAL
    if capture!='none':assert (b/CAPTURE).read_bytes()==CAPTURE_BYTES
    assert _raw(remote,'show','refs/heads/main:'+NEW)==ORIGINAL
    observation={'capture':capture,'result':result,'remaining_original':remaining,'commands':[cmd[4:] for cmd in commands],'local_status':_raw(b,'status','--porcelain').decode(),'head':_raw(b,'rev-parse','HEAD').decode().strip(),'remote':_raw(remote,'rev-parse','refs/heads/main').decode().strip()}
    (tmp_path/'r164-recovery-observation.json').write_text(json.dumps(observation,indent=2)+'\n',encoding='utf-8')
    assert result=='synced',observation
    assert remaining==[NEW] and not (b/OLD).exists()
    assert _raw(b,'rev-parse','HEAD')==_raw(remote,'rev-parse','refs/heads/main') and _raw(b,'status','--porcelain')==b''
    if capture!='none':assert _raw(remote,'show','refs/heads/main:'+CAPTURE)==CAPTURE_BYTES


def test_unsafe_incoming_tree_still_pauses_without_checkout_or_publication(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git);incoming(vault,remote,'aux.md')
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    assert g.sync(vault)==PREFIX+json.dumps(OLD)+SUFFIX
    assert snapshot(vault,remote)==before and not (vault/OLD).exists()
    assert not any(cmd[4] in {'add','commit','merge','merge-tree','push','reset'} for cmd in commands)


def test_existing_owner_staging_stays_before_fetch(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git);_raw(vault,'add','--','01-Projects/Ordinary.md')
    before=snapshot(vault,remote);commands=observe_transport(monkeypatch)
    assert g.sync(vault)=='paused: existing staged changes preserved'
    assert snapshot(vault,remote)==before and not any(cmd[4] in {'add','commit','fetch','merge','push','reset'} for cmd in commands)


def test_ordinary_local_capture_still_synchronizes(brain,tmp_path,monkeypatch,isolated_git):
    vault,state,remote,note=repository(brain,tmp_path,isolated_git);p=vault/CAPTURE;p.parent.mkdir();p.write_bytes(CAPTURE_BYTES)
    assert g.sync(vault)=='synced' and p.read_bytes()==CAPTURE_BYTES==_raw(remote,'show','refs/heads/main:'+CAPTURE)
