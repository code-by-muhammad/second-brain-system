"""Small standard float parser contracts; maximum175 groups, no providers."""
import json
import sys

import pytest
import yaml

import brain_markdown as md
import brain_settings as cfg
import brain_store as s
import brain_wiki as w


def note(form, groups, invalid_bool=False):
    assert form in ('explicit', 'implicit') and groups in (174, 175)
    scalar = ':'.join(['0'] * groups)
    value = '!!float ' + scalar if form == 'explicit' else scalar + '.'
    header = 'reviewed: !!bool maybe\n' if invalid_bool else ''
    return '---\n' + header + 'date: 2026-10-16\nvalue: ' + value + '\n---\n# Small float source\nOriginal synthetic evidence remains available.'


def observe(function):
    code = yaml.constructor.SafeConstructor.construct_yaml_float.__code__
    calls = 0
    previous = sys.getprofile()

    def profile(frame, event, argument):
        nonlocal calls
        if event == 'call' and frame.f_code is code:
            calls += 1

    sys.setprofile(profile)
    try:
        try:
            result = function()
            return 'returned', result, calls
        except (OverflowError, ValueError, yaml.YAMLError) as error:
            return type(error).__name__, None, calls
    finally:
        sys.setprofile(previous)


def saved(tmp_path, **values):
    values.update(actual_provider_Git_service_calls=0, maximum_sexagesimal_groups=175)
    with (tmp_path / 'observation.json').open('x', encoding='utf-8') as stream:
        json.dump(values, stream, indent=2)


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
@pytest.mark.parametrize('strict', [False, True])
def test_overflow_float_has_existing_controlled_frontmatter_contract(tmp_path, form, strict):
    text = note(form, 175)
    outcome, result, calls = observe(lambda: md.frontmatter(text, strict=strict))
    saved(tmp_path, form=form, strict=strict, outcome=outcome, original_float_constructor_calls=calls,
          source_bytes=len(text.encode()))
    if strict:
        assert outcome == 'ValueError', 'Strict standard constructor faults must become controlled ValueError.'
    else:
        assert outcome == 'returned' and result == {}, 'Lenient standard constructor faults must retain empty metadata fallback.'


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
def test_overflow_float_inventory_retains_original_and_uses_safe_metadata_fallback(brain, tmp_path, form):
    source = s.VAULT / '01-Projects/Float.md'
    source.parent.mkdir()
    raw = note(form, 175).encode()
    source.write_bytes(raw)
    outcome, rows, calls = observe(w.inventory)
    saved(tmp_path, form=form, outcome=outcome, original_float_constructor_calls=calls,
          original_unchanged=source.read_bytes() == raw, source_bytes=len(raw))
    assert source.read_bytes() == raw
    assert outcome == 'returned', 'One standard constructor fault must not abort inventory.'
    assert len(rows) == 1 and rows[0]['date'] == 'unknown' and rows[0]['sha256'] == s.digest(raw)


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
def test_invalid_bool_preceded_overflow_has_contained_event_fallback(brain, tmp_path, form):
    source = s.VAULT / '01-Projects/Float.md'
    source.parent.mkdir()
    raw = note(form, 175, invalid_bool=True).encode()
    source.write_bytes(raw)
    assert md.frontmatter(raw.decode()) == {}
    rows = w.inventory()
    assert len(rows) == 1 and rows[0]['sha256'] == s.digest(raw)
    outcome, _, calls = observe(lambda: w._screen_source_header(raw.decode()))
    saved(tmp_path, form=form, outcome=outcome, original_float_constructor_calls=calls,
          original_unchanged=source.read_bytes() == raw, inventory_rows=len(rows), source_bytes=len(raw))
    assert source.read_bytes() == raw
    assert outcome == 'returned', 'Harmless invalid standard constructors must retain the documented safe event fallback.'


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
@pytest.mark.parametrize('strict', [False, True])
def test_fitting_174_group_float_remains_supported(tmp_path, form, strict):
    outcome, result, calls = observe(lambda: md.frontmatter(note(form, 174), strict=strict))
    saved(tmp_path, form=form, strict=strict, outcome=outcome, original_float_constructor_calls=calls)
    assert outcome == 'returned' and result['value'] == 0.0


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
def test_fitting_float_inventory_keeps_date_and_original(brain, tmp_path, form):
    source = s.VAULT / '01-Projects/Fitting.md'
    source.parent.mkdir()
    raw = note(form, 174).encode()
    source.write_bytes(raw)
    outcome, rows, calls = observe(w.inventory)
    saved(tmp_path, form=form, outcome=outcome, original_float_constructor_calls=calls,
          original_unchanged=source.read_bytes() == raw)
    assert outcome == 'returned' and len(rows) == 1 and rows[0]['date'] == '2026-10-16'
    assert source.read_bytes() == raw


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
def test_invalid_bool_preceded_fitting_float_event_control(brain, tmp_path, form):
    text = note(form, 174, invalid_bool=True)
    assert md.frontmatter(text) == {}
    outcome, _, calls = observe(lambda: w._screen_source_header(text))
    saved(tmp_path, form=form, outcome=outcome, original_float_constructor_calls=calls)
    assert outcome == 'returned'



