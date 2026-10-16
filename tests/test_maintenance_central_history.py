"""Names-only diagnostics, immutable proof age, and bounded central traversal."""
import builtins
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time

import pytest
import brain_git
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki


GUIDE = Path(__file__).resolve().parents[1] / 'docs/DEVICE-SETUP.md'
WARNING = 'designated derived namespace could not be enumerated; original preserved'


def digest(text):
    return hashlib.sha256(text.encode('utf-8', errors='surrogatepass')).hexdigest()


def put(vault, relative, raw=b'# Synthetic owner original\n'):
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def lookup(vault, relative, monkeypatch, forbidden=()):
    section = GUIDE.read_text(encoding='utf-8').split('### Local path-SHA lookup', 1)[1]
    snippet = section.split('```python\n', 1)[1].split('\n```', 1)[0]
    answers = iter([str(vault), digest(relative)])
    output = io.StringIO()
    old_scan = os.scandir
    def opened(*args, **kwargs):
        raise AssertionError('Names-only lookup must not open a file or linked target.')
    def scanned(path):
        assert Path(path) not in forbidden, 'Names-only lookup must not descend into hidden or linked directories.'
        return old_scan(path)
    with monkeypatch.context() as guard:
        guard.setattr(builtins, 'input', lambda prompt='': next(answers))
        guard.setattr(Path, 'open', opened)
        guard.setattr(os, 'scandir', scanned)
        with contextlib.redirect_stdout(output):
            exec(compile(snippet, str(GUIDE), 'exec'), {})
    return output.getvalue()


@pytest.mark.parametrize('kind', ['hidden-central', 'hidden-capture', 'hidden-receipt',
                                'linked-file', 'linked-directory', 'hidden-directory',
                                'ordinary-file', 'ordinary-directory', 'vault-root', 'unicode-file'])
def test_saved_lookup_identifies_each_entry_without_opening_or_descending_into_it(brain, tmp_path, monkeypatch, kind):
    vault, _ = brain
    relative = {'hidden-central': '02-Areas/AI-Memory/.Draft.md',
                'hidden-capture': '00-Inbox/AI-Captures/.Capture.md',
                'hidden-receipt': wiki.BASE + 'Receipts/.Receipt.md',
                'linked-file': '02-Areas/AI-Memory/Linked.md',
                'linked-directory': '02-Areas/AI-Memory/Linked',
                'hidden-directory': '02-Areas/AI-Memory/.Private',
                'ordinary-file': '03-Resources/Ordinary.md',
                'ordinary-directory': '03-Resources/Ordinary', 'vault-root': '.',
                'unicode-file': '03-Resources/Olé résumé.md'}[kind]
    entry = vault / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / 'private-link-target'
    forbidden = []
    if kind == 'linked-file':
        target.write_bytes(b'# Private synthetic linked original\n')
        entry.symlink_to(target)
    elif kind == 'linked-directory':
        target.mkdir()
        (target / 'Never-read.md').write_bytes(b'# Private synthetic target\n')
        entry.symlink_to(target, target_is_directory=True)
        forbidden = [entry, target]
    elif kind in ('hidden-directory', 'ordinary-directory', 'vault-root'):
        entry.mkdir(exist_ok=True)
        if kind == 'hidden-directory': forbidden = [entry]
    else:
        entry.write_bytes(b'# Synthetic original\n')
    rendered = lookup(vault, relative, monkeypatch, forbidden)
    assert 'Matching local paths: 1' in rendered and ascii(relative) in rendered
    if kind == 'linked-file': assert target.read_bytes() == b'# Private synthetic linked original\n'


@pytest.mark.parametrize('kind', ['hidden', 'linked'])
def test_lookup_does_not_reach_descendants_beyond_an_opaque_directory_entry(brain, tmp_path, monkeypatch, kind):
    vault, _ = brain
    entry = vault / ('.Hidden' if kind == 'hidden' else 'Linked')
    target = tmp_path / 'synthetic-link-directory'
    if kind == 'hidden': entry.mkdir(); actual = entry
    else:
        target.mkdir()
        entry.symlink_to(target, target_is_directory=True)
        actual = target
    original = put(actual, 'Child.md')
    before = original.read_bytes()
    rendered = lookup(vault, entry.relative_to(vault).as_posix() + '/Child.md', monkeypatch, (entry, target))
    assert 'Matching local paths: 0' in rendered and original.read_bytes() == before


