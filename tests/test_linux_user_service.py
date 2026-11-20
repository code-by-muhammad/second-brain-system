"""User-space Linux service records. No keeper is spawned and no crontab is edited."""
import hashlib
import os
import sys
from pathlib import Path
import pytest
import brain_linux_user_service as user
import brain_process as processes
import brain_settings as cfg

pytestmark=pytest.mark.skipif(sys.platform!='linux',reason='the user-space service runs on Linux')


def _service(monkeypatch):
    monkeypatch.setattr(user,'account_home',lambda:Path.home().resolve())
    monkeypatch.setattr(user,'shutil_which',lambda name:None)
    monkeypatch.setattr(user,'pid1_comm',lambda:'tini')
    return user.LinuxUserService('second-brain.service',cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)


def test_tini_selects_the_user_service_and_systemd_does_not(monkeypatch):
    monkeypatch.setattr(user,'pid1_comm',lambda:'tini')
    assert user.systemd_manager_present() is False
    monkeypatch.setattr(user,'account_home',lambda:Path.home().resolve())
    selected=user.open_registration('second-brain.service',cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)
    assert isinstance(selected,user.LinuxUserService)
    monkeypatch.setattr(user,'pid1_comm',lambda:'systemd')
    assert user.systemd_manager_present() is True
    # The unit backend still runs its own checks. This host has no user bus, so it refuses
    # before any unit is written. It must not switch to the user-space service.
    try:user.open_registration('second-brain.service',cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)
    except user.LinuxUserService.__init__.__globals__['RegistrationError'] as error:
        assert 'User service' not in str(error)
    except FileNotFoundError:pass
    else:raise AssertionError('systemd PID 1 must keep the existing unit backend')


def test_install_enable_and_stop_are_file_state_only(monkeypatch):
    service=_service(monkeypatch)
    payload=service.installable
    assert service.inspect(prove_main=False)['file_present'] is False
    installed=service.install(payload,enabled=True)
    assert installed['enabled'] is True and installed['definition_sha256']==hashlib.sha256(payload).hexdigest()
    assert installed['quiescent'] is True and installed['running'] is False
    assert (Path.home()/'.config/second-brain/second-brain.service').read_bytes()==payload
    boot=Path.home()/'.config/second-brain/boot-start'
    assert os.access(boot,os.X_OK) and 'boot' in boot.read_text(encoding='utf-8')
    assert 'BEGIN SECOND-BRAIN USER SERVICE' in (Path.home()/'.profile').read_text(encoding='utf-8')
    desktop=(Path.home()/'.config/autostart/second-brain.desktop').read_text(encoding='utf-8')
    assert 'Exec="'+str(boot)+'"' in desktop and 'Type=Application' in desktop
    service.suspend_activation(installed)
    assert service.inspect(prove_main=False)['enabled'] is False
    stopped=service.native_stop()
    assert stopped['native_stopped'] is True and stopped['quiescent'] is True
    service.restore_activation(installed)
    assert service.inspect(prove_main=False)['enabled'] is True
    try:service.install(b'not-the-definition\n',enabled=True)
    except user.RegistrationError as error:assert 'exact' in str(error)
    else:raise AssertionError('a foreign definition must be refused')
    service.remove_boot_hooks()
    assert 'BEGIN SECOND-BRAIN USER SERVICE' not in (Path.home()/'.profile').read_text(encoding='utf-8')
    assert not (Path.home()/'.config/autostart/second-brain.desktop').exists()
