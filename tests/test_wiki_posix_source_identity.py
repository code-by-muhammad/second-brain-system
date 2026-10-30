"""Literal POSIX filenames cannot silently become separator aliases in wiki input."""
import copy
import json
import os
from pathlib import Path

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import note_snapshot


RAW = b'# Synthetic evidence\nA prototype was demonstrated and deployment remains planned.\n'


def write_source(relative, raw=RAW):
    path = s.VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def forbid_model(monkeypatch):
    calls = []
    monkeypatch.setattr(w.shutil, 'which', lambda name: 'synthetic-forbidden-model')
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Path/transport preflight must stop before any model call.')
    monkeypatch.setattr(w.subprocess, 'Popen', forbidden)
    return calls


def observe(tmp_path, report, calls):
    with w.db() as conn:
        batches = [dict(row) for row in conn.execute('SELECT id,status,attempts FROM batches ORDER BY id')]
        sources = [dict(row) for row in conn.execute('SELECT path,attempts FROM sources ORDER BY path')]
    observed = {'report': report, 'model_calls': len(calls), 'worker': w._read_worker_state(),
        'batches': batches, 'source_rows': sources,
        'saved_inputs': len(list((cfg.STATE / 'wiki/batches').glob('*/input.json'))),
        'saved_plans': len(list((cfg.STATE / 'wiki/batches').glob('*/publication.json')))}
    (tmp_path / 'r157-observation.json').write_text(json.dumps(observed, indent=2), encoding='utf-8')
    return observed


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX literal-backslash filename required.')
@pytest.mark.parametrize('counterpart', [False, True], ids=['normalized-missing', 'normalized-different-bytes'])
def test_literal_posix_backslash_source_requires_path_attention_before_selection(brain, monkeypatch, tmp_path, counterpart):
    relative = '03-Resources/regex\\d.md'
    original = write_source(relative)
    assert original.name == 'regex\\d.md' and b'\\' in os.fsencode(original.name)
    normalized = s.safe_path(relative)
    assert normalized != original and normalized == s.VAULT / '03-Resources/regex/d.md'
    if counterpart:
        write_source('03-Resources/regex/d.md', b'# Other synthetic evidence\nThis is a different original source.\n')
    else:
        assert not normalized.exists()
    before = note_snapshot()
    assert relative in [row['path'] for row in w.inventory()]
    calls = forbid_model(monkeypatch)
    report = w.run(4)
    observed = observe(tmp_path, report, calls)
    assert note_snapshot() == before and original.read_bytes() == RAW, observed
    assert not calls and observed['batches'] == [] and observed['source_rows'] == [], observed
    assert observed['saved_inputs'] == observed['saved_plans'] == 0, observed
    assert report.get('status') == 'attention' and report.get('integrity_field') == 'path', observed
    assert report.get('consecutive_failures') == 0 and report.get('retry_after') == 0, observed
    assert report.get('integrity_page') == relative or report.get('integrity_page_sha256') == s.digest(relative.encode()), observed
    public = w.status()['worker']
    assert public.get('integrity_field') == 'path', public
    assert public.get('integrity_page') == relative or public.get('integrity_page_sha256') == s.digest(relative.encode()), public
    repeated = w.run(4)
    assert repeated['status'] == 'attention' and not calls and note_snapshot() == before


def test_ordinary_unicode_nested_source_keeps_exact_context_identity(brain):
    original = write_source('03-Resources/café/regex-notes.md')
    before = note_snapshot()
    rows = w.inventory()
    assert len(rows) == 1 and s.safe_path(rows[0]['path']) == original
    w._validate_source_context({'sources': rows})
    assert note_snapshot() == before and original.read_bytes() == RAW


def test_public_backslash_separator_aliases_still_read_the_ordinary_original(brain):
    original = write_source('03-Resources/regex/d.md')
    alias = '03-Resources\\regex\\d.md'
    before = note_snapshot()
    assert s.safe_path(alias) == original
    assert s.read(alias) == s.read('03-Resources/regex/d.md')
    assert s.note_link(alias, '02-Areas/AI-Memory/Example.md') == 'Source path: `03-Resources/regex/d.md`'
    assert note_snapshot() == before


def test_real_source_io_failure_remains_retryable_without_quality_strikes(brain, monkeypatch, tmp_path):
    original = write_source('03-Resources/ordinary.md')
    before = note_snapshot()
    real_open = Path.open
    reads = []
    def opened(path, *args, **kwargs):
        if path == original and (args[0] if args else kwargs.get('mode', 'r')) == 'rb':
            reads.append(True)
            if len(reads) == 2:
                raise OSError('Synthetic transport/read failure after inventory.')
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    calls = forbid_model(monkeypatch)
    report = w.run(4)
    observed = observe(tmp_path, report, calls)
    assert len(reads) == 2 and report.get('status') == 'retryable' and report.get('reason') == 'OSError', observed
    assert report.get('backoff_seconds') == 60 and report.get('attention_required') is False, observed
    assert not calls and observed['batches'] == [] and observed['source_rows'] == []
    assert observed['saved_inputs'] == observed['saved_plans'] == 0 and note_snapshot() == before


def test_changed_snapshot_hash_stays_transient_instead_of_path_attention(brain):
    original = write_source('03-Resources/ordinary.md')
    before = note_snapshot()
    rows = copy.deepcopy(w.inventory())
    rows[0]['sha256'] = s.digest(b'A different synthetic selected snapshot.')
    with pytest.raises(w.WikiTransientError, match='Source changed'):
        w._validate_source_context({'sources': rows})
    assert original.read_bytes() == RAW and note_snapshot() == before
