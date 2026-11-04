"""Repair local directory diagnostics and reject undecodable shared ownership."""
import builtins
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path

import pytest
import brain_git
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


GUIDE = Path(__file__).resolve().parents[1] / 'docs/DEVICE-SETUP.md'
WARNING = 'designated derived namespace could not be enumerated; original preserved'


def sha(value):
    return hashlib.sha256(value.encode('utf-8', errors='surrogatepass')).hexdigest()


def snapshot(vault):
    return {path.relative_to(vault).as_posix(): path.read_bytes()
            for path in vault.rglob('*') if path.is_file()}


def saved_lookup(vault, relative, monkeypatch):
    section = GUIDE.read_text(encoding='utf-8').split('### Local path-SHA lookup', 1)[1]
    snippet = section.split('```python\n', 1)[1].split('\n```', 1)[0]
    answers = iter([str(vault), sha(relative)])
    output = io.StringIO()
    old_open = Path.open
    def no_note_read(path, *args, **kwargs):
        assert not path.is_relative_to(vault), 'Names-only lookup must never open notes.'
        return old_open(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(builtins, 'input', lambda prompt='': next(answers))
        guard.setattr(Path, 'open', no_note_read)
        with contextlib.redirect_stdout(output):
            exec(compile(snippet, str(GUIDE), 'exec'), {})
    return output.getvalue()


def notes(vault):
    for relative in ['03-Resources/Original.md', 'Maps/AI-Context.md',
                     '01-Projects/Example/DeniedSubtree/AI-Memory.md']:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'# Synthetic owner note\n')


@pytest.mark.parametrize('relative', ['03-Resources/Original.md', 'Maps',
                                    '01-Projects/Example/DeniedSubtree', '.'])
def test_actual_saved_lookup_resolves_file_directory_and_vault_identities(brain, monkeypatch, relative):
    vault, _ = brain
    notes(vault)
    before = snapshot(vault)
    rendered = saved_lookup(vault, relative, monkeypatch)
    assert 'Matching local paths: 1' in rendered and ascii(relative) in rendered
    assert snapshot(vault) == before


@pytest.mark.parametrize('fault', ['maps', 'vault-root', 'project-subtree'])
def test_real_enumeration_fault_keeps_precise_resolvable_warning_and_git(brain, monkeypatch, fault):
    vault, state = brain
    notes(vault)
    target = vault / {'maps': 'Maps', 'vault-root': '.',
                      'project-subtree': '01-Projects/Example/DeniedSubtree'}[fault]
    before = snapshot(vault)
    old_iterdir, old_scandir = Path.iterdir, os.scandir
    faults, syncs = [], []
    def iterdir(path):
        if fault != 'project-subtree' and path == target:
            faults.append('denied')
            raise PermissionError(13, 'Private exception diagnostic', str(path))
        return old_iterdir(path)
    def scandir(path):
        if fault == 'project-subtree' and Path(path) == target:
            faults.append('denied')
            raise PermissionError(13, 'Private exception diagnostic', str(path))
        return old_scandir(path)
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'iterdir', iterdir)
        guard.setattr(os, 'scandir', scandir)
        guard.setattr(brain_git, 'sync', lambda: syncs.append('sync') or 'synthetic synced')
        result = maintenance.maintenance()
    assert faults and syncs == ['sync'] and result['git'] == 'synthetic synced'
    assert result['status'] == 'completed'
    warnings = [row for row in result['review'] if WARNING in row['reasons']]
    assert len(warnings) == 1
    relative = target.relative_to(vault).as_posix()
    assert warnings[0]['path_withheld'] is True and warnings[0]['path_sha256'] == sha(relative)
    assert 'Matching local paths: 1' in saved_lookup(vault, relative, monkeypatch)
    assert 'Private exception diagnostic' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
    assert snapshot(vault) == before


@pytest.mark.parametrize('filename', ['absent', 'empty', 'relative', 'parent', 'outside',
                                    'sibling-namespace', 'hidden', 'bytes', 'number'])
