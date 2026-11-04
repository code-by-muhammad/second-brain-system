"""Integer construction is budgeted before SafeLoader's original conversion."""
from datetime import date

import pytest
import yaml

import brain_markdown as md
import brain_store as s
import brain_wiki as w


def _note(header):
    return '---\n' + header + '\n---\n# Integer evidence\nOriginal source remains available.'


def _cap(monkeypatch, limit):
    monkeypatch.setattr(md, '_YAML_INT_WORK_LIMIT', limit, raising=False)


def _observe_original(monkeypatch):
    original = yaml.constructor.SafeConstructor.construct_yaml_int
    calls = []

    def forwarded(loader, node):
        raw = loader.construct_scalar(node)
        calls.append(len(raw))
        return original(loader, node)

    # Before: inherited SafeLoader dispatch. After: local dispatch forwards the
    # same original method. Neither branch replaces conversion semantics.
    monkeypatch.setattr(yaml.constructor.SafeConstructor, 'construct_yaml_int', forwarded)
    monkeypatch.setitem(yaml.SafeLoader.yaml_constructors, 'tag:yaml.org,2002:int', forwarded)
    return calls


@pytest.mark.parametrize('strict', [False, True])
def test_default_limit_stops_measured_sexagesimal_before_original_constructor(monkeypatch, strict):
    calls = _observe_original(monkeypatch)
    raw = ':'.join(['1'] + ['0'] * 1999)
    assert len(raw) == 3999
    text = _note('count: !!int "' + raw + '"')
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}
    assert calls == []


@pytest.mark.parametrize('strict', [False, True])
def test_small_sexagesimal_obeys_injected_limit_before_conversion(monkeypatch, strict):
    _cap(monkeypatch, 64)
    calls = _observe_original(monkeypatch)
    text = _note('count: !!int "1:0:0:0"')
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}
    assert calls == []


@pytest.mark.parametrize('strict', [False, True])
def test_distinct_fitting_scalars_share_one_integer_limit(monkeypatch, strict):
    _cap(monkeypatch, 64)
    calls = _observe_original(monkeypatch)
    text = _note('counts: [!!int "1:0", !!int "1:0", !!int "1:0", !!int "1:0"]')
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}
    assert calls == [3, 3, 3]


def test_actual_inventory_preserves_original_with_bounded_integer_metadata(brain, monkeypatch):
    _cap(monkeypatch, 64)
    source = s.VAULT / '01-Projects/Integer.md'
    source.parent.mkdir()
    original = _note('date: 2026-10-16\ncount: !!int "1:0:0:0"').encode()
    source.write_bytes(original)
    rows = w.inventory()
    assert len(rows) == 1 and rows[0]['date'] == 'unknown'
    assert rows[0]['sha256'] == s.digest(original) and rows[0]['text'] == original.decode()
    assert source.read_bytes() == original


def test_aliases_reuse_one_converted_integer_within_budget(monkeypatch):
    _cap(monkeypatch, 64)
    calls = _observe_original(monkeypatch)
    result = md.frontmatter(_note('first: &count !!int "1:0:0"\ncounts: [*count, *count, *count, *count]'), strict=True)
    assert result['first'] == 3600 and result['counts'] == [3600] * 4
    assert all(value is result['first'] for value in result['counts'])
    assert calls == [5]


def test_integer_budget_resets_for_each_frontmatter_read(monkeypatch):
    _cap(monkeypatch, 64)
    for _ in range(3):
        assert md.frontmatter(_note('count: !!int "1:0:0"'), strict=True) == {'count': 3600}


@pytest.mark.parametrize('raw, expected', [
    ('42', 42), ('-42', -42), ('+42', 42), ('1_024', 1024),
    ('0b101010', 42), ('052', 42), ('0x2a', 42),
    ('1:0', 60), ('-1:0', -60), ('+1:0', 60), ('1_0:0', 600),
])
def test_standard_integer_spellings_keep_original_values(raw, expected):
    assert md.frontmatter(_note('count: !!int "' + raw + '"'), strict=True) == {'count': expected}


def test_known_value_mapping_form_remains_supported(monkeypatch):
    _cap(monkeypatch, 64)
    assert md.frontmatter(_note('count: !!int {=: "1:0"}'), strict=True) == {'count': 60}


def test_three_thousand_distinct_hundred_digit_integers_still_fit_default():
    raw = '1' + '0' * 99
    values = ', '.join([raw] * 3000)
    result = md.frontmatter(_note('counts: [' + values + ']'), strict=True)
    assert len(result['counts']) == 3000 and all(value == 10 ** 99 for value in result['counts'])


def test_four_thousand_digit_anchored_integer_is_converted_once(monkeypatch):
    calls = _observe_original(monkeypatch)
    raw = '1' + '0' * 3999
    header = 'first: &count !!int "' + raw + '"\ncounts: [' + ', '.join(['*count'] * 100) + ']'
    result = md.frontmatter(_note(header), strict=True)
    assert result['first'] == 10 ** 3999
    assert all(value is result['first'] for value in result['counts'])
    assert calls == [4000]


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('limit', [18, 19], ids=['below-boundary', 'exact-boundary'])
def test_integer_cost_boundary_has_no_off_by_one(monkeypatch, strict, limit):
    _cap(monkeypatch, limit)
    text = _note('count: !!int "1:0"')
    if limit == 19:
        assert md.frontmatter(text, strict=strict) == {'count': 60}
    elif strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}


def test_non_integer_standard_types_and_nonfinite_floats_remain_unchanged():
    result = md.frontmatter(_note('when: 2026-10-16\nactive: true\nempty: null\n'
                                 'data: !!binary QUJD\ninfinite: .inf\nnegative: -.inf\nnot_number: .nan'), strict=True)
    assert result['when'] == date(2026, 10, 16) and result['active'] is True and result['empty'] is None
    assert result['data'] == b'ABC' and result['infinite'] == float('inf') and result['negative'] == float('-inf')
    assert result['not_number'] != result['not_number']


def test_local_integer_policy_does_not_change_global_safe_loader(monkeypatch):
    _cap(monkeypatch, 0)
    assert md.frontmatter(_note('count: 42')) == {}
    assert yaml.safe_load('count: 42') == {'count': 42}


@pytest.mark.parametrize('raw', ['""', 'not-an-integer', '"1::0"'])
@pytest.mark.parametrize('strict', [False, True])
def test_invalid_integer_fallback_remains_controlled(raw, strict):
    text = _note('count: !!int ' + raw)
    if strict:
        with pytest.raises(ValueError, match='^Invalid YAML frontmatter\\.$'):
            md.frontmatter(text, strict=True)
    else:
        assert md.frontmatter(text) == {}
