"""Small private-cache correctness contracts; no model or service execution."""
import hashlib
import json
import os
import sys
import traceback

import pytest

import brain_settings as cfg
import brain_wiki as w


REASON = 'malformed private worker state; inspect it or reset with wiki retry'
MAX_DEPTH = 1100


def malformed(kind):
    if kind == 'oversized-integer-backoff':
        # A 310-digit ordinary JSON integer; no large input or stress fixture.
        return json.dumps({'status': 'idle', 'retry_after': 10 ** 309}).encode()
    assert kind == 'nested-json-cache'
    return ('{"status":"idle","retry_after":0,"details":' + '[' * MAX_DEPTH + '0' + ']' * MAX_DEPTH + '}').encode()


def call(entry):
    if entry == 'reader':
        return w._read_worker_state()
    if entry == 'summary':
        return w._worker_summary()
    if entry == 'status':
        return w.status()['worker']
    assert entry == 'run'
    return w.run(limit=1)


def observe(entry):
    try:
        return 'returned', call(entry), []
    except (OverflowError, RecursionError) as error:
        frames = [{'function': row.name, 'line': row.lineno, 'filename': row.filename}
                  for row in traceback.extract_tb(error.__traceback__)]
        return type(error).__name__, None, frames


def save(tmp_path, **values):
    values.update(pid=os.getpid(), maximum_json_container_depth=MAX_DEPTH,
                  recursion_limit=sys.getrecursionlimit(), actual_provider_processes_started=0,
                  actual_service_or_real_vault_access=False)
    with (tmp_path / 'observation.json').open('x', encoding='utf-8') as stream:
        json.dump(values, stream, indent=2)


@pytest.mark.parametrize('kind', ['oversized-integer-backoff'])
@pytest.mark.parametrize('entry', ['reader', 'summary', 'status', 'run'])
def test_malformed_private_worker_cache_returns_attention_and_preserves_raw(brain, monkeypatch, tmp_path, kind, entry):
    assert sys.getrecursionlimit() == 1000, 'This fixed small depth fixture requires the standard test-runtime limit.'
    path = cfg.STATE / 'wiki-worker-status.json'
    raw = malformed(kind)
    assert len(raw) < 2400
    path.write_bytes(raw)
    requests = []

    def forbidden(*args, **kwargs):
        requests.append(True)
        raise AssertionError('A malformed private worker cache must require operator attention before any model process.')

    monkeypatch.setattr(w.subprocess, 'Popen', forbidden)
    outcome, value, frames = observe(entry)
    save(tmp_path, entry=entry, kind=kind, outcome=outcome, traceback_frames=frames,
         private_raw_sha256=hashlib.sha256(raw).hexdigest(), private_raw_bytes=len(raw),
         private_raw_preserved=path.read_bytes() == raw, provider_process_requests=len(requests),
         synthetic_vault_remains_empty=not any(cfg.VAULT.rglob('*.md')))
    assert path.read_bytes() == raw and requests == []
    assert not any(cfg.VAULT.rglob('*.md'))
    assert outcome == 'returned', 'Malformed private cache must use its existing attention fallback.'
    assert value['status'] == 'attention' and value.get('attention_required', entry == 'run') is True
    assert value['consecutive_failures'] == 0 and value['reason'] == REASON
    if entry == 'run':
        assert value['manual_retry_required'] is True
    else:
        assert value['retry_after'] == 0


def benign(kind):
    if kind == 'missing':
        return None, 'idle', 0
    if kind == 'zero':
        return b'{"status":"idle","retry_after":0}', 'idle', 0
    if kind == 'fractional':
        return b'{"status":"retryable","retry_after":1234.5}', 'retryable', 1234.5
    if kind == 'large-finite-integer':
        return json.dumps({'status': 'output_backoff', 'retry_after': 10 ** 308}).encode(), 'output_backoff', 10 ** 308
    if kind == 'shallow-json':
        return ('{"status":"idle","retry_after":0,"details":' + '[' * 20 + '0' + ']' * 20 + '}').encode(), 'idle', 0
    assert kind == 'ordinary-invalid-json'
    return b'{ordinary invalid JSON', 'attention', 0


