"""Fresh and resumed publication must hash the same durable packet values."""
import json

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_selected_context_roundtrip import actual_topic, aliased_depth, claim, retired_fixture
from test_wiki_remediation import explicit, fake_model


def interrupted_fresh_run(brain, monkeypatch, extra):
    source, result = retired_fixture(brain)
    topic, original = actual_topic(claim(extra))
    originals = (source.read_bytes(), topic.read_bytes())
    calls = fake_model(monkeypatch, explicit({'sources': w.inventory()}, result))
    write = s._write

    def stop_index(path, text, kind, *args, **kwargs):
        if kind == 'wiki_index':
            raise OSError('Synthetic interruption after actual topic publication')
        return write(path, text, kind, *args, **kwargs)

    monkeypatch.setattr(s, '_write', stop_index)
    with pytest.raises(OSError):
        w.run(1)  # Intentionally no explicit prepare: exercise the fresh path.
    monkeypatch.setattr(s, '_write', write)
    ident = w._read_worker_state()['batch_id']
    folder = cfg.STATE / 'wiki/batches' / ident
    saved = {name: (folder / name).read_bytes() for name in ('input.json', 'output.json', 'publication.json')}
    plan = json.loads(saved['publication.json'])
    semantic = next(row for row in plan['writes'] if row['kind'] == 'wiki_compile')
    published = s.safe_path(semantic['path'])
    published_raw = published.read_bytes()
    assert semantic['before'] != semantic['after'] and s.digest(published_raw) == semantic['after']
    assert len(calls) == 1 and source.read_bytes() == originals[0] and topic.read_bytes() == originals[1]
    return source, topic, originals, original, calls, folder, saved, published, published_raw


@pytest.mark.parametrize('extra', [
    {'ranked': {'9': 'first', '10': 'second'}},
    {'ranked': {1: 'first', 2: 'second'}},
    {'ranked': {9: 'first', 10: 'second'}, 'annotation': 'caf\u00e9'},
    {'ranked': {9: 'first', 10: 'second'}, 'fitting_alias': aliased_depth(62)['extra']},
], ids=['string-key-control', 'integer-order-control', 'integer-lexical-order', 'integer-and-fitting-alias'])
def test_fresh_partial_publication_uses_saved_packet_identity_and_one_model_call(brain, monkeypatch, extra):
    source, topic, originals, original, calls, folder, saved, published, published_raw = interrupted_fresh_run(brain, monkeypatch, extra)
    packet = json.loads(saved['input.json'])
    plan = json.loads(saved['publication.json'])
    w.reset_retries()
    report = w.run(1)
    assert report['status'] == 'complete' and report['batch_id'] == packet['batch_id']
    assert plan['packet_sha256'] == w._json_hash(packet)
    journal = json.loads((folder / 'journal.json').read_bytes())
    assert journal['state'] == 'complete' and journal['packet_sha256'] == plan['packet_sha256']
    assert len(calls) == 1 and published.read_bytes() == published_raw
    assert source.read_bytes() == originals[0] and topic.read_bytes() == originals[1]
    assert w.meta(topic.read_text('utf-8'))['wiki_claims'][0] == original
    assert all((folder / name).read_bytes() == raw for name, raw in saved.items())


@pytest.mark.parametrize('conflict', ['owner-index', 'saved-output', 'saved-plan'], ids=['owner-cas-hold', 'output-identity-hold', 'plan-identity-hold'])
def test_canonical_packet_does_not_weaken_owner_or_saved_proof_holds(brain, monkeypatch, conflict):
    source, topic, originals, _, calls, folder, saved, published, published_raw = interrupted_fresh_run(brain, monkeypatch, {'ranked': {9: 'first', 10: 'second'}})
    if conflict == 'owner-index':
        target = s.VAULT / w.BASE / 'Index.md'
        prior = target.read_bytes() if target.exists() else None
        changed = b'# Owner Index content must survive recovery\n'
        target.write_bytes(changed)
    else:
        name = 'output.json' if conflict == 'saved-output' else 'publication.json'
        target = folder / name
        prior = saved[name]
        value = json.loads(prior)
        if conflict == 'saved-output':
            value['pages'][0]['claims'][0]['text'] += ' Changed synthetic output.'
        else:
            value['packet_sha256'] = 'f' * 64
        target.write_bytes(json.dumps(value, sort_keys=True).encode())
        changed = target.read_bytes()
    w.reset_retries()
    with pytest.raises((w.WikiIntegrityError, w.WikiTransientError, w.WikiOutputError)):
        w.run(1)
    assert target.read_bytes() == changed and len(calls) == 1
    assert published.read_bytes() == published_raw and source.read_bytes() == originals[0] and topic.read_bytes() == originals[1]
    if prior is None:
        target.unlink()
    else:
        target.write_bytes(prior)
    w.reset_retries()
    assert w.run(1)['status'] == 'complete' and len(calls) == 1
    assert all((folder / name).read_bytes() == raw for name, raw in saved.items())


