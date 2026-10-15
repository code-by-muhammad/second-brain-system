"""Evidence-first resource, diagnostic and serialization boundary regressions.

All vaults are disposable, all model calls are fake, and credentials are synthetic.
"""
import copy
import json
from pathlib import Path

import pytest

import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_prepare_integrity import existing_topic, note_snapshot
from test_wiki_remediation import explicit, fake_model


def worker_state():
    return json.loads((cfg.STATE/'wiki-worker-status.json').read_text(encoding='utf-8'))


def run_caught():
    error = None
    report = None
    try:
        report = w.run()
    except Exception as exc:
        error = exc
    return error, report, worker_state()


def test_credential_like_catalog_refusal_has_safe_actionable_lint_locator(brain, monkeypatch):
    vault, _ = brain
    relative = w.BASE+'Projects/sk-hynix-supplier-evaluation.md'
    target = vault/relative
    target.parent.mkdir(parents=True)
    target.write_bytes(s.note_text({'wiki_kind':'Projects', 'title':'Supplier evaluation', 'wiki_claims':[]},
                                  '# Preserved legacy topic.').encode('utf-8'))
    before = note_snapshot()
    calls = fake_model(monkeypatch, {'pages':[]})
    report = w.run()
    issues = w.lint()['issues']
    matching = [item for item in issues if item.get('field')=='path']
    assert report['status']=='attention' and len(calls)==0 and before==note_snapshot()
    assert len(matching)==1
    assert matching[0]['page']==w.BASE+'Index.md'
    assert matching[0]['path_sha256']==s.digest(relative.encode('utf-8'))
    assert relative not in json.dumps(issues)
    w.reset_retries()
    assert w.run()['status']=='attention' and len(calls)==0 and before==note_snapshot()


def test_unused_filtered_source_packet_is_superseded_and_unrelated_source_progresses(brain, monkeypatch):
    source, old, result = fixture(brain)
    renamed = source.with_name('SK-Hynix-supplier-evaluation.md')
    source.rename(renamed)
    row = old['sources'][0]
    row['path'] = renamed.relative_to(s.VAULT).as_posix()
    row['job_key'] = row['path']+'::part1'
    folder = cfg.STATE/'wiki/batches'/old['batch_id']
    w._atomic_json(folder/'input.json', old)
    # Deterministically exercise the oldest resume candidate, regardless of
    # same-second timestamps and lexicographic batch identity ordering.
    with w.db() as conn:
        conn.execute('UPDATE batches SET updated=? WHERE id=?', ('2000-01-01T00:00:00+00:00', old['batch_id']))
    original_input = (folder/'input.json').read_bytes()
    original_source = renamed.read_bytes()
    unrelated = renamed.with_name('Unrelated.md')
    quote = 'A separate historical requirement needs review.'
    unrelated.write_bytes(('# Unrelated project\n'+quote+'\n').encode('utf-8'))
    unrelated_original = unrelated.read_bytes()
    fresh = w.prepare()
    assert [item['path'] for item in fresh['sources']]==[unrelated.relative_to(s.VAULT).as_posix()]
    result = copy.deepcopy(result)
    result['pages'][0].update(slug='unrelated', title='Unrelated project')
    result['pages'][0]['claims'][0].update(source=unrelated.relative_to(s.VAULT).as_posix(), quote=quote,
                                        text='A separate requirement was documented.')
    calls = fake_model(monkeypatch, explicit(fresh, result))
    report = w.run()
    with w.db() as conn:
        retired = conn.execute('SELECT status FROM batches WHERE id=?', (old['batch_id'],)).fetchone()[0]
    assert retired=='superseded'
    assert report['status']=='complete' and report['batch_id']==fresh['batch_id'] and len(calls)==1
    assert (folder/'input.json').read_bytes()==original_input
    assert renamed.read_bytes()==original_source and unrelated.read_bytes()==unrelated_original
    assert not (s.VAULT/w.BASE/'Projects/sk-hynix-supplier-evaluation.md').exists()
    assert w.run()['status']=='idle' and len(calls)==1


def test_unused_legacy_credential_like_target_plan_requires_attention_without_strikes(brain, monkeypatch):
    _, _, result = fixture(brain)
    result = copy.deepcopy(result)
    result['pages'][0]['slug'] = 'sk-hynix-supplier-evaluation'
    original_clean = s.clean_text

    def legacy_clean(value, *args, **kwargs):
        if isinstance(value, str) and 'sk-hynix-supplier-evaluation' in value.lower():
            return value
        return original_clean(value, *args, **kwargs)

    monkeypatch.setattr(s, 'clean_text', legacy_clean)
    packet = w.prepare()
    calls = fake_model(monkeypatch, explicit(packet, result))
    original_write = s._write

    def stop_before_first_topic(path, *args, **kwargs):
        if args and args[1]=='wiki_compile':
            raise OSError('Synthetic pre-publication interruption')
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(s, '_write', stop_before_first_topic)
    with pytest.raises(OSError):
        w.run()
    folder = cfg.STATE/'wiki/batches'/packet['batch_id']
    saved = {name:(folder/name).read_bytes() for name in ('input.json','output.json','publication.json')}
    assert not w._saved_publication_started(packet, folder/'publication.json')
    monkeypatch.setattr(s, '_write', original_write)
    monkeypatch.setattr(s, 'clean_text', original_clean)
    w.reset_retries()
    before = note_snapshot()
    error, _, state = run_caught()
    assert error is None or isinstance(error, w.WikiIntegrityError)
    assert state['attention_required'] and state['consecutive_failures']==0
    assert state['integrity_field']=='path'
    assert len(calls)==1 and before==note_snapshot()
    assert all((folder/name).read_bytes()==raw for name,raw in saved.items())
    assert w.run()['status']=='attention' and len(calls)==1


