"""Fresh source failures retain safe actionable identity without changing evidence."""
import json
import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki_remediation import fake_model
from test_wiki_compact_aggregate_budget import QUOTE


def bad_source(brain, date_yaml, *, long=False):
    source = s.VAULT / '01-Projects/Actual.md'
    source.parent.mkdir(parents=True)
    raw = ('---\ndate: ' + date_yaml + '\n---\n# Actual Project\n' + QUOTE + '\n' + ('Ordinary historical body. ' * 1200 if long else '')).encode()
    source.write_bytes(raw)
    return source, raw


def no_new_topic():
    return {'pages': [], 'dispositions': [{'source': '01-Projects/Actual.md', 'part': 1, 'status': 'skipped',
            'reason': 'This synthetic source needs no new topic.', 'quote': QUOTE}]}


@pytest.mark.parametrize('date_yaml', ['"\\0bad"', '"\\uD800bad"'])
def test_bad_decoded_source_date_reports_source_before_durable_selection_and_repair_can_retry(brain, monkeypatch, date_yaml):
    source, raw = bad_source(brain, date_yaml)
    calls = fake_model(monkeypatch, no_new_topic())
    report = w.run(1)
    actual = '01-Projects/Actual.md'
    assert report['status'] == 'attention' and report['integrity_field'] == 'content'
    assert report['integrity_page'] == actual or report.get('integrity_page_sha256') == s.digest(actual.encode())
    public = w.status()['worker']
    assert public.get('integrity_page_sha256') == s.digest(actual.encode()) or public['integrity_page'] == actual
    assert source.read_bytes() == raw and not calls and not list((cfg.STATE / 'wiki/batches').glob('*/input.json'))
    assert w.status()['pending_source_parts'] == 1
    repaired = raw.replace(date_yaml.encode(), b'"2026-10-01"')
    source.write_bytes(repaired)  # Explicit synthetic owner repair, never a compiler rewrite.
    w.reset_retries()
    assert w.run(1)['status'] == 'complete' and len(calls) == 1 and source.read_bytes() == repaired


def test_attention_keeps_every_original_part_and_neighbor_pending_without_strikes(brain, monkeypatch):
    source, raw = bad_source(brain, '"\\0bad"', long=True)
    neighbor = source.with_name('Neighbor.md'); neighbor.write_bytes(('# Neighbor\n' + QUOTE).encode())
    expected = w.inventory()
    assert len(expected) == 3
    calls = fake_model(monkeypatch, no_new_topic())
    report = w.run(4)
    assert report['status'] == 'attention' and report['integrity_page'] == '01-Projects/Actual.md'
    assert w.status()['pending_source_parts'] == 3 and w.inventory() == expected and source.read_bytes() == raw and not calls
    with w.db() as conn:
        assert conn.execute('SELECT count(*) FROM batches').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0] == 0


@pytest.mark.parametrize('relative', ['01-Projects/x\x00.md', '01-Projects/x\udcff.md', '01-Projects/sk-' + 'SyntheticA' * 3 + '.md'])
def test_invalid_source_path_uses_only_a_safe_opaque_actual_path_identity(brain, relative):
    row = {'path': relative}
    with pytest.raises(w.WikiIntegrityError) as caught:
        w._validate_packet_paths({'sources': [row]})
    error = caught.value
    assert error.page == w.BASE + 'Index.md' and error.field == 'path'
    assert getattr(error, 'page_sha256', None) == s.digest(relative.encode('utf-8', errors='surrogatepass'))
    assert relative not in str(error) and 'SyntheticA' not in str(error)


@pytest.mark.parametrize('hash_value,accepted', [('a' * 64, True), ('unsafe', False)])
def test_safe_cached_source_locator_survives_public_rendering_without_cache_rewrite(brain, hash_value, accepted):
    state = cfg.STATE / 'wiki-worker-status.json'
    raw = json.dumps({'status': 'attention', 'attention_required': True, 'consecutive_failures': 0, 'retry_after': 0,
                      'reason': 'WikiIntegrityError', 'integrity_page': w.BASE + 'Index.md',
                      'integrity_field': 'path', 'integrity_page_sha256': hash_value}).encode()
    state.write_bytes(raw)
    result = w._worker_summary()
    assert result.get('integrity_page_sha256') == (hash_value if accepted else None)
    assert state.read_bytes() == raw
