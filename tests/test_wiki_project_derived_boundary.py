"""Project summaries are derived context, while neighboring original notes stay eligible."""
import json
from pathlib import Path

import pytest

import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_saved_plan_repair import interrupted_plan


FIRST = 'This project summary records the earlier source-backed prototype demonstration.'
SECOND = 'A later refresh records another source-backed planning consideration.'


def snapshot(root):
    return {path.relative_to(root).as_posix(): s.digest(path.read_bytes())
            for path in root.rglob('*') if path.is_file()}


def project_summary(source, path, monkeypatch):
    monkeypatch.setattr(s, 'stamp', lambda: '2026-10-16T00:00:00+05:00')
    canonical = path.rsplit('/', 1)[0] + '/AI-Memory.md'
    relative = source.relative_to(s.VAULT).as_posix()
    receipt = s.update_current(canonical, 'Project summary', FIRST, [relative])
    target = s.VAULT / canonical
    assert receipt['status'] == 'saved'
    metadata = w.meta(target.read_text(encoding='utf-8'))
    assert metadata['date'] == '2026-10-16'
    assert metadata['source_hashes'][relative] == s.digest(source.read_bytes())
    if path != canonical:
        temporary = target.with_name('temporary-memory-case.md')
        target.rename(temporary)
        temporary.rename(s.VAULT / path)
        target = s.VAULT / path
    return target, receipt


@pytest.mark.parametrize('path', [
    '01-Projects/AI-Memory.md',
    '01-Projects/Example/AI-Memory.md',
    '01-Projects/Example/Nested/AI-Memory.md',
    '01-Projects/Example/ai-memory.md',
])
@pytest.mark.parametrize('include_excluded', [False, True])
def test_reserved_project_summary_is_excluded_before_read(brain, monkeypatch, path, include_excluded):
    source, packet, result = fixture(brain)
    w.apply(packet, result)
    target, _ = project_summary(source, path, monkeypatch)
    notes = snapshot(s.VAULT)
    original_read = Path.read_bytes

    def guarded_read(current):
        assert current != target, 'Derived project summaries must not be read as source inputs.'
        return original_read(current)

    with monkeypatch.context() as guard:
        guard.setattr(Path, 'read_bytes', guarded_read)
        rows = w.inventory(include_excluded=include_excluded)
    assert [row['path'] for row in rows] == [source.relative_to(s.VAULT).as_posix()]
    assert snapshot(s.VAULT) == notes


@pytest.mark.parametrize('path', [
    '01-Projects/Example/AI-Memory-Research.md',
    '01-Projects/Example/Memory.md',
    '03-Resources/AI-Memory.md',
    '03-Resources/Example/AI-Memory.md',
])
def test_original_neighbor_paths_and_derived_tags_remain_sources(brain, monkeypatch, path):
    source, packet, result = fixture(brain)
    w.apply(packet, result)
    original = s.VAULT / path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text(s.note_text({'date': '2020-01-02', 'tags': ['ai-memory', 'derived']},
                                  '# Original owner research\n\n' + FIRST), encoding='utf-8')
    notes = snapshot(s.VAULT)
    for include_excluded in (False, True):
        selected = {row['path'] for row in w.inventory(include_excluded=include_excluded)}
        assert path in selected
    prepared = w.prepare(4)
    assert [row['path'] for row in prepared['sources']] == [path]
    assert snapshot(s.VAULT) == notes


@pytest.mark.parametrize('path', ['01-Projects/AI-Memory.md', '01-Projects/Example/Nested/AI-Memory.md'])
def test_prepare_selects_pending_original_instead_of_project_summary(brain, monkeypatch, path):
    source, packet, result = fixture(brain)
    w.apply(packet, result)
    target, _ = project_summary(source, path, monkeypatch)
    pending = source.with_name('New-Original.md')
    pending.write_text('# New original decision\n\nA separate owner decision requires evidence-backed review.\n', encoding='utf-8')
    notes = snapshot(s.VAULT)
    prepared = w.prepare(1)
    assert [row['path'] for row in prepared['sources']] == [pending.relative_to(s.VAULT).as_posix()]
    assert all(row['path'] != target.relative_to(s.VAULT).as_posix() for row in prepared['sources'])
    assert snapshot(s.VAULT) == notes


