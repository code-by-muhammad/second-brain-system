"""Synthetic, bounded, no-model/no-host-service reproductions of Claude R11 #2-4."""
import copy, json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model

@pytest.fixture
def brain(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    vault = tmp_path / 'vault'
    state = root / 'state'
    state.mkdir(parents=True)
    vault.mkdir()
    (root / 'logs').mkdir()
    for module in (cfg, s):
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'STATE', state)
        monkeypatch.setattr(module, 'VAULT', vault)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'test-host')
    return (vault, state)

def row(packet):
    with w.db() as conn:
        return dict(conn.execute('SELECT * FROM batches WHERE id=?', (packet['batch_id'],)).fetchone())

def snap():
    return {p.relative_to(s.VAULT).as_posix(): p.read_bytes() for p in s.VAULT.rglob('*.md')}

def caught():
    try:
        return (w.run(), None)
    except Exception as exc:
        return (None, type(exc).__name__)

def altered(result):
    new = copy.deepcopy(result)
    new['pages'][0]['slug'] = 'supplier-evaluation'
    new['pages'][0]['title'] = 'Supplier evaluation'
    return new

def test_unused_archive_collision_repair_reuses_saved_output_not_a_paid_new_call(brain, monkeypatch):
    source, packet, result = fixture(brain)
    source_raw = source.read_bytes()
    log = s.VAULT / w.BASE / 'Log.md'
    log.parent.mkdir(parents=True, exist_ok=True)
    raw = b'# Retained history\n' + b'x' * 1000001
    log.write_bytes(raw)
    archive = log.parent / 'Log-Archives' / (s.digest(raw) + '.md')
    archive.parent.mkdir()
    archive.write_bytes(b'Synthetic owner collision, preserved.')
    result = explicit(packet, result)
    calls = fake_model(monkeypatch, result)
    first, error = caught()
    initial = row(packet)['status']
    before = snap()
    saved = (cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'output.json').read_bytes()
    assert error == 'WikiIntegrityError' and len(calls) == 1 and (not w._saved_publication_started(packet, cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'publication.json'))
    owner_collision = archive.read_bytes()
    archive.rename(archive.with_suffix('.owner-preserved'))
    w.reset_retries()
    calls2 = fake_model(monkeypatch, altered(result))
    second, error2 = caught()
    final = w._read_worker_state()
    assert second is not None and second['status'] == 'complete' and len(calls2) == 0
    assert initial == 'error' and error2 is None and final['attention_required'] is False
    assert source.read_bytes() == source_raw and archive.read_bytes() == raw
    assert archive.with_suffix('.owner-preserved').read_bytes() == owner_collision
    assert (cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'output.json').read_bytes() == saved
    assert w.run()['status'] == 'idle' and len(calls2) == 0

def test_unused_credential_like_legacy_target_can_recover_without_rewriting_private_plan(brain, monkeypatch):
    source, _, result = fixture(brain)
    result['pages'][0]['slug'] = 'sk-hynix-supplier-evaluation'
    original_clean = s.clean_text

    def legacy_clean(value, *args, **kwargs):
        return value if isinstance(value, str) and 'sk-hynix-supplier-evaluation' in value.lower() else original_clean(value, *args, **kwargs)
    monkeypatch.setattr(s, 'clean_text', legacy_clean)
    packet = w.prepare()
    result = explicit(packet, result)
    calls = fake_model(monkeypatch, result)
    write = s._write

    def stop(path, text, kind, *args):
        if kind == 'wiki_compile':
            raise OSError('Synthetic pre-topic interruption')
        return write(path, text, kind, *args)
    monkeypatch.setattr(s, '_write', stop)
    _, first_error = caught()
    assert first_error == 'OSError'
    monkeypatch.setattr(s, '_write', write)
    monkeypatch.setattr(s, 'clean_text', original_clean)
    w.reset_retries()
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    saved = {name: (folder / name).read_bytes() for name in ('input.json', 'output.json', 'publication.json')}
    assert not w._saved_publication_started(packet, folder / 'publication.json')
    _, guard_error = caught()
    guard_status = row(packet)['status']
    no_writes = snap()
    w.reset_retries()
    calls2 = fake_model(monkeypatch, altered(result))
    report, error = caught()
    state = w._read_worker_state()
    assert report is not None and report['status'] == 'complete'
    assert guard_error == 'WikiIntegrityError' and guard_status == 'superseded'
    assert len(calls2) == 1 and report['batch_id'] != packet['batch_id']
    assert all((folder / name).read_bytes() == raw for name, raw in saved.items())
    assert not (s.VAULT / w.BASE / 'Projects/sk-hynix-supplier-evaluation.md').exists()
    assert not w.lint()['issues'] and w.run()['status'] == 'idle' and len(calls2) == 1

