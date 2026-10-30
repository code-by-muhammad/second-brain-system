"""Synthetic 2 MB boundaries for public source-backed writes; no owner data."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
import brain_settings as cfg
import brain_store as s

LIMIT = 2_000_000
TARGET = '02-Areas/AI-Memory/Current.md'


def make_note(relative, size=None):
    path = s.VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = b'---\ntype: note\ntags: [ai-memory, derived]\nsources: []\n---\n# Synthetic evidence\n'
    raw = prefix if size is None else prefix + b'x' * (size - len(prefix))
    path.write_bytes(raw)
    return path, raw


def protected():
    result = {'vault/' + p.relative_to(s.VAULT).as_posix(): s.digest(p.read_bytes()) for p in s.VAULT.rglob('*.md')}
    for p in s.STATE.rglob('*'):
        if p.is_file() and (p.name == 'audit.jsonl' or 'write-backups' in p.parts):
            result['state/' + p.relative_to(s.STATE).as_posix()] = s.digest(p.read_bytes())
    return result


def observe(monkeypatch, watched, *, grow_on_open=False, change_on_lock=None, growth_requires_writer=True):
    """Observe real reads and the real writer lock; never replace product results."""
    reads = []; locks = {'constructed': 0, 'entered': 0, 'holding': False, 'grew': False}
    real_open = Path.open; real_lock = s.FileLock
    class Stream:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, name): return getattr(self.stream, name)
        def read(self, size=-1):
            reads.append({'requested': size, 'under_writer': locks['holding']})
            return self.stream.read(size)
    def opened(path, mode='r', *args, **kwargs):
        if path == watched and mode in ('r', 'rb'):
            if grow_on_open and not locks['grew']:
                if growth_requires_writer:
                    assert locks['holding'], 'Growth fixture must occur after the real writer lock.'
                with real_open(path, 'ab') as stream:
                    stream.write(b'x' * (LIMIT + 1 - path.stat().st_size))
                locks['grew'] = True
            return Stream(real_open(path, mode, *args, **kwargs))
        return real_open(path, mode, *args, **kwargs)
    class Lock:
        def __init__(self, *args, **kwargs):
            locks['constructed'] += 1
            self.real = real_lock(*args, **kwargs)
        def __enter__(self):
            self.real.__enter__(); locks['entered'] += 1; locks['holding'] = True
            if change_on_lock: change_on_lock()
            return self
        def __exit__(self, *args):
            try: return self.real.__exit__(*args)
            finally: locks['holding'] = False
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(s, 'FileLock', Lock)
    return reads, locks


def call_update(sources, expected=None):
    return s.update_current(TARGET, 'Synthetic current summary', 'Supported synthetic evidence.', sources, expected)


def captured(call):
    try: return {'receipt': call()}
    except ValueError as exc: return {'error': type(exc).__name__, 'message': str(exc)}


def record(tmp_path, result, reads, locks, before):
    evidence = {'result': result, 'actual_reads': reads, 'writer': locks,
                'protected_before': before, 'protected_after': protected()}
    (tmp_path / 'bounded-read-observation.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


@pytest.mark.parametrize('kind', ['source', 'destination'])
def test_oversize_existing_note_refused_before_writer_lock(brain, monkeypatch, tmp_path, kind):
    source, original = make_note('01-Projects/Evidence.md', LIMIT + 1 if kind == 'source' else None)
    target, old = make_note(TARGET, LIMIT + 1 if kind == 'destination' else None)
    watched = source if kind == 'source' else target
    with pytest.raises(ValueError, match='Note exceeds 2 MB'):
        s.read(watched.relative_to(s.VAULT).as_posix())
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, watched)
        result = captured(lambda: call_update(['01-Projects/Evidence.md'], s.digest(old)))
    evidence = record(tmp_path, result, reads, locks, before)
    assert result.get('error') == 'ValueError' and '2 MB' in result.get('message', ''), evidence
    assert locks['constructed'] == 0 and not reads and protected() == before, evidence
    assert source.read_bytes() == original and target.read_bytes() == old


@pytest.mark.parametrize('kind', ['source', 'destination'])
def test_growth_at_actual_binary_open_is_bounded_under_writer(brain, monkeypatch, tmp_path, kind):
    source, _ = make_note('01-Projects/Evidence.md')
    target, old = make_note(TARGET)
    watched = source if kind == 'source' else target
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, watched, grow_on_open=True)
        result = captured(lambda: call_update(['01-Projects/Evidence.md'], s.digest(old)))
    expected = dict(before)
    expected['vault/' + watched.relative_to(s.VAULT).as_posix()] = s.digest(watched.read_bytes())
    evidence = record(tmp_path, result, reads, locks, expected)
    assert locks['grew'] and locks['entered'] == 1 and watched.stat().st_size == LIMIT + 1
    assert result.get('error') == 'ValueError' and '2 MB' in result.get('message', ''), evidence
    assert reads and all(0 < item['requested'] <= LIMIT + 1 and item['under_writer'] for item in reads), evidence
    assert protected() == expected and not (s.STATE / 'write-backups').exists(), evidence


def test_direct_writer_destination_read_is_also_bounded_and_preserved(brain, monkeypatch, tmp_path):
    target, _ = make_note(TARGET, LIMIT + 1)
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, target)
        result = captured(lambda: s._write(target, '# Synthetic replacement\n', 'synthetic-test'))
    evidence = record(tmp_path, result, reads, locks, before)
    assert result.get('error') == 'ValueError' and '2 MB' in result.get('message', ''), evidence
    assert reads and all(0 < item['requested'] <= LIMIT + 1 for item in reads), evidence
    assert protected() == before and not (s.STATE / 'write-backups').exists(), evidence


def test_exact_2mb_sources_destination_30_source_hashes_backup_cas_and_retry(brain, monkeypatch):
    monkeypatch.setattr(s, 'stamp', lambda: '2026-10-17T23:00:00+05:00')
    sources = []
    for i in range(30):
        source, _ = make_note('01-Projects/Evidence-' + str(i) + '.md', LIMIT if i == 0 else None)
        sources.append(source.relative_to(s.VAULT).as_posix())
    target, old = make_note(TARGET, LIMIT)
    source_hashes = {name: s.digest((s.VAULT / name).read_bytes()) for name in sources}
    first = call_update(sources, s.digest(old))
    assert first['status'] == 'saved' and (s.STATE / 'write-backups' / s.digest(old)).read_bytes() == old
    assert s.digest(target.read_bytes()) == first['sha256']
    metadata = s.frontmatter(target.read_text(encoding='utf-8'), strict=True)
    assert metadata['sources'] == sources and metadata['source_hashes'] == source_hashes
    after = protected()
    assert call_update(sources, first['sha256'])['status'] == 'unchanged' and protected() == after
    with pytest.raises(ValueError, match='Concurrent change'):
        call_update(sources, '0' * 64)
    assert protected() == after


def test_source_hash_is_fresh_after_real_writer_acquisition(brain, monkeypatch, tmp_path):
    source, _ = make_note('01-Projects/Evidence.md')
    fresh = b'\xef\xbb\xbf# Fresh synthetic evidence\r\nChanged before writer-owned read.\r\n'
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, source, change_on_lock=lambda: source.write_bytes(fresh))
        result = captured(lambda: call_update(['01-Projects/Evidence.md']))
    evidence = record(tmp_path, result, reads, locks, before)
    assert result.get('receipt', {}).get('status') == 'saved', evidence
    info = s.frontmatter((s.VAULT / TARGET).read_text(encoding='utf-8'), strict=True)
    assert info['source_hashes'] == {'01-Projects/Evidence.md': s.digest(fresh)}
    assert source.read_bytes() == fresh and locks['entered'] == 1 and reads and all(row['under_writer'] for row in reads)


def test_31_sources_still_refused_before_lock(brain, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Invalid list must precede writer acquisition.')
    monkeypatch.setattr(s, 'FileLock', forbidden)
    with pytest.raises(ValueError, match='1–30'):
        call_update(['01-Projects/Synthetic-' + str(i) + '.md' for i in range(31)])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['source', 'destination'])
async def test_registered_mcp_update_refuses_same_oversize_note_as_brain_read(brain, kind):
    """Actual in-process registered tools; no HTTP/auth boundary claim or network."""
    from fastmcp import Client
    source, original = make_note('01-Projects/Evidence.md', LIMIT + 1 if kind == 'source' else None)
    target, old = make_note(TARGET, LIMIT + 1 if kind == 'destination' else None)
    (s.STATE / 'access.json').write_text(json.dumps({'token_sha256': hashlib.sha256(b'synthetic-r156-only-token').hexdigest()}), encoding='utf-8')
    (s.STATE / 'config.json').write_text(json.dumps({'projects': {'brain': {'path': str(s.VAULT), 'mode': 'local'}},
        'default_project': 'brain', 'semantic_search_enabled': False}), encoding='utf-8')
    server = importlib.import_module('brain_server')
    watched = source if kind == 'source' else target
    async with Client(server.mcp) as client:
        read = await client.call_tool('brain_read', {'path': watched.relative_to(s.VAULT).as_posix()}, raise_on_error=False)
        assert read.is_error
        before = protected()
        result = await client.call_tool('brain_update_current', {'path': TARGET, 'title': 'Synthetic current summary',
            'content': 'Supported synthetic evidence.', 'source_paths': ['01-Projects/Evidence.md'],
            'expected_sha256': s.digest(old)}, raise_on_error=False)
        (s.STATE.parent / 'registered-mcp-observation.json').write_text(json.dumps({'brain_read_is_error': read.is_error,
            'brain_update_is_error': result.is_error, 'source_size': len(original), 'destination_size': len(old),
            'protected_before': before, 'protected_after': protected(), 'in_process_registered_tools': True,
            'authenticated_http_claimed': False}, indent=2), encoding='utf-8')
        assert result.is_error and protected() == before


CAPTURE = ('synthetic-r156-capture', 'Synthetic milestone', 'Supported synthetic event.', 'tests', 'synthetic-local-proof')


@pytest.mark.parametrize('grow', [False, True], ids=['already-oversize', 'growth-at-open'])
def test_capture_existing_note_size_refusal_preserves_immutable_capture(brain, monkeypatch, tmp_path, grow):
    initial = s.capture(*CAPTURE)
    capture = s.VAULT / initial['path']
    if not grow:
        with capture.open('ab') as stream:
            stream.write(b'x' * (LIMIT + 1 - capture.stat().st_size))
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, capture, grow_on_open=grow)
        result = captured(lambda: s.capture(*CAPTURE))
    expected = dict(before); expected['vault/' + initial['path']] = s.digest(capture.read_bytes())
    evidence = record(tmp_path, result, reads, locks, expected)
    assert result.get('error') == 'ValueError' and '2 MB' in result.get('message', ''), evidence
    if grow:
        assert locks['entered'] == 1 and locks['grew'] and reads
        assert all(0 < row['requested'] <= LIMIT + 1 and row['under_writer'] for row in reads), evidence
    else:
        assert locks['constructed'] == 0 and not reads, evidence
    assert capture.stat().st_size == LIMIT + 1 and protected() == expected, evidence


def test_capture_exact_2mb_bom_crlf_duplicate_and_large_reference_stay_valid(brain):
    original, original_raw = make_note('01-Projects/Large-reference.md', LIMIT + 1)
    result = s.capture(*CAPTURE, evidence_paths=['01-Projects/Large-reference.md'])
    capture = s.VAULT / result['path']
    raw = b'\xef\xbb\xbf' + capture.read_bytes().replace(b'\n', b'\r\n')
    raw += b'x' * (LIMIT - len(raw))
    capture.write_bytes(raw)
    before = protected()
    duplicate = s.capture(*CAPTURE, evidence_paths=['01-Projects/Large-reference.md'])
    assert duplicate['status'] == 'duplicate' and duplicate['sha256'] == s.digest(raw)
    assert capture.read_bytes() == raw and original.read_bytes() == original_raw and protected() == before


def test_fresh_search_skips_open_time_growth_and_keeps_other_results(brain, monkeypatch, tmp_path):
    source, _ = make_note('01-Projects/Evidence.md')
    neighbor, neighbor_raw = make_note('01-Projects/Neighbor.md')
    before = protected()
    with monkeypatch.context() as patch:
        reads, locks = observe(patch, source, grow_on_open=True, growth_requires_writer=False)
        result = s.fresh_search('synthetic evidence')
    evidence = record(tmp_path, {'matches': result}, reads, locks, before)
    assert locks['constructed'] == 0 and locks['grew'] and source.stat().st_size == LIMIT + 1
    assert reads and all(0 < row['requested'] <= LIMIT + 1 for row in reads), evidence
    assert [row['path'] for row in result] == ['01-Projects/Neighbor.md'], evidence
    assert neighbor.read_bytes() == neighbor_raw


@pytest.mark.parametrize('invalid', [False, True], ids=['bom-mixed-newlines', 'invalid-utf8'])
def test_fresh_search_preserves_decoding_newlines_and_skip_contract(brain, invalid):
    source, _ = make_note('01-Projects/Unicode.md')
    raw = b'\xffinvalid needle' if invalid else '\ufeff# Synthetic\r\nordinary first\rneedle café\n'.encode('utf-8')
    source.write_bytes(raw)
    result = s.fresh_search('needle')
    assert result == [] if invalid else len(result) == 1 and result[0]['line'] == 3
    assert source.read_bytes() == raw