def publishing_model(monkeypatch, path):
    calls = []
    monkeypatch.setattr(w.shutil, 'which', lambda name: 'synthetic-codex')

    class Process:
        returncode = 0

        def __init__(self, command, **options):
            self.output = Path(command[command.index('-o') + 1])

        def communicate(self, model_input, timeout):
            packet = json.loads(model_input.split('INPUT_JSON:\n', 1)[1])
            calls.append(packet)
            row = next(row for row in packet['sources'] if row['path'] == path)
            quote = FIRST if len(calls) == 1 else SECOND
            result = {
                'pages': [{'category': 'Concepts', 'slug': 'project-original-boundary',
                           'title': 'Original project evidence', 'related': [],
                           'claims': [{'text': 'The original records evidence round ' + str(len(calls)),
                                       'source': path, 'quote': quote, 'status': 'historical'}]}],
                'dispositions': [{'source': path, 'part': row['part'], 'status': 'published',
                                  'reason': 'Exact quoted selected evidence.', 'quote': quote}],
            }
            self.output.write_text(json.dumps(result), encoding='utf-8')

    monkeypatch.setattr(w.subprocess, 'Popen', Process)
    return calls


@pytest.mark.parametrize('derived', [True, False], ids=['project-summary', 'original-research'])
def test_two_actual_refresh_rounds_keep_summaries_out_of_model_evidence(brain, monkeypatch, derived):
    source, packet, result = fixture(brain)
    w.apply(packet, result)
    raw = source.read_bytes()
    path = '01-Projects/Example/' + ('AI-Memory.md' if derived else 'AI-Memory-Research.md')
    if derived:
        target, receipt = project_summary(source, path, monkeypatch)
    else:
        target = s.VAULT / path
        target.parent.mkdir(parents=True)
        target.write_text(s.note_text({'date': '2020-01-02'}, '# Original research\n\n' + FIRST), encoding='utf-8')
    calls = publishing_model(monkeypatch, path)
    reports = [w.run(limit=4)]
    if derived:
        s.update_current(path, 'Project summary', FIRST + '\n\n' + SECOND,
                         [source.relative_to(s.VAULT).as_posix()], expected_sha256=receipt['sha256'])
    else:
        target.write_bytes(target.read_bytes() + ('\n\n' + SECOND).encode())
    reports.append(w.run(limit=4))
    topic = s.VAULT / w.BASE / 'Concepts/project-original-boundary.md'
    claims = w.meta(topic.read_text(encoding='utf-8'))['wiki_claims'] if topic.exists() else []
    assert source.read_bytes() == raw
    assert len(calls) == (0 if derived else 2)
    assert [report['status'] for report in reports] == (['idle', 'idle'] if derived else ['complete', 'complete'])
    if derived:
        assert not topic.exists() and not claims
        assert w.prepare(4) is None
    else:
        assert len(claims) == 2 and {claim['source'] for claim in claims} == {path}
        assert {claim['source_revision']['date'] for claim in claims} == {'2020-01-02'}


def test_new_project_summary_does_not_release_actual_partial_publication_hold(brain, monkeypatch):
    source, packet, result, folder, _ = interrupted_plan(brain, monkeypatch, kind='wiki_source')
    project_summary(source, '01-Projects/Example/AI-Memory.md', monkeypatch)
    source.write_bytes(source.read_bytes() + b'\nOwner changes the original after partial publication.\n')
    index = s.VAULT / w.BASE / 'Index.md'
    index.write_bytes(b'# Owner Index preserved during recovery\n')
    notes, private = snapshot(s.VAULT), snapshot(folder)
    monkeypatch.setattr(w.shutil, 'which', lambda name: pytest.fail('Partial publication must not request new model output.'))
    w.reset_retries()
    with pytest.raises(w.WikiIntegrityError):
        w.run()
    assert snapshot(s.VAULT) == notes and snapshot(folder) == private
    assert w._read_worker_state()['attention_required'] is True
    with w.db() as conn:
        state = conn.execute('SELECT status FROM batches WHERE id=?', (packet['batch_id'],)).fetchone()[0]
    assert state not in ('stale', 'superseded')
