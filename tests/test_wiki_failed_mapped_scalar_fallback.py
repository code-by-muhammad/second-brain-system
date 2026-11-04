"""R137 original contracts for retained, selected, known raw scalar evidence.

Every original is below 1 KiB. All provider results are synthetic; no actual
provider, unknown constructor or arbitrary decoder is requested.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE, skip_output

TRACE = Path(os.environ['R137_TRACE']) if 'R137_TRACE' in os.environ else None
TAGS = ['int', 'binary', 'float', 'bool']


def escaped(value):
    return '"' + ''.join('\\u' + format(ord(character), '04x') for character in value) + '"'


PASSWORD = escaped('password')


def record(case, **data):
    if TRACE is None:
        return
    with TRACE.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'case': case, **data}, ensure_ascii=True) + '\n')


@pytest.fixture
def brain(tmp_path, monkeypatch):
    root, vault = tmp_path / 'runtime', tmp_path / 'vault'
    state = root / 'state'
    state.mkdir(parents=True)
    vault.mkdir()
    (root / 'logs').mkdir()
    for module in (cfg, s):
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'VAULT', vault)
        monkeypatch.setattr(module, 'STATE', state)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'synthetic-r137-host')
    return vault, state


def source(brain, header):
    path = brain[0] / '01-Projects/Example.md'
    path.parent.mkdir()
    raw = ('---\n' + header + '\n---\n# Example Project\n' + QUOTE + '\n').encode('utf-8')
    assert len(raw) < 1024 and not s.SECRET.search(raw.decode('utf-8'))
    path.write_bytes(raw)
    rows = w.inventory()
    assert len(rows) == 1
    assert rows[0]['sha256'] == hashlib.sha256(raw).hexdigest()
    return path, raw, rows


def run_contract(brain, monkeypatch, header, unsafe, case, *, unused_packet=False):
    path, raw, rows = source(brain, header)
    initial_pending = w.status()['pending_source_parts']
    saved_path, saved = None, None
    if unused_packet:
        with monkeypatch.context() as legacy:
            legacy.setattr(w, '_validate_source_context', lambda packet: None)
            packet = w.prepare(1)
        saved_path = cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'input.json'
        saved = saved_path.read_bytes()
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    status = w.status()
    with w.db() as connection:
        source_strikes = connection.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]
        batch_strikes = connection.execute('SELECT count(*) FROM batches WHERE attempts>0').fetchone()[0]
    sent_original = bool(calls.inputs and header in json.loads(calls.inputs[0][len(w.PROMPT):])['sources'][0]['text'])
    saved_preserved = saved_path is None or (saved_path.exists() and saved_path.read_bytes() == saved)
    observation = {'header': header, 'original_bytes': len(raw), 'original_sha256': hashlib.sha256(raw).hexdigest(),
                   'original_preserved': path.read_bytes() == raw, 'inventory_preserved': w.inventory() == rows,
                   'metadata_empty': w.meta(raw.decode('utf-8')) == {}, 'worker_status': report['status'],
                   'integrity_field': report.get('integrity_field'), 'integrity_page': report.get('integrity_page'),
                   'fake_provider_calls': len(calls), 'original_reached_fake_provider': sent_original,
                   'pending_before': initial_pending, 'pending_after': status['pending_source_parts'],
                   'source_strikes': source_strikes, 'batch_strikes': batch_strikes,
                   'systemic_strikes': report.get('consecutive_failures', 0), 'unused_saved_packet_preserved': saved_preserved}
    record(case, **observation)
    assert observation['original_preserved'] and observation['inventory_preserved']
    assert initial_pending == len(rows)
    if unsafe:
        assert report['status'] == 'attention' and report.get('integrity_field') == 'content' and not calls
        assert report['integrity_page'] == '01-Projects/Example.md'
        assert status['pending_source_parts'] == initial_pending
        assert source_strikes == batch_strikes == report['consecutive_failures'] == 0
        assert saved_preserved
        if not unused_packet:
            assert not list((cfg.STATE / 'wiki/batches').glob('*/input.json'))
    else:
        assert report['status'] == 'complete' and len(calls) == 1
        assert status['pending_source_parts'] == 0 and sent_original


@pytest.mark.parametrize('tag', TAGS)
@pytest.mark.parametrize('shape', ['direct-mapped', 'same-tag-inner-cache-hit', 'benign-invalid-fallback'])
def test_r137_core_twelve_selected_known_raw_fallback_contracts(brain, monkeypatch, tag, shape):
    unsafe = shape != 'benign-invalid-fallback'
    value = 'A' * (21 if tag == 'binary' and unsafe else 20 if unsafe else 1)
    scalar = ('!!' + tag + ' ' if shape == 'same-tag-inner-cache-hit' else '') + '"' + value + '"'
    header = PASSWORD + ': !!' + tag + ' {=: ' + scalar + '}'
    assert bool(s.SECRET.search('password: ' + value)) == unsafe
    with pytest.raises((yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError, IndexError)):
        yaml.safe_load('value: !!' + tag + ' {=: ' + scalar + '}')
    run_contract(brain, monkeypatch, header, unsafe, 'core-' + shape + '-' + tag)


@pytest.mark.parametrize('tag', TAGS)
def test_r137_identical_failed_scalar_already_retains_outer_association(brain, monkeypatch, tag):
    value = 'A' * (21 if tag == 'binary' else 20)
    run_contract(brain, monkeypatch, PASSWORD + ': !!' + tag + ' "' + value + '"', True,
                 'existing-invalid-scalar-' + tag)


@pytest.mark.parametrize('tag,value', [('int', '"0x0000000000000001"'), ('float', '12345678901234567890'),
                                      ('bool', 'true'), ('binary', '"b3JkaW5hcnk="')])
def test_r137_successful_known_typed_views_keep_existing_normalization(brain, monkeypatch, tag, value):
    run_contract(brain, monkeypatch, PASSWORD + ': !!' + tag + ' {=: ' + value + '}', False,
                 'successful-normalization-' + tag)


@pytest.mark.parametrize('shape', ['timestamp', 'missing-selector', 'quoted-selector', 'sequence-selector', 'unknown-map-tag'])
def test_r137_unestablished_scalar_consumption_keeps_collection_fallback(brain, monkeypatch, shape):
    values = {'timestamp': '!!timestamp {=: "' + 'A' * 20 + '"}',
              'missing-selector': '!!int {different: "' + 'A' * 20 + '"}',
              'quoted-selector': '!!int {"=": "' + 'A' * 20 + '"}',
              'sequence-selector': '!!int {=: ["' + 'A' * 20 + '"]}',
              'unknown-map-tag': '!owner-custom {=: "' + 'A' * 20 + '"}'}
    run_contract(brain, monkeypatch, PASSWORD + ': ' + values[shape], False, 'collection-fallback-' + shape)


@pytest.mark.parametrize('shape', ['first-short-selected', 'first-unsafe-selected', 'selected-scalar-alias-cache'])
def test_r137_first_selector_and_alias_identity_retain_actual_selected_raw(brain, monkeypatch, shape):
    if shape == 'first-short-selected':
        header = PASSWORD + ': !!int {=: "A", =: "' + 'A' * 20 + '"}'
    elif shape == 'first-unsafe-selected':
        header = PASSWORD + ': !!int {=: "' + 'A' * 20 + '", =: "A"}'
    else:
        header = 'part: &part !!int "' + 'A' * 20 + '"\n' + PASSWORD + ': !!int {=: *part}'
    run_contract(brain, monkeypatch, header, shape != 'first-short-selected', 'selection-' + shape)


def test_r137_unused_saved_packet_rechecks_original_before_provider_without_rewriting_authority(brain, monkeypatch):
    header = PASSWORD + ': !!int {=: "' + 'A' * 20 + '"}'
    run_contract(brain, monkeypatch, header, True, 'unused-saved-packet', unused_packet=True)


def measured_call(function, *args):
    observed = {'event_work': [], 'constructed_work': [], 'semantic': [], 'header_return': None,
                'event_final_work': None, 'constructed_final_work': None}
    previous = sys.getprofile()
    def profile(frame, event, argument):
        name = frame.f_code.co_qualname
        if event == 'call' and name == '_screen_source_header.<locals>.reserve':
            observed['event_work'].append(frame.f_locals['amount'])
        elif event == 'call' and name == '_screen_source_metadata.<locals>.reserve':
            observed['constructed_work'].append(frame.f_locals['amount'])
        elif event == 'call' and name == '_screen_source_header.<locals>.semantic':
            observed['semantic'].append(frame.f_locals['amount'])
        elif event == 'return' and name == '_screen_source_header':
            observed['header_return'] = argument
            observed['event_final_work'] = frame.f_locals['work']
        elif event == 'return' and name == '_screen_source_metadata':
            observed['constructed_final_work'] = frame.f_locals['work']
    sys.setprofile(profile)
    try:
        result = function(*args)
    finally:
        sys.setprofile(previous)
    assert sum(observed['event_work']) <= 8_000_000
    assert sum(observed['constructed_work']) <= 8_000_000
    assert sum(observed['semantic']) <= 2_000_000
    return result, observed


@pytest.mark.parametrize('shape,total', [('raw-string-definition-and-alias', 13),
                                        ('numeric-zero-weight-selected-definition-and-alias', 56),
                                        ('nested-zero-weight-selected-definition-and-alias', 57)])
def test_r137_failed_fallback_charges_one_definition_and_each_alias_without_double_charge(brain, shape, total):
    if shape == 'raw-string-definition-and-alias':
        header = 'field: &same !!int {=: "A"}\nagain: *same'
    else:
        selected = '{=: *raw}' if shape.startswith('nested') else '*raw'
        header = 'plain: &raw !!int 12345678901234567890\nfield: &same !!bool {=: ' + selected + '}\nagain: *same'
    result, observed = measured_call(w._screen_source_header, '---\n' + header + '\n---\n')
    record('semantic-' + shape, expected_semantic=total, actual_semantic=sum(observed['semantic']), **observed)
    assert result == sum(observed['event_work']) == observed['event_final_work']
    assert sum(observed['semantic']) == total


@pytest.mark.parametrize('phase', ['event', 'constructed'])
def test_r137_forwarding_counters_preserve_separate_single_work_envelopes_and_cache(brain, monkeypatch, phase):
    constructors = []
    actual = yaml.SafeLoader.construct_yaml_int
    def forwarded(loader, node):
        constructors.append(type(node).__name__)
        return actual(loader, node)
    monkeypatch.setattr(yaml.SafeLoader, 'construct_yaml_int', forwarded)
    if phase == 'event':
        header = 'first: &same !!int {=: !!int "A"}\n' + ''.join('ref' + str(index) + ': *same\n' for index in range(32))
        result, observed = measured_call(w._screen_source_header, '---\n' + header + '\n---\n')
        assert constructors == ['ScalarNode']
        assert result == sum(observed['event_work']) == observed['event_final_work']
        assert not observed['constructed_work']
    else:
        value = b'ordinary'
        graph = {'first': value, **{'ref' + str(index): value for index in range(32)}}
        _, observed = measured_call(w._screen_source_metadata, graph)
        assert not constructors and not observed['event_work']
        assert sum(observed['constructed_work']) == observed['constructed_final_work']
    record('work-' + phase, constructors=constructors, **observed)


@pytest.mark.parametrize('failure_type', [RuntimeError, MemoryError])
def test_r137_failed_mapped_constructor_keeps_unrelated_and_fatal_errors_outside_fallback(brain, monkeypatch, failure_type):
    failure = failure_type('Synthetic unrelated constructor failure')
    def failed(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(yaml.SafeLoader, 'construct_yaml_int', failed)
    with pytest.raises(failure_type) as caught:
        w._screen_source_header('---\nfield: !!int {=: "A"}\n---\n')
    assert caught.value is failure
