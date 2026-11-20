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
    nested=root/'app'/'nested';nested.mkdir()
    extra=nested/'extra.bin';extra.write_bytes(b'extra')
    assert not setup_device.runtime_matches_release(root, release, '0.2.15')
    extra.unlink();nested.rmdir()
    cache=root/'app'/'__pycache__';cache.mkdir()
    (cache/'brain_settings.cpython-312.pyc').write_bytes(b'cache')
    assert setup_device.runtime_matches_release(root, release, '0.2.15')
    (root/'app'/'brain_settings.py').write_text('VERSION = "0.2.15"\n# older build\n',encoding='utf-8')
    assert not setup_device.runtime_matches_release(root, release, '0.2.15')
    assert not setup_device.runtime_matches_release(root, release, '0.2.14')
    with pytest.raises(SystemExit, match='does not match this release'):
        setup_device.refuse_same_version_drift('0.2.15', release)


def test_check_requires_the_stable_launcher_on_posix(tmp_path):
    import check_setup
    from brain_connection import launcher_bytes
    root=tmp_path/'runtime'
    python=tmp_path/'python'
    launcher=root/'bin'/'second-brain-mcp'
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(launcher_bytes(root,python))
    launcher.chmod(0o700)
    entry={'command':str(launcher),'args':[]}
    old={'command':str(python),'args':[str(root/'app'/'brain_client.py')]}
    if os.name=='nt':
        assert check_setup.owned(old, python, root)
        assert not check_setup.owned(entry, python, root)
    else:
        assert check_setup.owned(entry, python, root)
        assert not check_setup.owned(old, python, root)


def test_same_name_app_drift_refuses_upgrade(portable, monkeypatch):
    h=prepare(portable, monkeypatch, previous_version='0.2.14', target_version='0.2.16')
    target=next((h.root/'app').glob('*.py'))
    drift=target.read_bytes()+b'\n# unsupported build\n'
    target.write_bytes(drift)
    with pytest.raises(ValueError,match='every verified predecessor'):
        h.updater.perform(h.root,'v0.2.16')
    assert target.read_bytes()==drift
    assert not any(row.get('action')=='hold' or row['kind']=='provision' for row in h.events)


def test_an_extra_app_file_still_refuses_the_snapshot(portable, monkeypatch):
    h=prepare(portable, monkeypatch, previous_version='0.2.14', target_version='0.2.16')
    (h.root/'app'/'extra_payload.py').write_text('x\n',encoding='utf-8')
    with pytest.raises(ValueError,match='every verified predecessor'):
        h.updater.perform(h.root,'v0.2.16')


@pytest.mark.parametrize('rollback',[False,True])
def test_catalog_is_independent_of_retained_checkout(portable,monkeypatch,rollback):
    h=prepare(portable,monkeypatch,previous_version='0.2.14',target_version='0.2.16')
    verify=h.updater.verify
    def dirty(path,**options):
        if path==Path(h.device['release_root']):
            raise ValueError('Synthetic retained checkout is dirty.')
        return verify(path,**options)
    monkeypatch.setattr(h.updater,'verify',dirty)
    before={p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}
    if rollback:
        h.fail_health=True
        with pytest.raises(RuntimeError,match='previous runtime health verified'):
            h.updater.perform(h.root,'v0.2.16')
        result=json.loads((h.root/'state/upgrade-status.json').read_bytes())
        assert {p.name:p.read_bytes() for p in (h.root/'app').glob('*.py')}==before
        assert json.loads((h.root/'device.json').read_bytes())['python']==h.device['python']
    else:
        result=h.updater.perform(h.root,'v0.2.16')
    sealed=json.loads((Path(result['snapshot'])/'snapshot.json').read_bytes())
    assert sealed['predecessor']['source']=='candidate catalog'


@pytest.mark.parametrize('commit,manifest_digest',[
    ('43a36d9ea9bf63f7c23023c935a505fc97af413a',
     '7b283ba308165d7848629e8f16b98a551abeefbdbdffa2b314092ef67bb7acee'),
    ('f7d3eb9d4f0df4af6274f7371b904b305dabc070',
     '6217dedb15689f9b65c5eeea9f9ab234dd5cbf209105139be588c63185ed0f5e')])
def test_shipped_v0215_catalog_admits_each_real_build(portable,monkeypatch,commit,manifest_digest):
    import io
    import subprocess
    import tarfile

    install,updater,root,_,_=portable
    source=install.REPO
    helper=updater.release_helpers()
    data=(source/'predecessor-builds.json').read_bytes()
    builds=helper.catalog(data)
    pair={'commit':commit,'manifest_sha256':manifest_digest}
    selected,=[build for build in builds if pair in build['provenance']]
    other,=[build for build in builds if build['version']=='0.2.15' and pair not in build['provenance']]
    assert selected['version']=='0.2.15' and selected['app_sha256']!=other['app_sha256']
    git=['git','-C',str(source)]
    manifest=subprocess.run([*git,'show',commit+':release-manifest.json'],capture_output=True,check=False,timeout=10)
    if manifest.returncode:
        pytest.skip('Predecessor Git objects are missing from this shallow checkout.')
    assert helper.digest(manifest.stdout)==manifest_digest
    assert helper.manifest_app(helper.json_object(manifest.stdout),'0.2.15')==selected['app_sha256']
    archive=subprocess.check_output([*git,'archive','--format=tar',commit,'app'],timeout=10)
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for name,expected in selected['app_sha256'].items():
            with files.extractfile('app/'+name) as blob:raw=blob.read()
            assert helper.digest(raw)==expected
            path=root/'app'/name
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def verify(*args,**kwargs):
        return {'version':'0.2.15','sha256':{'app/'+name:sha for name,sha in other['app_sha256'].items()},
                'git_identity':{'commit':other['provenance'][0]['commit']}}
    monkeypatch.setattr(updater,'verify',verify)
    _,evidence=updater._predecessor(root,{'version':'0.2.15','release_root':str(root/'retained')},
                                    source,{'sha256':{'predecessor-builds.json':helper.digest(data)}})
    assert evidence['source']=='candidate catalog' and pair in evidence['provenance']
