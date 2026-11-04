"""Shared derived reads must validate the raw original used by each caller."""
from datetime import datetime
import json
import os
from pathlib import Path

import pytest

import brain_maintenance as m
import brain_settings as cfg
import brain_store as s
import brain_wiki as w


LABEL = 'r163outsidefield'


def put(relative, raw):
    path = s.VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def queue_snapshot():
    with w.db() as conn:
        return {'sources': [dict(row) for row in conn.execute('SELECT * FROM sources ORDER BY path')],
            'batches': [dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY id')]}


def protect_queue():
    with w.db() as conn:
        conn.execute('INSERT INTO batches(id,status,receipt,updated) VALUES(?,?,?,?)', ('a' * 24, 'pending', None, s.stamp()))
    artifact = cfg.STATE / 'wiki/batches' / ('a' * 24) / 'input.json'
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'{"synthetic":"pending work preserved"}\n')
    return artifact, artifact.read_bytes(), queue_snapshot()


def original_snapshot():
    """Never follow synthetic links; daily output is evaluated separately."""
    files, links = {}, {}
    for path in s.VAULT.rglob('*.md'):
        relative = path.relative_to(s.VAULT).as_posix()
        if relative.startswith('05-Daily/'):
            continue
        if path.is_symlink():
            links[relative] = os.readlink(path)
        else:
            files[relative] = s.digest(path.read_bytes())
    return {'files': files, 'links': links}


def observed(monkeypatch, outside, call):
    opened, models = [], []
    real_open = Path.open
    with monkeypatch.context() as context:
        def opening(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get('mode', 'r')
            if 'r' in mode and path.resolve() == outside:
                opened.append({'requested': str(path), 'resolved': str(path.resolve()), 'mode': mode})
            return real_open(path, *args, **kwargs)
        def no_model(*args, **kwargs):
            models.append(True)
            raise AssertionError('No model, Git, or subprocess call is allowed in this synthetic local test.')
        context.setattr(Path, 'open', opening)
        context.setattr(w.subprocess, 'Popen', no_model)
        context.setattr(w.subprocess, 'run', no_model)
        result = call()
    return result, opened, models


def record(tmp_path, **value):
    (tmp_path / 'r163-observation.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def optional_receipt():
    # A valid deferred receipt exercises parsing without changing completion.
    entry = {'job_key': 'unrelated::part1', 'status': 'deferred', 'proofs': []}
    return w._receipt_note('b' * 24, [entry]).encode()


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX literal-backslash derived symlink required.')
@pytest.mark.parametrize('route', ['capture-daily', 'derived-review', 'optional-receipt-status'])
def test_discovered_derived_reader_never_opens_outside_original(brain, monkeypatch, tmp_path, route):
    if route == 'capture-daily':
        relative = '00-Inbox/AI-Captures/session\\capture.md'
        raw = s.note_text({'captured_at': '2026-10-17T12:00:00+05:00', 'event_id': LABEL,
            'source_app': LABEL}, '# ' + LABEL + '\n\nHarmless external capture-shaped fixture.').encode()
        put('Templates/daily-note.md', b'# Synthetic daily\n\n## Work Log\n')
    elif route == 'derived-review':
        relative = '02-Areas/AI-Memory/review\\summary.md'
        raw = s.note_text({'sources': ['03-Resources/' + LABEL + '.md']}, '# Harmless external summary').encode()
    else:
        relative = w.BASE + 'Receipts/part\\proof.md'
        raw = optional_receipt()
    outside = tmp_path / 'outside-derived-synthetic.md'
    outside.write_bytes(raw)
    path = s.VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    assert path.is_symlink() and path.resolve() == outside and s.safe_path(relative) != path
    assert not s.safe_path(relative).exists()
    artifact, artifact_raw, before_queue = protect_queue()
    before_originals = original_snapshot()
    call = w.status if route == 'optional-receipt-status' else lambda: m.maintenance(sync=False)
    result, opened, models = observed(monkeypatch, outside, call)
    daily = [p.read_text(encoding='utf-8') for p in (s.VAULT / '05-Daily').glob('*.md')]
    local_field_leak = LABEL in json.dumps(daily)
    record(tmp_path, kind='outside-derived', route=route, product_outside_opens=opened,
        local_daily_field_leak=local_field_leak, model_calls=len(models), result=result)
    assert original_snapshot() == before_originals and outside.read_bytes() == raw
    assert queue_snapshot() == before_queue and artifact.read_bytes() == artifact_raw
    assert not models
    if route != 'optional-receipt-status':
        assert result['git'] == 'not requested'
    assert not opened, 'Derived reader opened outside original before raw-path validation.'
    assert not local_field_leak
    if route == 'capture-daily':
        assert result['captures_linked'] == 0 and result['review']
    elif route == 'derived-review':
        assert result['review'] and LABEL not in json.dumps(result)
    else:
        assert result['receipt_issue_count'] == 1 and result['portable_receipt_parts'] == 0


def test_ordinary_capture_links_daily_exactly_once_and_preserves_pending_work(brain, tmp_path):
    put('Templates/daily-note.md', b'# Synthetic daily\n\n## Work Log\n')
    capture = s.capture('r163-ordinary-capture', 'Ordinary synthetic milestone', 'Harmless local evidence.', 'synthetic test', 'synthetic fixture')
    original = (s.VAULT / capture['path']).read_bytes()
    artifact, artifact_raw, before_queue = protect_queue()
    first, second = m.maintenance(sync=False), m.maintenance(sync=False)
    daily = next((s.VAULT / '05-Daily').glob('*.md')).read_text(encoding='utf-8')
    assert first['captures_linked'] == 1 and second['captures_linked'] == 0
    assert daily.count('<!-- brain-capture:r163-ordinary-capture -->') == 1
    assert 'Ordinary synthetic milestone' in daily and first['git'] == second['git'] == 'not requested'
    assert (s.VAULT / capture['path']).read_bytes() == original
    assert queue_snapshot() == before_queue and artifact.read_bytes() == artifact_raw
    record(tmp_path, kind='ordinary-capture-control', linked_once=True, pending_work_preserved=True)


def test_ordinary_derived_review_and_valid_optional_receipt_still_work(brain, tmp_path):
    source = put('03-Resources/ordinary.md', b'# Ordinary source\nHarmless original evidence.\n')
    put('02-Areas/AI-Memory/Ordinary.md', s.note_text({'sources': ['03-Resources/ordinary.md'],
        'source_hashes': {'03-Resources/ordinary.md': s.digest(source.read_bytes())}}, '# Derived control').encode())
    put(w.BASE + 'Receipts/ordinary.md', optional_receipt())
    artifact, artifact_raw, before_queue = protect_queue()
    before = original_snapshot()
    maintenance, status = m.maintenance(sync=False), w.status()
    assert maintenance['review'] == [] and maintenance['git'] == 'not requested'
    assert status['receipt_issue_count'] == 0 and status['portable_receipt_parts'] == 0
    assert original_snapshot() == before and queue_snapshot() == before_queue and artifact.read_bytes() == artifact_raw
    record(tmp_path, kind='ordinary-derived-receipt-control', valid=True, pending_work_preserved=True)


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX literal-backslash derived original required.')
def test_raw_regular_derived_original_and_public_alias_remain_distinct(brain, tmp_path):
    raw = b'# Literal original\nRaw original retained.\n'
    original = put('02-Areas/AI-Memory/review\\summary.md', raw)
    nested = put('02-Areas/AI-Memory/review/summary.md', b'# Nested alias target\nDifferent original.\n')
    assert w._read_derived(original) == (raw, raw.decode())
    alias = '02-Areas\\AI-Memory\\review\\summary.md'
    assert s.safe_path(alias) == nested and 'Different original.' in s.read(alias)['content']
    assert s.note_link(alias, 'Maps/AI-Context.md') == 'Source path: `02-Areas/AI-Memory/review/summary.md`'
    record(tmp_path, kind='regular-and-alias-control', raw_original_preserved=True)


@pytest.mark.parametrize('field', ['content', 'encoding'])
def test_derived_content_and_encoding_classifications_stay_integrity_errors(brain, tmp_path, field):
    relative = '02-Areas/AI-Memory/Invalid.md'
    raw = b'x' * 2_000_001 if field == 'content' else b'\xffinvalid synthetic UTF-8'
    original = put(relative, raw)
    with pytest.raises(w.WikiIntegrityError) as raised:
        w._read_derived(original)
    assert raised.value.field == field and raised.value.page == relative and original.read_bytes() == raw
    record(tmp_path, kind='integrity-classification-control', field=field)
