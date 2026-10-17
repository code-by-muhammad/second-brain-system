"""Independent R135/R136 contracts; actual modules, wholly synthetic local data.

The tiny-reader probes enforce a positive bounded request before returning any
contents. Capacity fixtures are at most 2.31 MB; no exhaustion is attempted.
"""
import hashlib
import io
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

import brain_git as g
import brain_maintenance as m
import brain_settings as cfg
import brain_store as s
import brain_wiki as w

LIMIT = 2_000_000
TRACE = Path(os.environ['R135_R136_TRACE']) if 'R135_R136_TRACE' in os.environ else None


def record(case, **data):
    if TRACE is None:
        return
    with TRACE.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'case': case, **data}, ensure_ascii=True) + '\n')


def files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    vault = tmp_path / 'vault'
    state = root / 'state'
    state.mkdir(parents=True)
    vault.mkdir()
    (root / 'logs').mkdir()
    for module in (cfg, s):
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'VAULT', vault)
        monkeypatch.setattr(module, 'STATE', state)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'synthetic-test-host')
    # This maintenance test never runs the worker, provider, or Git integration.
    monkeypatch.setattr(w, 'cleanup', lambda: 0)
    template = vault / 'Templates/daily-note.md'
    template.parent.mkdir()
    template.write_text('# Synthetic daily\n\n## Work Log\n', encoding='utf-8')
    return vault, state


def note(brain, raw, relative='01-Projects/Synthetic.md'):
    path = brain[0] / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path, relative


class BoundedRead:
    def __init__(self, stream, requests, maximum):
        self.stream, self.requests, self.maximum = stream, requests, maximum

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size=-1):
        self.requests.append(size)
        assert type(size) is int and 0 < size <= self.maximum, 'Unbounded ordinary note read requested'
        return self.stream.read(size)


def watch_read(monkeypatch, path, maximum=LIMIT + 1):
    original = Path.open
    requests = []

    def opened(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        mode = args[0] if args else kwargs.get('mode', 'r')
        if self == path and 'r' in mode:
            return BoundedRead(stream, requests, maximum)
        return stream

    monkeypatch.setattr(Path, 'open', opened)
    return requests


def fake_sync_git(monkeypatch, expected_text):
    """Reach real validation with a pinned, bounded synthetic staged-object transport."""
    calls = []
    added = False
    oid = 'a' * 40
    encoded = expected_text.encode('utf-8')

    def run(command, **kwargs):
        nonlocal added
        assert command[:2] == ['git', '--literal-pathspecs']
        args = command[4:]
        calls.append(args)
        if args == ['--version']:
            output = 'git version 2.44.0\n'
        elif args and args[0] == 'diff':
            output = ''
        elif args == ['ls-files', '--stage', '-z']:
            output = '100644 ' + oid + ' 0\t01-Projects/Synthetic.md\0' if added else ''
        elif args == ['ls-files', '--modified', '--others', '--exclude-standard', '-z']:
            output = '01-Projects/Synthetic.md\0'
        elif args and args[0] == 'add':
            added = True
            output = ''
        elif args == ['cat-file', '-s', oid]:
            assert kwargs.get('stdout') == subprocess.PIPE and kwargs.get('stderr') == subprocess.DEVNULL
            assert kwargs['env']['GIT_ALLOW_PROTOCOL'] == '' and kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
            return subprocess.CompletedProcess(command, 0, str(len(encoded)).encode() + b'\n', b'')
        elif args == ['cat-file', 'blob', oid]:
            assert kwargs.get('stdout') not in (None, subprocess.PIPE)
            kwargs['stdout'].write(encoded)
            kwargs['stdout'].flush()
            return subprocess.CompletedProcess(command, 0, None, None)
        elif args == ['status', '--porcelain']:
            output = '?? synthetic-unrelated.txt\n'
        else:
            raise AssertionError('Unapproved synthetic Git operation: ' + repr(args))
        return subprocess.CompletedProcess(command, 0, output, '')

    monkeypatch.setattr(g.subprocess, 'run', run)
    return calls


def test_r135_brain_read_requests_bounded_bytes_before_loading_tiny_original(brain, monkeypatch):
    raw = b'Small harmless synthetic note.\n'
    path, relative = note(brain, raw)
    requests = watch_read(monkeypatch, path)
    try:
        result = s.read(relative)
    finally:
        record('read-tiny-request', requests=requests)
    assert requests and result['sha256'] == hashlib.sha256(raw).hexdigest()
    with io.open(path, 'rb') as stream:
        assert stream.read(len(raw) + 1) == raw


def test_r135_sync_requests_bounded_decoded_characters_before_loading_tiny_original(brain, monkeypatch):
    raw = 'Small harmless é note.\r\n'.encode('utf-8')
    path, _ = note(brain, raw)
    expected = raw.decode('utf-8').replace('\r\n', '\n')
    calls = fake_sync_git(monkeypatch, expected)
    requests = watch_read(monkeypatch, path)
    try:
        result = g.sync(brain[0])
    finally:
        record('sync-tiny-request', requests=requests, git_calls=calls)
    assert requests and result.startswith('paused: other local changes')
    assert any(command[0] == 'add' for command in calls)
    with io.open(path, 'rb') as stream:
        assert stream.read(len(raw) + 1) == raw


@pytest.mark.parametrize('length', [LIMIT, LIMIT + 1], ids=['at-byte-cap', 'over-byte-cap'])
def test_r135_brain_read_byte_capacity_and_original_preservation(brain, length):
    raw = b'x' * length
    path, relative = note(brain, raw)
    before = hashlib.sha256(raw).hexdigest()
    if length > LIMIT:
        with pytest.raises(ValueError, match='Note exceeds 2 MB'):
            s.read(relative)
    else:
        result = s.read(relative)
        assert result['sha256'] == before and result['total_lines'] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    record('read-capacity', length=length, preserved_sha256=before)


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbfFirst\r\nSecond\rThird\n', b'\xffinvalid UTF8'],
                         ids=['bom-newlines', 'invalid-utf8'])
