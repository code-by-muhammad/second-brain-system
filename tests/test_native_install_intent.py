"""Durable install-intent decisions; no native calls or qualification claims."""
import hashlib

import pytest
import brain_process as processes
import brain_service as service


OLD=hashlib.sha256(b'old').hexdigest()
TARGET=hashlib.sha256(b'target').hexdigest()
OTHER=hashlib.sha256(b'other').hexdigest()


def present(sha,enabled):
    return {'file_present':True,'definition_sha256':sha,'enabled':enabled}


def record(status='install failed',*,prior=None,intent=True,target=TARGET):
    return {'status':status,'prior_native':prior or present(OLD,True),
            'requested_enabled':intent,'expected_native_definition_sha256':target}


@pytest.mark.parametrize('enabled',[True,False])
def test_new_install_preserves_observed_existing_intent(monkeypatch,enabled):
    current=present(OLD,enabled)
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:None)
    assert service._native_install_intent(current,TARGET)==(current,enabled)


def test_new_absent_linux_registration_has_explicit_default_intent(monkeypatch):
    current={'file_present':False,'definition_sha256':None,'enabled':None}
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:None)
    assert service._native_install_intent(current,TARGET)==(current,True)


@pytest.mark.parametrize('current_sha',[OLD,TARGET])
@pytest.mark.parametrize('status',['installing','install failed'])
def test_retry_reuses_first_intent_before_or_after_atomic_replacement(monkeypatch,current_sha,status):
    history=record(status)
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:history)
    prior,intent=service._native_install_intent(present(current_sha,False),TARGET)
    assert prior==present(OLD,True) and intent is True


def test_retry_of_fresh_install_does_not_infer_disabled_intent(monkeypatch):
    prior={'file_present':False,'definition_sha256':None,'enabled':None}
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:record(prior=prior))
    assert service._native_install_intent(present(TARGET,False),TARGET)==(prior,True)


@pytest.mark.parametrize('history',[record('installed'),record(target=OTHER)])
def test_completed_or_other_target_record_cannot_override_new_intent(monkeypatch,history):
    current=present(TARGET,False)
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:history)
    assert service._native_install_intent(current,TARGET)==(current,False)


def test_foreign_definition_after_failed_install_preserves_receipt(monkeypatch):
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:record())
    with pytest.raises(processes.OwnershipError,match='original or target'):
        service._native_install_intent(present(OTHER,False),TARGET)


@pytest.mark.parametrize('history',[record(intent=None),record(prior={'file_present':True,'enabled':True}),
    record(prior={'file_present':False,'enabled':None,'definition_sha256':OLD})])
def test_incomplete_history_is_not_replaced_with_a_guessed_intent(monkeypatch,history):
    monkeypatch.setattr(service,'_native_control_status',lambda **kwargs:history)
    with pytest.raises(processes.OwnershipError):service._native_install_intent(present(TARGET,False),TARGET)