@pytest.mark.parametrize('value, expected', [
    ('!!float 1.5', 1.5), ('1.5', 1.5), ('!!float .inf', 'positive-infinity'),
    ('.inf', 'positive-infinity'), ('-.Inf', 'negative-infinity'),
    ('!!float .nan', 'nan'), ('.nan', 'nan'), ('1:30.0', 90.0),
])
def test_standard_finite_and_nonfinite_float_semantics_remain_supported(value, expected):
    import math
    actual = md.frontmatter('---\nvalue: ' + value + '\n---\n# Evidence', strict=True)['value']
    if expected == 'nan':
        assert math.isnan(actual)
    elif expected == 'positive-infinity':
        assert math.isinf(actual) and actual > 0
    elif expected == 'negative-infinity':
        assert math.isinf(actual) and actual < 0
    else:
        assert actual == expected


def test_standard_float_value_mapping_keeps_constructor_semantics():
    assert md.frontmatter('---\nvalue: !!float {=: "1:30"}\n---\n# Evidence', strict=True)['value'] == 90.0


def test_anchored_float_aliases_construct_once_and_reuse_the_object():
    text = '---\nfirst: &number !!float 1.5\nsecond: *number\n---\n# Evidence'
    outcome, result, calls = observe(lambda: md.frontmatter(text, strict=True))
    assert outcome == 'returned' and result['first'] is result['second'] and calls == 1


def test_ordinary_external_safe_loader_keeps_its_original_behavior():
    # The application-local wrapper must not mutate installed PyYAML dispatch.
    raw = ':'.join(['0'] * 175)
    with pytest.raises(OverflowError):
        yaml.safe_load('value: !!float ' + raw)


@pytest.mark.parametrize('failure', [RuntimeError('synthetic programming error'), MemoryError('synthetic memory error')])
@pytest.mark.parametrize('strict', [False, True])
def test_fatal_or_unrelated_float_constructor_errors_propagate_unchanged(monkeypatch, failure, strict):
    def failed(*args, **kwargs):
        raise failure
    # Fail inside the original constructor on both existing and local dispatch.
    monkeypatch.setattr(yaml.constructor, 'float', failed, raising=False)
    with pytest.raises(type(failure)) as caught:
        md.frontmatter('---\nvalue: !!float 1.5\n---\n# Evidence', strict=strict)
    assert caught.value is failure


@pytest.mark.parametrize('failure', [RuntimeError('synthetic programming error'), MemoryError('synthetic memory error')])
def test_source_float_constructor_does_not_swallow_fatal_or_unrelated_errors(brain, monkeypatch, failure):
    def failed(*args, **kwargs):
        raise failure
    monkeypatch.setattr(yaml.SafeLoader, 'construct_yaml_float', failed)
    with pytest.raises(type(failure)) as caught:
        w._screen_source_header('---\nreviewed: !!bool maybe\nvalue: !!float 1.5\n---\n# Evidence')
    assert caught.value is failure


@pytest.mark.parametrize('strict', [False, True])
def test_common_float_containment_does_not_hide_nonfloat_overflow(monkeypatch, strict):
    failure = OverflowError('synthetic unrelated integer implementation error')
    def failed(*args, **kwargs):
        raise failure
    monkeypatch.setattr(yaml.constructor.SafeConstructor, 'construct_yaml_int', failed)
    with pytest.raises(OverflowError) as caught:
        md.frontmatter('---\nvalue: !!int 42\n---\n# Evidence', strict=strict)
    assert caught.value is failure


def test_source_scalar_rendering_overflow_is_outside_constructor_fallback(brain, monkeypatch):
    failure = OverflowError('synthetic renderer implementation error')
    def failed(*args, **kwargs):
        raise failure
    monkeypatch.setattr(w.json, 'dumps', failed)
    with pytest.raises(OverflowError) as caught:
        w._screen_source_header('---\nreviewed: !!bool maybe\nvalue: !!float 1.5\n---\n# Evidence')
    assert caught.value is failure


@pytest.mark.parametrize('form', ['explicit', 'implicit'])
def test_overflow_in_derived_topic_sets_attention_without_model_or_source_strikes(brain, monkeypatch, form):
    from test_wiki_prepare_integrity import existing_topic, note_snapshot
    from test_wiki_remediation import fake_model
    _, _, topic, result = existing_topic(brain)
    topic.write_bytes(note(form, 175).encode())
    before = note_snapshot()
    w._atomic_json(cfg.STATE / 'wiki-worker-status.json', {'status': 'complete', 'at': s.stamp(),
        'consecutive_failures': 0, 'attention_required': False})
    calls = fake_model(monkeypatch, result)
    report = w.run()
    assert report['status'] == 'attention' and report['integrity_field'] == 'frontmatter'
    assert report['consecutive_failures'] == 0 and len(calls) == 0
    assert w.status()['worker']['status'] == 'attention'
    assert w.run()['status'] == 'attention' and note_snapshot() == before and len(calls) == 0
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0] == 0
