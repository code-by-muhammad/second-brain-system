"""Read-only candidate triage with synthetic local state and no real model calls."""
import hashlib
import json
import os
from pathlib import Path

import pytest
import brain_settings as cfg
import brain_store as s
import brain_wiki as w
from test_wiki import fixture
from test_wiki_remediation import explicit, fake_model
from test_wiki_saved_plan_repair import interrupted_plan, partial_index_conflict, row


@pytest.fixture
def brain(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'; vault = tmp_path / 'vault'; state = root / 'state'
    state.mkdir(parents=True); vault.mkdir(); (root / 'logs').mkdir()
    for module in (cfg, s):
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'STATE', state)
        monkeypatch.setattr(module, 'VAULT', vault)
    monkeypatch.setattr(cfg, 'ROLE', 'maintainer')
    monkeypatch.setattr(cfg, 'DEVICE_ID', 'test-host')
    return vault, state


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def observe(*args): pass


def caught():
    try:
        return w.run(), None
    except Exception as error:
        return None, type(error).__name__


def damage_input(folder, corruption):
    path = folder / 'input.json'
    if corruption == 'missing': path.unlink()
    elif corruption == 'scalar': path.write_bytes(b'42')
    elif corruption == 'invalid-json': path.write_bytes(b'{bad synthetic JSON')
    elif corruption == 'shape': path.write_bytes(b'{"sources":[]}')
    elif corruption == 'oversize': path.write_bytes(b'x' * 2_000_001)
    else: raise AssertionError(corruption)


def dynamic_skip_model(monkeypatch):
    """Real validation/publication paths, deterministic evidence-backed fake model."""
    calls = []
    monkeypatch.setattr(w.shutil, 'which', lambda name: 'synthetic-codex')
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.output = Path(command[command.index('-o') + 1])
        def communicate(self, model_input, timeout):
            packet = json.loads(model_input.split('INPUT_JSON:\n', 1)[1])
            calls.append([part['job_key'] for part in packet['sources']])
            result = {'pages': [], 'dispositions': [
                {'source': part['path'], 'part': part['part'], 'status': 'skipped',
                 'reason': 'Synthetic administrative evidence explicitly reviewed without a reusable topic.',
                 'quote': part['text'].strip()[:120]} for part in packet['sources']]}
            self.output.write_text(json.dumps(result), encoding='utf-8')
    monkeypatch.setattr(w.subprocess, 'Popen', Process)
    return calls


@pytest.mark.parametrize('corruption', ['missing', 'scalar', 'oversize', 'output', 'plan'])
def test_r77_generic_recovery_attention_identifies_exact_affected_batch(brain, monkeypatch, corruption):
    source, packet, result, folder, _ = interrupted_plan(brain, monkeypatch)
    if corruption in ('missing', 'scalar', 'oversize'):
        damage_input(folder, corruption)
    elif corruption == 'output': (folder / 'output.json').write_bytes(b'42')
    else: (folder / 'publication.json').write_bytes(b'42')
    w.reset_retries()
    private = snapshot(folder); notes = snapshot(s.VAULT)
    calls = dynamic_skip_model(monkeypatch)
    report, error = caught(); state = w._read_worker_state(); status = w.status()
    repeated, repeated_error = caught()
    observe('R77-' + corruption, {
        'affected_batch_id': packet['batch_id'], 'exception': error,
        'report': report, 'private_worker_state': state, 'public_worker': status['worker'],
        'repeated_attention': repeated, 'repeated_exception': repeated_error,
        'recovery_issues': status['recovery_issues'], 'model_calls': len(calls),
        'private_artifacts_preserved': snapshot(folder) == private,
        'vault_preserved': snapshot(s.VAULT) == notes})
    assert not calls and snapshot(folder) == private and snapshot(s.VAULT) == notes
    assert state['attention_required'] is True
    assert state['integrity_field'] == ('packet' if corruption == 'oversize' else 'recovery')
    assert state.get('batch_id') == packet['batch_id']
    assert status['worker'].get('batch_id') == packet['batch_id']
    assert repeated.get('batch_id') == packet['batch_id']