@pytest.mark.parametrize('aliases', [100, 200])
def test_json_shared_scalar_aliases_are_measured_with_bounded_character_work(monkeypatch, aliases):
    metadata = w.meta('---\nbase: &a '+('S'*150)+'\nvalues: ['+','.join('*a' for _ in range(aliases))+']\n---\n# Actual YAML aliases.')
    values = metadata['values']
    assert all(item is values[0] for item in values)
    visits = 0

    def count_ord(char):
        nonlocal visits
        visits += 1
        return ord(char)

    monkeypatch.setattr(w, 'ord', count_ord, raising=False)
    with pytest.raises(ValueError):
        w._bounded_json(values, limit=2000)
    assert visits<=8000, 'Repeated aliases must not rescan identical scalar bytes before aggregate bounds.'


def test_json_shared_aliases_below_budget_preserve_exact_encoder_result():
    metadata = w.meta('---\nshared: &a [one, two]\nvalues: [*a, *a]\n---\n# Shared acyclic container.')
    values = metadata['values']
    assert values[0] is values[1]
    assert w._bounded_json(values, limit=100, ensure_ascii=False)==json.dumps(values, ensure_ascii=False)


@pytest.mark.parametrize('kind', ['cycle', 'unsupported-key', 'unsupported-value'])
def test_json_oversize_does_not_mask_invalid_graph_structure_or_types(kind):
    if kind=='cycle':
        invalid = []
        invalid.append(invalid)
        error = ValueError
    elif kind=='unsupported-key':
        invalid = {b'not-json':'preserve invalid key'}
        error = TypeError
    else:
        invalid = object()
        error = TypeError
    # The stack visits this oversized scalar first, then the invalid node.
    with pytest.raises(error) as caught:
        w._bounded_json([invalid, 'S'*3000], limit=2000)
    if kind=='cycle':
        assert 'Circular' in str(caught.value)


@pytest.mark.parametrize('stage', ['prepare', 'saved-packet'])
def test_large_full_catalog_limit_identifies_packet_operation_not_claim_metadata(brain, monkeypatch, stage):
    _, packet, _ = fixture(brain)
    pages = [{'path':w.BASE+f'Projects/catalog-{i:05d}.md', 'sha256':'a'*64,
              'meta':{'wiki_kind':'Projects', 'title':f'A synthetic catalog title for the scale boundary number {i:05d}',
                      'wiki_claims':[]}} for i in range(9000)]
    catalog = [{'path':page['path'], 'title':page['meta']['title'], 'sha256':page['sha256'], 'claim_count':0}
               for page in pages]
    assert len(json.dumps({'version':2,'sources':w.inventory(),'current_pages':[],'catalog':catalog},
                          sort_keys=True, default=str))>2_000_000
    assert len(json.dumps(catalog[:160], ensure_ascii=False))<40_000
    before = note_snapshot()
    if stage=='prepare':
        monkeypatch.setattr(w, '_checked_pages', lambda:pages)
        operation = w.prepare
    else:
        packet = {**packet, 'catalog':catalog}
        operation = lambda:w._validate_packet_context(packet)
    with pytest.raises(w.WikiIntegrityError) as caught:
        operation()
    assert caught.value.field=='packet'
    assert caught.value.page==w.BASE+'Index.md' and caught.value.claim_index is None
    assert before==note_snapshot()


@pytest.mark.parametrize('field,invalid', [
    ('related', 'ghp_'+'syntheticmarker'*4),
    ('related', 'Z'*10000),
    ('related', '../outside.md'),
    ('related', '01-Projects/invalid\x00.md'),
    ('related', '01-Projects/invalid\ud800.md'),
    ('source_hashes', '01-Projects/ghp_'+'syntheticmarker'*4+'.md'),
    ('source_hashes', 'Z'*10000+'.md'),
    ('source_hashes', '../outside.md'),
    ('source_hashes', '01-Projects/invalid\x00.md'),
    ('source_hashes', '01-Projects/invalid\ud800.md'),
], ids=['related-secret','related-overlong','related-traversal','related-nul','related-surrogate',
        'hash-key-secret','hash-key-overlong','hash-key-traversal','hash-key-nul','hash-key-surrogate'])
