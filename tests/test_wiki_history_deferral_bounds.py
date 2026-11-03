"""Bounded history deferral: fake Git transport, real history parser and worker."""
import copy
import hashlib
import json
import subprocess

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_prepare_integrity import existing_topic, note_snapshot
from test_wiki_remediation import explicit, fake_model
from test_wiki_output_preservation import batch, missing_old_source, no_git_process


def history_transport(monkeypatch, versions, fault):
    """Synthetic Git objects only: the real _git/_versions/source parser still runs."""
    objects = {}
    paths = {}
    for relative, bodies in versions.items():
        paths[relative] = []
        for index, raw in enumerate(bodies):
            commit = f'{index + 1:040x}'
            oid = hashlib.sha1(relative.encode() + str(index).encode() + raw).hexdigest()
            paths[relative].append((commit, oid))
            objects[oid] = (relative, raw)
    calls = []
    def transport(command, **kwargs):
        args = command[command.index('-C') + 2:]
        calls.append(args)
        if args[0] == 'log':
            data = b''.join(commit.encode() + b'\n' for commit, _ in paths.get(args[-1], []))
        elif args[0] == 'ls-tree':
            oid = next(oid for commit, oid in paths[args[-1]] if commit == args[2])
            data = b'100644 blob ' + oid.encode() + b'\t' + args[-1].encode() + b'\0'
        elif args[:2] == ['cat-file', '-s']:
            relative, raw = objects[args[2]]
            size = 2_000_001 if fault.get('kind') in ('blob-size', 'blob-size-then-deadline') else len(raw)
            if fault.get('kind') == 'blob-size-then-deadline':
                fault['history'].deadline = w.time.monotonic() - 1
            data = str(size).encode() + b'\n'
        elif args[:2] == ['cat-file', 'blob']:
            relative, data = objects[args[2]]
            if fault.get('kind') == 'blob-timeout' and fault.get('source', relative) == relative:
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        else:
            pytest.fail('Unexpected synthetic Git request: ' + repr(args))
        return subprocess.CompletedProcess(command, 0, stdout=data, stderr=b'')
    monkeypatch.setattr(w, '_GIT_RUN', transport)
    return calls


def history_initialization(monkeypatch, fault):
    actual = w._EvidenceHistory.__init__
    def initialized(self):
        actual(self)
        fault['history'] = self
        if fault.get('kind') == 'deadline': self.deadline = w.time.monotonic() - 1
        if fault.get('kind') == 'commands': self.commands = 256
        if fault.get('kind') == 'blob-bytes': self.bytes = 16_000_000
    monkeypatch.setattr(w._EvidenceHistory, '__init__', initialized)


@pytest.mark.parametrize('kind', ['deadline', 'blob-timeout'])
def test_third_persistent_history_deferral_requires_manual_retry_and_reuses_output(brain, monkeypatch, kind):
    source, another, topic, packet, result, original = missing_old_source(brain)
    before = note_snapshot()
    fault = {'kind': kind}
    git_calls = history_transport(monkeypatch, {source.relative_to(s.VAULT).as_posix(): [original]}, fault)
    history_initialization(monkeypatch, fault)
    calls = fake_model(monkeypatch, result)
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    states = []
    for attempt in range(3):
        with pytest.raises(ValueError) as caught: w.run()
        state = w._read_worker_state()
        states.append({key: state.get(key) for key in ('status', 'history_deferrals', 'attention_required',
                                                      'consecutive_failures', 'backoff_seconds', 'history_reasons')})
        assert len(calls) == 1 and note_snapshot() == before and not source.exists()
        assert state['consecutive_failures'] == 0 and state['backoff_seconds'] == 0
        assert batch(packet)['attempts'] == 0 and batch(packet)['status'] == 'retryable'
        if attempt < 2: assert state['status'] == 'retryable' and not state['attention_required']
    print('BOUNDED_HISTORY_OBSERVATION ' + json.dumps({'kind': kind, 'states': states, 'fake_model_calls': len(calls)}, sort_keys=True))
    assert state['status'] == 'attention' and state['attention_required'] and state['manual_retry_required']
    assert state['history_deferrals'] == 3 and state['integrity_field'] == 'source_revision'
    assert state['integrity_page'] == topic.relative_to(s.VAULT).as_posix() and state['claim_index'] == 0
    expected = ['deadline'] if kind == 'deadline' else ['git-timeout']
    assert state['history_reasons'] == expected
    assert caught.value.page == state['integrity_page'] and caught.value.claim_index == 0
    assert list(caught.value.reasons) == expected
    saved = {name: (folder/name).read_bytes() for name in ('input.json', 'output.json')}
    request_count = len(git_calls)
    assert w.run()['status'] == 'attention' and len(calls) == 1 and len(git_calls) == request_count
    assert not (folder/'publication.json').exists()
    w.reset_retries()
    assert all(key not in w._read_worker_state() for key in ('history_deferrals', 'history_reasons', 'integrity_page'))
    fault.clear()
    recovered = w.run()
    assert recovered['status'] == 'complete' and recovered['batch_id'] == packet['batch_id']
    assert len(calls) == 1 and not source.exists() and another.read_bytes() == before['01-Projects/Another.md']
    assert all((folder/name).read_bytes() == raw for name, raw in saved.items())
    assert all(key not in w._read_worker_state() for key in ('history_deferrals', 'history_reasons', 'integrity_page'))


