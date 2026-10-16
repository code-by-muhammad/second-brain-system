"""Optional aggregate context limits do not misclassify valid individual claims."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model

QUOTE = 'A prototype was demonstrated. Production deployment is still planned.'


def source_and_topic(brain, extras, bad_text=False):
    source = s.VAULT / '01-Projects/Example.md'
    source.parent.mkdir(parents=True)
    source.write_bytes(('---\ndate: 2026-10-01\n---\n# Example Project\n' + QUOTE + '\n').encode())
    origin = w._source_bytes(source.read_bytes())
    revision = {key: origin[value] for key, value in [('sha256', 'raw_sha256'), ('text_sha256', 'text_sha256'), ('date', 'date')]}
    claims = [{'text': '' if bad_text and index == 0 else 'The source records a prototype.', 'source': '01-Projects/Example.md',
               'quote': QUOTE, 'status': 'historical', 'source_revision': revision.copy(), 'extra': extra} for index, extra in enumerate(extras)]
    topic = s.VAULT / w.BASE / 'Projects/example.md'
    topic.parent.mkdir(parents=True)
    topic.write_bytes(s.note_text({'wiki_kind': 'Projects', 'title': 'Example Project', 'wiki_claims': claims,
                                  'source_hashes': {'01-Projects/Example.md': origin['raw_sha256']}}, '# Example Project\n').encode())
    return source, topic


def skip_output():
    return {'pages': [], 'dispositions': [{'source': '01-Projects/Example.md', 'part': 1, 'status': 'skipped',
            'reason': 'This synthetic source needs no new topic.', 'quote': QUOTE}]}


def test_valid_aggregate_structural_context_is_omitted_with_complete_catalog_and_safe_saved_input(brain):
    source, topic = source_and_topic(brain, [[0] * 17000 for _ in range(6)])
    originals = source.read_bytes(), topic.read_bytes()
    assert w.lint()['issues'] == []
    packet = w.prepare(1)
    assert packet['current_pages'] == [] and packet['catalog'][0]['claim_count'] == 6
    assert packet['catalog'][0]['path'] == topic.relative_to(s.VAULT).as_posix()
    saved = w._read_saved_packet(cfg.STATE / 'wiki/batches' / packet['batch_id'] / 'input.json', packet['batch_id'])
    w._validate_packet_context(saved)
    assert (source.read_bytes(), topic.read_bytes()) == originals


def test_omitted_aggregate_work_finishes_only_from_verified_disposition_and_part_receipt(brain, monkeypatch):
    source, topic = source_and_topic(brain, [[0] * 17000 for _ in range(6)])
    originals = source.read_bytes(), topic.read_bytes()
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    assert report['status'] == 'complete' and len(calls) == 1 and report['lint']['issues'] == []
    status = w.status()
    assert status['pending_source_parts'] == 0 and status['receipt_issue_count'] == 0
    with w.db() as conn:
        assert conn.execute('SELECT processed_hash FROM sources WHERE path=?', ('01-Projects/Example.md::part1',)).fetchone()[0] == s.digest(originals[0])
    w.reset_retries()
    assert w.run(1)['status'] == 'idle' and len(calls) == 1
    assert (source.read_bytes(), topic.read_bytes()) == originals


@pytest.mark.parametrize('bad', ['empty-text', 'deep', 'cycle', 'individual-nodes'])
def test_aggregate_omission_does_not_hide_invalid_individual_context(brain, monkeypatch, bad):
    extras = [[0] * 17000 for _ in range(6)]
    if bad == 'deep':
        value = 0
        for _ in range(65): value = [value]
        extras.insert(0, value)
    elif bad == 'cycle':
        value = []; value.append(value); extras.insert(0, value)
    elif bad == 'individual-nodes': extras.insert(0, [0] * 20001)
    source, topic = source_and_topic(brain, extras, bad_text=bad == 'empty-text')
    originals = source.read_bytes(), topic.read_bytes()
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    assert report['status'] == 'attention' and report['integrity_field'] == 'wiki_claims' and not calls
    assert not list((cfg.STATE / 'wiki/batches').glob('*/input.json'))
    assert (source.read_bytes(), topic.read_bytes()) == originals


def test_fitting_context_remains_selected_with_original_claims(brain):
    source, topic = source_and_topic(brain, [{'ordinary': [1, 2, 3]}])
    original = w.meta(topic.read_text('utf-8'))['wiki_claims']
    packet = w.prepare(1)
    assert packet['current_pages'][0]['meta']['wiki_claims'] == original
    assert packet['catalog'][0]['claim_count'] == 1 and w.lint()['issues'] == []


@pytest.mark.parametrize('ordinary_claims', [5, 6], ids=['character-omission-control', 'structural-omission'])
def test_omitted_raw_alias_graph_keeps_existing_decoded_expansion_policy(brain, ordinary_claims):
    shared = [0] * 2000
    source, topic = source_and_topic(brain, [[shared] * 10] + [[0] * 17000 for _ in range(ordinary_claims)])
    original = source.read_bytes(), topic.read_bytes()
    parsed = w.meta(topic.read_text('utf-8'))['wiki_claims'][0]
    w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(), parsed, 0)
    with pytest.raises(w.WikiIntegrityError):
        w._validate_existing_claim(topic.relative_to(s.VAULT).as_posix(), json.loads(w._bounded_json(parsed)), 0)
    packet = w.prepare(1)
    assert packet['current_pages'] == [] and packet['catalog'][0]['claim_count'] == ordinary_claims + 1
    assert w.lint()['issues'] == [] and (source.read_bytes(), topic.read_bytes()) == original
