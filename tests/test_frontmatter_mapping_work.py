"""Shared frontmatter bounds merge construction before duplicate keys collapse."""
from datetime import date

import pytest
import yaml

import brain_markdown as md
import brain_store as s
import brain_wiki as w


def _header(level, *, dated=False):
    assert 0 <= level <= 12
    lines = ['date: 2026-10-16'] if dated else []
    lines.append('a0: &node0 {ordinary: value0}')
    for number in range(1, level + 1):
        prior = 'node' + str(number - 1)
        lines.append('a' + str(number) + ': &node' + str(number)
                     + ' {<<: [*' + prior + ', *' + prior
                     + '], ordinary: value' + str(number) + '}')
    lines.append('final: *node' + str(level))
    return '\n'.join(lines)


def _note(header):
    return '---\n' + header + '\n---\n# Bounded evidence\nOriginal body remains available.'


def _cap(monkeypatch, limit):
    # The unchanged pre-fix reader has no policy constant. This makes the same
    # small fixture/oracle runnable before and after, without changing that code.
    monkeypatch.setattr(md, '_YAML_WORK_LIMIT', limit, raising=False)


@pytest.mark.parametrize('strict', [False, True])
def test_compact_doubling_merges_obey_low_aggregate_limit(monkeypatch, strict):
    _cap(monkeypatch, 1024)
    text = _note(_header(8))
    assert len(text.encode()) < 600
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}


@pytest.mark.parametrize('strict', [False, True])
def test_separate_fitting_merges_share_one_limit(monkeypatch, strict):
    _cap(monkeypatch, 1024)
    header = 'base: &base {a: 1, b: 2, c: 3, d: 4}\n'
    header += '\n'.join('part' + str(i) + ': {<<: *base}' for i in range(60))
    text = _note(header)
    assert len(text.encode()) < 1600
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}


def test_inventory_keeps_original_source_when_metadata_work_is_bounded(brain, monkeypatch):
    _cap(monkeypatch, 1024)
    source = s.VAULT / '01-Projects/Bounded.md'
    source.parent.mkdir()
    original = _note(_header(8, dated=True)).encode()
    source.write_bytes(original)
    rows = w.inventory()
    assert len(rows) == 1
    assert rows[0]['date'] == 'unknown'
    assert rows[0]['sha256'] == s.digest(original)
    assert rows[0]['text'] == original.decode()
    assert source.read_bytes() == original


def test_flat_mapping_iteration_is_reserved_before_constructor_loop(monkeypatch):
    _cap(monkeypatch, 64)
    original = yaml.constructor.BaseConstructor.construct_mapping
    visits = []

    def forwarded(loader, node, deep=False):
        visits.append(len(node.value))
        return original(loader, node, deep=deep)

    monkeypatch.setattr(yaml.constructor.BaseConstructor, 'construct_mapping', forwarded)
    header = '\n'.join('key' + str(i) + ': value' for i in range(24))
    assert md.frontmatter(_note(header)) == {}
    assert visits == []


def test_small_doubling_merges_preserve_overrides_and_alias_identity(monkeypatch):
    _cap(monkeypatch, 1024)
    parsed = md.frontmatter(_note(_header(2)), strict=True)
    assert parsed['a0'] == {'ordinary': 'value0'}
    assert parsed['a1'] == {'ordinary': 'value1'}
    assert parsed['a2'] == {'ordinary': 'value2'}
    assert parsed['final'] is parsed['a2']


def test_measured_level_twelve_fits_default_limit():
    parsed = md.frontmatter(_note(_header(12)), strict=True)
    assert parsed['final'] == {'ordinary': 'value12'}
    assert parsed['final'] is parsed['a12']


def test_normal_dated_inventory_keeps_its_date_and_source_bytes(brain, monkeypatch):
    _cap(monkeypatch, 1024)
    source = s.VAULT / '01-Projects/Ordinary.md'
    source.parent.mkdir()
    original = _note(_header(2, dated=True)).encode()
    source.write_bytes(original)
    rows = w.inventory()
    assert len(rows) == 1 and rows[0]['date'] == '2026-10-16'
    assert rows[0]['sha256'] == s.digest(original)
    assert source.read_bytes() == original


