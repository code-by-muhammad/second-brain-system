"""Only the prospective local bool-query failure seam is simulated; Git repos are real."""
import json
import os
import subprocess

import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git
from test_sync_windows_checkout_length import repo, incoming, relative_for, snapshot, CAPTURE, CAPTURE_RAW

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows-specific local checkout configuration.')
MARKER = 'SYNTHETIC_PRIVATE_CONFIG_OUTPUT'


@pytest.mark.parametrize('mode', ['timeout', 'exit-error', 'malformed-bool'])
def test_longpath_config_failures_refuse_checkout_without_exposing_output(brain, tmp_path, monkeypatch, isolated_git, mode):
    vault, state, remote = repo(brain, tmp_path, isolated_git, 'false')
    relative = relative_for(vault, 'file', 260)
    incoming(vault, remote, relative)
    before = snapshot(vault, remote)
    shipping = g._run_git
    commands, queries = [], []
    def observe(command, **kwargs):
        commands.append(list(command))
        if list(command)[4:] == ['config', '--bool', '--get', 'core.longpaths']:
            queries.append({'timeout': kwargs.get('timeout'), 'capture_output': kwargs.get('capture_output'), 'text': kwargs.get('text'), 'encoding': kwargs.get('encoding')})
            if mode == 'timeout': raise subprocess.TimeoutExpired(command, kwargs.get('timeout'), output=MARKER, stderr=MARKER)
            return subprocess.CompletedProcess(command, 128 if mode == 'exit-error' else 0, MARKER, MARKER)
        return shipping(command, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(g, '_run_git', observe)
        try:
            result, error = g.sync(), None
        except g.GitSyncError as exc:
            result, error = None, str(exc)
    after = snapshot(vault, remote)
    data = {'mode': mode, 'result': result, 'error': error, 'queries': queries, 'commands': commands, 'before': before, 'after': after}
    (tmp_path/'r166-checkout-observation.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    assert error == 'Windows Git long-path setting could not be verified; local changes preserved.' and result is None, data
    assert MARKER not in error and before == after and (vault/CAPTURE).read_bytes() == CAPTURE_RAW, data
    assert queries == [{'timeout': 90, 'capture_output': True, 'text': True, 'encoding': 'utf-8'}], data
    assert not any(command[4] in ('merge', 'merge-tree', 'push', 'add', 'commit', 'reset') for command in commands), data
