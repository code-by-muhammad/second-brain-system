"""The compiler and Git share one finite archive capacity; no large fixtures."""
import copy
from pathlib import Path

import pytest

import brain_git
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model


def test_legacy_history_over_shared_capacity_stops_before_reads_model_or_publication(brain, monkeypatch):
    source, packet, result = fixture(brain)
    original = source.read_bytes()
    log = store.VAULT / wiki.BASE / 'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    raw = b'# Existing history\n' + b'a' * 100
    log.write_bytes(raw)
    calls = fake_model(monkeypatch, explicit(packet, result))
    original_open = Path.open
    reads = []

    def refuse_read(self, mode='r', *args, **kwargs):
        if self == log and mode == 'rb':
            reads.append(mode)
            pytest.fail('Unsupported legacy history must stop before content reads.')
        return original_open(self, mode, *args, **kwargs)

    with monkeypatch.context() as cap:
        cap.setattr(brain_git, 'ARCHIVE_BYTES_LIMIT', 32, raising=False)
        cap.setattr(Path, 'open', refuse_read)
        report = wiki.run()
    assert report['status'] == 'attention' and report['integrity_field'] == 'archive_capacity'
    assert not reads and not calls
    assert log.read_bytes() == raw and source.read_bytes() == original
    assert not (log.parent / 'Log-Archives').exists()
    assert not (cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'publication.json').exists()
    assert wiki._worker_summary()['integrity_field'] == 'archive_capacity'


def test_stream_fstat_capacity_guard_rechecks_after_path_stat(brain, monkeypatch):
    log = store.VAULT / '01-Projects/History.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b'a' * 33)
    with pytest.raises(wiki.WikiIntegrityError) as caught:
        wiki._stream_file(log, max_bytes=32)
    assert caught.value.field == 'archive_capacity'
    assert log.read_bytes() == b'a' * 33


def test_legacy_history_at_shared_capacity_is_staged_exactly(brain, monkeypatch):
    _, packet, _ = fixture(brain)
    log = store.VAULT / wiki.BASE / 'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    raw = b'Preserve exact history.\r\n' * 4
    log.write_bytes(raw)
    monkeypatch.setattr(brain_git, 'ARCHIVE_BYTES_LIMIT', len(raw), raising=False)
    monkeypatch.setattr(wiki, 'LOG_LIMIT', 32)
    snapshot = wiki._log_snapshot(cfg.STATE / 'wiki/batches' / packet['batch_id'])
    assert snapshot['bytes'] == len(raw) and snapshot['sha256'] == store.digest(raw)
    assert (cfg.STATE / 'wiki/batches' / packet['batch_id'] / snapshot['spool']).read_bytes() == raw
    assert log.read_bytes() == raw


@pytest.mark.parametrize('change', ['descriptor', 'spool'])
def test_saved_archive_payload_over_capacity_is_refused_before_reading_spool(brain, monkeypatch, change):
    _, packet, result = fixture(brain)
    log = store.VAULT / wiki.BASE / 'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    raw = b'Historical compiler evidence.\n' * 100
    log.write_bytes(raw)
    monkeypatch.setattr(wiki, 'LOG_LIMIT', 1024)
    plan = wiki._publication_plan(packet, result, wiki._dispositions(packet, result))
    assert plan['log_rotation']['bytes'] == len(raw)
    spool = cfg.STATE / 'wiki/batches' / packet['batch_id'] / plan['log_rotation']['spool']
    original_open = Path.open

    def refuse_read(self, mode='r', *args, **kwargs):
        if self == spool and mode == 'rb':
            pytest.fail('Oversize archive descriptor must stop before spool content reads.')
        return original_open(self, mode, *args, **kwargs)

    if change == 'descriptor':
        monkeypatch.setattr(brain_git, 'ARCHIVE_BYTES_LIMIT', 32, raising=False)
    else:
        plan = copy.deepcopy(plan)
        plan['log_rotation']['bytes'] = 32
        # The descriptor fits, but fstat sees a larger immutable spool.
        monkeypatch.setattr(brain_git, 'ARCHIVE_BYTES_LIMIT', 32, raising=False)
    with monkeypatch.context() as guard:
        if change == 'descriptor': guard.setattr(Path, 'open', refuse_read)
        with pytest.raises(wiki.WikiIntegrityError):
            wiki._archive_payload(plan, packet)
    assert log.read_bytes() == raw and spool.read_bytes() == raw
