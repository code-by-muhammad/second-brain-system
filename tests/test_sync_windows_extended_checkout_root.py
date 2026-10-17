"""A synthetic ordinary-drive vault may be passed with the supported extended prefix."""
import json
import os
from pathlib import Path

import pytest
import brain_git as g
from test_sync_fsmonitor_policy import isolated_git
from test_sync_windows_checkout_length import repo, incoming, relative_for, snapshot, units, INCOMING, CAPTURE, CAPTURE_RAW

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows extended drive root.')


def test_extended_drive_root_does_not_invent_a_checkout_length_failure(brain, tmp_path, isolated_git):
    vault, state, remote = repo(brain, tmp_path, isolated_git, 'false')
    relative = relative_for(vault, 'file', 256)
    incoming(vault, remote, relative)
    extended = Path('\\\\?\\'+str(vault))
    before = snapshot(vault, remote)
    try:
        result, error = g.sync(vault=extended), None
    except g.GitSyncError as exc:
        result, error = None, str(exc)
    after = snapshot(vault, remote)
    data = {'ordinary_file_units': units(vault/relative), 'extended_literal_file_units': units(extended/relative), 'result': result, 'error': error, 'before': before, 'after': after}
    (tmp_path/'r166-checkout-observation.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    assert error is None and result == 'synced', data
    assert (vault/relative).read_bytes() == INCOMING and (vault/CAPTURE).read_bytes() == CAPTURE_RAW, data
    assert after['head'] == after['remote'] and after['status'] == '' and before['config_sha256'] == after['config_sha256'], data
