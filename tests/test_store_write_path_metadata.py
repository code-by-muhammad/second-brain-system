"""Credential screening at shared-note path boundaries; all inputs are synthetic."""
import hashlib
import json

import pytest

import brain_store as store


SENSITIVE_FILENAMES = [
    'sk-SyntheticCredential0123456789',
    'ghp_SyntheticCredential0123456789',
    'xoxb-SyntheticCredential0123456789',
    'api_key=SyntheticCredential0123456789',
    'password=SyntheticCredential0123456789',
    'access_token=SyntheticCredential0123456789',
    'secret=SyntheticCredential0123456789',
    '-----BEGIN PRIVATE KEY-----',
]
CHANNELS = ['capture-evidence', 'update-source', 'update-destination']


def snapshot(vault, state):
    return {
        label + '/' + path.relative_to(root).as_posix():
            path.read_bytes() if path.is_file() else None
        for label, root in [('vault', vault), ('state', state)]
        for path in root.rglob('*')
    }


def prepare(brain, filename, channel, existing_target):
    vault, state = brain
    source_name = filename if channel != 'update-destination' else 'Ordinary-evidence'
    source = vault / ('01-Projects/' + source_name + '.md')
    source.parent.mkdir(parents=True)
    source.write_bytes(b'# Existing original evidence\nDo not alter this source.\n')
    target_name = filename if channel == 'update-destination' else 'Current'
    target = vault / ('02-Areas/AI-Memory/' + target_name + '.md')
    if existing_target:
        target.parent.mkdir(parents=True)
        target.write_bytes(b'# Existing derived note\nRetain until a valid update.\n')
    (state / 'audit.jsonl').write_bytes(b'{"existing": true}\n')
    backup = state / 'write-backups' / ('e' * 64)
    backup.parent.mkdir()
    backup.write_bytes(b'Existing backup must remain unchanged.\n')
    return source.relative_to(vault).as_posix(), target.relative_to(vault).as_posix()


def write(channel, source, target, expected=None):
    if channel == 'capture-evidence':
        return store.capture('synthetic-path-screening', 'Synthetic milestone',
                             'Supported synthetic outcome.', 'test',
                             'disposable synthetic fixture', evidence_paths=[source])
    return store.update_current(target, 'Synthetic derived summary',
                                'Supported synthetic content.', [source], expected)


def refuse_before_selection(monkeypatch, channel, source, target, expected=None):
    def forbidden(*args, **kwargs):
        raise AssertionError('Refusal must precede hashing, lock acquisition and writing.')

    with monkeypatch.context() as patch:
        patch.setattr(store, 'digest', forbidden)
        patch.setattr(store, 'FileLock', forbidden)
        with pytest.raises(ValueError, match='credential'):
            write(channel, source, target, expected)


@pytest.mark.parametrize('filename', SENSITIVE_FILENAMES)
@pytest.mark.parametrize('channel', CHANNELS)
def test_sensitive_path_is_refused_without_changing_existing_data(brain, monkeypatch, filename, channel):
    vault, state = brain
    source, target = prepare(brain, filename, channel, existing_target=True)
    assert store.SECRET.search(source if channel != 'update-destination' else target)
    before = snapshot(vault, state)
    expected = hashlib.sha256((vault / target).read_bytes()).hexdigest()
    refuse_before_selection(monkeypatch, channel, source, target, expected)
    assert snapshot(vault, state) == before


@pytest.mark.parametrize('channel', CHANNELS)
def test_sensitive_path_refusal_creates_no_note_lock_backup_or_audit(brain, monkeypatch, channel):
    vault, state = brain
    source, target = prepare(brain, SENSITIVE_FILENAMES[3], channel, existing_target=False)
    before = snapshot(vault, state)
    refuse_before_selection(monkeypatch, channel, source, target)
    assert snapshot(vault, state) == before


@pytest.mark.parametrize('channel', CHANNELS)
def test_ordinary_credential_discussion_filenames_remain_writable(brain, channel):
    vault, state = brain
    source, target = prepare(brain, 'API-key-setup-and-password-guidance', channel, existing_target=False)
    result = write(channel, source, target)
    saved = (vault / result['path']).read_text('utf-8')
    assert result['status'] == 'saved'
    assert source[:-3] in saved
    if channel == 'capture-evidence':
        before = snapshot(vault, state)
        assert write(channel, source, target)['status'] == 'duplicate'
        assert snapshot(vault, state) == before
    else:
        before = snapshot(vault, state)
        with pytest.raises(ValueError, match='SHA256'):
            write(channel, source, target)
        with pytest.raises(ValueError, match='Concurrent change'):
            write(channel, source, target, '0' * 64)
        assert snapshot(vault, state) == before
        assert write(channel, source, target, result['sha256'])['status'] in ('saved', 'unchanged')


def test_sensitive_named_original_remains_readable_without_a_new_copy(brain):
    vault, state = brain
    source, _ = prepare(brain, SENSITIVE_FILENAMES[3], 'capture-evidence', existing_target=False)
    before = snapshot(vault, state)
    read = store.read(source)
    assert read['sha256'] == hashlib.sha256((vault / source).read_bytes()).hexdigest()
    assert 'Do not alter this source.' in read['content']
    assert snapshot(vault, state) == before


def test_refusal_preserves_historical_sensitive_capture_instead_of_replaying_it(brain, monkeypatch):
    vault, state = brain
    source, target = prepare(brain, SENSITIVE_FILENAMES[3], 'capture-evidence', existing_target=False)
    payload = dict(event_id='synthetic-path-screening', title='Synthetic milestone',
                   outcome='Supported synthetic outcome.', source_app='test',
                   source_reference='disposable synthetic fixture', project='',
                   decisions=[], next_steps=[], evidence_paths=[source])
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = hashlib.sha256(payload['event_id'].encode()).hexdigest()[:24]
    historical = vault / ('00-Inbox/AI-Captures/' + key + '.md')
    historical.parent.mkdir(parents=True)
    historical.write_text(store.note_text({'event_id': payload['event_id'], 'payload_sha256': fingerprint},
                                         '# Historical capture\n- [[' + source[:-3] + ']]'), encoding='utf-8')
    before = snapshot(vault, state)
    refuse_before_selection(monkeypatch, 'capture-evidence', source, target)
    assert snapshot(vault, state) == before
