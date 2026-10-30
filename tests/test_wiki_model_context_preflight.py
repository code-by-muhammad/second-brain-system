"""Synthetic R83-R86 worker contracts; no real providers or personal notes."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model
from test_wiki_saved_plan_repair import interrupted_plan, partial_index_conflict, row
from test_wiki_saved_recovery_attention import complete_unfinalized


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
        monkeypatch.setattr(module, 'STATE', state)
        monkeypatch.setattr(module, 'VAULT', vault)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'test-host')
    return vault, state


def fake_skip_model(monkeypatch):
    class Calls(list):
        pass
    calls = Calls()
    calls.inputs = []
    monkeypatch.setattr(w.shutil, 'which', lambda name: 'synthetic-codex')
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.output = Path(command[command.index('-o') + 1])
            calls.append(command)
        def communicate(self, model_input, timeout):
            calls.inputs.append(model_input)
            packet = json.loads(model_input.split('INPUT_JSON:\n', 1)[1])
            dispositions = []
            for row in packet['sources']:
                quote = 'Evidence line.' if 'Evidence line.' in row['text'] else 'A prototype was demonstrated.'
                dispositions.append({'source': row['path'], 'part': row['part'], 'status': 'skipped',
                                     'reason': 'This synthetic administrative source needs no reusable topic.', 'quote': quote})
            self.output.write_text(json.dumps({'pages': [], 'dispositions': dispositions}), encoding='utf-8')
    monkeypatch.setattr(w.subprocess, 'Popen', Process)
    return calls


def attempt():
    try:
        value = w.run(limit=1)
        return {'status': value.get('status'), 'integrity_field': value.get('integrity_field'), 'exception': None}
    except Exception as exc:
        return {'status': None, 'integrity_field': getattr(exc, 'field', None), 'exception': type(exc).__name__}


def worker():
    path = cfg.STATE / 'wiki-worker-status.json'
    value = json.loads(path.read_text()) if path.exists() else {}
    return {key: value.get(key) for key in ('status', 'reason', 'integrity_field', 'consecutive_failures', 'backoff_seconds')}


WORDS = [
    ('task', 'task-management-framework-design'),
    ('risk', 'risk-assessment-methodology'),
    ('long-ghp-word', 'highp_SyntheticMarkerAlphaBetaGammaDelta'),
    ('long-xoxb-word', 'lexoxb-SyntheticMarkerAlphaBetaGammaDelta'),
]


@pytest.mark.parametrize('prepared',['fresh','saved'])
@pytest.mark.parametrize('label,word', WORDS, ids=[row[0] for row in WORDS])
def test_whole_source_benign_word_must_not_become_chunk_credential(brain, monkeypatch, request, label, word, prepared):
    vault, _ = brain
    source = vault / '01-Projects' / 'Boundary.md'
    source.parent.mkdir()
    text = 'a' * 22998 + word + '\n' + 'Evidence line.\n' * 2000
    source.write_bytes(text.encode())
    before = source.read_bytes()
    s.clean_text(text, 2_000_000)
    isolated_rejected = False
    try:
        s.clean_text(text[23000:47000], 2_000_000)
    except ValueError:
        isolated_rejected = True
    assert isolated_rejected, 'Fixture must exercise classifier context loss.'
    calls = fake_skip_model(monkeypatch)
    first = attempt()
    first_calls = len(calls)
    if prepared=='saved':
        packet=w.prepare(1)
        assert packet['sources'][0]['part']==2
    second = attempt()
    second_calls = len(calls) - first_calls
    second_worker = worker()
    w.reset_retries()
    retry = attempt()
    retry_calls = len(calls) - first_calls - second_calls
    preserved = source.read_bytes() == before
    assert preserved
    assert first['status'] == 'complete' and first_calls == 1
    assert second['status'] == 'complete' and second_calls == 1
    assert retry['status'] == 'complete' and retry_calls == 1


def test_shifted_chunk_boundary_control(brain, monkeypatch, request):
    vault, _ = brain
    source = vault / '01-Projects' / 'BoundaryControl.md'
    source.parent.mkdir()
    text = 'a' * 22997 + WORDS[0][1] + '\n' + 'Evidence line.\n' * 2000
    source.write_bytes(text.encode())
    before = source.read_bytes()
    s.clean_text(text, 2_000_000)
    s.clean_text(text[23000:47000], 2_000_000)
    calls = fake_skip_model(monkeypatch)
    values = [attempt(), attempt()]
    assert [row['status'] for row in values] == ['complete', 'complete'] and len(calls) == 2
    assert source.read_bytes() == before


def test_genuine_source_credential_control(brain, monkeypatch, request):
    vault, _ = brain
    source = vault / '01-Projects' / 'Excluded.md'
    source.parent.mkdir()
    source.write_bytes(('# Synthetic\n' + 'ghp_' + 'SyntheticMarkerAlphaBetaGammaDelta').encode())
    before = source.read_bytes()
    calls = fake_skip_model(monkeypatch)
    inventory = w.inventory()
    value = attempt()
    assert not inventory and not calls and value['status'] == 'idle'
    assert source.read_bytes() == before


LOCATIONS = ['top-extra', 'source-extra', 'catalog-extra', 'catalog-claim-count',
             'page-claim-count', 'page-meta-extra', 'top-key', 'source-key']


def snapshot(root):
    return {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}


def prepared_with_page(brain):
    source, first, result = fixture(brain)
    w.apply(first, result)
    source.write_bytes(source.read_bytes() + b'\nNew owner evidence for another compilation.\n')
    packet = w.prepare(1)
    assert packet['catalog'] and packet['current_pages']
    return source, packet


def insert(packet, location, value, key_value=None):
    key_value = value if key_value is None else key_value
    if location == 'top-extra': packet['probe_extra'] = value
    elif location == 'source-extra': packet['sources'][0]['probe_extra'] = value
    elif location == 'catalog-extra': packet['catalog'][0]['probe_extra'] = value
    elif location == 'catalog-claim-count': packet['catalog'][0]['claim_count'] = value
    elif location == 'page-claim-count': packet['current_pages'][0]['claim_count'] = value
    elif location == 'page-meta-extra': packet['current_pages'][0]['meta']['probe_extra'] = value
    elif location == 'top-key': packet[key_value] = value
    elif location == 'source-key': packet['sources'][0][key_value] = value
    else: raise AssertionError('Unknown synthetic location')


def strings(value):
    if isinstance(value, str): yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from strings(key)
            yield from strings(child)
    elif isinstance(value, list):
        for child in value: yield from strings(child)


def received_flags(calls, credential):
    decoded = [json.loads(text.split('INPUT_JSON:\n', 1)[1]) for text in calls.inputs]
    values = list(strings(decoded))
    return {'stub_received_credential': any(credential in text for text in values),
            'stub_received_surrogate': any(any(0xD800 <= ord(char) <= 0xDFFF for char in text) for text in values)}


@pytest.mark.parametrize('location', LOCATIONS)
@pytest.mark.parametrize('shape', ['credential', 'surrogate'])
def test_unsafe_extra_decoded_string_never_reaches_model_stub(brain, monkeypatch, request, location, shape):
    source, packet = prepared_with_page(brain)
    before = source.read_bytes()
    changed = copy.deepcopy(packet)
    credential = 'ghp_' + 'SyntheticMarkerAlphaBetaGammaDelta'
    insert(changed, location, credential if shape == 'credential' else '\ud800')
    input_path = cfg.STATE / 'wiki' / 'batches' / packet['batch_id'] / 'input.json'
    input_path.write_bytes(json.dumps(changed).encode('ascii'))
    private=snapshot(input_path.parent); notes=snapshot(s.VAULT)
    calls = fake_skip_model(monkeypatch)
    value = attempt()
    flags = received_flags(calls, credential)
    preserved = source.read_bytes() == before
    assert preserved
    assert not flags['stub_received_credential'] and not flags['stub_received_surrogate']
    assert not calls and value['status']=='attention' and value['integrity_field']=='content'
    assert snapshot(input_path.parent)==private and snapshot(s.VAULT)==notes
    assert worker()['consecutive_failures']==0


@pytest.mark.parametrize('location', LOCATIONS)
def test_benign_extra_string_control(brain, monkeypatch, request, location):
    source, packet = prepared_with_page(brain)
    before = source.read_bytes()
    changed = copy.deepcopy(packet)
    insert(changed, location, 'Rotation policy is discussed without storing a credential.', 'ordinary-extra-key')
    (cfg.STATE / 'wiki' / 'batches' / packet['batch_id'] / 'input.json').write_bytes(json.dumps(changed).encode('ascii'))
    calls = fake_skip_model(monkeypatch)
    value = attempt()
    assert value['status'] == 'complete' and len(calls) == 1
    assert source.read_bytes() == before


@pytest.mark.parametrize('shape', ['credential', 'surrogate'])
def test_known_source_field_is_already_guarded_control(brain, monkeypatch, request, shape):
    source, packet = prepared_with_page(brain)
    before = source.read_bytes()
    changed = copy.deepcopy(packet)
    changed['sources'][0]['text'] = ('ghp_' + 'SyntheticMarkerAlphaBetaGammaDelta') if shape == 'credential' else '\ud800'
    (cfg.STATE / 'wiki' / 'batches' / packet['batch_id'] / 'input.json').write_bytes(json.dumps(changed).encode('ascii'))
    calls = fake_skip_model(monkeypatch)
    value = attempt()
    assert not calls and (value['status'] == 'attention' or value['exception'] == 'WikiIntegrityError')
    assert source.read_bytes() == before


def caught_run():
    try: return w.run(), None
    except Exception as error: return None, error


@pytest.mark.parametrize('filename', ['output.json', 'publication.json'])
def test_deep_saved_json_records_controlled_recovery(brain, monkeypatch, filename):
    _, packet, result, folder, _ = interrupted_plan(brain, monkeypatch)
    (folder / filename).write_bytes(b'[' * 100_000 + b'0' + b']' * 100_000)
    w.reset_retries()
    private, notes, before = snapshot(folder), snapshot(s.VAULT), row(packet)
    calls = fake_model(monkeypatch, explicit(packet, result))
    _, error = caught_run()
    state = w._read_worker_state()
    assert error is None or isinstance(error, w.WikiIntegrityError)
    assert state['attention_required'] and state['integrity_field'] == 'recovery'
    assert state['batch_id'] == packet['batch_id'] and state['consecutive_failures'] == 0
    assert not calls and snapshot(folder) == private and snapshot(s.VAULT) == notes and row(packet) == before
    assert w.run()['batch_id'] == packet['batch_id']
    assert w._saved_publication_started(packet, folder / 'publication.json') is False


def test_valid_unused_saved_plan_completes_without_new_call_control(brain, monkeypatch):
    _, packet, result, _, _ = interrupted_plan(brain, monkeypatch)
    calls = fake_model(monkeypatch, explicit(packet, result))
    w.reset_retries()
    report, error = caught_run()
    assert error is None and report['status'] == 'complete' and not calls


@pytest.mark.parametrize('collection', ['current_pages', 'catalog'])
@pytest.mark.parametrize('unsafe', ['../outside.md', '02-Areas/AI-Memory/Wiki/Projects/example.txt'])
def test_saved_page_path_is_validated_before_owner_hash(brain, monkeypatch, collection, unsafe):
    _, packet = prepared_with_page(brain)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    changed = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    changed[collection][0]['path'] = unsafe
    (folder / 'input.json').write_text(json.dumps(changed), encoding='utf-8')
    w.reset_retries()
    private, notes, before = snapshot(folder), snapshot(s.VAULT), row(packet)
    calls = fake_skip_model(monkeypatch)
    _, error = caught_run()
    state = w._read_worker_state()
    assert error is None or isinstance(error, w.WikiIntegrityError)
    assert state['attention_required'] and state['integrity_field'] == 'path'
    assert state['batch_id'] == packet['batch_id'] and state['consecutive_failures'] == 0
    assert not calls and snapshot(folder) == private and snapshot(s.VAULT) == notes and row(packet) == before


def test_valid_saved_current_page_preserves_existing_behavior_control(brain, monkeypatch):
    source, packet = prepared_with_page(brain)
    raw = source.read_bytes()
    calls = fake_skip_model(monkeypatch)
    report, error = caught_run()
    assert error is None and report['status'] == 'complete' and len(calls) == 1
    assert source.read_bytes() == raw


def test_actual_partial_invalid_saved_source_pointer_is_controlled(brain, monkeypatch):
    _, packet, _, index, _, owner, _ = partial_index_conflict(brain, monkeypatch)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    changed = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    changed['sources'][0].update(path='../outside.md', job_key='../outside.md::part1')
    (folder / 'input.json').write_text(json.dumps(changed), encoding='utf-8')
    w.reset_retries()
    private, notes = snapshot(folder), snapshot(s.VAULT)
    calls = fake_skip_model(monkeypatch)
    _, error = caught_run()
    state = w._read_worker_state()
    assert error is None or isinstance(error, w.WikiIntegrityError)
    assert state['attention_required'] and state['batch_id'] == packet['batch_id']
    assert not calls and snapshot(folder) == private and snapshot(s.VAULT) == notes and index.read_bytes() == owner


def test_deep_complete_journal_retains_batch_recovery(brain, monkeypatch):
    _, packet, _, folder = complete_unfinalized(brain, monkeypatch)
    (folder / 'journal.json').write_bytes(b'[' * 100_000 + b'0' + b']' * 100_000)
    w.reset_retries()
    private, notes, before = snapshot(folder), snapshot(s.VAULT), row(packet)
    calls = fake_skip_model(monkeypatch)
    _, error = caught_run()
    state = w._read_worker_state()
    assert isinstance(error, w.WikiIntegrityError) and state['integrity_field'] == 'recovery'
    assert state['attention_required'] and state['batch_id'] == packet['batch_id']
    assert not calls and snapshot(folder) == private and snapshot(s.VAULT) == notes and row(packet) == before


@pytest.mark.parametrize('finalized', [False, True])
def test_valid_completion_journal_and_legacy_expiry_still_recover(brain, monkeypatch, finalized):
    if finalized:
        _, packet, result = fixture(brain)
        w.apply(packet, result)
        folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
        (folder / 'input.json').unlink()
    else:
        _, packet, _, folder = complete_unfinalized(brain, monkeypatch)
    calls = fake_skip_model(monkeypatch)
    report, error = caught_run()
    assert error is None and report['status'] == ('idle' if finalized else 'complete') and not calls


def test_saved_json_memory_failure_remains_visible(brain, monkeypatch):
    _, _, _, folder, _ = interrupted_plan(brain, monkeypatch)
    output = folder / 'output.json'
    original = Path.read_text
    def fatal(path, *args, **kwargs):
        if path == output: raise MemoryError('Synthetic fatal resource failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', fatal)
    w.reset_retries()
    with pytest.raises(MemoryError): w.run()


@pytest.mark.parametrize('shape', ['deep', 'scalar', 'valid-control'])
def test_invalid_completed_journal_preserves_artifacts_and_cleanup_progress(brain, shape):
    source, packet, result = fixture(brain)
    w.apply(packet, result)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    journal = folder / 'journal.json'
    if shape == 'deep': journal.write_bytes(b'[' * 100_000 + b'0' + b']' * 100_000)
    elif shape == 'scalar': journal.write_bytes(b'42')
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with w.db() as conn: conn.execute('UPDATE batches SET updated=? WHERE id=?', (old, packet['batch_id']))
    private, notes = snapshot(folder), snapshot(s.VAULT)
    removed = w.cleanup()
    assert snapshot(s.VAULT) == notes and journal.read_bytes() == private['journal.json'] and source.exists()
    if shape == 'valid-control': assert removed > 0
    else: assert removed == 0 and snapshot(folder) == private


def test_cleanup_journal_fatal_memory_error_remains_visible(brain, monkeypatch):
    _, packet, result = fixture(brain)
    w.apply(packet, result)
    journal = cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'journal.json'
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with w.db() as conn: conn.execute('UPDATE batches SET updated=? WHERE id=?', (old, packet['batch_id']))
    original = Path.read_text
    def fatal(path, *args, **kwargs):
        if path == journal: raise MemoryError('Synthetic fatal resource failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', fatal)
    with pytest.raises(MemoryError): w.cleanup()