@pytest.mark.parametrize('merge', [
    'out: {<<: *left, shared: explicit}',
    'out: {shared: explicit, <<: *left}',
    'out: {<<: [*left, *right], shared: explicit}',
])
def test_explicit_keys_override_standard_single_and_sequence_merges(merge):
    header = ('left: &left {shared: left, only_left: 1}\n'
              'right: &right {shared: right, only_right: 2}\n' + merge)
    result = md.frontmatter(_note(header), strict=True)['out']
    assert result['shared'] == 'explicit' and result['only_left'] == 1
    if '[*left, *right]' in merge:
        assert result['only_right'] == 2


@pytest.mark.parametrize('order, expected', [
    ('[*left, *right]', 'left'), ('[*right, *left]', 'right'),
])
def test_merge_sequence_earlier_mapping_has_precedence(order, expected):
    header = ('left: &left {shared: left}\nright: &right {shared: right}\n'
              'out: {<<: ' + order + '}')
    assert md.frontmatter(_note(header), strict=True)['out'] == {'shared': expected}


def test_existing_duplicate_key_and_value_tag_semantics_are_preserved():
    result = md.frontmatter(_note('out: {duplicate: first, duplicate: last, =: literal}'), strict=True)
    assert result == {'out': {'duplicate': 'last', '=': 'literal'}}


def test_self_merge_keeps_pinned_mutation_semantics():
    result = md.frontmatter(_note('out: &self {<<: *self, kept: ordinary}'), strict=True)
    assert result == {'out': {'kept': 'ordinary'}}


def test_recursive_collection_alias_identity_is_preserved():
    result = md.frontmatter(_note('out: &self {nested: *self}'), strict=True)
    assert result['out']['nested'] is result['out']


def test_standard_scalar_and_special_collection_constructors_remain_supported():
    header = ('active: !!bool true\ncount: !!int 42\nwhen: !!timestamp 2026-10-16\n'
              'data: !!binary QUJD\nset: !!set {first: null, second: null}\n'
              'ordered: !!omap [first: 1, second: 2]\npairs: !!pairs [first: 1, first: 2]')
    result = md.frontmatter(_note(header), strict=True)
    assert result['active'] is True and result['count'] == 42
    assert result['when'] == date(2026, 10, 16) and result['data'] == b'ABC'
    assert result['set'] == {'first', 'second'}
    assert result['ordered'] == [('first', 1), ('second', 2)]
    assert result['pairs'] == [('first', 1), ('first', 2)]


def test_limit_is_local_to_each_read_and_does_not_mutate_pyyaml(monkeypatch):
    _cap(monkeypatch, 64)
    fitting = _note('out: {ordinary: value}')
    assert md.frontmatter(fitting, strict=True) == {'out': {'ordinary': 'value'}}
    assert md.frontmatter(fitting, strict=True) == {'out': {'ordinary': 'value'}}
    header = _header(8)
    assert md.frontmatter(_note(header)) == {}
    # Other PyYAML consumers are not given the frontmatter-specific policy.
    ordinary = yaml.safe_load(header)
    assert ordinary['final'] == {'ordinary': 'value8'}


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_exact_fences_bom_and_quoted_fence_content_remain_supported(newline):
    text = '\ufeff' + _note('title: "before --- after"\nquote: |\n  ---\nwhen: 2026-10-16')
    result = md.frontmatter(text.replace('\n', newline), strict=True)
    assert result == {'title': 'before --- after', 'quote': '---\n', 'when': date(2026, 10, 16)}


@pytest.mark.parametrize('strict', [False, True])
def test_text_without_an_opening_fence_does_not_construct_yaml(monkeypatch, strict):
    _cap(monkeypatch, 0)
    assert md.frontmatter('# Ordinary body\n---\nvalue: evidence', strict=strict) == {}


@pytest.mark.parametrize('header', ['bad: !!bool maybe', 'bad: [broken', 'bad: !!python/object:builtins.object {}'])
@pytest.mark.parametrize('strict', [False, True])
def test_existing_invalid_safe_metadata_fallback_contract_is_preserved(header, strict):
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(_note(header), strict=True)
    else:
        assert md.frontmatter(_note(header)) == {}
