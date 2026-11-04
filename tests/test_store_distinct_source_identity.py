"""Distinct evidence follows actual filesystem identity, with literal links retained."""
from pathlib import Path

import pytest
import yaml

import brain_store as store


TARGET = '02-Areas/AI-Memory/Current.md'


def files(vault, state):
    return {label + '/' + p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
            for label, root in [('vault', vault), ('state', state)] for p in root.rglob('*')}


def prepare(brain):
    vault, state = brain
    target = vault / TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(b'# Existing derived note\nOwner-controlled historical content.\n')
    source = vault / '01-Projects/Distinct.md'
    source.parent.mkdir()
    source.write_bytes(b'# Distinct source evidence\nSupported synthetic event.\n')
    return target, source


def update(source, expected):
    return store.update_current(TARGET, 'Supported derived summary', 'Supported synthetic content.', [source], expected)


def assert_refused(brain, monkeypatch, source, expected):
    vault, state = brain
    before = files(vault, state)
    def forbidden(*args, **kwargs):
        raise AssertionError('Self-source refusal must precede writer lock and writes.')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'FileLock', forbidden)
        with pytest.raises(ValueError, match='distinct'):
            update(source, expected)
    assert files(vault, state) == before


@pytest.mark.parametrize('spelling', ['literal', 'backslash', 'filename-case', 'directory-case'])
def test_actual_self_identity_is_refused_but_distinct_case_sensitive_file_is_accepted(brain, monkeypatch, spelling):
    vault, state = brain
    target, _ = prepare(brain)
    aliases = {'literal': TARGET, 'backslash': TARGET.replace('/', '\\'),
               'filename-case': '02-Areas/AI-Memory/current.md',
               'directory-case': '02-Areas/ai-memory/Current.md'}
    relative = aliases[spelling]
    source = store.safe_path(relative)
    original = target.read_bytes()
    expected = store.digest(original)
    if source.exists() and source.samefile(target):
        assert_refused(brain, monkeypatch, relative, expected)
    else:
        # Real case-sensitive filesystems can hold a genuinely different file.
        # Such evidence must remain usable without case-folding stored paths.
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'# Separate case-sensitive source evidence\n')
        assert not source.samefile(target)
        source_before = source.read_bytes()
        receipt = update(relative, expected)
        assert receipt['status'] == 'saved'
        meta = yaml.safe_load(target.read_text('utf-8').split('---', 2)[1])
        assert meta['sources'] == [relative]
        assert meta['source_hashes'][relative] == store.digest(source_before)
        assert source.read_bytes() == source_before


def test_alias_refusal_precedes_missing_compare_and_swap_without_creating_a_backup(brain, monkeypatch):
    prepare(brain)
    assert_refused(brain, monkeypatch, TARGET.replace('/', '\\'), None)


def test_one_distinct_source_does_not_allow_a_second_self_alias(brain, monkeypatch):
    vault, state = brain
    target, source = prepare(brain)
    before = files(vault, state)
    def forbidden(*args, **kwargs):
        raise AssertionError('Every source must be distinct before locking or writing.')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'FileLock', forbidden)
        with pytest.raises(ValueError, match='distinct'):
            store.update_current(TARGET, 'Supported summary', 'Synthetic content.',
                                 [source.relative_to(vault).as_posix(), TARGET.replace('/', '\\')],
                                 store.digest(target.read_bytes()))
    assert files(vault, state) == before


def test_missing_target_accepts_distinct_existing_evidence_and_preserves_literal_path(brain):
    vault, state = brain
    source = vault / '01-Projects/Distinct.md'
    source.parent.mkdir()
    source.write_bytes(b'# Distinct supporting source\n')
    literal = source.relative_to(vault).as_posix().replace('/', '\\')
    before = source.read_bytes()
    assert not (vault / TARGET).exists()
    receipt = update(literal, None)
    assert receipt['status'] == 'saved'
    meta = yaml.safe_load((vault / TARGET).read_text('utf-8').split('---', 2)[1])
    assert meta['sources'] == [literal] and meta['source_hashes'][literal] == store.digest(before)
    assert source.read_bytes() == before and not (state / 'write-backups').exists()


def test_existing_target_uses_distinct_source_with_compare_and_swap_and_read_alias_still_works(brain):
    vault, state = brain
    target, source = prepare(brain)
    old = target.read_bytes()
    original_source = source.read_bytes()
    assert store.read(TARGET.replace('/', '\\'))['sha256'] == store.digest(old)
    before = files(vault, state)
    with pytest.raises(ValueError, match='Concurrent change'):
        update(source.relative_to(vault).as_posix(), '0' * 64)
    # FileLock may retain an empty runtime lock file on POSIX. The CAS
    # contract protects note, audit and backup bytes, not lock-file lifetime.
    protected = lambda values: {k: v for k, v in values.items() if k != 'state/writer.lock'}
    assert protected(files(vault, state)) == protected(before)
    receipt = update(source.relative_to(vault).as_posix(), store.digest(old))
    assert receipt['status'] == 'saved'
    assert (state / 'write-backups' / store.digest(old)).read_bytes() == old
    assert source.read_bytes() == original_source


def test_missing_source_is_still_refused_without_any_mutation(brain):
    vault, state = brain
    target, _ = prepare(brain)
    before = files(vault, state)
    with pytest.raises(ValueError, match='distinct'):
        update('01-Projects/Missing.md', store.digest(target.read_bytes()))
    assert files(vault, state) == before
