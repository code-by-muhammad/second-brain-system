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


@pytest.mark.skipif(os.name=='nt',reason='Actual two POSIX peers must already track the incompatible original.')
def test_edited_old_name_keeps_pending_capture_and_receives_owner_rename_after_owner_commit(brain,tmp_path,monkeypatch,isolated_git):
    a,b,remote=peers(brain,tmp_path,isolated_git)
    edited=ORIGINAL+b'\nPeer B reviewed local addition.\n'
    (b/OLD).write_bytes(edited)
    capture=b/CAPTURE;capture.parent.mkdir();capture.write_bytes(CAPTURE_BYTES)
    _raw(a,'mv','--',OLD,NEW);_raw(a,'commit','-m','owner supplies one authoritative rename')
    assert g.sync(a)=='synced' and _raw(remote,'show','refs/heads/main:'+NEW)==ORIGINAL
    before=snapshot(b,remote);commands=observe_transport(monkeypatch)
    first=g.sync(b);after=snapshot(b,remote)
    first_commands=[cmd[4:] for cmd in commands]
    assert after==before and (b/OLD).read_bytes()==edited and capture.read_bytes()==CAPTURE_BYTES
    assert not any(cmd[4] in {'add','commit','fetch','merge','merge-tree','push','reset'} for cmd in commands)
    # Explicit owner action preserves only the existing tracked edit; capture stays pending.
    _raw(b,'add','--',OLD);_raw(b,'commit','-m','owner preserves local tracked edit before receiving shared rename')
    assert capture.read_bytes()==CAPTURE_BYTES and CAPTURE.encode() in _raw(b,'ls-files','--others','--exclude-standard')
    commands.clear();recovered=g.sync(b)
    assert recovered=='synced' and (b/NEW).read_bytes()==edited and not (b/OLD).exists()
    assert capture.read_bytes()==CAPTURE_BYTES==_raw(remote,'show','refs/heads/main:'+CAPTURE)
    assert _raw(remote,'show','refs/heads/main:'+NEW)==edited
    assert OLD not in _raw(remote,'ls-tree','-r','--name-only','refs/heads/main').decode().splitlines()
    assert _raw(b,'status','--porcelain')==b'' and _raw(b,'rev-parse','HEAD')==_raw(remote,'rev-parse','refs/heads/main')
    observation={'first_result':first,'first_commands':first_commands,'first_snapshot_preserved':before==after,
        'manual_owner_commit_then_sync':recovered,'edited_source_recovered_under_owner_name':True,
        'pending_capture_preserved_and_published':True,'final_clean_and_remote_equal':True}
    (tmp_path/'r170-edited-recovery-observation.json').write_text(json.dumps(observation,indent=2)+'\n',encoding='utf-8')
    expected=('paused: tracked Markdown edit uses a Windows-incompatible path: '+json.dumps(OLD)+
        '. Preserve the edit and pending captures. If another device has committed the shared rename, explicitly commit this tracked edit under its current name, then sync. Otherwise make one explicit owner rename on a device that can open the original. Do not choose different replacement names on each peer; original and staging preserved.')
    assert first==expected,observation
