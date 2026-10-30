"""Typed hold history preserves real crashes without granting replay authority."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from conftest import brain
from test_wiki import fixture
from test_wiki_model_context_preflight import fake_skip_model, snapshot
from test_wiki_remediation import explicit, fake_model
from test_wiki_saved_plan_repair import row


def interrupted(brain, monkeypatch, field='in_flight', legacy=False):
    source, packet, result = fixture(brain)
    first = fake_model(monkeypatch, explicit(packet, result))
    write, atomic = s._write, w._atomic_json
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    journal = folder/'journal.json'
    class Crash(BaseException): pass
    def checkpoint(path, value):
        if legacy and path == journal and value.get('state') == 'applying':
            value = {key: val for key, val in value.items() if key not in
                     ('semantic_intent_version', 'in_flight', 'prior_semantic', 'legacy_semantic_ambiguity')}
        saved = atomic(path, value)
        if field != 'in_flight' and path == journal and value.get('completed'):
            raise Crash()
        return saved
    def replace(path, text, kind, *args):
        receipt = write(path, text, kind, *args)
        if field == 'in_flight' and kind == 'wiki_compile': raise Crash()
        return receipt
    with monkeypatch.context() as scope:
        scope.setattr(s, '_write', replace)
        scope.setattr(w, '_atomic_json', checkpoint)
        with pytest.raises(Crash): w.run()
    topic = s.VAULT/w.BASE/'Projects/example.md'
    assert len(first) == 1 and topic.is_file()
    assert w._saved_publication_started(packet, folder/'publication.json')
    if field == 'prior_semantic':
        def replay_checkpoint(path, value):
            saved = atomic(path, value)
            if path == journal and value.get('state') == 'applying' and value.get('prior_semantic') and not value.get('completed'):
                raise Crash()
            return saved
        with monkeypatch.context() as scope:
            scope.setattr(w, '_atomic_json', replay_checkpoint)
            with pytest.raises(Crash): w.run()
    plan = json.loads((folder/'publication.json').read_text(encoding='utf-8'))
    saved = json.loads(journal.read_text(encoding='utf-8'))
    if not legacy:
        record = saved[field] if field == 'in_flight' else saved[field][0]
        assert record['path'] == topic.relative_to(s.VAULT).as_posix()
        assert record['sha256'] == s.digest(topic.read_bytes())
    return source, packet, folder, plan, saved


def hold_and_repair(monkeypatch, source, packet, folder, original):
    topic = s.VAULT/w.BASE/'Projects/example.md'
    published = topic.read_bytes()
    topic.write_bytes(published+b'\nOwner retained topic commentary.\n')
    index = s.VAULT/w.BASE/'Index.md'
    index.write_bytes(b'# Owner retained Index\n')
    notes, private = snapshot(s.VAULT), snapshot(folder)
    raw = source.read_bytes()
    calls = fake_skip_model(monkeypatch)
    for _ in range(2):
        w.reset_retries()
        before = row(packet)
        with pytest.raises(w.WikiIntegrityError): w.run()
        state = w._read_worker_state()
        assert state['attention_required'] and state['consecutive_failures'] == 0
        assert state['batch_id'] == packet['batch_id']
        assert state['recovery_action'] == 'reconcile_saved_publication_then_retry'
        assert not calls and row(packet) == before
        assert snapshot(s.VAULT) == notes and snapshot(folder) == private
        assert not w._saved_publication_started(packet, folder/'publication.json')
    # Explicit fixture owner reconciliation restores actual checkpoint/afterimage.
    (folder/'journal.json').write_bytes(original)
    topic.write_bytes(published)
    index.unlink()
    w.reset_retries()
    assert w.run()['status'] == 'complete' and not calls
    assert source.read_bytes() == raw


@pytest.mark.parametrize('field,value',
    [('semantic_intent_version', value) for value in [None, True, False, 1.0, '1', [], {}, '']]
    + [('legacy_semantic_ambiguity', value) for value in [None, 1, 1.0, '1', [], {}, '']])
def test_present_semantic_controls_require_their_exact_json_types(brain, monkeypatch, field, value):
    source, packet, folder, plan, saved = interrupted(brain, monkeypatch, legacy=True)
    original = (folder/'journal.json').read_bytes()
    saved[field] = value
    w._atomic_json(folder/'journal.json', saved)
    with pytest.raises(w.WikiIntegrityError): w._publication_hold_records(plan, saved)
    hold_and_repair(monkeypatch, source, packet, folder, original)


@pytest.mark.parametrize('field', ['in_flight', 'completed', 'prior_semantic'])
@pytest.mark.parametrize('damage', ['scalar', 'null', 'list', 'empty', 'missing-path', 'missing-sha', 'path-list', 'sha-dict', 'unknown-pair'])
def test_actual_progress_records_require_a_typed_known_plan_pair(brain, monkeypatch, field, damage):
    source, packet, folder, plan, saved = interrupted(brain, monkeypatch, field)
    original = (folder/'journal.json').read_bytes()
    record = saved[field] if field == 'in_flight' else saved[field][0]
    corrupt = {'scalar': 42, 'null': None, 'list': [], 'empty': {},
               'missing-path': {'sha256': record['sha256']}, 'missing-sha': {'path': record['path']},
               'path-list': {**record, 'path': []}, 'sha-dict': {**record, 'sha256': {}},
               'unknown-pair': {**record, 'sha256': '0'*64}}[damage]
    saved[field] = corrupt if field == 'in_flight' else [corrupt]
    w._atomic_json(folder/'journal.json', saved)
    with pytest.raises(w.WikiIntegrityError): w._publication_hold_records(plan, saved)
    hold_and_repair(monkeypatch, source, packet, folder, original)


@pytest.mark.parametrize('field', ['completed', 'prior_semantic'])
@pytest.mark.parametrize('value', [None, 42, {}, ''])
def test_supplied_progress_containers_must_remain_lists(brain, monkeypatch, field, value):
    source, packet, folder, plan, saved = interrupted(brain, monkeypatch, field)
    original = (folder/'journal.json').read_bytes()
    saved[field] = value
    w._atomic_json(folder/'journal.json', saved)
    with pytest.raises(w.WikiIntegrityError): w._publication_hold_records(plan, saved)
    hold_and_repair(monkeypatch, source, packet, folder, original)


@pytest.mark.parametrize('field', ['in_flight', 'completed', 'prior_semantic'])
def test_harmless_extra_fields_preserve_real_hold_and_explicit_recovery(brain, monkeypatch, field):
    source, packet, folder, plan, saved = interrupted(brain, monkeypatch, field)
    record = saved[field] if field == 'in_flight' else saved[field][0]
    record['future_description'] = {'retained': True}
    saved['future_metadata'] = ['compatible']
    w._atomic_json(folder/'journal.json', saved)
    original = (folder/'journal.json').read_bytes()
    assert w._publication_hold_records(plan, saved) == [{'path': record['path'], 'sha256': record['sha256']}]
    hold_and_repair(monkeypatch, source, packet, folder, original)


@pytest.mark.parametrize('version', ['missing', 0, 2, 42, -1])
def test_legacy_missing_fields_and_unknown_integer_versions_stay_conservative(brain, monkeypatch, version):
    source, packet, folder, plan, saved = interrupted(brain, monkeypatch, legacy=True)
    if version != 'missing': saved['semantic_intent_version'] = version
    w._atomic_json(folder/'journal.json', saved)
    original = (folder/'journal.json').read_bytes()
    assert w._publication_hold_records(plan, saved) == []
    hold_and_repair(monkeypatch, source, packet, folder, original)


def test_real_archive_only_checkpoint_remains_valid_without_policy_authority(brain, monkeypatch):
    source, packet, result = fixture(brain)
    log = s.VAULT/w.BASE/'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    history = b'# Retained history\n'+b'x'*1_000_001
    log.write_bytes(history)
    first = fake_model(monkeypatch, explicit(packet, result))
    atomic = w._atomic_json
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    class Crash(BaseException): pass
    def checkpoint(path, value):
        saved = atomic(path, value)
        if path == folder/'journal.json' and value.get('state') == 'applying' and len(value.get('completed', [])) == 1:
            raise Crash()
        return saved
    with monkeypatch.context() as scope:
        scope.setattr(w, '_atomic_json', checkpoint)
        with pytest.raises(Crash): w.run()
    plan = json.loads((folder/'publication.json').read_text(encoding='utf-8'))
    saved = json.loads((folder/'journal.json').read_text(encoding='utf-8'))
    archive = plan['writes'][0]
    assert archive['kind'] == 'wiki_log_archive'
    assert saved['completed'] == [{'path': archive['path'], 'sha256': archive['after']}]
    assert (s.VAULT/archive['path']).read_bytes() == history
    assert w._publication_hold_records(plan, saved) == []
    assert not w._saved_publication_started(packet, folder/'publication.json')
    policy = s.VAULT/w.SOURCE_POLICY
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text('```json\n'+json.dumps({'version': 1, 'exclude_paths': [packet['sources'][0]['path']], 'exclude_prefixes': []})+'\n```\n', encoding='utf-8')
    notes, private = snapshot(s.VAULT), snapshot(folder)
    calls = fake_skip_model(monkeypatch)
    w.reset_retries()
    assert w.run()['status'] == 'idle' and len(first) == 1 and not calls
    assert snapshot(s.VAULT) == notes and snapshot(folder) == private
    assert source.is_file() and log.read_bytes() == history


def test_real_unchanged_plan_checkpoint_does_not_become_semantic_history(brain, monkeypatch):
    monkeypatch.setattr(s, 'stamp', lambda: '2026-10-16T00:00:00+00:00')
    source, packet, result = fixture(brain)
    model_result = explicit(packet, result)
    preliminary = w._publication_plan(packet, model_result, w._dispositions(packet, model_result))
    index_write = next(write for write in preliminary['writes'] if write['kind'] == 'wiki_index')
    index = s.VAULT/index_write['path']
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(index_write['text'], encoding='utf-8', newline='')
    native_plan, atomic = w._publication_plan, w._atomic_json
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    first = fake_model(monkeypatch, model_result)
    class Crash(BaseException): pass
    def index_first(*args):
        plan = native_plan(*args)
        plan['writes'].sort(key=lambda write: write['kind'] != 'wiki_index')
        return plan
    def checkpoint(path, value):
        saved = atomic(path, value)
        if path == folder/'journal.json' and value.get('state') == 'applying' and value.get('completed'):
            raise Crash()
        return saved
    with monkeypatch.context() as scope:
        scope.setattr(w, '_publication_plan', index_first)
        scope.setattr(w, '_atomic_json', checkpoint)
        with pytest.raises(Crash): w.run()
    plan = json.loads((folder/'publication.json').read_text(encoding='utf-8'))
    saved = json.loads((folder/'journal.json').read_text(encoding='utf-8'))
    w._verify_publication_plan(plan, packet, model_result)
    unchanged = plan['writes'][0]
    assert unchanged['kind'] == 'wiki_index' and unchanged['before'] == unchanged['after']
    assert saved['completed'] == [{'path': unchanged['path'], 'sha256': unchanged['after']}]
    assert w._publication_hold_records(plan, saved) == []
    assert not w._saved_publication_started(packet, folder/'publication.json')
    calls = fake_skip_model(monkeypatch)
    w.reset_retries()
    assert w.run()['status'] == 'complete' and not calls and len(first) == 1
    assert source.is_file()


@pytest.mark.parametrize('position', ['journal-before-plan', 'publication-after-plan'])
@pytest.mark.parametrize('excluded', [False, True])
def test_unused_archive_replan_normalizes_only_valid_progress_before_plan_change(brain, monkeypatch, position, excluded):
    source, packet, result = fixture(brain)
    log = s.VAULT/w.BASE/'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    original = b'# Original history\n'+b'x'*1_000_001
    log.write_bytes(original)
    first = fake_model(monkeypatch, explicit(packet, result))
    write = s._write
    def stop(path, text, kind, *args):
        if kind == 'wiki_compile': raise OSError('Genuine caught failure before semantic replacement')
        return write(path, text, kind, *args)
    with monkeypatch.context() as scope:
        scope.setattr(s, '_write', stop)
        with pytest.raises(OSError): w.run()
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    publication, journal = folder/'publication.json', folder/'journal.json'
    old_plan = publication.read_bytes()
    saved = json.loads(journal.read_text(encoding='utf-8'))
    plan = json.loads(old_plan)
    assert len(saved['completed']) == 1 and plan['writes'][0]['kind'] == 'wiki_log_archive'
    assert w._publication_hold_records(plan, saved) == []
    saved['future_metadata'] = {'retain': 'harmless owner-independent journal metadata'}
    w._atomic_json(journal, saved)
    controls = {key: saved[key] for key in ('semantic_intent_version', 'legacy_semantic_ambiguity', 'future_metadata')}
    updated = original+b'\nOwner additional history\n'
    log.write_bytes(updated)
    atomic = w._atomic_json
    fired = False
    class Crash(BaseException): pass
    def checkpoint(path, value):
        nonlocal fired
        persisted = atomic(path, value)
        before_plan = path == journal and value.get('state') == 'applying' and value.get('completed') == [] and publication.read_bytes() == old_plan
        after_plan = path == publication and publication.read_bytes() != old_plan
        if not fired and ((position == 'journal-before-plan' and before_plan) or (position == 'publication-after-plan' and after_plan)):
            fired = True
            assert json.loads(journal.read_text(encoding='utf-8'))['completed'] == []
            raise Crash()
        return persisted
    calls = fake_skip_model(monkeypatch)
    with monkeypatch.context() as scope:
        scope.setattr(w, '_atomic_json', checkpoint)
        w.reset_retries()
        with pytest.raises(Crash): w.run()
    assert fired and len(first) == 1 and not calls and log.read_bytes() == updated
    saved = json.loads(journal.read_text(encoding='utf-8'))
    assert {key: saved[key] for key in controls} == controls
    assert saved['completed'] == [] and saved['prior_semantic'] == [] and 'in_flight' not in saved
    assert not w._saved_publication_started(packet, publication)
    raw = source.read_bytes()
    if excluded:
        policy = s.VAULT/w.SOURCE_POLICY
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text('```json\n'+json.dumps({'version': 1, 'exclude_paths': [packet['sources'][0]['path']], 'exclude_prefixes': []})+'\n```\n', encoding='utf-8')
        notes, private = snapshot(s.VAULT), snapshot(folder)
    w.reset_retries()
    report = w.run()
    assert report['status'] == ('idle' if excluded else 'complete') and not calls
    assert source.read_bytes() == raw
    if excluded:
        assert snapshot(s.VAULT) == notes and snapshot(folder) == private and log.read_bytes() == updated
        assert not (s.VAULT/w.BASE/'Projects/example.md').exists()
    else:
        archive = s.VAULT/w.BASE/'Log-Archives'/(s.digest(updated)+'.md')
        assert archive.read_bytes() == updated and not w.lint()['issues']