def test_untrusted_walk_error_filename_falls_back_to_known_namespace(brain, monkeypatch, filename):
    vault, state = brain
    notes(vault)
    root = vault / '01-Projects'
    target = root / 'Example/DeniedSubtree'
    values = {'absent': None, 'empty': '', 'relative': 'Example/DeniedSubtree',
              'parent': str(root / '..' / 'Maps'), 'outside': str(vault.parent / 'Outside'),
              'sibling-namespace': str(vault / 'Maps'), 'hidden': str(root / '.private'),
              'bytes': b'opaque-byte-filename', 'number': 123}
    before = snapshot(vault)
    old_scandir = os.scandir
    syncs = []
    def scandir(path):
        if Path(path) == target:
            raise PermissionError(13, 'Private diagnostic should stay withheld', values[filename])
        return old_scandir(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scandir)
        guard.setattr(brain_git, 'sync', lambda: syncs.append('sync') or 'synthetic synced')
        result = maintenance.maintenance()
    warning = next(row for row in result['review'] if WARNING in row['reasons'])
    assert warning['path_withheld'] is True and warning['path_sha256'] == sha('01-Projects')
    assert result['status'] == 'completed' and syncs == ['sync']
    assert 'Private diagnostic' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
    assert snapshot(vault) == before


def test_private_failed_subtree_only_exposes_opaque_identity(brain, monkeypatch):
    vault, state = brain
    target = vault / ('01-Projects/api_key=' + 'SyntheticAlphaBetaGammaDelta')
    target.mkdir(parents=True)
    (target / 'AI-Memory.md').write_bytes(b'# Synthetic private summary\n')
    before = snapshot(vault)
    old_scandir = os.scandir
    def scandir(path):
        if Path(path) == target:
            raise PermissionError(13, 'Synthetic private diagnostic', str(path))
        return old_scandir(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scandir)
        result = maintenance.maintenance(sync=False)
    relative = target.relative_to(vault).as_posix()
    warning = next(row for row in result['review'] if WARNING in row['reasons'])
    assert warning['path_sha256'] == sha(relative) and warning['path_withheld'] is True
    assert 'SyntheticAlphaBetaGammaDelta' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
    assert 'Matching local paths: 1' in saved_lookup(vault, relative, monkeypatch)
    assert snapshot(vault) == before


@pytest.mark.parametrize('target', ['unreadable-folder', 'unreadable-vault'])
def test_names_only_lookup_identifies_directory_without_listing_that_directory(brain, monkeypatch, target):
    vault, _ = brain
    notes(vault)
    denied = vault / '01-Projects/Example/DeniedSubtree' if target == 'unreadable-folder' else vault
    old_scandir = os.scandir
    def scandir(path):
        if Path(path) == denied:
            raise PermissionError(13, 'Synthetic denial', str(path))
        return old_scandir(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scandir)
        rendered = saved_lookup(vault, denied.relative_to(vault).as_posix(), guard)
    assert 'Matching local paths: 1' in rendered


@pytest.mark.parametrize('kind', ['hidden', 'symlink', 'junction'])
def test_lookup_does_not_visit_hidden_or_linked_directory_targets(brain, monkeypatch, kind):
    vault, _ = brain
    target = vault / ('.hidden' if kind == 'hidden' else 'Linked')
    target.mkdir()
    (target / 'Private.md').write_bytes(b'# Synthetic private note\n')
    before = snapshot(vault)
    old_scandir, old_symlink, old_junction = os.scandir, Path.is_symlink, os.path.isjunction
    def scandir(path):
        assert Path(path) != target, 'Hidden and linked directories must not be traversed.'
        return old_scandir(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scandir)
        if kind == 'symlink': guard.setattr(Path, 'is_symlink', lambda path: path == target or old_symlink(path))
        if kind == 'junction': guard.setattr(os.path, 'isjunction', lambda path: Path(path) == target or old_junction(path))
        rendered = saved_lookup(vault, target.relative_to(vault).as_posix() + '/Private.md', guard)
    assert 'Matching local paths: 0' in rendered and snapshot(vault) == before