def stale_unused(brain):
    source, packet, result = fixture(brain)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    source.write_bytes(source.read_bytes() + b'\nSynthetic changed source evidence.\n')
    assert w._resume_packet() is None
    assert row(packet)['status'] == 'stale'
    assert not (folder / 'publication.json').exists()
    assert not w._saved_publication_started(packet, folder / 'publication.json')
    unrelated = source.with_name('Unrelated.md')
    unrelated.write_bytes(b'# Unrelated work\nAn unrelated administrative source now requires explicit evidence-backed review.\n')
    return source, packet, result, folder, unrelated


@pytest.mark.parametrize('corruption', ['missing', 'scalar', 'invalid-json', 'shape'])
def test_r78_unused_stale_without_plan_does_not_block_eligible_work(brain, monkeypatch, corruption):
    source, packet, result, folder, unrelated = stale_unused(brain)
    damage_input(folder, corruption)
    notes = snapshot(s.VAULT); private = snapshot(folder)
    calls = dynamic_skip_model(monkeypatch); w.reset_retries()
    report, error = caught(); state = w._read_worker_state()
    observe('R78-stale-' + corruption, {
        'stale_batch_id': packet['batch_id'], 'stale_batch_status': row(packet)['status'],
        'publication_plan_exists': (folder / 'publication.json').exists(),
        'exception': error, 'report': report, 'worker': state, 'model_calls': len(calls),
        'eligible_parts_after': w.status()['ready_source_parts'],
        'original_sources_preserved': all(s.digest((s.VAULT / path).read_bytes()) == digest for path, digest in notes.items()),
        'private_artifacts_preserved': snapshot(folder) == private})
    assert snapshot(folder) == private
    assert all(s.digest((s.VAULT / path).read_bytes()) == digest for path, digest in notes.items())
    assert error is None and report is not None and report['status'] == 'complete'
    assert len(calls) == 1 and '01-Projects/Unrelated.md::part1' in calls[0]


def test_control_valid_stale_without_plan_skips_and_processes_new_sources(brain, monkeypatch):
    source, packet, result, folder, unrelated = stale_unused(brain)
    private = snapshot(folder); originals = snapshot(s.VAULT)
    calls = dynamic_skip_model(monkeypatch)
    report, error = caught()
    observe('control-valid-stale', {'report': report, 'exception': error, 'model_calls': len(calls),
        'old_private_preserved': snapshot(folder) == private, 'old_status': row(packet)['status']})
    assert error is None and report['status'] == 'complete' and len(calls) == 1
    assert '01-Projects/Unrelated.md::part1' in calls[0] and snapshot(folder) == private
    assert all(s.digest((s.VAULT / path).read_bytes()) == digest for path, digest in originals.items())


@pytest.mark.parametrize('corruption', ['missing', 'scalar'])
def test_control_actual_partial_corrupt_input_retains_attention_and_holds_queue(brain, monkeypatch, corruption):
    source, packet, old_calls, index, before, owner, plan = partial_index_conflict(brain, monkeypatch)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    assert w._saved_publication_started(packet, folder / 'publication.json')
    damage_input(folder, corruption)
    unrelated = source.with_name('Unrelated.md'); unrelated.write_bytes(b'# Unrelated\nThis unrelated source must wait for verified partial recovery.\n')
    private = snapshot(folder); notes = snapshot(s.VAULT)
    calls = dynamic_skip_model(monkeypatch); w.reset_retries()
    report, error = caught()
    observe('control-partial-' + corruption, {'report': report, 'exception': error,
        'batch_id': packet['batch_id'], 'worker': w._read_worker_state(),
        'new_model_calls': len(calls), 'old_model_calls': len(old_calls),
        'private_preserved': snapshot(folder) == private, 'vault_preserved': snapshot(s.VAULT) == notes})
    assert error == 'WikiIntegrityError' and not calls
    assert w._read_worker_state()['attention_required'] is True
    assert snapshot(folder) == private and snapshot(s.VAULT) == notes


