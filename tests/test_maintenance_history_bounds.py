"""Maintenance keeps compiler history opaque and bounds optional note metadata."""
from pathlib import Path

import pytest

import brain_git
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki
from test_wiki import fixture


LIMIT = 2_000_000
DERIVED = '02-Areas/AI-Memory/'


def _capture_and_git(brain, monkeypatch):
    vault, _ = brain
    templates = vault / 'Templates'
    templates.mkdir(exist_ok=True)
    (templates / 'daily-note.md').write_text(
        '# Synthetic daily\n\n## Work Log\n', encoding='utf-8')
    store.capture('bounded-maintenance-capture', 'Synthetic milestone',
                  'Synthetic bounded-maintenance result.', 'test',
                  'synthetic fixture')
    calls = []
    monkeypatch.setattr(brain_git, 'sync', lambda: calls.append(True) or 'synced')
    return calls


def _bounded_binary_spy(monkeypatch, target):
    """Reject whole-file inspection and record actual bytes consumed by review."""
    original_open = Path.open
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    reads = []

    class BoundedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, size=-1):
            assert isinstance(size, int) and 0 < size <= LIMIT + 1
            chunk = self.handle.read(size)
            assert isinstance(chunk, bytes), 'Derived metadata must be decoded after its byte bound'
            reads.append(len(chunk))
            assert sum(reads) <= LIMIT + 1
            return chunk

    def monitored_open(path, mode='r', *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path != target:
            return handle
        assert mode == 'rb', 'Derived metadata inspection must use a binary byte bound'
        return BoundedReader(handle)

    def monitored_text(path, *args, **kwargs):
        assert path != target, 'Derived metadata must not use a whole-file text read'
        return original_read_text(path, *args, **kwargs)

    def monitored_bytes(path, *args, **kwargs):
        assert path != target, 'Derived metadata must not use a whole-file byte read'
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', monitored_open)
    monkeypatch.setattr(Path, 'read_text', monitored_text)
    monkeypatch.setattr(Path, 'read_bytes', monitored_bytes)
    return reads


@pytest.mark.parametrize('role', ['maintainer', 'capture'])
def test_permanent_raw_sha_archive_generated_by_apply_is_never_read(brain, monkeypatch, role):
    _, packet, result = fixture(brain)
    log = store.VAULT / wiki.BASE / 'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    raw = b'# Historical compilation history\n' + b'x' * 2_000_001
    log.write_bytes(raw)
    wiki.apply(packet, result)
    archive = store.VAULT / wiki.BASE / 'Log-Archives' / (store.digest(raw) + '.md')
    assert archive.read_bytes() == raw
    log_before = log.read_bytes()
    original_open = Path.open
    original_text = Path.read_text
    original_bytes = Path.read_bytes
    reads = []
    calls = []
    monkeypatch.setattr(cfg, 'ROLE', role)
    monkeypatch.setattr(brain_git, 'sync', lambda: calls.append(True) or 'synced')

    def reject(method, path, *args, **kwargs):
        if path == archive:
            reads.append(method.__name__)
            raise AssertionError('Maintenance attempted to read permanent compiler history')
        return method(path, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(Path, 'open', lambda path, *a, **k: reject(original_open, path, *a, **k))
        guard.setattr(Path, 'read_text', lambda path, *a, **k: reject(original_text, path, *a, **k))
        guard.setattr(Path, 'read_bytes', lambda path, *a, **k: reject(original_bytes, path, *a, **k))
        result = maintenance.maintenance()
    assert reads == []
    assert calls == [True] and result['git'] == 'synced' and result['status'] == 'completed'
    assert archive.read_bytes() == raw and log.read_bytes() == log_before


@pytest.mark.parametrize('relative', [
    wiki.BASE + 'Log.md',
    wiki.BASE + 'lOg.Md',
    wiki.BASE + 'Log-Archives/' + 'a' * 64 + '.md',
    wiki.BASE + 'lOg-ArChIvEs/Nested/Owner-History.md',
    wiki.BASE + 'Log-Archives/Nested/Deeper/Log.md',
])
@pytest.mark.parametrize('role', ['maintainer', 'capture'])
def test_compiler_history_paths_are_skipped_before_any_content_read(brain, monkeypatch, relative, role):
    target = brain[0] / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    # Invalid text makes an accidental read observable even without a frontmatter header.
    raw = b'\xff\xfe' + b'x' * (LIMIT + 9)
    target.write_bytes(raw)
    original_open = Path.open
    original_text = Path.read_text
    original_bytes = Path.read_bytes
    reads = []
    calls = []
    monkeypatch.setattr(cfg, 'ROLE', role)
    monkeypatch.setattr(brain_git, 'sync', lambda: calls.append(True) or 'synced')

    def reject(method, path, *args, **kwargs):
        if path == target:
            reads.append(method.__name__)
            raise AssertionError('Compiler history must be skipped before reading')
        return method(path, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(Path, 'open', lambda path, *a, **k: reject(original_open, path, *a, **k))
        guard.setattr(Path, 'read_text', lambda path, *a, **k: reject(original_text, path, *a, **k))
        guard.setattr(Path, 'read_bytes', lambda path, *a, **k: reject(original_bytes, path, *a, **k))
        result = maintenance.maintenance()
    assert reads == []
    assert result['status'] == 'completed' and result['review'] == []
    assert calls == [True] and result['git'] == 'synced'
    assert target.read_bytes() == raw


@pytest.mark.parametrize('raw', [
    b'---\ntitle: Invalid text\n---\nBody \xff must stay unchanged.\n',
    b'---\ntitle: [unterminated\n---\nBody must stay unchanged.\n',
    b'---\n- unsupported root\n---\nBody must stay unchanged.\n',
    b'---\ntitle: !!bool maybe\n---\nBody must stay unchanged.\n',
    b'---\ndate: 2026-99-99\n---\nBody must stay unchanged.\n',
    b'---\ntitle: Oversized ASCII\n---\n' + b'x' * LIMIT,
    b'---\ntitle: Oversized UTF8\n---\n' + '\u00e9'.encode('utf-8') * (LIMIT // 2),
], ids=['invalid-utf8', 'malformed-yaml', 'root-list', 'explicit-constructor',
        'invalid-date', 'oversized-ascii', 'oversized-utf8'])
def test_unreadable_derived_metadata_is_bounded_preserved_and_does_not_block_work(
        brain, monkeypatch, raw):
    calls = _capture_and_git(brain, monkeypatch)
    target = brain[0] / DERIVED / 'Owner-Review.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    with monkeypatch.context() as guard:
        reads = _bounded_binary_spy(guard, target)
        result = maintenance.maintenance()
    assert reads and sum(reads) <= LIMIT + 1
    assert target.read_bytes() == raw
    rows = [row for row in result['review'] if row['path'] == target.relative_to(brain[0]).as_posix()]
    assert rows == [{'path': target.relative_to(brain[0]).as_posix(),
                     'reasons': ['derived metadata could not be read; original preserved']}]
    assert result['captures_linked'] == 1 and calls == [True]
    assert result['git'] == 'synced' and result['status'] == 'completed'


@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'], ids=['utf8', 'utf8-bom'])
def test_bounded_valid_title_and_source_sha_metadata_remain_unchanged(brain, monkeypatch, bom):
    source = brain[0] / '01-Projects/Source.md'
    source.parent.mkdir(parents=True)
    original_source = b'Synthetic source evidence.\r\n'
    source.write_bytes(original_source)
    target = brain[0] / DERIVED / 'Current.md'
    target.parent.mkdir(parents=True)
    raw = bom + ('---\ntitle: Preserve the title\nsources:\n- 01-Projects/Source.md\n'
                 'source_hashes:\n  01-Projects/Source.md: ' + store.digest(original_source)
                 + '\n---\n\n# Preserve the body\nSynthetic current evidence.\n').encode('utf-8')
    target.write_bytes(raw)
    with monkeypatch.context() as guard:
        reads = _bounded_binary_spy(guard, target)
        result = maintenance.maintenance(sync=False)
    assert sum(reads) == len(raw)
    assert result['review'] == [] and result['status'] == 'completed'
    assert target.read_bytes() == raw and source.read_bytes() == original_source


@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'], ids=['utf8', 'utf8-bom'])
def test_bounded_utf8_sig_frontmatter_still_reports_a_missing_source(brain, monkeypatch, bom):
    target = brain[0] / DERIVED / 'Missing-Source.md'
    target.parent.mkdir(parents=True)
    raw = bom + b'---\ntitle: Missing evidence\nsources: [01-Projects/Missing.md]\n---\n\nBody.\n'
    target.write_bytes(raw)
    with monkeypatch.context() as guard:
        reads = _bounded_binary_spy(guard, target)
        result = maintenance.maintenance(sync=False)
    assert sum(reads) == len(raw) and target.read_bytes() == raw
    assert result['review'] == [{'path': DERIVED + 'Missing-Source.md',
                                 'reasons': ['missing source: 01-Projects/Missing.md']}]


@pytest.mark.parametrize('byte_count', [LIMIT, LIMIT + 1], ids=['exact-bound', 'one-byte-over'])
def test_derived_read_boundary_uses_bytes_and_preserves_body(brain, monkeypatch, byte_count):
    target = brain[0] / DERIVED / 'Boundary.md'
    target.parent.mkdir(parents=True)
    header = b'---\ntitle: Boundary\n---\n'
    raw = header + b'x' * (byte_count - len(header))
    target.write_bytes(raw)
    with monkeypatch.context() as guard:
        reads = _bounded_binary_spy(guard, target)
        result = maintenance.maintenance(sync=False)
    assert sum(reads) == byte_count and target.read_bytes() == raw
    expected = [] if byte_count == LIMIT else [
        {'path': DERIVED + 'Boundary.md',
         'reasons': ['derived metadata could not be read; original preserved']}]
    assert result['review'] == expected


def test_other_log_named_derived_note_is_still_reviewed(brain, monkeypatch):
    target = brain[0] / DERIVED / 'Log.md'
    target.parent.mkdir(parents=True)
    raw = b'---\ntitle: Ordinary note\nsources: [01-Projects/Missing.md]\n---\nBody.\n'
    target.write_bytes(raw)
    with monkeypatch.context() as guard:
        reads = _bounded_binary_spy(guard, target)
        result = maintenance.maintenance(sync=False)
    assert sum(reads) == len(raw) and target.read_bytes() == raw
    assert result['review'] == [{'path': DERIVED + 'Log.md',
                                 'reasons': ['missing source: 01-Projects/Missing.md']}]


def _source_metadata(brain, raw, recorded=None):
    source = brain[0] / '01-Projects/Owner source.md'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    page = brain[0] / DERIVED / 'Owner derived.md'
    page.parent.mkdir(parents=True, exist_ok=True)
    relative = source.relative_to(brain[0]).as_posix()
    info = {'title': 'Preserve owner title', 'sources': [relative],
            'source_hashes': {relative: recorded if recorded is not None else store.digest(raw)}}
    page.write_text(store.note_text(info, '# Preserve owner body.\n'), encoding='utf-8')
    return source, page, info


def _streaming_source_spy(monkeypatch, target, fault=None):
    original_open = Path.open
    original_bytes = Path.read_bytes
    original_text = Path.read_text
    requests = []

    class StreamingReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, size=-1):
            assert isinstance(size, int) and 0 < size <= 65536
            requests.append(size)
            if fault == 'io':
                raise OSError('Synthetic private I/O failure must stay local')
            chunk = self.handle.read(size)
            assert isinstance(chunk, bytes)
            if fault == 'growth' and size == 1 and not chunk:
                return b'!'
            return chunk

    def monitored_open(path, mode='r', *args, **kwargs):
        if path == target:
            assert mode == 'rb', 'Source SHA must hash raw bytes without text normalization'
            return StreamingReader(original_open(path, mode, *args, **kwargs))
        return original_open(path, mode, *args, **kwargs)

    def reject_bytes(path, *args, **kwargs):
        assert path != target, 'Source SHA must not whole-file read the original'
        return original_bytes(path, *args, **kwargs)

    def reject_text(path, *args, **kwargs):
        assert path != target, 'Source SHA must not decode the original'
        return original_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', monitored_open)
    monkeypatch.setattr(Path, 'read_bytes', reject_bytes)
    monkeypatch.setattr(Path, 'read_text', reject_text)
    return requests


@pytest.mark.parametrize('changed', [False, True], ids=['valid-recorded-large-sha', 'owner-source-grew'])
def test_source_raw_sha_large_original_is_streamed(brain, monkeypatch, changed):
    prefix = b'\xef\xbb\xbf# Owner source\r\n'
    raw = prefix + b'x' * (LIMIT + 1 - len(prefix))
    recorded = store.digest(b'# Earlier owner revision.\n') if changed else None
    source, page, info = _source_metadata(brain, raw, recorded=recorded)
    before_page = page.read_bytes()
    with monkeypatch.context() as guard:
        requests = _streaming_source_spy(guard, source)
        stale = maintenance.review_sources(page, info)
    assert requests and max(requests) <= 65536
    # Include the one-byte EOF/growth probe, without reading beyond the initial file size.
    assert sum(requests) == len(raw) + 1
    expected = ['source changed: 01-Projects/Owner source.md'] if changed else []
    assert stale == expected
    assert source.read_bytes() == raw and page.read_bytes() == before_page


def test_small_bom_crlf_source_sha_remains_exact(brain):
    raw = b'\xef\xbb\xbf# Owner evidence\r\nExact raw source bytes remain unchanged.\r\n'
    source, page, info = _source_metadata(brain, raw)
    before_page = page.read_bytes()
    normalized = raw.decode('utf-8-sig').replace('\r\n', '\n').encode('utf-8')
    assert store.digest(normalized) != info['source_hashes']['01-Projects/Owner source.md']
    assert maintenance.review_sources(page, info) == []
    assert source.read_bytes() == raw and page.read_bytes() == before_page


@pytest.mark.parametrize('fault', ['growth', 'io'])
def test_source_streaming_fault_is_generic_and_preserves_work_and_git_sync(brain, monkeypatch, fault):
    calls = _capture_and_git(brain, monkeypatch)
    raw = b'\xef\xbb\xbf# Owner source\r\n' + b'x' * (LIMIT + 1)
    source, page, _ = _source_metadata(brain, raw)
    before_page = page.read_bytes()
    with monkeypatch.context() as guard:
        requests = _streaming_source_spy(guard, source, fault=fault)
        result = maintenance.maintenance()
    assert requests and max(requests) <= 65536
    assert source.read_bytes() == raw and page.read_bytes() == before_page
    assert result['captures_linked'] == 1 and calls == [True]
    assert result['git'] == 'synced' and result['status'] == 'completed'
    assert result['review'] == [{'path': DERIVED + 'Owner derived.md',
                                 'reasons': ['source metadata could not be validated; original preserved']}]
    assert 'Synthetic private' not in str(result)