def test_history_deferral_count_does_not_follow_a_different_batch(brain, monkeypatch):
    source, _, _, packet, result, original = missing_old_source(brain)
    fault = {'kind': 'blob-timeout'}
    history_transport(monkeypatch, {source.relative_to(s.VAULT).as_posix(): [original]}, fault)
    w._atomic_json(cfg.STATE/'wiki-worker-status.json', {
        'status': 'retryable', 'batch_id': 'f'*24, 'history_deferrals': 2,
        'consecutive_failures': 0, 'retry_after': 0, 'attention_required': False})
    calls = fake_model(monkeypatch, result)
    with pytest.raises(ValueError): w.run()
    state = w._read_worker_state()
    assert state['batch_id'] == packet['batch_id'] and state['history_deferrals'] == 1
    assert state['status'] == 'retryable' and not state['attention_required'] and len(calls) == 1


def test_cached_lookup_replays_hard_limit_and_deadline_from_actual_parser(brain, monkeypatch):
    source, _, _, _, _, original = missing_old_source(brain)
    relative = source.relative_to(s.VAULT).as_posix()
    fault = {'kind': 'blob-size-then-deadline'}
    calls = history_transport(monkeypatch, {relative: [original, original + b'\nLater revision.\n']}, fault)
    history = w._EvidenceHistory()
    fault['history'] = history
    previous = history.failure_serial
    assert history.source(relative, s.digest(original), 'A prototype was demonstrated.') is None
    first = {reason for reason, serial in history.failure_serial_by_reason.items() if serial > previous}
    assert first == {'blob-size', 'deadline'}
    count = len(calls)
    previous = history.failure_serial
    assert history.source(relative, s.digest(original), 'A prototype was demonstrated.') is None
    replayed = {reason for reason, serial in history.failure_serial_by_reason.items() if serial > previous}
    print('CACHE_REASON_OBSERVATION ' + json.dumps({'first': sorted(first), 'replayed': sorted(replayed),
          'transport_calls_before_replay': count, 'transport_calls_after_replay': len(calls)}, sort_keys=True))
    assert replayed == first and len(calls) == count and history.commands == 3


@pytest.mark.parametrize('kind', ['commands', 'commits', 'blob-size', 'blob-bytes'])
def test_actual_history_parser_hard_limits_remain_integrity_attention(brain, monkeypatch, kind):
    source, _, _, packet, result, original = missing_old_source(brain)
    relative = source.relative_to(s.VAULT).as_posix()
    fault = {'kind': kind}
    bodies = [b'# A different synthetic historical revision.\n']*65 if kind == 'commits' else [original]
    transport_calls = history_transport(monkeypatch, {relative: bodies}, fault)
    history_initialization(monkeypatch, fault)
    calls = fake_model(monkeypatch, result)
    before = note_snapshot()
    with pytest.raises(w.WikiIntegrityError) as caught: w.run()
    state = w._read_worker_state()
    history = fault['history']
    assert kind in history.failure_serial_by_reason and caught.value.field == 'source_revision'
    assert state['status'] == 'attention' and state['attention_required'] and state['consecutive_failures'] == 0
    assert state.get('history_deferrals', 0) == 0 and note_snapshot() == before
    assert not (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists() and len(calls) == 1
    assert history.commands <= 256 and history.bytes <= 16_000_000
    if kind in ('blob-size', 'blob-bytes'):
        assert not any(args[:2] == ['cat-file', 'blob'] for args in transport_calls)


def test_earlier_current_claim_hard_limit_does_not_taint_later_missing_claim_timeout(brain, monkeypatch):
    source, another, topic, result = existing_topic(brain)
    original = source.read_bytes()
    current = source.with_name('Current.md')
    current.write_bytes(original)
    info = w.meta(topic.read_text(encoding='utf-8'))
    current_claim = copy.deepcopy(info['wiki_claims'][0])
    current_claim['source'] = current.relative_to(s.VAULT).as_posix()
    current_claim['source_revision']['sha256'] = 'f'*64
    info['wiki_claims'].insert(0, current_claim)
    topic.write_bytes(s.note_text(info, '# Synthetic two-claim topic.').encode())
    source.unlink()
    packet = w.prepare()
    fault = {'kind': 'blob-timeout', 'source': source.relative_to(s.VAULT).as_posix()}
    history_transport(monkeypatch, {source.relative_to(s.VAULT).as_posix(): [original],
        current.relative_to(s.VAULT).as_posix(): [b'# Different historical bytes.\n']*65}, fault)
    history_initialization(monkeypatch, fault)
    calls = fake_model(monkeypatch, explicit(packet, result))
    before = note_snapshot()
    with pytest.raises(ValueError) as caught: w.run()
    state = w._read_worker_state()
    assert 'commits' in fault['history'].failure_serial_by_reason
    assert type(caught.value).__name__ == 'WikiHistoryDeferred'
    assert caught.value.claim_index == 1 and list(caught.value.reasons) == ['git-timeout']
    assert state['history_reasons'] == ['git-timeout'] and state['history_deferrals'] == 1
    assert state['claim_index'] == 1 and state['status'] == 'retryable' and not state['attention_required']
    assert len(calls) == 1 and note_snapshot() == before
    assert not (cfg.STATE/'wiki/batches'/packet['batch_id']/'publication.json').exists()