@pytest.mark.parametrize('kind', ['missing', 'zero', 'fractional', 'large-finite-integer', 'shallow-json', 'ordinary-invalid-json'])
@pytest.mark.parametrize('entry', ['reader', 'summary', 'status'])
def test_existing_worker_state_controls_keep_status_values_and_private_bytes(brain, tmp_path, kind, entry):
    raw, expected, retry = benign(kind)
    path = cfg.STATE / 'wiki-worker-status.json'
    if raw is not None:
        path.write_bytes(raw)
    outcome, value, frames = observe(entry)
    save(tmp_path, entry=entry, kind=kind, outcome=outcome, traceback_frames=frames,
         private_raw_preserved=path.read_bytes() == raw if raw is not None else not path.exists())
    assert outcome == 'returned' and value['status'] == expected and value['retry_after'] == retry
    assert value['attention_required'] is (expected == 'attention') and value['consecutive_failures'] == 0
    if raw is not None:
        assert path.read_bytes() == raw
    else:
        assert not path.exists()


@pytest.mark.parametrize('failure', [RuntimeError('synthetic unrelated bug'), MemoryError('synthetic allocation failure')])
@pytest.mark.parametrize('entry', ['reader', 'summary'])
def test_unrelated_or_fatal_worker_json_errors_remain_visible(brain, monkeypatch, tmp_path, entry, failure):
    path = cfg.STATE / 'wiki-worker-status.json'
    raw = b'{"status":"idle","retry_after":0}'
    path.write_bytes(raw)

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(w.json, 'loads', failed)
    with pytest.raises(type(failure)) as caught:
        call(entry)
    assert caught.value is failure and path.read_bytes() == raw
    save(tmp_path, entry=entry, kind='unrelated-or-fatal-control', outcome=type(caught.value).__name__,
         exact_exception_instance_preserved=caught.value is failure,
         private_raw_sha256=hashlib.sha256(raw).hexdigest(), private_raw_preserved=path.read_bytes() == raw)


@pytest.mark.parametrize('entry', ['reader', 'summary', 'status'])
def test_original1100_successful_cache_is_benign_and_preserved(brain, tmp_path, entry):
    # Explicit new control derived from the preserved v1 observation, rather
    # than rewriting its invalid attention oracle or imposing a depth policy.
    path = cfg.STATE / 'wiki-worker-status.json'
    raw = malformed('nested-json-cache')
    assert len(raw) == 2245 and MAX_DEPTH == 1100
    path.write_bytes(raw)
    outcome, value, frames = observe(entry)
    save(tmp_path, entry=entry, kind='explicit-original1100-benign-control', outcome=outcome,
         traceback_frames=frames, private_raw_sha256=hashlib.sha256(raw).hexdigest(),
         private_raw_preserved=path.read_bytes() == raw, actual_JSON_container_depth=1100)
    assert outcome == 'returned' and value['status'] == 'idle'
    assert value['attention_required'] is False and value['consecutive_failures'] == value['retry_after'] == 0
    assert path.read_bytes() == raw


@pytest.mark.parametrize('entry', ['reader', 'summary', 'status', 'run'])
def test_injected_decoder_recursion_uses_existing_cache_attention_boundary(brain, monkeypatch, tmp_path, entry):
    # Portable boundary contract. This deliberately injected decoder failure
    # does not claim that the same JSON depth fails on Windows, Linux or macOS.
    path = cfg.STATE / 'wiki-worker-status.json'
    raw = b'{"status":"idle","retry_after":0}'
    path.write_bytes(raw)
    decoder_calls, process_requests = [], []

    def decoder_failed(*args, **kwargs):
        decoder_calls.append(True)
        raise RecursionError('synthetic JSON decoder boundary fault')

    def forbidden(*args, **kwargs):
        process_requests.append(True)
        raise AssertionError('A decoder fault must require operator attention before any provider process.')

    monkeypatch.setattr(w.json, 'loads', decoder_failed)
    monkeypatch.setattr(w.subprocess, 'Popen', forbidden)
    outcome, value, frames = observe(entry)
    save(tmp_path, entry=entry, kind='injected-decoder-recursion-boundary', outcome=outcome,
         traceback_frames=frames, private_raw_sha256=hashlib.sha256(raw).hexdigest(),
         private_raw_preserved=path.read_bytes() == raw, decoder_calls=len(decoder_calls),
         provider_process_requests=len(process_requests), injected_failure_not_actual_depth_claim=True)
    assert path.read_bytes() == raw and process_requests == [] and decoder_calls
    assert outcome == 'returned' and value['status'] == 'attention' and value['reason'] == REASON
    assert value['consecutive_failures'] == 0 and value.get('attention_required', entry == 'run') is True
    if entry == 'run':
        assert value['manual_retry_required'] is True
    else:
        assert value['retry_after'] == 0
