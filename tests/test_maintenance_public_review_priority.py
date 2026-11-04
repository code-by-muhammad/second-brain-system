"""Public actionable warnings precede age reminders without changing policy."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import brain_git
import brain_maintenance as maintenance
import brain_settings as cfg
import brain_store as store
import brain_wiki as wiki
import test_maintenance_central_history as shipping


AGE = 'review older than 30 days'
QUOTE = 'A synthetic dated source supports a reusable historical statement.'


def cached_health(state, rows, limit=20):
    path = state / 'maintenance-status.json'
    raw = json.dumps({'status': 'completed', 'git': 'synthetic synchronized', 'review': rows}).encode()
    path.write_bytes(raw)
    result = maintenance.health_status(path, review_limit=limit)
    assert path.read_bytes() == raw, 'Public priority must not rewrite the private receipt.'
    return result


def row(name, reasons):
    return {'path': '02-Areas/AI-Memory/' + name + '.md', 'reasons': reasons}


@pytest.mark.parametrize('limit', [1, 2, 20])
def test_public_truncation_retains_actionable_rows_before_pure_age_with_stable_groups(brain, limit):
    _, state = brain
    ages = [row('Age-' + str(number), [AGE]) for number in range(25)]
    first = row('Changed', ['source changed: Templates/Evidence.md'])
    second = row('Mixed', [AGE, 'missing source: Templates/Missing.md'])
    original = [ages[0], first, *ages[1:], second]
    result = cached_health(state, original, limit)
    expected = [first, second, *ages][:limit]
    assert [entry['path'] for entry in result['review']] == [entry['path'] for entry in expected]
    assert result['review_count'] == len(original) and result['review_truncated'] is True
    assert all(entry['reason_count'] == len(expected[index]['reasons']) for index, entry in enumerate(result['review']))


@pytest.mark.parametrize('kind', ['all-age', 'all-actionable', 'empty'])
def test_existing_single_priority_order_and_counts_stay_unchanged(brain, kind):
    _, state = brain
    rows = [] if kind == 'empty' else [row('Stable-' + str(number), [AGE] if kind == 'all-age' else ['synthetic actionable review']) for number in range(30)]
    result = cached_health(state, rows)
    assert [entry['path'] for entry in result['review']] == [entry['path'] for entry in rows[:20]]
    assert result['review_count'] == len(rows) and result['review_truncated'] is (len(rows) > 20)


@pytest.mark.parametrize('reasons', [None, [], [AGE, 'source metadata could not be validated; original preserved']])
def test_uncertain_or_mixed_review_is_not_hidden_by_age_only_rows(brain, reasons):
    _, state = brain
    uncertain = row('Uncertain', reasons)
    result = cached_health(state, [row('Age-' + str(number), [AGE]) for number in range(21)] + [uncertain], 1)
    assert result['review'][0]['path'] == uncertain['path']
    assert result['review_count'] == 22 and result['review_truncated'] is True


def test_prioritized_public_review_retains_redaction_bounds_and_private_cache(brain):
    _, state = brain
    private = 'api_key=' + 'SyntheticAlphaBetaGammaDelta'
    unsafe = {'path': '02-Areas/AI-Memory/' + private + '.md', 'reasons': [private] * 7,
              'unchecked_private_attribute': private}
    result = cached_health(state, [row('Age-' + str(number), [AGE]) for number in range(30)] + [unsafe], 1)
    item = result['review'][0]
    assert item['path_withheld'] is True and item['path_sha256'] == store.digest(unsafe['path'].encode())
    assert item['reason_count'] == 7 and item['reasons_truncated'] is True and len(item['reasons']) == 5
    assert item['reasons'] == [maintenance.WITHHELD_DETAIL] * 5
    assert 'unchecked_private_attribute' not in item and private not in json.dumps(result)
    assert result['review_count'] == 31 and result['review_truncated'] is True


def compiled_wiki(brain):
    vault, _ = brain
    for number in range(21):
        shipping.put(vault, '01-Projects/Source-' + str(number).zfill(3) + '.md',
                     ('---\ndate: 2026-09-01\n---\n# Source ' + str(number) + ' Project\n' + QUOTE + '\n').encode())
    batches = []
    while packet := wiki.prepare(4):
        result = {'pages': [], 'dispositions': []}
        for source in packet['sources']:
            result['pages'].append({'category': 'Projects', 'slug': Path(source['path']).stem.lower(), 'title': source['title'],
                'related': [], 'claims': [{'text': 'The synthetic source records a historical statement.',
                    'source': source['path'], 'quote': QUOTE, 'status': 'historical'}]})
            result['dispositions'].append({'source': source['path'], 'part': source['part'], 'status': 'published', 'reason': '', 'quote': ''})
        wiki.apply(packet, result)
        batches.append(packet['batch_id'])
    control = shipping.put(vault, wiki.BASE + 'Control.md', store.note_text({'maintainer_device_id': cfg.DEVICE_ID}, '# Owner control\n').encode())
    assert cfg.require_maintainer() is None
    assert len(batches) == 6 and wiki.status()['portable_receipt_parts'] == 21 and wiki.status()['pending_source_parts'] == 0
    assert wiki.lint()['issues'] == []
    generated = sorted((vault / wiki.BASE).rglob('*.md'))
    assert len(generated) == 51 and control in generated
    return generated


def test_all_generated_records_keep_age_policy_while_changed_maps_survives_public_limit(brain, monkeypatch):
    vault, state = brain
    generated = compiled_wiki(brain)
    for path in generated: shipping.age(path)
    source = shipping.put(vault, 'Templates/Context.md', b'# Earlier actual evidence\n')
    context = shipping.put(vault, 'Maps/AI-Context.md', store.note_text({'sources': ['Templates/Context.md'],
        'source_hashes': {'Templates/Context.md': store.digest(source.read_bytes())}}, '# Context summary\n').encode())
    source.write_bytes(b'# Later actual evidence\n')
    originals = {path: path.read_bytes() for path in [source, context, *generated]}
    calls = []
    monkeypatch.setattr(brain_git, 'sync', lambda: calls.append('stub sync') or 'synthetic synchronized')
    result = maintenance.maintenance()
    cached = json.loads((state / 'maintenance-status.json').read_text(encoding='utf-8'))
    raw = (state / 'maintenance-status.json').read_bytes()
    public = maintenance.health_status(state / 'maintenance-status.json')
    expected_age = {path.relative_to(vault).as_posix() for path in generated if not maintenance._compiler_history(path)}
    actual_age = {entry['path'] for entry in result['review'] if AGE in entry['reasons']}
    assert expected_age == actual_age and len(actual_age) == 44 and wiki.BASE + 'Control.md' in actual_age
    assert len(result['review']) == 45 and cached['review'] == result['review']
    assert calls == ['stub sync'] and result['status'] == 'completed'
    assert public['review'][0]['path'] == 'Maps/AI-Context.md'
    assert public['review'][0]['reasons'] == ['source changed: Templates/Context.md']
    assert public['review_count'] == 45 and public['review_truncated'] is True and len(public['review']) == 20
    assert (state / 'maintenance-status.json').read_bytes() == raw
    assert all(path.read_bytes() == body for path, body in originals.items())


@pytest.mark.parametrize('placement', ['central-root', 'linked-parent', 'central-child', 'healthy-root'])
def test_real_directory_link_boundary_warning_remains_resolvable_without_target_reads(brain, tmp_path, monkeypatch, placement):
    vault, state = brain
    central = vault / '02-Areas/AI-Memory'
    linked = placement != 'healthy-root'
    entry = vault / '02-Areas' if placement == 'linked-parent' else central if placement == 'central-root' else central / 'Linked'
    target = tmp_path / 'private-linked-namespace' if linked else central
    if linked: entry.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True)
    original = shipping.put(target, 'AI-Memory/Current.md' if placement == 'linked-parent' else 'Current.md',
                            store.note_text({'type': 'note'}, '# Private synthetic summary\n').encode())
    shipping.age(original)
    raw = original.read_bytes()
    if linked: shipping.directory_link(entry, target, tmp_path)
    old_scan, old_read = os.scandir, maintenance._read_derived
    visits, reads, calls = [], [], []
    def scanned(path):
        if linked and (Path(path).is_relative_to(entry) or Path(path).is_relative_to(target)):
            visits.append(str(path))
        return old_scan(path)
    def read(path):
        if linked and (path.is_relative_to(entry) or path.is_relative_to(target)):
            reads.append(str(path))
            raise AssertionError('A linked namespace target must not be read.')
        return old_read(path)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(os, 'scandir', scanned)
            guard.setattr(maintenance, '_read_derived', read)
            guard.setattr(brain_git, 'sync', lambda: calls.append('stub sync') or 'synthetic synchronized')
            result = maintenance.maintenance()
        assert result['status'] == 'completed' and calls == ['stub sync'] and original.read_bytes() == raw
        assert visits == [] and reads == []
        if placement in ('central-root', 'linked-parent'):
            relative = entry.relative_to(vault).as_posix()
            assert len(result['review']) == 1 and result['review'][0]['path_withheld'] is True
            assert result['review'][0]['path_sha256'] == shipping.digest(relative)
            assert result['review'][0]['reasons'] == [shipping.WARNING]
            assert 'Matching local paths: 1' in shipping.lookup(vault, relative, monkeypatch, (entry, target))
            assert 'private-linked-namespace' not in json.dumps(maintenance.health_status(state / 'maintenance-status.json'))
        elif placement == 'central-child': assert result['review'] == []
        else: assert result['review'] == [{'path': '02-Areas/AI-Memory/Current.md', 'reasons': [AGE]}]
    finally:
        if linked: shipping.unlink_directory(entry, target, tmp_path)


@pytest.mark.parametrize('platform_name,code,expected', [('nt', 1314, 'skip'), ('nt', 5, 'error'), ('posix', 1314, 'error')])
def test_file_symlink_fixture_limits_privilege_skip_to_explicit_windows1314(brain, tmp_path, monkeypatch, platform_name, code, expected):
    original_os = shipping.os
    class LocalOSProxy:
        name = platform_name
        def __getattr__(self, name): return getattr(original_os, name)
    failure = OSError(1, 'Explicit synthetic symlink failure')
    failure.winerror = code
    calls = []
    def denied(path, *args, **kwargs): calls.append('denied'); raise failure
    with monkeypatch.context() as guard:
        # Proxy only this test module's branch; never change global os.name or
        # pretend that the native host is an unprivileged Windows machine.
        guard.setattr(shipping, 'os', LocalOSProxy())
        guard.setattr(Path, 'symlink_to', denied)
        if expected == 'skip':
            with pytest.raises(pytest.skip.Exception):
                shipping.test_saved_lookup_identifies_each_entry_without_opening_or_descending_into_it(brain, tmp_path, guard, 'linked-file')
        else:
            with pytest.raises(OSError) as caught:
                shipping.test_saved_lookup_identifies_each_entry_without_opening_or_descending_into_it(brain, tmp_path, guard, 'linked-file')
            assert caught.value is failure
    assert calls == ['denied']


@pytest.mark.parametrize('case', ['linked-directory', 'linked-descendant'])
def test_directory_fixture_uses_actual_native_link_adapter_without_file_symlink_privilege_claim(brain, tmp_path, monkeypatch, case):
    failure = OSError(1, 'Explicit synthetic Windows privilege failure')
    failure.winerror = 1314
    calls = []
    def denied(path, *args, **kwargs): calls.append('symlink_to'); raise failure
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'symlink_to', denied)
        def run_shipping():
            if case == 'linked-descendant':
                shipping.test_lookup_does_not_reach_descendants_beyond_an_opaque_directory_entry(brain, tmp_path, guard, 'linked')
            else:
                shipping.test_saved_lookup_identifies_each_entry_without_opening_or_descending_into_it(brain, tmp_path, guard, case)
        if os.name == 'nt':
            run_shipping()
            assert calls == [], 'Windows directory coverage must use its actual junction adapter.'
        else:
            # On actual Linux this artificial error belongs to native symlink
            # creation and propagates; it is not proof of Windows behavior.
            with pytest.raises(OSError) as caught: run_shipping()
            assert caught.value is failure and calls == ['symlink_to']
