"""Known parsed source scalars are screened without decoding arbitrary body text."""
import copy,json
from pathlib import Path
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE, source_and_topic, skip_output
from test_wiki_selected_context_roundtrip import aliased_depth

TOKEN = 'ghp_' + 'SyntheticValueA12345' * 2


def escaped(value, style='unicode'):
    return ''.join(('\\u' + format(ord(char), '04x')) if style == 'unicode' else ('\\x' + format(ord(char), '02x')) for char in value)


def original_source(brain, header, body=QUOTE):
    path = s.VAULT / '01-Projects/Example.md'; path.parent.mkdir(parents=True)
    raw = ('---\n' + header + '\n---\n# Example Project\n' + body + '\n').encode()
    path.write_bytes(raw)
    return path, raw


@pytest.mark.parametrize('header', [
    'annotation: "' + escaped(TOKEN) + '"',
    'annotation: "' + escaped(TOKEN, 'hex') + '"',
    '"' + escaped(TOKEN) + '": harmless',
    '"' + escaped('password') + '": "' + escaped('SyntheticValue1234567890') + '"',
    'outer:\n  items:\n  - nested: "' + escaped(TOKEN) + '"',
    'first: &shared "' + escaped(TOKEN) + '"\nsecond: *shared',
    '"' + escaped('access_token') + '": 12345678901234567890',
    'padding: "' + 'A' * 25000 + '"\nannotation: "' + escaped(TOKEN) + '"',
], ids=['unicode-value','hex-value','decoded-key','decoded-association','nested-scalar','shared-alias','numeric-association','beyond-first-chunk'])
def test_known_decoded_frontmatter_credentials_remain_pending_with_safe_source_attention(brain, monkeypatch, header):
    source, raw = original_source(brain, header)
    assert not s.SECRET.search(raw.decode())
    rows = w.inventory(); assert rows and rows[0]['path'] == '01-Projects/Example.md'
    output = skip_output()
    output['dispositions'][0]['quote'] = QUOTE if QUOTE in rows[0]['text'] else 'A' * 20
    calls = fake_model(monkeypatch, output)
    report = w.run(1)
    assert report['status'] == 'attention' and report['integrity_field'] == 'content' and not calls
    assert report['integrity_page'] == '01-Projects/Example.md'
    assert source.read_bytes() == raw and w.inventory() == rows and w.status()['pending_source_parts'] == len(rows)
    assert not list((cfg.STATE / 'wiki/batches').glob('*/input.json'))
    rendered = json.dumps({'first': report, 'cached': w._read_worker_state(), 'public': w.status()['worker']})
    assert TOKEN not in rendered and 'SyntheticValue' not in rendered and '\\ud800' not in rendered


@pytest.mark.parametrize('header', [
    'annotation: "' + escaped('Ordinary owner annotation') + '"',
    'annotation: "' + escaped('Discuss API key setup without storing credentials.') + '"',
    '"' + escaped('api_key') + '": "rotate through the provider console; never store tokens"',
    'date: "2026-99-99"\ncount: 12\nenabled: true\nempty: null',
    'a: &shared [one, two]\nb: *shared',
], ids=['ordinary-escaped','api-discussion','benign-association','ordinary-date-scalars','fitting-alias'])
def test_benign_parsed_source_metadata_and_date_contract_still_complete(brain, monkeypatch, header):
    source, raw = original_source(brain, header)
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    assert report['status'] == 'complete' and len(calls) == 1 and source.read_bytes() == raw


def test_old_unused_saved_packet_cannot_bypass_new_full_source_metadata_guard(brain, monkeypatch):
    source, raw = original_source(brain, 'annotation: "' + escaped(TOKEN) + '"')
    old_validator = w._validate_source_context
    with monkeypatch.context() as legacy:
        legacy.setattr(w, '_validate_source_context', lambda packet: None)
        packet = w.prepare(1)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']; saved = (folder / 'input.json').read_bytes()
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    assert report['status'] == 'attention' and report['integrity_field'] == 'content' and not calls
    assert source.read_bytes() == raw and (folder / 'input.json').read_bytes() == saved


def test_source_reread_is_bounded_and_owner_race_remains_transient(brain, monkeypatch):
    source, raw = original_source(brain, 'annotation: ordinary')
    row = w.inventory()[0]
    old_open = Path.open; reads = []
    class Reader:
        def __enter__(self): self.stream = old_open(source, 'rb'); return self
        def __exit__(self, *args): self.stream.close()
        def read(self, count): reads.append(count); return self.stream.read(count)
    monkeypatch.setattr(Path, 'open', lambda path, *a, **k: Reader() if path == source and a == ('rb',) else old_open(path, *a, **k))
    w._validate_source_context({'sources': [row]})
    assert reads == [2_000_001]
    source.write_bytes(raw + b'Owner changed the source deliberately.\n')
    with pytest.raises(w.WikiTransientError): w._validate_source_context({'sources': [row]})
    assert source.read_bytes() == raw + b'Owner changed the source deliberately.\n'


@pytest.mark.parametrize('count', [119, 120, 121])
def test_actual_last_claim_index_survives_attention_without_expanding_topic_capacity(brain, monkeypatch, count):
    source, topic = source_and_topic(brain, [{} for _ in range(count)])
    metadata = w.meta(topic.read_text('utf-8'))
    metadata['wiki_claims'][-1] = {**aliased_depth(66), 'source_revision': metadata['wiki_claims'][-1]['source_revision']}
    topic.write_bytes(s.note_text(metadata, '# Example Project\n').encode())
    original = source.read_bytes(), topic.read_bytes()
    calls = fake_model(monkeypatch, skip_output())
    report = w.run(1)
    assert report['status'] == 'attention' and report['claim_index'] == count - 1 and not calls
    assert (source.read_bytes(), topic.read_bytes()) == original


@pytest.mark.parametrize('count', [True, -1, 12.0])
def test_invalid_claim_count_types_keep_zero_offset_fallback(brain, count):
    source, topic = source_and_topic(brain, [{}])
    packet = w.prepare(1)
    packet['current_pages'][0]['claim_count'] = count
    packet['current_pages'][0]['meta']['wiki_claims'] = [json.loads(w._bounded_json(aliased_depth(66)))]
    with pytest.raises(w.WikiIntegrityError) as caught: w._validate_packet_context(packet)
    assert caught.value.claim_index == 0 and caught.value.field == 'wiki_claims'