def partial_index_conflict(brain, monkeypatch):
    source, packet, result = fixture(brain)
    result = explicit(packet, result)
    calls = fake_model(monkeypatch, result)
    write = s._write

    def stop(path, text, kind, *args):
        if kind == 'wiki_index':
            raise OSError('Synthetic interruption before Index replacement')
        return write(path, text, kind, *args)
    monkeypatch.setattr(s, '_write', stop)
    _, error = caught()
    assert error == 'OSError'
    monkeypatch.setattr(s, '_write', write)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    plan = json.loads((folder / 'publication.json').read_text(encoding='utf-8'))
    assert w._saved_publication_started(packet, folder / 'publication.json')
    index = s.VAULT / w.BASE / 'Index.md'
    original = index.read_bytes() if index.exists() else None
    owner = b'# Owner concurrent Index content\n'
    index.write_bytes(owner)
    for _ in range(2):
        state = w._read_worker_state()
        state['retry_after'] = 0
        w._atomic_json(cfg.STATE / 'wiki-worker-status.json', state)
        caught()
    return (source, packet, calls, index, original, owner, plan)

def test_actual_partial_index_conflict_preserves_owner_and_recovers_after_explicit_cas_resolution(brain, monkeypatch):
    source, packet, calls, index, original, owner, plan = partial_index_conflict(brain, monkeypatch)
    assert index.read_bytes() == owner and len(calls) == 1 and (w._read_worker_state()['status'] == 'attention')
    source_raw = source.read_bytes()
    preserved_partial = (s.VAULT / w.BASE / 'Projects/example.md').read_bytes()
    if original is None:
        index.unlink()
    else:
        index.write_bytes(original)
    w.reset_retries()
    report = w.run()
    assert report['status'] == 'complete' and len(calls) == 1 and (source.read_bytes() == source_raw)

def test_actual_partial_index_conflict_attention_names_the_exact_conflicting_page(brain, monkeypatch):
    _, packet, calls, index, original, owner, plan = partial_index_conflict(brain, monkeypatch)
    state = w._read_worker_state()
    unrelated = s.VAULT / '01-Projects/Unrelated.md'
    unrelated.write_bytes(b'# Unrelated\nA separate historical requirement needs review.\n')
    report = w.run()
    assert state.get('integrity_page') == index.relative_to(s.VAULT).as_posix()
    assert state['integrity_field'] == 'publication_cas'
    assert state['recovery_action'] == 'resolve_saved_publication_cas_then_retry'
    assert report['integrity_page'] == state['integrity_page'] and report['integrity_field'] == state['integrity_field']
    assert w.status()['worker']['recovery_action'] == state['recovery_action']
    assert report['status'] == 'attention' and len(calls) == 1 and index.read_bytes() == owner
    assert not (s.VAULT / w.BASE / 'Projects/unrelated.md').exists()

def interrupted_plan(brain, monkeypatch, kind='wiki_compile'):
    source, packet, result = fixture(brain)
    result = explicit(packet, result)
    calls = fake_model(monkeypatch, result)
    original = s._write
    def stop(path, text, action, *args):
        if action == kind:
            raise OSError('Synthetic interrupted publication')
        return original(path, text, action, *args)
    with monkeypatch.context() as context:
        context.setattr(s, '_write', stop)
        with pytest.raises(OSError):
            w.run()
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    assert (folder / 'publication.json').exists() and len(calls) == 1
    return source, packet, result, folder, calls


@pytest.mark.parametrize('saved_kind', ['invalid-json', 'valid-but-unverified-output'])
def test_generic_error_without_durable_plan_never_replays_saved_model_output(brain, monkeypatch, saved_kind):
    source, packet, result = fixture(brain)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    output = 'not valid json' if saved_kind == 'invalid-json' else json.dumps(explicit(packet, altered(result)))
    (folder / 'output.json').write_text(output, encoding='utf-8')
    with w.db() as conn:
        conn.execute("UPDATE batches SET status='error' WHERE id=?", (packet['batch_id'],))
    calls = fake_model(monkeypatch, explicit(packet, result))
    report = w.run()
    assert report['status'] == 'complete' and len(calls) == 1
    assert not (s.VAULT / w.BASE / 'Projects/supplier-evaluation.md').exists()
    assert (s.VAULT / w.BASE / 'Projects/example.md').exists()