@pytest.mark.parametrize('relative,filename', [
    ('02-Areas/AI-Memory', 'actual'), ('02-Areas/AI-Memory/Denied', 'actual'),
    ('02-Areas/AI-Memory/Denied', 'outside'), ('02-Areas/AI-Memory/Denied', 'absent')])
def test_central_enumeration_denial_reports_precise_or_bounded_fallback_and_keeps_git(brain, monkeypatch, relative, filename):
    vault, state = brain
    denied = vault / relative
    original = put(denied, 'Current.md', b'---\ntype: note\n---\n# Synthetic summary\n')
    before = original.read_bytes()
    old_scan = os.scandir
    faults, calls = [], []
    value = str(denied) if filename == 'actual' else str(vault.parent / 'Private-outside') if filename == 'outside' else None
    def scan(path):
        if Path(path) == denied:
            faults.append('denied')
            raise PermissionError(13, 'Private diagnostic must stay withheld', value)
        return old_scan(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scan)
        guard.setattr(brain_git, 'sync', lambda: calls.append('stub sync') or 'synthetic synchronized')
        result = maintenance.maintenance()
    expected = relative if filename == 'actual' else '02-Areas/AI-Memory'
    assert faults and calls == ['stub sync'] and result['status'] == 'completed'
    rows = [row for row in result['review'] if WARNING in row['reasons']]
    assert len(rows) == 1 and rows[0]['path_withheld'] is True
    assert rows[0]['path_sha256'] == digest(expected)
    assert 'Private diagnostic' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
    assert 'Matching local paths: 1' in lookup(vault, expected, monkeypatch)
    assert original.read_bytes() == before


@pytest.mark.parametrize('relative', ['02-Areas/AI-Memory/.Hidden', '02-Areas/AI-Memory/Nested/.Hidden'])
def test_central_hidden_directory_is_pruned_before_enumeration_or_note_read(brain, monkeypatch, relative):
    vault, _ = brain
    hidden = vault / relative
    original = put(hidden, 'Current.md', b'\xffPrivate synthetic hidden source\n')
    raw = original.read_bytes()
    old_scan, old_read = os.scandir, maintenance._read_derived
    def scanned(path):
        assert Path(path) != hidden, 'Central hidden subtree must be pruned before scandir.'
        return old_scan(path)
    def read(path):
        assert not path.is_relative_to(hidden), 'Central hidden originals must not be opened.'
        return old_read(path)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'scandir', scanned)
        guard.setattr(maintenance, '_read_derived', read)
        result = maintenance.maintenance(sync=False)
    assert result['status'] == 'completed' and result['review'] == [] and original.read_bytes() == raw


def directory_link(entry, target, workspace):
    assert entry.parent.resolve().is_relative_to(workspace.resolve())
    assert target.resolve().is_relative_to(workspace.resolve()) and not entry.exists()
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(entry), str(target)], capture_output=True)
        assert result.returncode == 0 and os.path.isjunction(entry)
    else:
        entry.symlink_to(target, target_is_directory=True)
        assert entry.is_symlink()
    assert entry.resolve() == target.resolve()


def unlink_directory(entry, target, workspace):
    assert entry.parent.resolve().is_relative_to(workspace.resolve())
    assert target.resolve().is_relative_to(workspace.resolve()) and entry.resolve() == target.resolve()
    if os.name == 'nt':
        assert os.path.isjunction(entry)
        entry.rmdir()  # Only this exact private reparse point, never recursively or its target.
    else:
        assert entry.is_symlink()
        entry.unlink()
    assert not entry.exists() and target.is_dir()


@pytest.mark.parametrize('placement', ['central-root', 'central-child'])
def test_actual_central_directory_link_is_never_traversed_or_read(brain, tmp_path, monkeypatch, placement):
    vault, _ = brain
    central = vault / '02-Areas/AI-Memory'
    entry = central if placement == 'central-root' else central / 'Linked'
    entry.parent.mkdir(parents=True)
    target = tmp_path / 'private-directory-target'
    target.mkdir()
    original = put(target, 'Current.md', b'---\ntype: note\n---\n# Private synthetic target\n')
    raw = original.read_bytes()
    directory_link(entry, target, tmp_path)
    old_scan, old_read = os.scandir, maintenance._read_derived
    def scanned(path):
        assert Path(path) not in (entry, target), 'Linked central directories must not be listed.'
        return old_scan(path)
    def read(path):
        assert not path.is_relative_to(entry) and not path.is_relative_to(target), 'Linked central originals must not be read.'
        return old_read(path)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(os, 'scandir', scanned)
            guard.setattr(maintenance, '_read_derived', read)
            result = maintenance.maintenance(sync=False)
        assert result['status'] == 'completed' and result['review'] == [] and original.read_bytes() == raw
    finally:
        unlink_directory(entry, target, tmp_path)


