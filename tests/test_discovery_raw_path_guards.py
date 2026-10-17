"""Discovery must validate the actual enumerated original before reading it."""
import json
import os
from pathlib import Path

import pytest

import brain_store as s
import brain_wiki as w


SENTINEL = 'r161outsideevidence'
OUTSIDE = ('# Synthetic outside original\nHarmless fixture only.\n' + SENTINEL + '\n').encode()
RAW = b'# Literal original\nr161literaloriginal\n'
NESTED = b'# Different nested original\nr161nestedoriginal\n'


def put(relative, raw):
    path = s.VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def discover(route, query=SENTINEL):
    return s.fresh_search(query) if route == 'fresh-search' else w.inventory()


def observe_product_opens(monkeypatch, outside, operation):
    """Only product execution is observed; fixture/snapshot reads stay outside."""
    actual = []
    real_open = Path.open
    with monkeypatch.context() as context:
        def opened(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get('mode', 'r')
            if 'r' in mode and path.resolve() == outside:
                actual.append({'requested': str(path), 'resolved': str(path.resolve()), 'mode': mode})
            return real_open(path, *args, **kwargs)
        context.setattr(Path, 'open', opened)
        result = operation()
    return result, actual


def save_observation(tmp_path, **value):
    (tmp_path / 'r161-observation.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX literal-backslash symlink required.')
@pytest.mark.parametrize('route', ['fresh-search', 'wiki-inventory'])
@pytest.mark.parametrize('counterpart', [False, True], ids=['normalized-missing', 'normalized-different-bytes'])
def test_literal_backslash_symlink_never_opens_outside_original(brain, monkeypatch, tmp_path, route, counterpart):
    relative = '03-Resources/research\\source.md'
    outside = tmp_path / 'outside-synthetic.md'
    outside.write_bytes(OUTSIDE)
    raw = s.VAULT / relative
    raw.parent.mkdir(parents=True)
    raw.symlink_to(outside)
    normalized = s.safe_path(relative)
    assert raw.name == 'research\\source.md' and raw.is_symlink() and raw.resolve() == outside
    assert normalized == s.VAULT / '03-Resources/research/source.md' and normalized != raw
    if counterpart:
        put('03-Resources/research/source.md', NESTED)
    else:
        assert not normalized.exists()
    link_before = (raw.lstat().st_ino, raw.lstat().st_mtime_ns, os.readlink(raw))
    result, opened = observe_product_opens(monkeypatch, outside, lambda: discover(route))
    leaked = any(row['path'] == relative for row in result)
    save_observation(tmp_path, kind='outside-link', route=route, counterpart=counterpart,
        product_outside_opens=opened, leaked_raw_path=leaked, result=result)
    assert outside.read_bytes() == OUTSIDE
    assert link_before == (raw.lstat().st_ino, raw.lstat().st_mtime_ns, os.readlink(raw))
    assert not opened, 'Discovery opened outside original before raw-path validation.'
    assert not leaked
    if counterpart and route == 'wiki-inventory':
        assert any(row['path'] == '03-Resources/research/source.md' and row['sha256'] == s.digest(NESTED) for row in result)


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX literal-backslash original required.')
@pytest.mark.parametrize('counterpart', [False, True], ids=['normalized-missing', 'normalized-different-bytes'])
def test_regular_literal_original_keeps_raw_discovery_identity(brain, tmp_path, counterpart):
    relative = '03-Resources/research\\source.md'
    original = put(relative, RAW)
    if counterpart:
        put('03-Resources/research/source.md', NESTED)
    found = s.fresh_search('r161literaloriginal')
    rows = w.inventory()
    raw_row = next(row for row in rows if row['path'] == relative)
    assert found[0]['path'] == relative and raw_row['sha256'] == s.digest(RAW)
    assert 'r161literaloriginal' in raw_row['text']
    assert all(row['path'] != relative for row in s.fresh_search('r161nestedoriginal'))
    if counterpart:
        assert next(row for row in rows if row['path'] == '03-Resources/research/source.md')['sha256'] == s.digest(NESTED)
    with pytest.raises(w.WikiIntegrityError) as raised:
        w._validate_source_context({'sources': [raw_row]})
    assert raised.value.field == 'path'
    assert original.read_bytes() == RAW
    save_observation(tmp_path, kind='regular-literal', counterpart=counterpart,
        raw_hash=raw_row['sha256'], raw_search=found, r157_path_attention_preserved=True)


@pytest.mark.skipif(os.name == 'nt', reason='Actual POSIX ordinary symlink control required.')
@pytest.mark.parametrize('route', ['fresh-search', 'wiki-inventory'])
def test_ordinary_link_guard_still_prevents_outside_reads(brain, monkeypatch, tmp_path, route):
    outside = tmp_path / 'outside-synthetic.md'
    outside.write_bytes(OUTSIDE)
    linked = s.VAULT / '03-Resources/linked.md'
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    result, opened = observe_product_opens(monkeypatch, outside, lambda: discover(route))
    assert not opened and not result
    assert outside.read_bytes() == OUTSIDE and linked.is_symlink()
    save_observation(tmp_path, kind='ordinary-link-control', route=route, product_outside_opens=opened, result=result)


def test_public_backslash_aliases_still_select_the_nested_original(brain, tmp_path):
    original = put('03-Resources/research/source.md', NESTED)
    alias = '03-Resources\\research\\source.md'
    assert s.safe_path(alias) == original
    assert s.read(alias) == s.read('03-Resources/research/source.md')
    assert s.note_link(alias, '02-Areas/AI-Memory/Example.md') == 'Source path: `03-Resources/research/source.md`'
    save_observation(tmp_path, kind='public-alias-control', unchanged=True)


def test_ordinary_unicode_nested_discovery_stays_available(brain, tmp_path):
    original = put('03-Resources/café/source.md', RAW)
    found = s.fresh_search('r161literaloriginal')
    rows = w.inventory()
    assert found[0]['path'] == '03-Resources/café/source.md'
    assert len(rows) == 1 and rows[0]['sha256'] == s.digest(RAW)
    w._validate_source_context({'sources': rows})
    assert original.read_bytes() == RAW
    save_observation(tmp_path, kind='ordinary-unicode-control', unchanged=True)
