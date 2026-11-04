"""Synthetic shared Control admission: actual links, positive bounded I/O, compatibility."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import brain_settings as cfg

REL = '02-Areas/AI-Memory/Wiki/Control.md'
LIMIT = 2_000_000
VALID = b'---\nmaintainer_device_id: test-host\n---\n'


def put(path, raw=VALID):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def outcome(call):
    try:
        result = call()
        return {'error': None, 'result': result}, None
    except Exception as exc:
        return {'error': type(exc).__name__, 'message': str(exc)}, exc


def record(tmp_path, data):
    (tmp_path/'r164-control-observation.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')


def linked_control(vault, tmp_path, kind):
    outside = tmp_path/'external-fixture'
    target = outside/'Control.md'
    if not kind.startswith('dangling'):
        put(target)
    else:
        outside.mkdir()
    control = vault/REL
    if kind in ('file', 'dangling-file'):
        if os.name == 'nt':
            pytest.skip('Native file symlinks require a Windows privilege; real ext4 coverage is separate.')
        control.parent.mkdir(parents=True)
        control.symlink_to(target)
        link = control
    else:
        relative = {'wiki': '02-Areas/AI-Memory/Wiki', 'area': '02-Areas/AI-Memory', 'areas': '02-Areas', 'dangling-wiki': '02-Areas/AI-Memory/Wiki'}[kind]
        link = vault/relative
        tail = control.relative_to(link)
        if kind != 'dangling-wiki':
            put(outside/tail)
        else:
            outside = outside/'missing-directory'
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            made = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True, text=True)
            assert made.returncode == 0 and os.path.isjunction(link), made.stderr
        else:
            link.symlink_to(outside, target_is_directory=True)
        target = outside/tail
    return control, link, target


@pytest.mark.parametrize('kind', ['file', 'wiki', 'area', 'areas', 'dangling-file', 'dangling-wiki'])
def test_actual_control_link_or_parent_is_refused_before_open(brain, monkeypatch, tmp_path, kind):
    vault, _ = brain
    control, link, target = linked_control(vault, tmp_path, kind)
    before = target.read_bytes() if target.exists() else None
    link_before = link.lstat()
    opened = []
    original = Path.open
    def observe(path, *args, **kwargs):
        if path == control:
            opened.append({'mode': args[0] if args else kwargs.get('mode', 'r')})
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', observe)
        actual, error = outcome(cfg.require_maintainer)
    after = target.read_bytes() if target.exists() else None
    data = {'kind': kind, 'actual': actual, 'control_opens': opened, 'target_unchanged': before == after, 'link_unchanged': link_before == link.lstat(), 'platform': os.name}
    record(tmp_path, data)
    assert before == after and link_before == link.lstat(), data
    assert isinstance(error, PermissionError) and 'could not be verified' in str(error), data
    assert opened == [], data


class Probe:
    def __init__(self, stream, rows):
        self.stream, self.rows = stream, rows
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def read(self, size=-1):
        value = self.stream.read(size)
        self.rows.append({'requested': size, 'returned': len(value), 'binary': isinstance(value, bytes)})
        return value
    def __getattr__(self, name):
        return getattr(self.stream, name)


@pytest.mark.parametrize('mode', ['small', 'exact', 'oversized', 'grown'])
def test_control_reads_have_positive_byte_cap_and_refuse_oversize(brain, monkeypatch, tmp_path, mode):
    vault, _ = brain
    size = LIMIT if mode == 'exact' else LIMIT + 1 if mode == 'oversized' else len(VALID)
    control = put(vault/REL, VALID+b' '*(size-len(VALID)))
    rows, grown = [], []
    original = Path.open
    def observe(path, *args, **kwargs):
        if path != control:
            return original(path, *args, **kwargs)
        if mode == 'grown' and not grown:
            with original(path, 'ab') as stream:
                stream.write(b' '*(LIMIT+1-size))
            grown.append({'before': size, 'after': LIMIT+1})
        return Probe(original(path, *args, **kwargs), rows)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', observe)
        actual, error = outcome(cfg.require_maintainer)
    raw = control.read_bytes()
    data = {'mode': mode, 'actual': actual, 'actual_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'reads': rows, 'growth': grown}
    record(tmp_path, data)
    assert raw == VALID+b' '*((LIMIT+1 if mode in ('oversized', 'grown') else size)-len(VALID)), data
    if mode in ('oversized', 'grown'):
        assert isinstance(error, PermissionError) and 'could not be verified' in str(error), data
    else:
        assert error is None and actual['result'] is None, data
    assert rows and all(row['requested'] == LIMIT+1 and row['binary'] for row in rows), data


@pytest.mark.parametrize('mode', ['valid-lf', 'valid-crlf', 'valid-bom', 'missing', 'wrong-owner', 'malformed', 'invalid-utf8', 'utf16-le', 'utf16-be'])
def test_control_ordinary_ownership_compatibility(brain, tmp_path, mode):
    vault, _ = brain
    raw = VALID
    if mode == 'valid-crlf': raw = raw.replace(b'\n', b'\r\n')
    elif mode == 'valid-bom': raw = b'\xef\xbb\xbf'+raw
    elif mode == 'wrong-owner': raw = raw.replace(b'test-host', b'other-host')
    elif mode == 'malformed': raw = b'# No ownership line\n'
    elif mode == 'invalid-utf8': raw = b'\xff'+raw
    elif mode == 'utf16-le': raw = b'\xff\xfe'+raw.decode().encode('utf-16-le')
    elif mode == 'utf16-be': raw = b'\xfe\xff'+raw.decode().encode('utf-16-be')
    control = vault/REL
    if mode != 'missing': put(control, raw)
    actual, error = outcome(cfg.require_maintainer)
    data = {'mode': mode, 'actual': actual, 'unchanged': not control.exists() if mode == 'missing' else control.read_bytes() == raw}
    record(tmp_path, data)
    assert data['unchanged'], data
    if mode.startswith('valid') or mode == 'missing':
        assert error is None and actual['result'] is None, data
    else:
        assert isinstance(error, PermissionError), data
        expected = 'could not be verified' if mode in ('invalid-utf8', 'utf16-le', 'utf16-be') else 'belongs to a different device'
        assert expected in str(error), data


def test_capture_role_stops_before_control_io(brain, monkeypatch, tmp_path):
    vault, _ = brain
    control = put(vault/REL, b'\xffinvalid fixture')
    opened = []
    original = Path.open
    def observe(path, *args, **kwargs):
        if path == control: opened.append('control')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(cfg, 'ROLE', 'capture')
        patch.setattr(Path, 'open', observe)
        actual, error = outcome(cfg.require_maintainer)
    record(tmp_path, {'actual': actual, 'control_opens': opened})
    assert isinstance(error, PermissionError) and 'capture/search only' in str(error) and not opened


@pytest.mark.parametrize('route', ['update-current', 'wiki-run'])
def test_public_callers_refuse_linked_control_before_later_work(brain, monkeypatch, tmp_path, route):
    vault, _ = brain
    control, link, target = linked_control(vault, tmp_path, 'file')
    before = target.read_bytes()
    later = []
    def prohibited(*args, **kwargs):
        later.append('later-work')
        raise AssertionError('Fixture refuses every later stage; no actual note/model work.')
    if route == 'update-current':
        import brain_store as store
        monkeypatch.setattr(store, 'clean_text', prohibited)
        call = lambda: store.update_current('02-Areas/AI-Memory/Fixture.md', 'Fixture', 'Fixture', ['01-Projects/Fixture.md'])
    else:
        import brain_wiki as wiki
        monkeypatch.setattr(wiki, 'prepare', prohibited)
        call = lambda: wiki.run(1)
    actual, error = outcome(call)
    data = {'route': route, 'actual': actual, 'later': later, 'target_unchanged': target.read_bytes() == before}
    record(tmp_path, data)
    assert isinstance(error, PermissionError) and not later and data['target_unchanged'], data
