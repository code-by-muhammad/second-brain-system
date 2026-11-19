"""Same-version setup must not report success when the installed runtime is another build."""
import hashlib
import json
import os
from pathlib import Path

import pytest

import setup_device
from upgrade_fixture import prepare
from test_portable_remediation import portable


def test_runtime_match_requires_every_app_script(tmp_path):
    release=tmp_path/'release'
    (release/'app').mkdir(parents=True)
    (release/'app'/'brain_settings.py').write_text('VERSION = "0.2.15"\n',encoding='utf-8')
    root=tmp_path/'runtime'
    (root/'app').mkdir(parents=True)
    (root/'app'/'brain_settings.py').write_text('VERSION = "0.2.15"\n',encoding='utf-8')
    digest=hashlib.sha256((release/'app'/'brain_settings.py').read_bytes()).hexdigest()
    (release/'release-manifest.json').write_text(json.dumps({
        'version':'0.2.15','sha256':{'app/brain_settings.py':digest}}),encoding='utf-8')
    assert setup_device.runtime_matches_release(root, release, '0.2.15')
    (root/'app'/'brain_settings.py').write_text('VERSION = "0.2.15"\n# older build\n',encoding='utf-8')
    assert not setup_device.runtime_matches_release(root, release, '0.2.15')
    assert setup_device.runtime_matches_release(root, release, '0.2.14')
    with pytest.raises(SystemExit, match='does not match this release'):
        setup_device.refuse_same_version_drift('0.2.15', release)


def test_check_requires_the_stable_launcher_on_posix(tmp_path):
    import check_setup
    root=tmp_path/'runtime'
    python=tmp_path/'python'
    launcher=root/'bin'/'second-brain-mcp'
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec '+str(python)+' '+str(root/'app'/'brain_client.py')+'\n',encoding='utf-8')
    launcher.chmod(0o700)
    entry={'command':str(launcher),'args':[]}
    old={'command':str(python),'args':[str(root/'app'/'brain_client.py')]}
    if os.name=='nt':
        assert check_setup.owned(old, python, root)
        assert not check_setup.owned(entry, python, root)
    else:
        assert check_setup.owned(entry, python, root)
        assert not check_setup.owned(old, python, root)


def test_same_name_app_drift_is_replaced(portable, monkeypatch):
    h=prepare(portable, monkeypatch, previous_version='0.2.14', target_version='0.2.15')
    target=next((h.root/'app').glob('*.py'))
    target.write_bytes(target.read_bytes()+b'\n# older build\n')
    result=h.updater.perform(h.root, 'v0.2.15')
    assert result['status']=='updated'
    installed=b''.join(path.read_bytes() for path in (h.root/'app').glob('*.py'))
    assert b'# older build' not in installed
    assert (h.root/'app'/'brain_cli.py').read_bytes().startswith(b'# synthetic')


def test_an_extra_app_file_still_refuses_the_snapshot(portable, monkeypatch):
    h=prepare(portable, monkeypatch, previous_version='0.2.14', target_version='0.2.15')
    (h.root/'app'/'extra_payload.py').write_text('x\n',encoding='utf-8')
    with pytest.raises(RuntimeError, match='before a hold was created') as error:
        h.updater.perform(h.root, 'v0.2.15')
    assert 'previous release payload' in str(error.value.__cause__)
