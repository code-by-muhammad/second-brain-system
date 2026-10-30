"""All designated summaries retain evidence review; synthetic notes only."""
import os
import time
from pathlib import Path

import pytest

import brain_git
import brain_maintenance as m
import brain_store as s


CASES = [
    ('02-Areas/AI-Memory/Current.md', None),
    ('01-Projects/AI-Memory.md', None),
    ('01-Projects/Example/AI-Memory.md', None),
    ('01-Projects/Example/Nested/AI-Memory.md', None),
    ('Maps/AI-Context.md', None),
    ('01-Projects/Example/AI-Memory.md', '01-Projects/Example/aI-mEmOrY.Md'),
    ('Maps/AI-Context.md', 'Maps/aI-cOnTeXt.Md'),
    ('01-Projects/AI-Memory.md', '01-projects/ai-memory.MD'),
    ('Maps/AI-Context.md', 'maps/ai-context.MD'),
]


def _snapshot(vault):
    return {p.relative_to(vault).as_posix(): s.digest(p.read_bytes())
            for p in vault.rglob('*') if p.is_file()}


def _page(brain, canonical, renamed=None):
    vault, _ = brain
    source = vault / '03-Resources/Original.md'
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists(): source.write_bytes(b'# Original owner evidence\n\nEarlier decision.\n')
    result = s.update_current(canonical, 'Synthetic summary',
                              'Earlier decision; verify the dated original.',
                              ['03-Resources/Original.md'])
    assert result['status'] == 'saved'
    target = vault / canonical
    if renamed:
        old_parts, new_parts = canonical.split('/'), renamed.split('/')
        if old_parts[0] != new_parts[0]:
            directory = vault / old_parts[0]
            temporary = vault / 'Synthetic-case-rename'
            directory.rename(temporary)
            temporary.rename(vault / new_parts[0])
            target = vault / new_parts[0] / Path(*old_parts[1:])
        temporary = target.with_name('Synthetic-case-rename.md')
        target.rename(temporary)
        target = vault / renamed
        temporary.rename(target)
    return source, target


@pytest.mark.parametrize('canonical,renamed', CASES,
                         ids=['central', 'project-root', 'project', 'project-deep', 'map',
                              'project-case', 'map-case', 'project-root-case', 'map-root-case'])
@pytest.mark.parametrize('mode', ['changed', 'aged', 'unchanged'])
def test_every_designated_summary_flags_changed_or_aged_evidence(brain, canonical, renamed, mode):
    source, target = _page(brain, canonical, renamed)
    if mode == 'changed': source.write_bytes(b'# Original owner evidence\n\nLater decision.\n')
    elif mode == 'aged':
        old = time.time() - 31 * 86400
        os.utime(target, (old, old))
    before = _snapshot(brain[0])
    result = m.maintenance(sync=False)
    assert result['status'] == 'completed' and result['git'] == 'not requested'
    assert result['captures_linked'] == 0
    expected = [] if mode == 'unchanged' else [{
        'path': target.relative_to(brain[0]).as_posix(),
        'reasons': ['source changed: 03-Resources/Original.md' if mode == 'changed'
                    else 'review older than 30 days']}]
    assert result['review'] == expected
    assert _snapshot(brain[0]) == before


def test_review_of_extended_pages_does_not_block_capture_linking_or_sync(brain, monkeypatch):
    vault, _ = brain
    source, target = _page(brain, '01-Projects/Example/AI-Memory.md')
    source.write_bytes(b'# Original owner evidence\n\nChanged decision.\n')
    before_source, before_derived = source.read_bytes(), target.read_bytes()
    (vault / 'Templates').mkdir()
    (vault / 'Templates/daily-note.md').write_text('# Synthetic daily\n\n## Work Log\n', encoding='utf-8')
    capture = s.capture('designated-derived-review-control', 'Synthetic milestone',
                        'Synthetic capture-link control.', 'test', 'synthetic fixture')
    calls = []
    monkeypatch.setattr(brain_git, 'sync', lambda: calls.append('sync') or 'synced')
    first = m.maintenance()
    second = m.maintenance()
    assert first['captures_linked'] == 1 and second['captures_linked'] == 0
    assert first['status'] == second['status'] == 'completed'
    assert first['git'] == second['git'] == 'synced' and calls == ['sync', 'sync']
    assert first['review'] == second['review'] == [{
        'path': '01-Projects/Example/AI-Memory.md',
        'reasons': ['source changed: 03-Resources/Original.md']}]
    assert source.read_bytes() == before_source and target.read_bytes() == before_derived
    daily = list((vault / '05-Daily').glob('*.md'))
    assert len(daily) == 1
    assert daily[0].read_text(encoding='utf-8').count('<!-- brain-capture:designated-derived-review-control -->') == 1
    assert (vault / capture['path']).is_file()


def test_designated_review_order_is_stable_and_pages_are_not_duplicated(brain):
    paths = ['Maps/AI-Context.md', '01-Projects/Zed/AI-Memory.md',
             '01-Projects/AI-Memory.md', '02-Areas/AI-Memory/Alpha.md',
             '01-Projects/Alpha/Nested/AI-Memory.md']
    for path in paths: source, _ = _page(brain, path)
    source.write_bytes(b'# Changed authoritative owner evidence\n')
    before = _snapshot(brain[0])
    first = m.maintenance(sync=False)
    second = m.maintenance(sync=False)
    expected = sorted(paths, key=lambda path: (path.casefold(), path))
    assert [row['path'] for row in first['review']] == expected
    assert second['review'] == first['review']
    assert _snapshot(brain[0]) == before


