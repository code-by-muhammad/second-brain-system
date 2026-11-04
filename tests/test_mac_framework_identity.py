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


@pytest.mark.parametrize('change',['exact','extra','duplicate-flag','reordered-flags','changed-code','server','no-flags'])
def test_only_exact_darwin_isolated_supervisor_bridge_is_owned(framework,monkeypatch,change):
    from brain_mac_registration import SERVICE_ENTRY
    flags=['-I','-B'];code=SERVICE_ENTRY;script=cfg.ROOT/'app/brain_supervisor.py';extra=[]
    if change=='extra':extra=['--extra']
    elif change=='duplicate-flag':flags=['-I','-B','-B']
    elif change=='reordered-flags':flags=['-B','-I']
    elif change=='changed-code':code+=';pass'
    elif change=='server':script=cfg.ROOT/'app/brain_server.py'
    elif change=='no-flags':flags=[]
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,cmdline=lambda:[str(framework.image),*flags,'-c',code,str(script),*extra],
        exe=lambda:str(framework.image),ppid=lambda:1))
    owned=processes.inspect(123)
    if change=='exact':
        assert owned.role=='supervisor' and owned.kind=='direct';owned.close()
    else:assert owned is None


def test_unknown_name_with_exact_runtime_script_refuses_instead_of_appearing_empty(framework,monkeypatch):
    row=SimpleNamespace(pid=123,info={'name':'python3.12','cmdline':[str(framework.image),str(cfg.ROOT/'app/brain_supervisor.py')]})
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter([row]))
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(exe=lambda:str(framework.image)))
    closed=[];monkeypatch.setattr(processes,'inspect',lambda *_:SimpleNamespace(close=lambda:closed.append(True)))
    with pytest.raises(processes.OwnershipError,match='unqualified image name'):processes.roots()
    assert closed==[True]


def test_unrelated_editor_and_unreadable_process_do_not_change_inventory_availability(framework,monkeypatch):
    rows=[SimpleNamespace(pid=123,info={'name':'editor','cmdline':['/other/editor',str(cfg.ROOT/'app/brain_supervisor.py')]}),
        SimpleNamespace(pid=124,info={'name':None,'cmdline':None})]
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter(rows))
    def fresh(pid):
        assert pid==123,'Do not inspect unavailable unrelated metadata'
        return SimpleNamespace(exe=lambda:'/other/editor')
    monkeypatch.setattr(processes.psutil,'Process',fresh)
    monkeypatch.setattr(processes,'inspect',lambda *_:pytest.fail('An unrelated editor is not a selected runtime'))
    assert processes.roots()==[]


def test_unknown_process_mentioning_unrelated_bm_does_not_trigger_executable_probe(framework,monkeypatch):
    row=SimpleNamespace(pid=123,info={'name':'screen','cmdline':['screen','-S','bm']})
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter([row]))
    monkeypatch.setattr(processes.psutil,'Process',lambda _:pytest.fail('Unrelated bm text must not probe protected processes'))
    assert processes.roots()==[]


def test_windows_unknown_name_filter_retains_previous_behavior(framework,monkeypatch):
    monkeypatch.setattr(processes,'os',SimpleNamespace(name='nt',path=os.path))
    row=SimpleNamespace(pid=123,info={'name':'other','cmdline':['other',str(cfg.ROOT/'app/brain_supervisor.py')]})
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter([row]))
    monkeypatch.setattr(processes.psutil,'Process',lambda _:pytest.fail('Windows unknown names must remain filtered'))
    assert processes.roots()==[]


@pytest.mark.parametrize('owned',[False,True])
def test_linux_canonical_selected_name_is_only_a_hint_before_full_ownership_inspect(framework,monkeypatch,owned):
    monkeypatch.setattr(processes,'sys',SimpleNamespace(platform='linux'))
    row=SimpleNamespace(pid=123,info={'name':framework.stub.name,'cmdline':[str(framework.stub),str(cfg.ROOT/'app/brain_supervisor.py')]})
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter([row]))
    calls=[];identity=SimpleNamespace(close=lambda:None)
    def inspect(pid):calls.append(pid);return identity if owned else None
    monkeypatch.setattr(processes,'inspect',inspect)
    assert processes.roots()==([identity] if owned else [])
    assert calls==[123]


def test_roots_discovers_the_actual_exact_mac_supervisor_bridge_format(framework,monkeypatch):
    from brain_mac_registration import SERVICE_ENTRY
    arguments=[str(framework.image),'-I','-B','-c',SERVICE_ENTRY,str(cfg.ROOT/'app/brain_supervisor.py')]
    row=SimpleNamespace(pid=123,info={'name':'Python','cmdline':arguments})
    monkeypatch.setattr(processes.psutil,'process_iter',lambda _:iter([row]))
    monkeypatch.setattr(processes.psutil,'Process',lambda _:SimpleNamespace(
        create_time=lambda:100,cmdline=lambda:arguments,exe=lambda:str(framework.image),ppid=lambda:1))
    found=processes.roots()
    assert len(found)==1 and found[0].role=='supervisor' and found[0].pid==123
    found[0].close()


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