@pytest.mark.parametrize('corruption', ['result', 'plan'])
def test_error_with_invalid_durable_plan_cannot_replay_or_spend_another_call(brain, monkeypatch, corruption):
    source, packet, result, folder, calls = interrupted_plan(brain, monkeypatch)
    if corruption == 'result':
        (folder / 'output.json').write_text(json.dumps(altered(result)), encoding='utf-8')
    else:
        plan = json.loads((folder / 'publication.json').read_text(encoding='utf-8'))
        plan['result_sha256'] = '0' * 64
        (folder / 'publication.json').write_text(json.dumps(plan), encoding='utf-8')
    with w.db() as conn:
        conn.execute("UPDATE batches SET status='error' WHERE id=?", (packet['batch_id'],))
    before = snap()
    private_before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    w.reset_retries()
    calls2 = fake_model(monkeypatch, altered(result))
    with pytest.raises(w.WikiIntegrityError):
        w.run()
    assert len(calls2) == 0 and snap() == before
    assert all((folder / name).read_bytes() == raw for name, raw in private_before.items())
    assert w.status()['worker']['attention_required']


@pytest.mark.parametrize('partial', [False, True])
def test_error_plan_policy_exemption_requires_actual_verified_partial_publication(brain, monkeypatch, partial):
    from test_wiki_source_policy import write_policy
    source, packet, result, folder, calls = interrupted_plan(brain, monkeypatch, 'wiki_index' if partial else 'wiki_compile')
    with w.db() as conn:
        conn.execute("UPDATE batches SET status='error' WHERE id=?", (packet['batch_id'],))
    assert w._saved_publication_started(packet, folder / 'publication.json') is partial
    raw = source.read_bytes()
    write_policy([source.relative_to(s.VAULT).as_posix()])
    w.reset_retries()
    replacement = fake_model(monkeypatch, altered(result))
    report = w.run()
    assert report['status'] == ('complete' if partial else 'idle') and len(replacement) == 0
    assert source.read_bytes() == raw
    assert (s.VAULT / w.BASE / 'Projects/example.md').exists() is partial
    if partial:
        assert report['recovered_excluded_source_parts'] == 1
    else:
        assert row(packet)['status'] == 'superseded'


@pytest.mark.parametrize('kind', ['wiki_source', 'wiki_index', 'wiki_log', 'wiki_receipt'])
def test_partial_cas_names_safe_target_holds_queue_and_recovers_only_after_owner_resolution(brain, monkeypatch, kind):
    source, packet, result, folder, calls = interrupted_plan(brain, monkeypatch, kind)
    plan_bytes = (folder / 'publication.json').read_bytes()
    plan = json.loads(plan_bytes)
    write = next(item for item in plan['writes'] if item['kind'] == kind)
    target = s.VAULT / write['path']
    original = target.read_bytes() if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    owner = b'# Preserve concurrent owner decision\n'
    target.write_bytes(owner)
    w.reset_retries()
    replacement = fake_model(monkeypatch, altered(result))
    for _ in range(3):
        state = w._read_worker_state()
        state['retry_after'] = 0
        w._atomic_json(cfg.STATE / 'wiki-worker-status.json', state)
        with pytest.raises(w.WikiTransientError):
            w.run()
        assert target.read_bytes() == owner and len(replacement) == 0
        assert (folder / 'publication.json').read_bytes() == plan_bytes
        summary = w.status()['worker']
        assert summary['integrity_page'] == write['path'] and summary['integrity_field'] == 'publication_cas'
    assert w.run()['status'] == 'attention' and len(replacement) == 0
    if original is None:
        target.unlink()
    else:
        target.write_bytes(original)
    w.reset_retries()
    assert w.run()['status'] == 'complete' and len(replacement) == 0
    assert target.read_bytes() == write['text'].encode('utf-8')
    assert (folder / 'publication.json').read_bytes() == plan_bytes


def test_conflict_locator_does_not_expose_unsafe_legacy_target(brain):
    marker = 'ghp_' + 'syntheticmarker' * 4
    error = w._publication_conflict(w.BASE + 'Projects/' + marker + '.md')
    assert marker not in error.page and marker not in str(error)
    assert error.field == 'publication_cas'
