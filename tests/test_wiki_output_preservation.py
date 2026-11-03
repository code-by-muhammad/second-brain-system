"""Synthetic worker replay: no real model, Git process, network or owner notes."""
import json
import subprocess

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic, note_snapshot
from test_wiki_remediation import explicit, fake_model, source_rows


@pytest.fixture(autouse=True)
def no_git_process(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('This proof must stub every historical Git process')
    monkeypatch.setattr(w, '_GIT_RUN', forbidden)


def batch(packet):
    with w.db() as conn:
        return dict(conn.execute('SELECT * FROM batches WHERE id=?', (packet['batch_id'],)).fetchone())


def observation(case, error, packet, calls):
    state = w._read_worker_state()
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    print('OUTPUT_PRESERVATION_OBSERVATION ' + json.dumps({
        'case': case, 'error': type(error).__name__, 'worker_status': state['status'],
        'batch_status': batch(packet)['status'], 'consecutive_failures': state['consecutive_failures'],
        'backoff_seconds': state['backoff_seconds'], 'attention_required': state['attention_required'],
        'batch_attempts': batch(packet)['attempts'], 'fake_model_calls': len(calls),
        'saved_output_exists': (folder/'output.json').exists(),
        'publication_exists': (folder/'publication.json').exists(),
        'actual_model_calls': 0, 'actual_git_processes': 0, 'owner_notes_used': False,
    }, sort_keys=True))
    return state, folder


def assert_retryable(state, packet):
    assert state['status'] == 'retryable'
    assert state['consecutive_failures'] == 0 and state['backoff_seconds'] == 0
    assert state['retry_after'] == 0 and not state['attention_required']
    assert batch(packet)['status'] == 'retryable' and batch(packet)['attempts'] == 0
    assert all(row['attempts'] == 0 for row in source_rows().values())


@pytest.mark.parametrize('stage', ['stopped', 'start failed'])
def test_hold_arriving_during_model_preserves_output_without_model_backoff(brain, monkeypatch, stage):
    source, packet, result = fixture(brain)
    before = note_snapshot()
    hold = cfg.STATE/'service-transaction.json'
    def installed_hold():
        hold.write_text(json.dumps({'root': str(cfg.ROOT), 'hold_id': 'a'*32, 'stage': stage}))
    calls = fake_model(monkeypatch, explicit(packet, result), after=installed_hold)
    with pytest.raises(ValueError) as caught:
        w.run()
    state, folder = observation('admission-' + stage, caught.value, packet, calls)
    assert note_snapshot() == before and hold.exists() and len(calls) == 1
    assert not (folder/'publication.json').exists()
    saved = {name: (folder/name).read_bytes() for name in ('input.json', 'output.json')}
    assert_retryable(state, packet)
    assert type(caught.value).__name__ == 'WikiAdmissionDeferred'
    # A failed start is repaired by the controller, simulated only in private test state.
    hold.unlink()
    recovered = w.run()
    assert recovered['status'] == 'complete' and recovered['batch_id'] == packet['batch_id']
    assert len(calls) == 1 and source.read_bytes() == before['01-Projects/Example.md']
    assert all((folder/name).read_bytes() == raw for name, raw in saved.items())


def test_operator_retry_does_not_repurchase_output_after_admission_deferral(brain, monkeypatch):
    source, packet, result = fixture(brain)
    original = source.read_bytes()
    hold = cfg.STATE/'service-transaction.json'
    first = [True]
    def hold_once():
        if first[0]:
            first[0] = False
            hold.write_text(json.dumps({'root': str(cfg.ROOT), 'hold_id': 'a'*32, 'stage': 'start failed'}))
    calls = fake_model(monkeypatch, explicit(packet, result), after=hold_once)
    with pytest.raises(ValueError):
        w.run()
    hold.unlink()
    w.reset_retries()  # Explicit operator retry after repairing the failed start.
    recovered = w.run()
    print('OUTPUT_REPURCHASE_OBSERVATION ' + json.dumps({
        'status': recovered['status'], 'fake_model_calls_total': len(calls),
        'same_batch': recovered['batch_id'] == packet['batch_id'],
        'source_preserved': source.read_bytes() == original,
        'actual_model_calls': 0, 'actual_git_processes': 0, 'owner_notes_used': False,
    }, sort_keys=True))
    assert recovered['status'] == 'complete' and recovered['batch_id'] == packet['batch_id']
    assert source.read_bytes() == original and len(calls) == 1


def missing_old_source(brain):
    source, another, topic, result = existing_topic(brain)
    original = source.read_bytes()
    source.unlink()  # Synthetic owner deletion; recovery must not restore original notes.
    packet = w.prepare()
    return source, another, topic, packet, explicit(packet, result), original


def fake_git_history(monkeypatch, relative, original, fault):
    """Feed exact synthetic historical bytes through the real bounded history parser."""
    def transport(command, **kwargs):
        args = command[command.index('-C') + 2:]
        if fault.get('kind') == 'git-timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if args[0] == 'log':
            assert args[-1] == relative
            data = b'' if fault.get('kind') == 'missing-revision' else b'a'*40 + b'\n'
        elif args[0] == 'ls-tree':
            assert args[-1] == relative
            data = b'100644 blob ' + b'b'*40 + b'\t' + relative.encode() + b'\0'
        elif args[:2] == ['cat-file', '-s']:
            data = str(len(original)).encode() + b'\n'
        elif args[:2] == ['cat-file', 'blob']:
            data = original
        else:
            pytest.fail('Unexpected synthetic Git request: ' + repr(args))
        return subprocess.CompletedProcess(command, 0, stdout=data, stderr=b'')
    monkeypatch.setattr(w, '_GIT_RUN', transport)


@pytest.mark.parametrize('failure', ['deadline', 'git-timeout'])
def test_transient_history_timeout_preserves_output_and_replays_exact_origin(brain, monkeypatch, failure):
    source, another, topic, packet, result, original = missing_old_source(brain)
    before = note_snapshot()
    fault = {'kind': failure}
    fake_git_history(monkeypatch, source.relative_to(s.VAULT).as_posix(), original, fault)
    actual_init = w._EvidenceHistory.__init__
    def expired(self):
        actual_init(self)
        if fault.get('kind') == 'deadline':
            self.deadline = w.time.monotonic() - 1
    monkeypatch.setattr(w._EvidenceHistory, '__init__', expired)
    calls = fake_model(monkeypatch, result)
    with pytest.raises(ValueError) as caught:
        w.run()
    state, folder = observation('history-' + failure, caught.value, packet, calls)
    assert note_snapshot() == before and not source.exists() and len(calls) == 1
    assert not (folder/'publication.json').exists()
    saved = {name: (folder/name).read_bytes() for name in ('input.json', 'output.json')}
    assert_retryable(state, packet)
    assert type(caught.value).__name__ == 'WikiHistoryDeferred'
    fault.clear()
    recovered = w.run()
    assert recovered['status'] == 'complete' and recovered['batch_id'] == packet['batch_id']
    assert len(calls) == 1 and not source.exists() and another.read_bytes() == before['01-Projects/Another.md']
    assert all((folder/name).read_bytes() == raw for name, raw in saved.items())
    assert not recovered['lint']['issues'] and recovered['lint']['historical_evidence_count'] == 1


@pytest.mark.parametrize('reasons', [[], ['commands'], ['commits'], ['blob-size'], ['blob-bytes'],
                                    ['git-error'], ['git-timeout', 'commits'], ['commits', 'deadline']])
def test_unverifiable_or_hard_limited_history_still_requires_integrity_review(brain, monkeypatch, reasons):
    source, _, _, packet, result, original = missing_old_source(brain)
    before = note_snapshot()
    fake_git_history(monkeypatch, source.relative_to(s.VAULT).as_posix(), original, {'kind': 'missing-revision'})
    actual_claim = w._EvidenceHistory.claim
    def incomplete_claim(self, page, claim):
        proof = actual_claim(self, page, claim)
        if claim['source'] == source.relative_to(s.VAULT).as_posix() and not proof:
            for reason in reasons:
                self._incomplete(reason)
        return proof
    monkeypatch.setattr(w._EvidenceHistory, 'claim', incomplete_claim)
    calls = fake_model(monkeypatch, result)
    with pytest.raises(w.WikiIntegrityError) as caught:
        w.run()
    state, folder = observation('history-conservative-' + ','.join(reasons), caught.value, packet, calls)
    assert caught.value.field == 'source_revision'
    assert state['status'] == 'attention' and state['attention_required'] and state['consecutive_failures'] == 0
    assert batch(packet)['status'] == 'error' and batch(packet)['attempts'] == 0
    assert note_snapshot() == before and not source.exists() and not (folder/'publication.json').exists()
    assert w.run()['status'] == 'attention' and len(calls) == 1


def test_spelling_scan_uncertainty_preserves_paid_output_for_same_batch_replay(brain, monkeypatch):
    source, packet, result = fixture(brain)
    original = source.read_bytes()
    before = note_snapshot()
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    fault = {'after_saved_output': False, 'scans_refused': 0}
    actual_spelling = s._existing_write_path
    def saved_model_output():
        assert (folder/'output.json').is_file()
        fault['after_saved_output'] = True
    def uncertain_spelling(path):
        if path == source and fault['after_saved_output']:
            fault['scans_refused'] += 1
            raise ValueError('Synthetic bounded directory scan uncertainty after paid output')
        return actual_spelling(path)
    monkeypatch.setattr(s, '_existing_write_path', uncertain_spelling)
    calls = fake_model(monkeypatch, explicit(packet, result), after=saved_model_output)
    with pytest.raises((OSError, w.WikiTransientError)) as caught:
        w.run()
    state, folder = observation('source-spelling-scan-uncertainty', caught.value, packet, calls)
    assert fault['scans_refused'] == 1 and len(calls) == 1
    assert source.read_bytes() == original and note_snapshot() == before
    assert not (folder/'publication.json').exists()
    saved = {name: (folder/name).read_bytes() for name in ('input.json', 'output.json')}
    assert state['status'] == 'retryable' and not state['attention_required']
    # Ordinary I/O failure is counted; unlike admission/history deferral it can back off.
    assert state['consecutive_failures'] == 1 and state['backoff_seconds'] == 3600
    assert batch(packet)['attempts'] == 1
    assert batch(packet)['status'] == 'retryable'
    assert type(caught.value) is OSError
    fault['after_saved_output'] = False
    w.reset_retries()  # Explicit operator retry after local scan access is repaired.
    recovered = w.run()
    print('SPELLING_SCAN_REPLAY_OBSERVATION ' + json.dumps({
        'status': recovered['status'], 'same_batch': recovered['batch_id'] == packet['batch_id'],
        'fake_model_calls_total': len(calls), 'source_preserved': source.read_bytes() == original,
        'saved_input_output_preserved': all((folder/name).read_bytes() == raw for name, raw in saved.items()),
        'actual_model_calls': 0, 'actual_git_processes': 0, 'owner_notes_used': False,
    }, sort_keys=True))
    assert recovered['status'] == 'complete' and recovered['batch_id'] == packet['batch_id']
    assert len(calls) == 1 and source.read_bytes() == original
    assert all((folder/name).read_bytes() == raw for name, raw in saved.items())
