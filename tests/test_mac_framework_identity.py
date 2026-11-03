"""Measured framework exec replacement: configured paths, never observed-exe adoption."""
import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import brain_process as processes
import brain_settings as cfg


@pytest.fixture
def framework(brain,tmp_path,monkeypatch):
    version=tmp_path/'Python.framework/Versions/3.12'
    stub=version/'bin/python3.12';stub.parent.mkdir(parents=True);stub.write_bytes(b'fake wrapper, never executed')
    image=version/'Resources/Python.app/Contents/MacOS/Python'
    image.parent.mkdir(parents=True);image.write_bytes(b'fake image, never executed')
    env=tmp_path/'venv';alias=env/'bin/python';alias.parent.mkdir(parents=True)
    try:alias.symlink_to(stub)
    except OSError:pytest.skip('Fixture needs symlink creation, no product execution')
    (env/'pyvenv.cfg').write_text('home = '+str(stub.parent)+'\n',encoding='utf-8')
    monkeypatch.setattr(cfg,'PYTHON',alias)
    monkeypatch.setattr(processes,'os',SimpleNamespace(name='posix',path=os.path))
    monkeypatch.setattr(processes,'sys',SimpleNamespace(platform='darwin'))
    return SimpleNamespace(version=version,stub=stub,image=image,alias=alias,env=env)


def test_framework_resource_image_is_direct_for_configured_venv(framework,monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *_a,**_kw:pytest.fail('Interpreter mapping must never execute code'))
    mapping=processes._launchers()
    assert mapping=={processes._path(framework.stub):processes._path(framework.stub),
                     processes._path(framework.image):processes._path(framework.image)}


def test_framework_resource_mapping_does_not_add_a_bm_directory(framework):
    assert processes._bm_paths()==set()


@pytest.mark.parametrize('change',['missing','linked-other-version','home-other-version'])
def test_framework_metadata_mismatch_refuses(framework,change,tmp_path):
    other=tmp_path/'Python.framework/Versions/3.13'
    (other/'bin').mkdir(parents=True)
    if change=='missing':framework.image.unlink()
    elif change=='linked-other-version':
        target=other/'Python';target.write_bytes(b'never executed')
        framework.image.unlink();framework.image.symlink_to(target)
    else:(framework.env/'pyvenv.cfg').write_text('home = '+str(other/'bin')+'\n',encoding='utf-8')
    with pytest.raises(processes.OwnershipError,match='framework'):
        processes._launchers()


def test_nonframework_interpreter_has_no_extra_image(framework,tmp_path,monkeypatch):
    executable=tmp_path/'plain/bin/python';executable.parent.mkdir(parents=True);executable.write_bytes(b'not executed')
    monkeypatch.setattr(cfg,'PYTHON',executable)
    assert processes._launchers()=={processes._path(executable):processes._path(executable)}


@pytest.mark.parametrize('extra',[[],['--pilot']])
@pytest.mark.parametrize('correct_script',[True,False])
def test_framework_inspection_keeps_exact_script_contract(framework,monkeypatch,extra,correct_script):
    script=cfg.ROOT/'app/brain_server.py' if correct_script else cfg.ROOT/'app/brain_hooks.py'
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,cmdline=lambda:[str(framework.image),str(script),*extra],
        exe=lambda:str(framework.image),ppid=lambda:1))
    owned=processes.inspect(123)
    # brain_hooks.py is deliberately not a service role.
    if correct_script and not extra:
        assert owned.kind=='direct' and owned.role=='server'
        owned.close()
    else:assert owned is None


def test_framework_bm_inside_app_bundle_is_not_adopted(framework,monkeypatch):
    fake=framework.image.parent/'bm'
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,cmdline=lambda:[str(fake),'reindex','-p','brain'],
        exe=lambda:str(fake),ppid=lambda:1))
    assert processes.inspect(123) is None


def test_framework_owned_venv_bm_is_an_index_worker(framework,monkeypatch):
    bm=framework.alias.parent/'bm';bm.write_bytes(b'fake entry point, never executed')
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,cmdline=lambda:[str(framework.image),str(bm),'reindex','-p','brain'],
        exe=lambda:str(framework.image),ppid=lambda:1))
    owned=processes.inspect(123)
    assert owned.role=='index_worker' and owned.kind=='direct'
    owned.close()


def test_linked_bm_is_not_ownership_authority(framework):
    bm=framework.alias.parent/'bm';bm.symlink_to(framework.stub)
    with pytest.raises(processes.OwnershipError,match='index executable'):
        processes._bm_paths()


@pytest.mark.parametrize('field',['python','controller_python'])
def test_retained_nonframework_python_has_no_extra_image(framework,tmp_path,field):
    previous=tmp_path/'retained/bin/python';previous.parent.mkdir(parents=True);previous.write_bytes(b'not executed')
    (cfg.ROOT/'state/previous-release.json').write_text(json.dumps({field:str(previous)}),encoding='utf-8')
    mapping=processes._launchers()
    assert mapping[processes._path(previous)]==processes._path(previous)
    assert len(mapping)==3


def test_relative_retained_python_refuses(framework):
    (cfg.ROOT/'state/previous-release.json').write_text(json.dumps({'controller_python':'relative/bin/python'}),encoding='utf-8')
    with pytest.raises(processes.OwnershipError,match='absolute'):processes._launchers()


def test_malformed_retained_metadata_is_an_ownership_refusal(framework):
    (cfg.ROOT/'state/previous-release.json').write_text('{broken',encoding='utf-8')
    with pytest.raises(processes.OwnershipError,match='metadata is invalid'):processes._launchers()


@pytest.mark.skipif(os.name=='nt',reason='Native POSIX metadata open contract')
@pytest.mark.parametrize('kind',['link','fifo'])
def test_native_metadata_link_and_fifo_refuse(framework,tmp_path,kind):
    target=framework.env/'pyvenv.cfg';target.unlink()
    if kind=='fifo':os.mkfifo(target)
    else:
        alternate=tmp_path/'config';alternate.write_text('home = '+str(framework.stub.parent)+'\n')
        target.symlink_to(alternate)
    with pytest.raises(processes.OwnershipError,match='metadata'):
        processes._launchers()


@pytest.mark.skipif(os.name!='nt',reason='Windows mapping compatibility')
def test_windows_launcher_base_mapping_is_unchanged(brain,tmp_path,monkeypatch):
    env=tmp_path/'windows';python=env/'Scripts/python.exe';python.parent.mkdir(parents=True)
    python.write_bytes(b'not executed')
    base=tmp_path/'base';base.mkdir()
    (env/'pyvenv.cfg').write_text('home = '+str(base)+'\n',encoding='utf-8')
    monkeypatch.setattr(cfg,'PYTHON',python)
    assert processes._launchers()=={processes._path(python):processes._path(base/'python.exe'),
        processes._path(python.with_name('pythonw.exe')):processes._path(base/'pythonw.exe')}