def complete_unfinalized(brain, monkeypatch):
    source, packet, result = fixture(brain); result = explicit(packet, result)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']
    original = w._atomic_json
    def stop(path, value):
        if path == folder / 'journal.json' and value.get('state') == 'complete':
            raise OSError('Synthetic post-SQLite pre-journal-finalization interruption')
        return original(path, value)
    with monkeypatch.context() as context:
        context.setattr(w, '_atomic_json', stop)
        with pytest.raises(OSError): w.apply(packet, result)
    assert row(packet)['status'] == 'complete'
    assert json.loads((folder / 'journal.json').read_text())['state'] == 'published'
    assert (folder / 'publication.json').exists()
    assert w._saved_publication_started(packet, folder / 'publication.json')
    return source, packet, result, folder


def test_control_complete_unfinalized_with_missing_input_retains_publication_recovery(brain, monkeypatch):
    source, packet, result, folder = complete_unfinalized(brain, monkeypatch)
    damage_input(folder, 'missing')
    source.with_name('Unrelated.md').write_bytes(b'# Unrelated\nA separate historical requirement needs review after verified finalization.\n')
    private = snapshot(folder); notes = snapshot(s.VAULT)
    calls = dynamic_skip_model(monkeypatch); w.reset_retries()
    report, error = caught()
    observe('control-complete-unfinalized-missing', {'report': report, 'exception': error,
        'batch_id': packet['batch_id'], 'database_status': row(packet)['status'],
        'journal_state': json.loads((folder / 'journal.json').read_text())['state'],
        'worker': w._read_worker_state(), 'new_model_calls': len(calls),
        'private_preserved': snapshot(folder) == private, 'vault_preserved': snapshot(s.VAULT) == notes})
    assert error == 'WikiIntegrityError' and not calls
    assert w._read_worker_state()['attention_required'] is True
    assert snapshot(folder) == private and snapshot(s.VAULT) == notes


def test_control_complete_unfinalized_valid_input_finishes_without_paid_call(brain, monkeypatch):
    source, packet, result, folder = complete_unfinalized(brain, monkeypatch)
    notes = snapshot(s.VAULT); calls = dynamic_skip_model(monkeypatch)
    report, error = caught()
    observe('control-complete-unfinalized-valid', {'report': report, 'exception': error,
        'new_model_calls': len(calls), 'journal_state': json.loads((folder / 'journal.json').read_text())['state']})
    assert error is None and report['status'] == 'complete' and not calls
    assert json.loads((folder / 'journal.json').read_text())['state'] == 'complete'
    assert snapshot(s.VAULT) == notes


def test_control_finalized_complete_missing_input_does_not_block_new_work(brain, monkeypatch):
    source, packet, result = fixture(brain); w.apply(packet, explicit(packet, result))
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']; damage_input(folder, 'missing')
    source.with_name('Unrelated.md').write_bytes(b'# Unrelated\nA separate administrative requirement needs evidence-backed review.\n')
    private = snapshot(folder); calls = dynamic_skip_model(monkeypatch)
    report, error = caught()
    observe('control-finalized-complete', {'report': report, 'exception': error,
        'new_model_calls': len(calls), 'old_private_preserved': snapshot(folder) == private})
    assert error is None and report['status'] == 'complete' and len(calls) == 1
    assert snapshot(folder) == private


def test_control_pending_no_plan_corrupt_input_does_not_skip_authoritative_work(brain, monkeypatch):
    source, packet, result = fixture(brain)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']; damage_input(folder, 'scalar')
    private = snapshot(folder); notes = snapshot(s.VAULT); calls = dynamic_skip_model(monkeypatch)
    report, error = caught()
    observe('control-pending-no-plan', {'report': report, 'exception': error,
        'new_model_calls': len(calls), 'worker': w._read_worker_state(), 'database_status': row(packet)['status']})
    assert error == 'WikiIntegrityError' and not calls and row(packet)['status'] == 'pending'
    assert snapshot(folder) == private and snapshot(s.VAULT) == notes


def test_control_error_no_plan_corrupt_input_already_skips_and_reprepares(brain, monkeypatch):
    source, packet, result = fixture(brain)
    folder = cfg.STATE / 'wiki/batches' / packet['batch_id']; damage_input(folder, 'scalar')
    with w.db() as conn: conn.execute("UPDATE batches SET status='error' WHERE id=?", (packet['batch_id'],))
    calls = dynamic_skip_model(monkeypatch)
    report, error = caught()
    observe('control-error-no-plan', {'report': report, 'exception': error, 'new_model_calls': len(calls)})
    assert error is None and report['status'] == 'complete' and len(calls) == 1