@pytest.mark.parametrize('suffix', ['.md', '.MD', '.Md'])
@pytest.mark.parametrize('reason', ['changed-source', 'aged'])
def test_central_ordinary_markdown_retains_changed_source_and_age_review(brain, suffix, reason):
    vault, _ = brain
    source = put(vault, 'Templates/Original.md', b'# Earlier evidence\n')
    target = put(vault, '02-Areas/AI-Memory/Current' + suffix,
                 store.note_text({'sources': ['Templates/Original.md'],
                     'source_hashes': {'Templates/Original.md': store.digest(source.read_bytes())}}, '# Derived summary\n').encode())
    if reason == 'changed-source': source.write_bytes(b'# Later evidence\n')
    else: age(target)
    before = source.read_bytes(), target.read_bytes()
    result = maintenance.maintenance(sync=False)
    row = next(row for row in result['review'] if row['path'] == target.relative_to(vault).as_posix())
    assert row['reasons'] == ['source changed: Templates/Original.md' if reason == 'changed-source' else 'review older than 30 days']
    assert (source.read_bytes(), target.read_bytes()) == before


def age(path):
    when = time.time() - 31 * 86400
    os.utime(path, (when, when))


def compile_receipts(brain, count):
    vault, _ = brain
    quote = 'A synthetic dated source supports a reusable historical statement.'
    for index in range(count):
        put(vault, '01-Projects/Receipt-' + str(index).zfill(3) + '.md',
            ('# Receipt ' + str(index) + '\n' + quote + '\n').encode())
    for _ in range(count):
        packet = wiki.prepare(1)
        assert len(packet['sources']) == 1
        row = packet['sources'][0]
        wiki.apply(packet, {'pages': [{'category': 'Projects', 'slug': Path(row['path']).stem.lower(),
            'title': row['title'], 'related': [], 'claims': [{'text': 'The synthetic source records a historical statement.',
                'source': row['path'], 'quote': quote, 'status': 'historical'}]}]})
    receipts = sorted((vault / wiki.BASE / 'Receipts').glob('*.md'))
    proof = wiki._hydrate_receipts(wiki.inventory())
    assert len(receipts) == count and proof['verified_parts'] == count and proof['issues'] == []
    assert wiki.lint()['issues'] == []
    return receipts


@pytest.mark.parametrize('aged', [False, True])
def test_actual_verified_immutable_receipt_does_not_acquire_a_summary_age_warning(brain, aged):
    vault, _ = brain
    receipts = compile_receipts(brain, 1)
    receipt = receipts[0]
    if aged: age(receipt)
    raw = receipt.read_bytes()
    result = maintenance.maintenance(sync=False)
    assert not any(row['path'] == receipt.relative_to(vault).as_posix() for row in result['review'])
    assert receipt.read_bytes() == raw and wiki.status()['receipt_issue_count'] == 0


def test_twenty_one_valid_aged_receipts_do_not_bury_actual_changed_context_in_public_health(brain):
    vault, state = brain
    receipts = compile_receipts(brain, 21)
    for receipt in receipts: age(receipt)
    source = put(vault, 'Templates/Context-source.md', b'# Earlier context evidence\n')
    context = put(vault, 'Maps/AI-Context.md', store.note_text({'sources': ['Templates/Context-source.md'],
        'source_hashes': {'Templates/Context-source.md': store.digest(source.read_bytes())}}, '# Context summary\n').encode())
    source.write_bytes(b'# Actually changed context evidence\n')
    before = {path: path.read_bytes() for path in [source, context, *receipts]}
    result = maintenance.maintenance(sync=False)
    public = maintenance.health_status(state / 'maintenance-status.json')
    assert any(row['path'] == 'Maps/AI-Context.md' and row['reasons'] == ['source changed: Templates/Context-source.md'] for row in public['review'])
    assert not any(row['path'].startswith(wiki.BASE + 'Receipts/') for row in result['review'])
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize('relative', ['02-Areas/AI-Memory/Current.md', '02-Areas/AI-Memory/Receipt.md', wiki.BASE + 'Control.md'])
def test_ordinary_derived_and_explicit_control_age_policy_remain_active(brain, relative):
    vault, _ = brain
    info = {'maintainer_device_id': cfg.DEVICE_ID} if relative.endswith('Control.md') else {'type': 'note'}
    target = put(vault, relative, store.note_text(info, '# Derived record\n').encode())
    age(target)
    raw = target.read_bytes()
    assert cfg.require_maintainer() is None
    result = maintenance.maintenance(sync=False)
    row = next(row for row in result['review'] if row['path'] == relative)
    assert row['reasons'] == ['review older than 30 days'] and target.read_bytes() == raw


