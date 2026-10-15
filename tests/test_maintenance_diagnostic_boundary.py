"""Synthetic-only public maintenance diagnostics, including old cache boundaries."""
import json,os
from pathlib import Path
import pytest
import brain_git
import brain_settings as cfg
import brain_store as s
import brain_maintenance as m

MARKER='sk-'+'synthetic'+'A'*24
assert s.SECRET.search(MARKER)


def setup_capture(brain,monkeypatch,role):
    vault,state=brain;templates=vault/'Templates';templates.mkdir(exist_ok=True)
    (templates/'daily-note.md').write_text('# Synthetic daily\n\n## Work Log\n',encoding='utf-8')
    s.capture('diagnostic-boundary','Synthetic milestone','Synthetic result.','test','synthetic fixture')
    monkeypatch.setattr(cfg,'ROLE',role);calls=[]
    monkeypatch.setattr(brain_git,'sync',lambda:calls.append(True) or 'synced')
    return calls


@pytest.mark.parametrize('role',['maintainer','capture'])
@pytest.mark.parametrize('branch',['missing','invalid-sha','changed','newer-no-hash'])
def test_source_diagnostic_branches_screen_filename_before_append(brain,monkeypatch,role,branch):
    vault,state=brain;calls=setup_capture(brain,monkeypatch,role);relative='01-Projects/'+MARKER+'.md';source=vault/relative
    info={'sources':[relative]}
    if branch!='missing':source.parent.mkdir(exist_ok=True);source.write_bytes(b'# Synthetic owner source.\r\n')
    if branch=='invalid-sha':info['source_hashes']={relative:'bad'}
    if branch=='changed':info['source_hashes']={relative:'0'*64}
    page=vault/'02-Areas/AI-Memory/Current.md';page.parent.mkdir(parents=True);page.write_text(s.note_text(info,'# Synthetic derived body.'),encoding='utf-8')
    if branch=='newer-no-hash':os.utime(source,(page.stat().st_mtime+10,page.stat().st_mtime+10))
    original=page.read_bytes();source_raw=source.read_bytes() if source.exists() else None
    result=m.maintenance();health=m.health_status(state/'maintenance-status.json')
    assert MARKER not in json.dumps(result) and MARKER not in json.dumps(health)
    assert result['git']=='synced' and calls==[True] and result['captures_linked']==(1 if role=='maintainer' else 0)
    assert health['review_count']==1 and page.read_bytes()==original
    assert (source.read_bytes() if source.exists() else None)==source_raw


@pytest.mark.parametrize('role',['maintainer','capture'])
def test_unreadable_derived_filename_has_only_safe_locator_and_raw_path_hash(brain,monkeypatch,role):
    vault,state=brain;calls=setup_capture(brain,monkeypatch,role);relative='02-Areas/AI-Memory/'+MARKER+'.md';page=vault/relative
    page.parent.mkdir(parents=True);raw=b'---\ntitle: [unterminated\n---\nKeep body.\n';page.write_bytes(raw)
    result=m.maintenance();health=m.health_status(state/'maintenance-status.json');row=health['review'][0]
    assert MARKER not in json.dumps(result) and MARKER not in json.dumps(health)
    assert row['path_withheld'] is True and row['path_sha256']==s.digest(relative.encode())
    assert row['path'].startswith('[withheld') and page.read_bytes()==raw and calls==[True]


@pytest.mark.parametrize('relative',['02-Areas/AI-Memory/'+MARKER+'.md','02-Areas/AI-Memory/'+('Z'*1000)+'.md','02-Areas/AI-Memory/\ud800.md','02-Areas/AI-Memory/nul\x00.md'],ids=['credential','overlong','surrogate','nul'])
def test_add_review_withholds_invalid_paths_without_echoing_them(brain,relative):
    status={'review':[]};m.add_review(status,s.VAULT/relative,['Synthetic metadata requires review']);row=status['review'][0]
    assert row.get('path_withheld') is True and row['path_sha256']==s.digest(relative.encode('utf-8',errors='surrogatepass'))
    assert row['path']!=relative


