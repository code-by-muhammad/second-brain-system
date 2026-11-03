"""Installed definitions need current bytes; loaded/running is a later boundary."""
import importlib.util
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def installer():
    spec=importlib.util.spec_from_file_location('registration_presence_installer',Path(__file__).resolve().parents[1]/'install.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def current():
    return {'status':'inspected','task_present':False,'task_enabled':True,
            'expected_native_definition_sha256':'a'*64,
            'native':{'file_present':True,'loaded':False,'definition_sha256':'a'*64}}


@pytest.mark.parametrize('platform',['darwin','linux'])
def test_current_native_file_does_not_need_to_be_loaded(installer,platform):
    assert installer._initial_registration_present(current(),platform=platform) is True


@pytest.mark.parametrize('platform',['darwin','linux'])
def test_deleted_native_definition_is_not_registered(installer,platform):
    value=current();value['native'].update(file_present=False,definition_sha256=None)
    assert installer._initial_registration_present(value,platform=platform) is False


@pytest.mark.parametrize('platform',['darwin','linux'])
@pytest.mark.parametrize('fault',['missing_native','string_presence','missing_expected','malformed_expected','stale_definition','missing_actual'])
def test_native_definition_proof_refuses_ambiguity(installer,platform,fault):
    value=current()
    if fault=='missing_native':value.pop('native')
    elif fault=='string_presence':value['native']['file_present']='true'
    elif fault=='missing_expected':value.pop('expected_native_definition_sha256')
    elif fault=='malformed_expected':value['expected_native_definition_sha256']='not-a-hash'
    elif fault=='stale_definition':value['native']['definition_sha256']='b'*64
    else:value['native'].pop('definition_sha256')
    with pytest.raises(ValueError,match='journal preserved'):
        installer._initial_registration_present(value,platform=platform)


@pytest.mark.parametrize('present',[False,True])
def test_windows_preserves_task_presence_contract(installer,present):
    value={'status':'inspected','task_present':present,'task_enabled':True,
           'native':{'file_present':'untrusted optional value'}}
    assert installer._initial_registration_present(value,platform='win32') is present


@pytest.mark.parametrize('platform',['win32','darwin','linux'])
@pytest.mark.parametrize('fault',['status','task_type'])
def test_inspection_envelope_is_required(installer,platform,fault):
    value=current()
    if fault=='status':value['status']='unavailable'
    else:value['task_present']='false'
    with pytest.raises(ValueError,match='journal preserved'):
        installer._initial_registration_present(value,platform=platform)