@pytest.mark.parametrize('mode',['stale-reverted','pending-control'])
def test_retired_stale_generation_preserves_input(brain,mode):
    source,packet,result=fixture(brain)
    folder=cfg.STATE/'wiki/batches'/packet['batch_id']; saved=folder/'input.json'
    if mode=='stale-reverted':
        original=source.read_bytes(); source.write_bytes(original+b'\nSynthetic temporary source edit.\n')
        assert w._resume_packet() is None
        with w.db() as conn: assert conn.execute('SELECT status FROM batches WHERE id=?',(packet['batch_id'],)).fetchone()[0]=='stale'
        source.write_bytes(original); saved.write_bytes(b'42')
    before=saved.read_bytes(); fresh=w.prepare()
    observation={'mode':mode,'previous_batch':packet['batch_id'],'fresh_batch':fresh['batch_id'],'old_input_preserved':saved.read_bytes()==before}
    if mode=='stale-reverted': assert fresh['batch_id']!=packet['batch_id'] and saved.read_bytes()==before
    else: assert fresh['batch_id']==packet['batch_id'] and saved.read_bytes()==before

@pytest.mark.parametrize('bad_id',['../outside','A'*24,'ghp_'+'SyntheticMarkerAlphaBetaGammaDelta'])
def test_invalid_database_batch_locator_is_withheld_before_path_access(brain,monkeypatch,bad_id):
    source,packet,result=fixture(brain);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];before=snapshot(folder);notes=snapshot(s.VAULT)
    with w.db() as conn:conn.execute('UPDATE batches SET id=? WHERE id=?',(bad_id,packet['batch_id']))
    calls=dynamic_skip_model(monkeypatch)
    with pytest.raises(w.WikiIntegrityError):w.run()
    state=w._read_worker_state();public=w.status()['worker'];repeated=w.run()
    assert state['attention_required'] and not state.get('batch_id') and not public.get('batch_id') and not repeated.get('batch_id')
    assert bad_id not in json.dumps([state,public,repeated])
    assert not calls and snapshot(folder)==before and snapshot(s.VAULT)==notes

def test_corrupt_packet_locator_cannot_replace_known_database_locator(brain,monkeypatch):
    source,packet,result,folder,_=interrupted_plan(brain,monkeypatch)
    value=json.loads((folder/'input.json').read_text());value['batch_id']='ghp_'+'SyntheticMarkerAlphaBetaGammaDelta'
    w._atomic_json(folder/'input.json',value);w.reset_retries();before=snapshot(folder);calls=dynamic_skip_model(monkeypatch)
    with pytest.raises(w.WikiIntegrityError):w.run()
    assert w._read_worker_state()['batch_id']==packet['batch_id'] and w.status()['worker']['batch_id']==packet['batch_id']
    assert not calls and snapshot(folder)==before

def test_reverted_stale_source_runs_new_generation_without_overwriting_old_input(brain,monkeypatch):
    source,packet,result=fixture(brain);original=source.read_bytes();source.write_bytes(original+b'\nTemporary owner source change.\n')
    assert w._resume_packet() is None and row(packet)['status']=='stale'
    source.write_bytes(original);folder=cfg.STATE/'wiki/batches'/packet['batch_id'];(folder/'input.json').write_bytes(b'42');before=snapshot(folder)
    calls=dynamic_skip_model(monkeypatch);report=w.run()
    assert report['status']=='complete' and report['batch_id']!=packet['batch_id'] and len(calls)==1
    assert snapshot(folder)==before and source.read_bytes()==original and row(packet)['status']=='stale'

def test_unused_source_context_attention_retains_valid_batch_locator(brain,monkeypatch):
    _,packet,result=fixture(brain);packet['sources'][0]['title']+=' ghp_'+'SyntheticMarkerAlphaBetaGammaDelta'
    folder=cfg.STATE/'wiki/batches'/packet['batch_id'];w._atomic_json(folder/'input.json',packet)
    calls=dynamic_skip_model(monkeypatch);report=w.run()
    assert report['status']=='attention' and report['integrity_field']=='content' and report['batch_id']==packet['batch_id']
    assert w.run()['batch_id']==packet['batch_id'] and not calls
