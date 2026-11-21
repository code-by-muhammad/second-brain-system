"""User-space Linux service records. No keeper is spawned and no crontab is edited."""
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import brain_linux_generation as lifecycle
import brain_linux_user_service as user
import brain_process as processes
import brain_service as controller
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
    # The unit backend can refuse registration when the host has no user bus.
    try:
        selected = user.open_registration(
            'second-brain.service', cfg.ROOT, processes._selected_python(), current_python=cfg.PYTHON,
        )
    except user.RegistrationError as error:
        assert 'User service' not in str(error)
    except FileNotFoundError:
        pass
    else:
        assert not isinstance(selected, user.LinuxUserService), 'systemd PID 1 must keep the existing unit backend'


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


def test_activation_accepts_owned_files_and_directories_from_umask_002(monkeypatch):
    service = _service(monkeypatch)
    config = service.directory.parent
    config.mkdir()
    config.chmod(0o775)
    installed = service.install(service.installable, enabled=True)
    assert installed['hook_skips'] == []
    service.enabled_path.chmod(0o664)

    service.suspend_activation(installed)
    assert service.enabled_path.read_bytes() == b'false\n'
    assert service.restore_activation(installed)['enabled'] is True
    assert service.enabled_path.stat().st_mode & 0o777 == 0o664

    service.enabled_path.chmod(0o666)
    with pytest.raises(user.HookRefusal, match='unsafe_file_mode'):
        service.suspend_activation(installed)
    config.chmod(0o777)
    with pytest.raises(user.RegistrationError, match='directory is unsafe'):
        service.inspect(prove_main=False)


def test_keeper_backs_off_and_only_rewrites_new_blockers(monkeypatch):
    service = _service(monkeypatch)
    service.install(service.installable, enabled=True)
    elapsed = 0.0
    handlers = {}
    attempts = []
    writes = []

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds
        if elapsed >= 31:
            handlers[user.signal.SIGTERM](None, None)

    def refuse_port(deadline):
        attempts.append(elapsed)
        reason = 'port_ownership_uncertain' if len(attempts) < 3 else 'unmanaged_index_worker'
        raise lifecycle.LifecycleError(reason, f'Attempt {len(attempts)}')

    original_write = lifecycle._write

    def write(registration, name, record):
        if name == lifecycle.BLOCKER:
            writes.append((elapsed, record['reason'], record['generation']))
        original_write(registration, name, record)

    monkeypatch.setattr(user, 'time', SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep))
    monkeypatch.setattr(user.signal, 'signal', handlers.__setitem__)
    monkeypatch.setattr(controller, 'spawn_allowed', lambda: True)
    monkeypatch.setattr(lifecycle, 'keeper_record', lambda registration: {})
    monkeypatch.setattr(lifecycle, 'GenerationDrain',
                        lambda registration, stack: SimpleNamespace(prove_recovery_port=refuse_port))
    monkeypatch.setattr(lifecycle, 'generation',
                        lambda registration: {'generation': ('a' if elapsed < 30 else 'b') * 32})
    monkeypatch.setattr(lifecycle, '_write', write)

    user.supervise()

    assert attempts == [0, 10, 20, 30]
    assert writes == [(0, 'port_ownership_uncertain', 'a' * 32),
                      (20, 'unmanaged_index_worker', 'a' * 32),
                      (30, 'unmanaged_index_worker', 'b' * 32)]
