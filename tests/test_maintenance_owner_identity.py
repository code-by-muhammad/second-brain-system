"""Preserve owner-edited daily identities and keep withheld diagnostics actionable."""
import json
from pathlib import Path

import pytest
import brain_git
import brain_maintenance as m
import brain_store as s
from test_maintenance_daily_portability import template, capture, daily_for


def snapshot(vault):
    return {path.relative_to(vault).as_posix():path.read_bytes() for path in vault.rglob('*.md')}


def identity(saved):
    return '- **What / Why / How / Result / Next:** [['+saved['path'][:-3]+'|Saved milestone and evidence]]'


def git_recorder(monkeypatch):
    calls=[]
    monkeypatch.setattr(brain_git,'sync',lambda *args,**kwargs:calls.append('sync') or 'synced')
    return calls


@pytest.mark.parametrize('edit',['annotation','alias','blank-line','inserted-line','removed-identity'])
def test_owner_edited_identity_is_reviewed_without_duplicate_or_note_writes(brain,monkeypatch,edit):
    vault,_=brain; template(vault); saved=capture('owner-edited-event')
    assert m.maintenance(sync=False)['captures_linked']==1
    daily=daily_for(saved); body=daily.read_text(encoding='utf-8'); line=identity(saved)
    replacement={'annotation':line+' - shipped','alias':line.replace('Saved milestone and evidence','Owner summary'),
                 'blank-line':'\n'+line,'inserted-line':'Owner annotation before identity.\n'+line,
                 'removed-identity':''}[edit]
    owner=body.replace(line,replacement).encode('utf-8'); daily.write_bytes(owner)
    before=snapshot(vault); calls=git_recorder(monkeypatch)
    for _ in range(2):
        result=m.maintenance()
        assert result['captures_linked']==0 and len(result['review'])==1
        assert result['review'][0]['path']==saved['path']
        assert 'owner must reconcile' in result['review'][0]['reasons'][0]
        assert snapshot(vault)==before and daily.read_bytes()==owner
    assert calls==['sync','sync']
    assert daily.read_text(encoding='utf-8').count('<!-- brain-capture:owner-edited-event -->')==1


@pytest.mark.parametrize('representation',['LF','CRLF+BOM'])
def test_genuine_exact_identity_remains_idempotent_without_review(brain,representation):
    vault,_=brain; template(vault); saved=capture('valid-owner-identity')
    assert m.maintenance(sync=False)['captures_linked']==1
    daily=daily_for(saved)
    if representation=='CRLF+BOM': daily.write_bytes(b'\xef\xbb\xbf'+daily.read_bytes().replace(b'\n',b'\r\n'))
    before=snapshot(vault)
    result=m.maintenance(sync=False)
    assert result['captures_linked']==0 and not result['review'] and snapshot(vault)==before


@pytest.mark.parametrize('event',['tab\tid','unicode\u2028id','vertical\x0bid'])
def test_accepted_event_ids_keep_exact_identity_idempotency(brain,event):
    vault,_=brain; template(vault); saved=capture(event)
    assert m.maintenance(sync=False)['captures_linked']==1
    before=snapshot(vault); result=m.maintenance(sync=False)
    assert result['captures_linked']==0 and not result['review'] and snapshot(vault)==before
    assert daily_for(saved).read_text(encoding='utf-8').count('<!-- brain-capture:'+event+' -->')==1


@pytest.mark.parametrize('placement',['title','source'])
def test_legacy_inline_forgery_does_not_suppress_genuine_capture(brain,placement):
    vault,_=brain; template(vault); saved=capture('real-event'); daily=daily_for(saved); daily.parent.mkdir()
    marker='<!-- brain-capture:real-event -->'
    inline=('### 10:00 — Old forged '+marker) if placement=='title' else ('- **Source:** Old forged '+marker)
    owner='# Owner daily\n\n## Work Log\n'+inline+'\n'; daily.write_text(owner,encoding='utf-8')
    capture_bytes=s.safe_path(saved['path']).read_bytes(); result=m.maintenance(sync=False)
    assert result['captures_linked']==1 and not result['review']
    assert owner.rstrip() in daily.read_text(encoding='utf-8') and identity(saved) in daily.read_text(encoding='utf-8')
    assert s.safe_path(saved['path']).read_bytes()==capture_bytes
    before=snapshot(vault); second=m.maintenance(sync=False)
    assert second['captures_linked']==0 and not second['review'] and snapshot(vault)==before


@pytest.mark.parametrize('ambiguous',['missing-link','wrong-link'])
def test_ambiguous_standalone_marker_requires_explicit_owner_resolution(brain,ambiguous):
    vault,_=brain; template(vault); saved=capture('ambiguous-event'); daily=daily_for(saved); daily.parent.mkdir()
    marker='<!-- brain-capture:ambiguous-event -->'
    detail='' if ambiguous=='missing-link' else '\n- **What / Why / How / Result / Next:** [[00-Inbox/AI-Captures/unrelated|Saved milestone and evidence]]'
    owner='# Owner daily\n\n## Work Log\n'+marker+detail+'\n'; daily.write_text(owner,encoding='utf-8')
    before=snapshot(vault)
    first=m.maintenance(sync=False)
    assert first['captures_linked']==0 and first['review'] and snapshot(vault)==before
    assert 'owner must reconcile' in first['review'][0]['reasons'][0]
    # Only this disposable fixture owner's explicit removal resolves ambiguity.
    resolved=owner.replace(marker+'\n','').replace(marker,'')
    daily.write_text(resolved,encoding='utf-8'); capture_bytes=s.safe_path(saved['path']).read_bytes()
    second=m.maintenance(sync=False)
    assert second['captures_linked']==1 and not second['review']
    assert identity(saved) in daily.read_text(encoding='utf-8')
    assert s.safe_path(saved['path']).read_bytes()==capture_bytes
    before=snapshot(vault); third=m.maintenance(sync=False)
    assert third['captures_linked']==0 and not third['review'] and snapshot(vault)==before