def test_original_neighbors_hidden_artifacts_and_compiler_history_stay_opaque(brain, monkeypatch):
    vault, _ = brain
    paths = ['01-Projects/Example/AI-Memory-Research.md', '01-Projects/Example/Original.md',
             '03-Resources/Example/AI-Memory.md', 'Maps/AI-Context-Research.md',
             '01-Projects/.editor/AI-Memory.md', '01-Projects/Example/.editor/AI-Memory.md',
             'Maps/.editor/AI-Context.md', '02-Areas/AI-Memory/Wiki/Log.md',
             '02-Areas/AI-Memory/Wiki/Log-Archives/Owner.md']
    targets = []
    for relative in paths:
        target = vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'\xff' + b'x' * 2_000_002)
        targets.append(target)
    before = _snapshot(vault)
    original = Path.open
    def opened(path, *args, **kwargs):
        assert path not in targets, 'Excluded original, hidden artifact or history must not be read.'
        return original(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'open', opened)
        result = m.maintenance(sync=False)
    assert result['status'] == 'completed' and result['review'] == []
    assert _snapshot(vault) == before


@pytest.mark.parametrize('kind', ['symlink', 'junction'])
def test_linked_extended_directories_are_not_traversed_or_read(brain, monkeypatch, kind):
    vault, _ = brain
    unsafe = vault / '01-Projects/Linked'
    target = unsafe / 'AI-Memory.md'
    unsafe.mkdir(parents=True)
    target.write_bytes(b'\xff' + b'x' * 2_000_002)
    original_open, original_walk = Path.open, os.walk
    seen = []
    def opened(path, *args, **kwargs):
        assert path != target, 'Linked derived paths must never be opened.'
        return original_open(path, *args, **kwargs)
    def walked(top, *args, **kwargs):
        for directory, dirs, files in original_walk(top, *args, **kwargs):
            assert Path(directory) != unsafe, 'Linked directories must be pruned before traversal.'
            seen.append(Path(directory))
            yield directory, dirs, files
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'open', opened)
        guard.setattr(os, 'walk', walked)
        if kind == 'symlink':
            old = Path.is_symlink
            guard.setattr(Path, 'is_symlink', lambda path: path == unsafe or old(path))
        else:
            old = os.path.isjunction
            guard.setattr(os.path, 'isjunction', lambda path: Path(path) == unsafe or old(path))
        result = m.maintenance(sync=False)
    assert result['status'] == 'completed' and result['review'] == []
    assert target.read_bytes().startswith(b'\xff')


def test_unsafe_extended_page_is_screened_before_bounded_read(brain, monkeypatch):
    vault, _ = brain
    target = vault / '01-Projects/Example/AI-Memory.md'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'\xff' + b'x' * 2_000_002)
    original_open, original_safe = Path.open, s.safe_path
    validated = []
    def safe(relative):
        validated.append(relative)
        if relative == target.relative_to(vault).as_posix(): raise ValueError('Synthetic unsafe path.')
        return original_safe(relative)
    def opened(path, *args, **kwargs):
        assert path != target, 'Path must be validated before any note read.'
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(s, 'safe_path', safe)
        guard.setattr(Path, 'open', opened)
        result = m.maintenance(sync=False)
    assert target.relative_to(vault).as_posix() in validated
    assert result['status'] == 'completed' and len(result['review']) == 1
    row = result['review'][0]
    assert row['path_withheld'] is True and row['path_sha256'] == s.digest(target.relative_to(vault).as_posix().encode())
    assert row['reasons'] == ['derived metadata could not be read; original preserved']


@pytest.mark.parametrize('failure', [PermissionError, FileNotFoundError], ids=['denied-maps', 'vanished-maps'])
def test_namespace_enumeration_failure_preserves_healthy_review_and_git_sync(brain, monkeypatch, failure):
    vault, _ = brain
    source, _ = _page(brain, '02-Areas/AI-Memory/Current.md')
    _page(brain, 'Maps/AI-Context.md')
    source.write_bytes(b'# Original owner evidence\n\nLater decision.\n')
    before = _snapshot(vault)
    maps = vault / 'Maps'
    original = Path.iterdir
    attempted, synced = [], []
    diagnostic = 'Synthetic enumeration diagnostic must not appear in shared status.'
    def entries(path):
        if path == maps:
            attempted.append('maps')
            raise failure(diagnostic)
        return original(path)
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'iterdir', entries)
        guard.setattr(brain_git, 'sync', lambda: synced.append('sync') or 'synthetic synchronized')
        result = m.maintenance()
    assert synced == ['sync'] and result['git'] == 'synthetic synchronized'
    assert result['status'] == 'completed' and result['captures_linked'] == 0
    assert {'path': '02-Areas/AI-Memory/Current.md',
            'reasons': ['source changed: 03-Resources/Original.md']} in result['review']
    if attempted:
        warnings = [row for row in result['review'] if row.get('path_withheld') is True]
        assert len(warnings) == 1 and warnings[0]['path_sha256'] == s.digest(b'Maps')
        assert warnings[0]['reasons'] == ['designated derived namespace could not be enumerated; original preserved']
    assert all(diagnostic not in reason for row in result['review'] for reason in row['reasons'])
    assert _snapshot(vault) == before


def test_central_uppercase_markdown_suffix_retains_source_review(brain):
    source, target = _page(brain, '02-Areas/AI-Memory/Current.MD')
    source.write_bytes(b'# Original owner evidence\n\nLater decision.\n')
    before = _snapshot(brain[0])
    result = m.maintenance(sync=False)
    assert result['status'] == 'completed' and result['git'] == 'not requested'
    assert result['review'] == [{'path': target.relative_to(brain[0]).as_posix(),
                                'reasons': ['source changed: 03-Resources/Original.md']}]
    assert _snapshot(brain[0]) == before