@pytest.mark.parametrize('field',['path','reason','decoded-tab','invalid-utf8-reason','nonlist-reasons','mapping-reason'])
def test_old_cached_review_is_screened_read_only(brain,field):
    _,state=brain;row={'path':'02-Areas/AI-Memory/Current.md','reasons':['Synthetic metadata requires review']}
    if field=='path':row['path']='02-Areas/AI-Memory/'+MARKER+'.md'
    elif field=='reason':row['reasons']=['source changed: 01-Projects/'+MARKER+'.md']
    elif field=='decoded-tab':row['reasons']=['password\t:\tsyntheticabcdefghijk123456789']
    elif field=='invalid-utf8-reason':row['reasons']=['invalid\ud800'+MARKER]
    elif field=='nonlist-reasons':row['reasons']={'password':MARKER}
    else:row['reasons']=[{'password':MARKER}]
    path=state/'maintenance-status.json';path.write_text(json.dumps({'status':'completed','git':'synced','review':[row]}),encoding='utf-8');before=path.read_bytes()
    result=m.health_status(path);encoded=json.dumps(result)
    assert MARKER not in encoded and 'syntheticabcdefghijk123456789' not in encoded and '\\ud800' not in encoded
    assert result['review_count']==1 and result['status']=='completed' and result['git']=='synced' and path.read_bytes()==before


@pytest.mark.parametrize('field',['status','git','error','stage','at','aggregation','error_type'])
def test_cached_top_level_public_text_is_screened(brain,field):
    path=brain[1]/'maintenance-status.json';cache={'status':'completed','git':'synced','review':[],field:MARKER};path.write_text(json.dumps(cache),encoding='utf-8');before=path.read_bytes()
    result=m.health_status(path)
    assert MARKER not in json.dumps(result) and path.read_bytes()==before and result['review_count']==0


def test_cached_unknown_and_structured_attributes_are_never_copied_verbatim(brain):
    path=brain[1]/'maintenance-status.json';cache={'status':'completed','git':{'password':MARKER},'captures_linked':2,'completed_private_artifacts_removed':3,
        'owner_note':{'api_key':MARKER},'review':[{'path':'02-Areas/AI-Memory/Current.md','reasons':['Safe review'], 'owner_note':MARKER,'path_sha256':MARKER}]}
    path.write_text(json.dumps(cache),encoding='utf-8');before=path.read_bytes();result=m.health_status(path)
    assert MARKER not in json.dumps(result) and 'owner_note' not in result and 'owner_note' not in result['review'][0]
    assert result['captures_linked']==2 and result['completed_private_artifacts_removed']==3 and path.read_bytes()==before


@pytest.mark.parametrize('reason',['missing source: 01-Projects/Ordinary-Source.md','Discuss API key setup after owner login; no credential value recorded'])
def test_safe_ordinary_diagnostics_and_api_key_discussion_remain_useful(brain,reason):
    path=brain[1]/'maintenance-status.json';row={'path':'02-Areas/AI-Memory/Current.md','reasons':[reason]*20}
    cache={'at':'2026-10-15T10:00:00+00:00','status':'completed','stage':'finished','git':'synced','aggregation':'skipped: capture/search device',
        'captures_linked':3,'completed_private_artifacts_removed':4,'review':[row]*1000}
    path.write_text(json.dumps(cache),encoding='utf-8');before=path.read_bytes();result=m.health_status(path)
    assert result['review_count']==1000 and len(result['review'])==20 and result['review_truncated'] is True
    assert result['review'][0]['reasons']==[reason]*5 and result['review'][0]['reason_count']==20 and result['review'][0]['reasons_truncated'] is True
    assert result['review'][0]['path']==row['path'] and all(result[key]==cache[key] for key in ('at','status','stage','git','aggregation','captures_linked','completed_private_artifacts_removed'))
    assert path.read_bytes()==before


def test_fresh_git_sync_diagnostic_does_not_echo_rejected_filename(brain,monkeypatch):
    calls=setup_capture(brain,monkeypatch,'capture')
    def fail():raise brain_git.GitSyncError('Note 01-Projects/'+MARKER+'.md blocks sync: owner review needed.')
    monkeypatch.setattr(brain_git,'sync',fail);result=m.maintenance();health=m.health_status(brain[1]/'maintenance-status.json')
    assert result['status']=='needs attention' and MARKER not in json.dumps(result) and MARKER not in json.dumps(health)



def test_safe_git_sync_explanation_remains_useful(brain,monkeypatch):
    setup_capture(brain,monkeypatch,'capture');message='Ordinary source conflict requires review; original notes preserved.'
    def fail():raise brain_git.GitSyncError(message)
    monkeypatch.setattr(brain_git,'sync',fail);result=m.maintenance();health=m.health_status(brain[1]/'maintenance-status.json')
    assert result['git']=='needs attention: '+message and health['git']==result['git'] and result['status']=='needs attention'
