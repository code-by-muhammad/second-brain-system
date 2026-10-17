"""Publication-only reference refusals must be attributed before model spending."""
import json
import os

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import note_snapshot
from test_wiki_remediation import fake_model


QUOTE = 'A synthetic prototype was demonstrated; deployment remains planned.'
BAD_CHARS = pytest.mark.parametrize('char', ['\u2028', '\u2029', '\x7f'], ids=['line-separator', 'paragraph-separator', 'del'])


def write_source(relative, body):
    source = s.VAULT / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    raw = body.encode('utf-8')
    source.write_bytes(raw)
    assert source.read_bytes() == raw and source.name == relative.split('/')[-1]
    return source, raw


def skip_result(relative):
    return {'pages': [], 'dispositions': [{'source': relative, 'part': 1, 'status': 'skipped',
            'reason': 'This synthetic administrative source needs no separate topic.', 'quote': QUOTE}]}


def observe_run(tmp_path, calls):
    try:
        report = w.run(1)
    except ValueError as exc:
        report = {'raised': type(exc).__name__, 'message': str(exc)}
    with w.db() as conn:
        batches = [dict(row) for row in conn.execute('SELECT id,status,attempts FROM batches ORDER BY id')]
        strikes = conn.execute('SELECT count(*) FROM sources WHERE attempts>0').fetchone()[0]
    observation = {'report': report, 'model_calls': len(calls), 'worker': w._read_worker_state(),
                   'batches': batches, 'source_strikes': strikes,
                   'publication_plan_count': len(list((cfg.STATE / 'wiki/batches').glob('*/publication.json')))}
    (tmp_path / 'path-preflight-observation.json').write_text(json.dumps(observation, ensure_ascii=True, indent=2), encoding='utf-8')
    return report, observation


def check_source_attention(brain, monkeypatch, tmp_path, char, short):
    relative = '03-Resources/Release' + char + 'notes.md'
    source, raw = write_source(relative, 'tiny' if short else '# Synthetic source\n' + QUOTE + '\n')
    before = note_snapshot()
    assert [row['path'] for row in w.inventory()] == [relative]
    calls = fake_model(monkeypatch, skip_result(relative))
    report, observed = observe_run(tmp_path, calls)
    assert source.read_bytes() == raw and note_snapshot() == before, observed
    assert report.get('status') == 'attention' and report.get('integrity_field') == 'path', observed
    assert report.get('consecutive_failures') == 0 and len(calls) == 0, observed
    assert report.get('integrity_page') == relative or report.get('integrity_page_sha256') == s.digest(relative.encode()), observed
    public = w.status()['worker']
    assert public.get('integrity_page') == relative or public.get('integrity_page_sha256') == s.digest(relative.encode())
    assert w.status()['pending_source_parts'] == 1 and observed['batches'] == [] and observed['source_strikes'] == 0
    assert observed['publication_plan_count'] == 0
    assert w.run(1)['status'] == 'attention' and len(calls) == 0 and note_snapshot() == before


@BAD_CHARS
@pytest.mark.parametrize('short', [False, True], ids=['substantive', 'short'])
def test_bad_source_reference_stops_before_durable_selection_or_model(brain, monkeypatch, tmp_path, char, short):
    check_source_attention(brain, monkeypatch, tmp_path, char, short)


@pytest.mark.skipif(os.name == 'nt', reason='NTFS refuses C0 filenames; actual POSIX filesystem case only.')
def test_actual_posix_c0_filename_uses_the_same_pre_model_attention(brain, monkeypatch, tmp_path):
    check_source_attention(brain, monkeypatch, tmp_path, '\x01', False)


@pytest.mark.parametrize('name', ['Release-notes.md', 'Release[#]^%notes.md'], ids=['ordinary', 'accepted-selector-display'])
def test_supported_source_path_keeps_one_model_call_and_replay_receipts(brain, monkeypatch, name):
    relative = '03-Resources/' + name
    source, raw = write_source(relative, '# Synthetic source\n' + QUOTE + '\n')
    calls = fake_model(monkeypatch, skip_result(relative))
    completed = w.run(1)
    assert completed['status'] == 'complete' and len(calls) == 1 and not completed['lint']['issues']
    assert source.read_bytes() == raw and w.status()['pending_source_parts'] == 0
    assert w.run(1)['status'] == 'idle' and len(calls) == 1
    record = s.VAULT / w.BASE / 'Sources' / (s.digest(relative.encode())[:16] + '.md')
    assert s.note_link(relative, record.relative_to(s.VAULT).as_posix()) in record.read_text(encoding='utf-8')


def test_relevant_previous_claim_bad_source_stops_before_model_and_preserves_owner_history(brain, monkeypatch, tmp_path):
    original, packet, result = fixture(brain)
    w.apply(packet, result)
    topic = s.VAULT / w.BASE / 'Projects/example.md'
    info = w.meta(topic.read_text(encoding='utf-8'))
    relative = '03-Resources/Historical\u2028notes.md'
    copied, copied_raw = write_source(relative, original.read_text(encoding='utf-8'))
    info['wiki_claims'][0]['source'] = relative
    info['sources'] = [relative]
    info['source_hashes'] = {relative: s.digest(copied_raw)}
    topic.write_bytes(s.note_text(info, '# Explicit synthetic owner attribution update; preserve it.').encode())
    # Isolate the prior-claim route: this copied original is not selected work.
    # This private queue setup is not a portable receipt or publication proof.
    with w.db() as conn:
        conn.execute('INSERT INTO sources(path,processed_hash) VALUES(?,?)', (relative + '::part1', s.digest(copied_raw)))
    another, another_raw = write_source('01-Projects/Another.md', original.read_text(encoding='utf-8'))
    result['pages'][0]['claims'][0]['source'] = '01-Projects/Another.md'
    result['dispositions'] = [{'source': '01-Projects/Another.md', 'part': 1, 'status': 'published', 'reason': '', 'quote': ''}]
    before = note_snapshot()
    calls = fake_model(monkeypatch, result)
    report, observed = observe_run(tmp_path, calls)
    assert note_snapshot() == before and copied.read_bytes() == copied_raw and another.read_bytes() == another_raw
    assert report.get('status') == 'attention' and report.get('integrity_page') == topic.relative_to(s.VAULT).as_posix(), observed
    assert report.get('claim_index') == 0 and report.get('consecutive_failures') == 0 and len(calls) == 0, observed
    assert observed['source_strikes'] == 0 and observed['publication_plan_count'] == 1
    assert w.run(1)['status'] == 'attention' and len(calls) == 0 and note_snapshot() == before