def test_apply_canonicalizes_a_local_copy_without_changing_caller_claim_types_or_aliases(brain):
    source, result = retired_fixture(brain)
    shared = ['A benign owner annotation']
    topic, original = actual_topic(claim({'ranked': {9: 'first', 10: 'second'}, 'aliases': [shared, shared]}))
    source_raw, topic_raw = source.read_bytes(), topic.read_bytes()
    packet = w.prepare(1)
    selected = packet['current_pages'][0]['meta']['wiki_claims'][0]
    assert set(selected['extra']['ranked']) == {9, 10}
    assert selected['extra']['aliases'][0] is selected['extra']['aliases'][1]
    receipts = w.apply(packet, explicit(packet, result))
    assert receipts and set(selected['extra']['ranked']) == {9, 10}
    assert selected['extra']['aliases'][0] is selected['extra']['aliases'][1]
    assert selected == original and source.read_bytes() == source_raw and topic.read_bytes() == topic_raw
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    plan = json.loads((folder / 'publication.json').read_bytes())
    saved = json.loads((folder / 'input.json').read_bytes())
    assert plan['packet_sha256'] == w._json_hash(saved)
    assert w.apply(packet, explicit(packet, result)) == receipts


def test_first_input_is_canonical_and_keeps_its_exact_bytes_during_publication(brain):
    _, result = retired_fixture(brain)
    actual_topic(claim({'ranked': {9: 'first', 10: 'second'}, 'annotation': 'caf\u00e9'}))
    packet = w.prepare(1)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    before = (folder / 'input.json').read_bytes()
    decoded = json.loads(before)
    assert before == json.dumps(decoded, ensure_ascii=False, sort_keys=True).encode('utf-8')
    w.apply(packet, explicit(packet, result))
    assert (folder / 'input.json').read_bytes() == before


def test_canonical_context_keeps_existing_target_claim_integer_metadata_and_aliases(brain, monkeypatch):
    source, result = retired_fixture(brain)
    shared = ['A benign owner annotation']
    old = claim({'ranked': {9: 'first', 10: 'second'}, 'aliases': [shared, shared]})
    origin = w._source_bytes(source.read_bytes())
    old['source_revision'] = {'sha256': origin['raw_sha256'], 'text_sha256': origin['text_sha256'], 'date': origin['date']}
    context, _ = actual_topic(old)
    target = context.with_name('example.md')
    context.rename(target)
    info = w.meta(target.read_text('utf-8'))
    info['source_hashes'] = {'01-Projects/Example.md': origin['raw_sha256']}
    target.write_bytes(s.note_text(info, '# Existing Example Project\n').encode())
    source_raw = source.read_bytes()
    prior = w.meta(target.read_text('utf-8'))['wiki_claims'][0]
    calls = fake_model(monkeypatch, explicit({'sources': w.inventory()}, result))
    report = w.run(1)
    assert report['status'] == 'complete' and len(calls) == 1 and report['lint']['issues'] == []
    saved = w.meta(target.read_text('utf-8'))['wiki_claims'][0]
    assert saved == prior and set(saved['extra']['ranked']) == {9, 10}
    assert saved['extra']['aliases'][0] is saved['extra']['aliases'][1]
    assert source.read_bytes() == source_raw