@pytest.mark.parametrize('duplicate',['two-exact','one-exact-one-ambiguous'])
def test_multiple_standalone_markers_are_ambiguous_even_with_one_exact_identity(brain,duplicate):
    vault,_=brain; template(vault); saved=capture('duplicate-marker-event')
    assert m.maintenance(sync=False)['captures_linked']==1
    daily=daily_for(saved); canonical=daily.read_text(encoding='utf-8')
    marker='<!-- brain-capture:duplicate-marker-event -->'
    owner=canonical+'\n'+marker+('\n'+identity(saved) if duplicate=='two-exact' else '\nOwner annotation.')+'\n'
    daily.write_text(owner,encoding='utf-8'); before=snapshot(vault)
    result=m.maintenance(sync=False)
    assert result['captures_linked']==0 and result['review'] and snapshot(vault)==before
    # The fixture owner preserves the original canonical entry and removes the extra.
    daily.write_text(canonical,encoding='utf-8'); before=snapshot(vault)
    result=m.maintenance(sync=False)
    assert result['captures_linked']==0 and not result['review'] and snapshot(vault)==before


def test_fresh_withheld_path_has_documented_local_lookup_and_correct_digest(brain):
    vault,state=brain; relative='02-Areas/AI-Memory/Keys-sk-'+'SyntheticAlphaBetaGammaDelta'+'.md'
    path=vault/relative; path.parent.mkdir(parents=True); raw=b'\xffunreadable synthetic owner note'; path.write_bytes(raw)
    before=snapshot(vault); result=m.maintenance(sync=False)
    stored=json.loads((state/'maintenance-status.json').read_text(encoding='utf-8'))
    public=m.health_status(state/'maintenance-status.json')
    for row in (result['review'][0],stored['review'][0],public['review'][0]):
        assert row['path']==m.WITHHELD_PATH and row['path_withheld'] is True
        assert row['path_sha256']==s.digest(relative.encode('utf-8'))
        assert 'DEVICE-SETUP' in row['path'] and 'local path-SHA lookup' in row['path']
        assert 'inspect private maintenance status' not in row['path']
    assert snapshot(vault)==before and path.read_bytes()==raw
    assert 'SyntheticAlphaBetaGammaDelta' not in json.dumps(public)


@pytest.mark.parametrize('locator',['legacy','current'])
def test_cached_withheld_locator_preserves_opaque_hash_and_cache_bytes(brain,locator):
    _,state=brain; opaque=s.digest(b'synthetic original relative path')
    token='sk-'+'SyntheticAlphaBetaGammaDelta'
    row={'path':m.LEGACY_WITHHELD_PATH if locator=='legacy' else m.WITHHELD_PATH,
         'path_withheld':True,'path_sha256':opaque,'reasons':[m.LEGACY_WITHHELD_DETAIL],
         'unsafe_cached_extra':token}
    status={'status':'completed','git':m.LEGACY_WITHHELD_DETAIL,'review':[row]}
    path=state/'maintenance-status.json'; raw=json.dumps(status).encode(); path.write_bytes(raw)
    public=m.health_status(path); item=public['review'][0]
    assert item['path']==m.WITHHELD_PATH and item['path_sha256']==opaque and item['path_withheld'] is True
    assert item['reasons']==[m.WITHHELD_DETAIL] and public['git']==m.WITHHELD_DETAIL
    assert 'unsafe_cached_extra' not in item and token not in json.dumps(public)
    assert 'inspect private maintenance status' not in json.dumps(public)
    assert path.read_bytes()==raw


def test_withheld_reasons_advice_is_screened_and_does_not_echo_secret(brain):
    secret='api_key='+'SyntheticAlphaBetaGammaDelta'
    assert m._review_reasons([secret])==[m.WITHHELD_DETAIL]
    assert 'local path-SHA lookup' in m.WITHHELD_DETAIL and 'DEVICE-SETUP' in m.WITHHELD_DETAIL
    assert m._diagnostic_text(m.WITHHELD_DETAIL)==m.WITHHELD_DETAIL
    assert m._diagnostic_text(m.WITHHELD_PATH)==m.WITHHELD_PATH


def test_ordinary_actionable_review_path_is_unchanged(brain):
    relative='02-Areas/AI-Memory/Ordinary.md'
    assert m._review_path({'path':relative})=={'path':relative}
    assert m._review_reasons(['missing source: 01-Projects/Ordinary.md'])==['missing source: 01-Projects/Ordinary.md']