@pytest.mark.parametrize('invalid', ['utf8', 'receipt-hash'])
def test_optional_receipt_integrity_still_reports_issues_after_maintenance(brain, invalid):
    vault, _ = brain
    receipt = compile_receipts(brain, 1)[0]
    if invalid == 'utf8': receipt.write_bytes(b'\xffInvalid synthetic receipt\n')
    else:
        info = wiki.meta(receipt.read_text(encoding='utf-8'))
        info['receipt_sha256'] = '0' * 64
        receipt.write_bytes(store.note_text(info, '# Invalid synthetic receipt hash\n').encode())
    raw = receipt.read_bytes()
    before = wiki.status()
    assert before['receipt_issue_count'] == 1
    maintenance.maintenance(sync=False)
    after = wiki.status()
    assert after['receipt_issue_count'] == 1 and before['receipt_issues'] == after['receipt_issues']
    assert receipt.read_bytes() == raw


@pytest.mark.parametrize('role', ['maintainer', 'capture'])
def test_immutable_receipt_history_is_excluded_before_any_optional_content_read(brain, monkeypatch, role):
    vault, _ = brain
    target = put(vault, wiki.BASE + 'Receipts/opaque.md', b'\xff' + b'x' * 2_000_001)
    raw = target.read_bytes()
    old_open = Path.open
    def opened(path, *args, **kwargs):
        assert path != target, 'Maintenance must not read immutable optional receipt history as a current summary.'
        return old_open(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(cfg, 'ROLE', role)
        guard.setattr(Path, 'open', opened)
        result = maintenance.maintenance(sync=False)
    assert result['status'] == 'completed' and result['review'] == [] and target.read_bytes() == raw


@pytest.mark.parametrize('kind', ['linked-parent', 'ordinary-parent'])
def test_central_intermediate_parent_has_a_resolvable_link_boundary_or_normal_review(brain, tmp_path, monkeypatch, kind):
    vault, state = brain
    entry = vault / '02-Areas'
    linked = kind == 'linked-parent'
    target = tmp_path / 'private-area-target' if linked else entry
    target.mkdir()
    original = put(target, 'AI-Memory/Current.md', store.note_text({'type': 'note'}, '# Synthetic existing summary\n').encode())
    age(original)
    raw = original.read_bytes()
    if linked: directory_link(entry, target, tmp_path)
    old_scan = os.scandir
    visits, calls = [], []
    def scanned(path):
        if linked and Path(path) in (entry, entry / 'AI-Memory', target, target / 'AI-Memory'):
            visits.append(str(path))
        return old_scan(path)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(os, 'scandir', scanned)
            guard.setattr(brain_git, 'sync', lambda: calls.append('stub sync') or 'synthetic synchronized')
            result = maintenance.maintenance()
        assert calls == ['stub sync'] and result['status'] == 'completed' and original.read_bytes() == raw
        if linked:
            assert visits == [], 'A linked intermediate central parent must be pruned before target enumeration.'
            rows = [row for row in result['review'] if WARNING in row['reasons']]
            assert len(rows) == 1 and rows[0]['path_withheld'] is True and rows[0]['path_sha256'] == digest('02-Areas')
            assert 'Matching local paths: 1' in lookup(vault, '02-Areas', monkeypatch, (entry, target))
            assert 'private-area-target' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
        else:
            assert result['review'] == [{'path': '02-Areas/AI-Memory/Current.md', 'reasons': ['review older than 30 days']}]
    finally:
        if linked: unlink_directory(entry, target, tmp_path)