def control(vault, mode):
    path = vault / '02-Areas/AI-Memory/Wiki/Control.md'
    if mode == 'missing': return path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '---\nmaintainer_device_id: ' + ('different-host' if mode == 'wrong-owner' else 'test-host') + '\n---\n'
    raw = b'\xffinvalid UTF-8 ownership' if mode == 'invalid-utf8' else text.encode(
        'utf-16-le' if mode == 'utf16-le' else 'utf-16-be' if mode == 'utf16-be' else 'utf-8')
    if mode == 'utf16-le': raw = b'\xff\xfe' + raw
    if mode == 'utf16-be': raw = b'\xfe\xff' + raw
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize('mode', ['invalid-utf8', 'utf16-le', 'utf16-be', 'wrong-owner', 'valid-owner', 'missing'])
def test_ownership_encoding_refuses_derived_authority_without_blocking_git(brain, monkeypatch, mode):
    vault, state = brain
    notes(vault)
    control(vault, mode)
    before = snapshot(vault)
    writes, syncs = [], []
    def forbid_write(*args, **kwargs):
        writes.append('write')
        raise AssertionError('No fixture authorizes a derived write.')
    monkeypatch.setattr(store, '_write', forbid_write)
    monkeypatch.setattr(brain_git, 'sync', lambda: syncs.append('sync') or 'synthetic synced')
    result = maintenance.maintenance()
    assert result['status'] == 'completed' and result['git'] == 'synthetic synced'
    assert syncs == ['sync'] and writes == [] and snapshot(vault) == before
    if mode not in ('missing', 'valid-owner'):
        assert result['aggregation'].startswith('skipped: shared ownership')
        with pytest.raises(PermissionError): cfg.require_maintainer()
    else:
        assert cfg.require_maintainer() is None


@pytest.mark.parametrize('mode', ['invalid-utf8', 'utf16-le', 'utf16-be', 'wrong-owner'])
def test_refused_compiler_invocation_preserves_historical_status_and_calls_no_provider(brain, monkeypatch, mode):
    vault, state = brain
    control(vault, mode)
    before = snapshot(vault)
    old = b'{"status":"complete","attention_required":false,"at":"2026-10-15T00:00:00+00:00"}'
    worker = state / 'wiki-worker-status.json'
    worker.write_bytes(old)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('forbidden')
        raise AssertionError('Ownership must refuse before preparation, writes or model calls.')
    monkeypatch.setattr(wiki, 'prepare', forbidden)
    monkeypatch.setattr(wiki.subprocess, 'run', forbidden)
    monkeypatch.setattr(store, '_write', forbidden)
    with pytest.raises(PermissionError): wiki.run(limit=4)
    assert calls == [] and snapshot(vault) == before and worker.read_bytes() == old


def test_capture_role_refuses_before_reading_malformed_shared_ownership(brain, monkeypatch):
    vault, _ = brain
    target = control(vault, 'invalid-utf8')
    raw = target.read_bytes()
    old_open = Path.open
    def opened(path, *args, **kwargs):
        assert path != target, 'Capture role must refuse before inspecting shared ownership.'
        return old_open(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(cfg, 'ROLE', 'capture')
        guard.setattr(Path, 'open', opened)
        with pytest.raises(PermissionError, match='capture/search only'): cfg.require_maintainer()
    assert target.read_bytes() == raw


@pytest.mark.parametrize('mode', ['invalid-utf8', 'utf16-le', 'utf16-be'])
def test_unverified_control_encoding_keeps_uncertainty_in_returned_cached_and_public_status(brain, monkeypatch, mode):
    vault, state = brain
    notes(vault)
    control(vault, mode)
    before = snapshot(vault)
    writes, syncs = [], []
    def forbidden_write(*args, **kwargs):
        writes.append('write')
        raise AssertionError('Unverified ownership must not authorize derived writes.')
    monkeypatch.setattr(store, '_write', forbidden_write)
    monkeypatch.setattr(brain_git, 'sync', lambda: syncs.append('sync') or 'synthetic synced')
    with pytest.raises(PermissionError, match='could not be verified'):
        cfg.require_maintainer()
    returned = maintenance.maintenance()
    cached = json.loads((state / 'maintenance-status.json').read_text(encoding='utf-8'))
    public = maintenance.health_status(state / 'maintenance-status.json')
    assert returned['status'] == 'completed' and returned['git'] == 'synthetic synced'
    assert syncs == ['sync'] and writes == [] and snapshot(vault) == before
    for status in (returned, cached, public):
        assert 'could not be verified' in status['aggregation']
        assert 'belongs to another device' not in status['aggregation']
        assert 'Git synchronization remains enabled' in status['aggregation']