def test_r135_brain_read_utf8_and_raw_revision_contract(brain, raw):
    path, relative = note(brain, raw)
    if raw.startswith(b'\xff'):
        with pytest.raises(UnicodeError):
            s.read(relative)
    else:
        result = s.read(relative, start_line=2, max_lines=1)
        assert result['sha256'] == hashlib.sha256(raw).hexdigest()
        assert result['total_lines'] == 3 and result['content'] == '2: Second'
    assert path.read_bytes() == raw


@pytest.mark.parametrize('kind', ['at-character-cap', 'over-character-cap', 'multibyte-over-byte-cap',
                                'crlf-normalized-character-cap', 'invalid-utf8', 'nul', 'credential'])
def test_r135_sync_original_limits_diagnostics_and_preservation(brain, monkeypatch, kind):
    raw = {'at-character-cap': b'x' * LIMIT,
           'over-character-cap': b'x' * (LIMIT + 1),
           'multibyte-over-byte-cap': 'é'.encode('utf-8') * (LIMIT // 2 + 1),
           'crlf-normalized-character-cap': b'x' * (LIMIT - 1) + b'\r\n',
           'invalid-utf8': b'\xffinvalid', 'nul': b'ordinary\x00invalid',
           'credential': b'password=' + b'a' * 16}[kind]
    path, _ = note(brain, raw)
    expected = raw.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    calls = fake_sync_git(monkeypatch, expected)
    refused = kind in ('over-character-cap', 'invalid-utf8', 'nul', 'credential')
    if refused:
        with pytest.raises(g.GitSyncError) as error:
            g.sync(brain[0])
        message = str(error.value)
        assert 'preserved' in message.lower()
        assert 'a' * 16 not in message
        assert not any(command[0] == 'add' for command in calls)
    else:
        assert g.sync(brain[0]).startswith('paused: other local changes')
        assert any(command[0] == 'add' for command in calls)
    assert path.read_bytes() == raw
    record('sync-capacity', kind=kind, bytes=len(raw), decoded_characters=len(expected), refused=refused,
           preserved_sha256=hashlib.sha256(raw).hexdigest(), git_calls=calls)


def test_r135_existing_small_derived_write_cas_backup_and_original_source_stay_intact(brain):
    original = b'# Synthetic source\r\nOwner evidence.\r\n'
    source, relative = note(brain, original)
    destination = '02-Areas/AI-Memory/Synthetic.md'
    first = s.update_current(destination, 'Synthetic derived page', 'Initial derived text.', [relative])
    old = (brain[0] / destination).read_bytes()
    with pytest.raises(ValueError, match='Concurrent change'):
        s.update_current(destination, 'Synthetic derived page', 'Replacement.', [relative], '0' * 64)
    assert (brain[0] / destination).read_bytes() == old
    second = s.update_current(destination, 'Synthetic derived page', 'Replacement.', [relative], first['sha256'])
    assert second['status'] == 'saved'
    assert (brain[1] / 'write-backups' / first['sha256']).read_bytes() == old
    assert source.read_bytes() == original


def test_r135_archive_231mb_exception_remains_streamed_and_hash_bound(brain, monkeypatch):
    raw = (b'Harmless synthetic compiler history.\n' * 70_000)[:2_310_000]
    assert len(raw) == 2_310_000 and len(raw) > LIMIT
    sha = hashlib.sha256(raw).hexdigest()
    relative = '02-Areas/AI-Memory/Wiki/Log-Archives/' + sha + '.md'
    path, _ = note(brain, raw, relative)
    requests = watch_read(monkeypatch, path, g.ARCHIVE_BLOCK)
    result = g._archive_file(relative, path)
    assert result['raw_sha256'] == sha and result['bytes'] == len(raw)
    assert requests and all(0 < request <= 65_536 for request in requests)
    with pytest.raises(g.GitSyncError, match='SHA256 filename'):
        g._archive_file(relative.replace(sha, '0' * 64), path)
    record('archive-exception', bytes=len(raw), sha256=sha, maximum_request=max(requests))


def capture(event):
    return s.capture(event, 'Synthetic milestone', 'Bounded synthetic outcome.', 'audit-probe',
                     'synthetic://r135-r136', decisions=['Keep original notes intact.'], next_steps=[])


@pytest.mark.parametrize('event', ['event\ridentity', 'event\nidentity', 'event\r\nidentity', 'event-->identity'],
                         ids=['CR', 'LF', 'CRLF', 'comment-terminator'])
def test_r136_frontdoor_rejects_unlinkable_event_before_capture_audit_or_backup_write(brain, event):
    before_vault, before_state = files(brain[0]), files(brain[1])
    error = None
    try:
        saved = capture(event)
    except ValueError as caught:
        error = str(caught)
        saved = None
    if saved is not None:
        capture_path = brain[0] / saved['path']
        original = capture_path.read_bytes()
        duplicate = capture(event)
        maintenance = m.maintenance(sync=False)
        record('capture-unlinkable', event=event, accepted=saved, duplicate=duplicate,
               linked=maintenance['captures_linked'], review=maintenance['review'],
               capture_sha256=hashlib.sha256(original).hexdigest(), preserved=capture_path.read_bytes() == original)
    else:
        record('capture-unlinkable', event=event, rejected=error)
    assert error is not None, 'Capture accepted an ID that maintenance permanently refuses'
    assert files(brain[0]) == before_vault and files(brain[1]) == before_state


@pytest.mark.parametrize('event', ['normal-event', 'tab\tid', 'unicode\u2028id', 'paragraph\u2029id',
                                 'vertical\x0bid', 'Unicode-اردو-漢字', 'x' * 180])
def test_r136_supported_identities_link_exactly_once_and_remain_immutable(brain, event):
    saved = capture(event)
    path = brain[0] / saved['path']
    original = path.read_bytes()
    assert capture(event)['status'] == 'duplicate'
    first = m.maintenance(sync=False)
    second = m.maintenance(sync=False)
    assert first['captures_linked'] == 1 and not first['review']
    assert second['captures_linked'] == 0 and not second['review']
    daily = list((brain[0] / '05-Daily').glob('*.md'))
    assert len(daily) == 1
    assert daily[0].read_text(encoding='utf-8').count('<!-- brain-capture:' + event + ' -->') == 1
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='immutable'):
        s.capture(event, 'Changed milestone', 'Different outcome.', 'audit-probe', 'synthetic://r135-r136')
    assert path.read_bytes() == original


@pytest.mark.parametrize('event', ['legacy\ridentity', 'legacy\nidentity', 'legacy-->identity'])
def test_r136_historical_unlinkable_capture_stays_intact_and_current_guard_remains(brain, event):
    relative = '00-Inbox/AI-Captures/' + hashlib.sha256(event.encode()).hexdigest()[:24] + '.md'
    raw = s.note_text({'date': '2026-10-17', 'event_id': event, 'captured_at': '2026-10-17T01:00:00+05:00',
                       'source_app': 'synthetic-legacy', 'status': 'captured'}, '# Synthetic historical capture\n').encode()
    path, _ = note(brain, raw, relative)
    for _ in range(2):
        result = m.maintenance(sync=False)
        assert result['captures_linked'] == 0 and len(result['review']) == 1
        assert result['review'][0]['reasons'] == ['invalid capture metadata; original preserved']
        assert path.read_bytes() == raw
    assert not (brain[0] / '05-Daily').exists()
