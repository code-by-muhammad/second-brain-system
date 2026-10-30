"""Lint diagnoses the decoded claim limits used by selected model context."""
import json

import pytest

import brain_store as s
import brain_wiki as w
from test_wiki_selected_context_roundtrip import actual_topic, aliased_depth, claim, nodes, retired_fixture
from test_wiki_remediation import explicit, fake_model


def proven_topic(brain, values):
    source, result = retired_fixture(brain)
    topic, _ = actual_topic(values[0])
    info = w.meta(topic.read_text('utf-8'))
    origin = w._source_bytes(source.read_bytes())
    for value in values:
        value['source_revision'] = {'sha256': origin['raw_sha256'], 'text_sha256': origin['text_sha256'], 'date': origin['date']}
    info['wiki_claims'] = values
    info['source_hashes'] = {'01-Projects/Example.md': origin['raw_sha256']}
    topic.write_bytes(s.note_text(info, '# Example Project\n').encode())
    return source, result, topic


def matching(report, topic, index):
    return [row for row in report['issues'] if row.get('page') == topic.relative_to(s.VAULT).as_posix() and row.get('field') == 'wiki_claims' and row.get('claim_index') == index]


@pytest.mark.parametrize('expanded_depth', [64, 65, 66])
def test_lint_exact_depth_boundary_matches_worker_and_preserves_valid_origin(brain, monkeypatch, expanded_depth):
    source, result, topic = proven_topic(brain, [aliased_depth(expanded_depth)])
    originals = source.read_bytes(), topic.read_bytes()
    parsed = w.meta(topic.read_text('utf-8'))['wiki_claims'][0]
    w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(), parsed, 0)
    calls = fake_model(monkeypatch, explicit({'sources': w.inventory()}, result))
    report = w.lint()
    if expanded_depth == 64:
        assert report['issues'] == []
        assert w.run(1)['status'] == 'complete' and len(calls) == 1
    else:
        worker = w.run(1)
        assert worker['status'] == 'attention' and worker['integrity_field'] == 'wiki_claims' and worker['claim_index'] == 0
        assert matching(report, topic, 0) and len(calls) == 0
        assert 'ranked' not in json.dumps(report)
    assert (source.read_bytes(), topic.read_bytes()) == originals


@pytest.mark.parametrize('bad_index', [0, 1, 12], ids=['older-unselected-control', 'first-selected-index', 'last-selected-index'])
def test_lint_uses_original_indices_and_only_the_last_twelve_decoded_claims(brain, monkeypatch, bad_index):
    values = [claim({'benign': number}) for number in range(13)]
    values[bad_index] = aliased_depth(66)
    source, result, topic = proven_topic(brain, values)
    originals = source.read_bytes(), topic.read_bytes()
    calls = fake_model(monkeypatch, explicit({'sources': w.inventory()}, result))
    report = w.lint()
    if bad_index == 0:
        assert not matching(report, topic, bad_index) and report['issues'] == []
        assert w.run(1)['status'] == 'complete' and len(calls) == 1
    else:
        worker = w.run(1)
        assert matching(report, topic, bad_index)
        assert worker['status'] == 'attention' and worker['claim_index'] == bad_index and len(calls) == 0
    assert (source.read_bytes(), topic.read_bytes()) == originals


@pytest.mark.parametrize('expanded_nodes', [20_000, 20_001])
def test_lint_does_not_invent_selected_node_failure_for_compact_omitted_claims(brain, expanded_nodes):
    shared = []
    value = claim([shared] * 10)
    # Include the authentic revision before measuring the exact expanded size.
    source, _, topic = proven_topic(brain, [value])
    info = w.meta(topic.read_text('utf-8'))
    value = info['wiki_claims'][0]
    needed = expanded_nodes - nodes(value)
    count, remainder = divmod(needed, 10)
    value['extra'][0].extend([0] * count)
    value['extra'].extend([0] * remainder)
    topic.write_bytes(s.note_text(info, '# Example Project\n').encode())
    originals = source.read_bytes(), topic.read_bytes()
    parsed = w.meta(topic.read_text('utf-8'))['wiki_claims'][0]
    decoded = json.loads(w._bounded_json(parsed, ensure_ascii=False, allow_nan=False))
    assert nodes(decoded) == expanded_nodes
    if expanded_nodes == 20_000:
        w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(), decoded, 0)
    else:
        with pytest.raises(w.WikiIntegrityError):
            w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(), decoded, 0)
    report = w.lint()
    packet = w.prepare(1)
    assert packet['current_pages'] == [] and len(packet['catalog']) == 1
    assert report['issues'] == [] and not matching(report, topic, 0)
    assert (source.read_bytes(), topic.read_bytes()) == originals


def test_decoded_diagnostic_does_not_bless_unverified_source_revision(brain):
    source, _, topic = proven_topic(brain, [aliased_depth(64)])
    info = w.meta(topic.read_text('utf-8'))
    info['wiki_claims'][0]['source_revision']['sha256'] = 'f' * 64
    topic.write_bytes(s.note_text(info, '# Example Project\n').encode())
    originals = source.read_bytes(), topic.read_bytes()
    report = w.lint()
    assert any(row.get('issue') == 'source revision unverified' and row.get('claim_index') == 0 for row in report['issues'])
    assert not matching(report, topic, 0) and (source.read_bytes(), topic.read_bytes()) == originals
