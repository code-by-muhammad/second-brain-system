"""Capture producer/consumer contracts using only disposable synthetic fixtures."""
import hashlib
import html
from pathlib import Path

import pytest

import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


@pytest.fixture
def brain(tmp_path, monkeypatch):
    root, vault = tmp_path/'runtime', tmp_path/'vault'
    state = root/'state'
    state.mkdir(parents=True); vault.mkdir(); (root/'logs').mkdir()
    for module in (cfg, store):
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'VAULT', vault)
        monkeypatch.setattr(module, 'STATE', state)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'synthetic-capture-contract-host')
    monkeypatch.setattr(wiki, 'cleanup', lambda: 0)
    template = vault/'Templates/daily-note.md'
    template.parent.mkdir()
    template.write_text('# Synthetic daily\n\n## Work Log\n', encoding='utf-8')
    evidence = vault/'01-Projects/Synthetic.md'
    evidence.parent.mkdir()
    evidence.write_bytes(b'Explicit synthetic evidence.\n')
    return vault, state


def snapshots(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def arguments(**changes):
    values = dict(event_id='synthetic-source-app-boundary', title='Synthetic milestone',
                  outcome='Explicit synthetic result.', source_app='synthetic-client',
                  source_reference='synthetic://capture-contract', project='',
                  decisions=[], next_steps=[], evidence_paths=['01-Projects/Synthetic.md'])
    values.update(changes)
    return values


@pytest.mark.parametrize('source_app', ['', 'x'*2001, 'x'*30000], ids=['empty', 'one-over-consumer-cap', 'old-producer-cap'])
def test_unlinkable_source_app_rejected_before_any_immutable_capture_or_state_write(brain, source_app):
    before = [snapshots(p) for p in brain]
    with pytest.raises(ValueError):
        store.capture(**arguments(source_app=source_app))
    assert [snapshots(p) for p in brain] == before


@pytest.mark.parametrize('source_app', ['x', 'x'*2000, 'Claude / Codex — اردو',
                                      'synthetic-client\n## Escaped daily section', ' \t\n'],
                         ids=['one', 'at-consumer-cap', 'unicode', 'multiline-escaped', 'truthy-whitespace-compatible'])
def test_supported_source_app_links_exactly_once_and_deduplicates_without_original_rewrite(brain, source_app):
    params = arguments(source_app=source_app)
    receipt = store.capture(**params)
    capture = brain[0]/receipt['path']; original = capture.read_bytes()
    assert store.capture(**params)['status'] == 'duplicate'
    first = maintenance.maintenance(sync=False)
    second = maintenance.maintenance(sync=False)
    assert first['captures_linked'] == 1 and not first['review']
    assert second['captures_linked'] == 0 and not second['review']
    daily = list((brain[0]/'05-Daily').glob('*.md'))
    assert len(daily) == 1
    body = daily[0].read_text(encoding='utf-8')
    assert body.count('<!-- brain-capture:'+params['event_id']+' -->') == 1
    assert '- **Source:** '+html.escape(' '.join(source_app.split()),quote=True)+'\n' in body
    assert capture.read_bytes() == original
    assert (brain[0]/'01-Projects/Synthetic.md').read_bytes() == b'Explicit synthetic evidence.\n'


def test_other_capture_fields_at_supported_limits_remain_linkable_and_immutable(brain):
    params = arguments(event_id='e'*180, title='t'*180, outcome='o'*30000,
                       source_app='s'*2000, source_reference='r'*30000, project='p'*180,
                       decisions=['d'*2000]*30, next_steps=['n'*2000]*30,
                       evidence_paths=['01-Projects/Synthetic.md']*30)
    receipt = store.capture(**params); path = brain[0]/receipt['path']; original = path.read_bytes()
    assert store.capture(**params)['status'] == 'duplicate'
    report = maintenance.maintenance(sync=False)
    assert report['captures_linked'] == 1 and not report['review']
    assert path.read_bytes() == original


@pytest.mark.parametrize('changes', [
    {'event_id':'e'*181}, {'title':'t'*181}, {'project':'p'*181},
    {'outcome':'o'*30001}, {'source_reference':'r'*30001},
    {'decisions':['d'*2001]}, {'next_steps':['n'*2001]},
    {'decisions':['d']*31}, {'next_steps':['n']*31},
    {'evidence_paths':['01-Projects/Synthetic.md']*31},
], ids=['event', 'title', 'project', 'outcome', 'reference', 'decision-item', 'next-item',
        'decision-count', 'next-count', 'evidence-count'])
def test_existing_other_field_limits_still_refuse_before_any_capture_or_state_write(brain, changes):
    before = [snapshots(p) for p in brain]
    with pytest.raises(ValueError):
        store.capture(**arguments(**changes))
    assert [snapshots(p) for p in brain] == before


@pytest.mark.parametrize('source_app', ['', 'x'*2001], ids=['empty', 'over-consumer-cap'])
def test_legacy_unlinkable_source_app_capture_is_reviewed_and_preserved(brain, source_app):
    event = 'synthetic-legacy-source-app'
    relative = '00-Inbox/AI-Captures/'+hashlib.sha256(event.encode()).hexdigest()[:24]+'.md'
    path = brain[0]/relative; path.parent.mkdir(parents=True)
    raw = store.note_text({'event_id':event, 'source_app':source_app,
                           'captured_at':'2026-10-17T01:00:00+05:00', 'status':'captured'},
                          '# Synthetic legacy milestone\n').encode('utf-8')
    path.write_bytes(raw)
    for _ in range(2):
        report = maintenance.maintenance(sync=False)
        assert report['captures_linked'] == 0 and len(report['review']) == 1
        assert path.read_bytes() == raw
    assert not (brain[0]/'05-Daily').exists()