def test_lint_invalid_path_metadata_uses_generic_field_index_without_raw_echo(brain, field, invalid):
    _, _, topic, _ = existing_topic(brain)
    info = w.meta(topic.read_text(encoding='utf-8'))
    info[field] = [invalid] if field=='related' else {invalid:'invalid-hash'}
    topic.write_bytes(s.note_text(info, '# Preserve unchecked owner metadata.').encode('utf-8'))
    before = note_snapshot()
    report = w.lint()
    serialized = json.dumps(report)
    assert json.dumps(invalid)[1:-1] not in serialized
    assert before==note_snapshot()
    issues = [issue for issue in report['issues'] if issue.get('field')==field]
    assert issues and issues[0]['related_index' if field=='related' else 'source_index']==0


@pytest.mark.parametrize('field', ['related', 'source_hashes'])
def test_lint_safe_missing_paths_remain_actionable(brain, field):
    _, _, topic, _ = existing_topic(brain)
    info = w.meta(topic.read_text(encoding='utf-8'))
    missing = w.BASE+'Projects/missing.md' if field=='related' else '01-Projects/Owner evidence.md'
    info[field] = [missing] if field=='related' else {missing:'invalid-hash'}
    topic.write_bytes(s.note_text(info, '# Preserve safe owner metadata.').encode('utf-8'))
    before = note_snapshot()
    report = w.lint()
    assert any(issue.get('target' if field=='related' else 'source')==missing for issue in report['issues'])
    assert before==note_snapshot()


def deep_source_record(source):
    relative = source.relative_to(s.VAULT).as_posix()
    target = s.VAULT/w.BASE/'Sources'/(s.digest(relative.encode('utf-8'))[:16]+'.md')
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = ('---\nsources: ['+relative+']\nlatest_source_part: 0\nwiki_pages: []\nowner_note: '
           +'['*350+'0'+']'*350+'\n---\n# Preserve nested owner metadata.\n').encode('utf-8')
    target.write_bytes(raw)
    assert 'owner_note' in w.meta(raw.decode('utf-8'))
    return target


@pytest.mark.parametrize('mode', ['worker', 'relink'])
def test_deep_source_record_serialization_failure_is_controlled_and_preserves_bytes(brain, monkeypatch, mode):
    if mode=='worker':
        _, source, _, result = existing_topic(brain)
        target = deep_source_record(source)
        packet = w.prepare()
        calls = fake_model(monkeypatch, explicit(packet, result))
        before = note_snapshot()
        error, _, state = run_caught()
        assert state['attention_required'] and state['consecutive_failures']==0
        assert state['integrity_page']==target.relative_to(s.VAULT).as_posix()
        assert state['integrity_field']=='serialization'
        assert len(calls)==1
        assert w.run()['status']=='attention' and len(calls)==1
    else:
        source, packet, result = fixture(brain)
        w.apply(packet, result)
        target = deep_source_record(source)
        before = note_snapshot()
        with pytest.raises(w.WikiIntegrityError) as caught:
            w.rebuild_source_links()
        error = caught.value
    assert isinstance(error, w.WikiIntegrityError)
    assert error.page==target.relative_to(s.VAULT).as_posix() and error.field=='serialization'
    assert before==note_snapshot()


def test_relink_late_unserializable_record_preflights_all_records_before_any_write(brain, monkeypatch):
    source, another, _, result = existing_topic(brain)
    packet = w.prepare()
    w.apply(packet, result)
    first = s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode('utf-8'))[:16]+'.md')
    info = w.meta(first.read_text(encoding='utf-8'))
    info['wiki_pages'] = []
    first.write_bytes(s.note_text(info, '# First source navigation needs repair.').encode('utf-8'))
    last = deep_source_record(another)
    directory = s.VAULT/w.BASE/'Sources'
    original_glob = Path.glob

    def ordered_glob(path, pattern, *args, **kwargs):
        if path==directory and pattern=='*.md':
            return iter([first, last])
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, 'glob', ordered_glob)
    before = note_snapshot()
    with pytest.raises(w.WikiIntegrityError) as caught:
        w.rebuild_source_links()
    assert caught.value.page==last.relative_to(s.VAULT).as_posix() and caught.value.field=='serialization'
    assert before==note_snapshot()


def test_source_record_serializable_custom_metadata_and_owner_body_are_preserved(brain):
    source, packet, result = fixture(brain)
    original_source = source.read_bytes()
    w.apply(packet, result)
    target = s.VAULT/w.BASE/'Sources'/(s.digest(source.relative_to(s.VAULT).as_posix().encode('utf-8'))[:16]+'.md')
    info = w.meta(target.read_text(encoding='utf-8'))
    info.update(wiki_pages=[], owner_note={'nested':['Preserve this evidence', {'value':42}]})
    target.write_bytes(s.note_text(info, '# Source\n\n## Owner history\nPreserve original owner history.').encode('utf-8'))
    report = w.rebuild_source_links()
    assert report['source_records_updated']==1
    updated = target.read_text(encoding='utf-8')
    assert w.meta(updated)['owner_note']==info['owner_note']
    assert 'Preserve original owner history.' in updated
    assert source.read_bytes()==original_source
