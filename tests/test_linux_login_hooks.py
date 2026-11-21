"""Preserve exact login and autostart images under the native Linux backend."""

from pathlib import Path
import sys

import pytest

import brain_linux_user_service as user
import brain_process as processes
import brain_service as controller
import brain_settings as cfg
from test_portable_remediation import load

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='requires Linux account and directory-handle hook semantics')


@pytest.fixture
def registration(monkeypatch):
    monkeypatch.setattr(user, 'account_home', lambda: Path.home().resolve())
    monkeypatch.setattr(user, 'shutil_which', lambda name: None)
    monkeypatch.setattr(user, 'pid1_comm', lambda: 'tini')
    return user.LinuxUserService('second-brain.service', cfg.ROOT, processes._selected_python(),
                                 current_python=cfg.PYTHON)


def test_profile_install_preserves_exact_bytes_and_mode(registration):
    profile = registration.home / '.profile'
    original = b'# owner bytes \xff\r\nexport OWNER_SETTING=yes'
    profile.write_bytes(original)
    profile.chmod(0o600)
    assert not registration._install_boot()
    installed = profile.read_bytes()
    assert installed.startswith(original + b'\n' + user.BOOT_MARK_BEGIN.encode())
    assert b' </dev/null >/dev/null 2>&1 &\n' in installed
    assert profile.stat().st_mode & 0o777 == 0o600
    assert not registration._install_boot()
    assert profile.read_bytes() == installed
    assert registration.boot_path.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('original', [None, b'', b'# owner bytes \xff\r\nexport OWNER_SETTING=yes'])
def test_profile_rollback_restores_absence_or_exact_original(registration, tmp_path, original):
    profile = registration.home / '.profile'
    if original is not None:
        profile.write_bytes(original)
        profile.chmod(0o600)
    target = tmp_path / 'snapshot'
    target.mkdir()
    images = [('unit', registration.path, (None, None), (registration.installable, 0o644), 'native'),
              ('activation', registration.enabled_path, (None, None), (b'false\n', 0o644), 'native')]
    images.extend(('hook', path, (before, mode), (after, new_mode), 'native')
                  for path, before, mode, after, new_mode in registration.hook_images())
    artifacts = load('update')._seal_artifacts(target, images)
    snapshot = {'root': str(cfg.ROOT), 'deployment': {'hold_id': 'a' * 32, 'service_policy': 'first',
                'target_app_sha256': {}, 'artifacts': artifacts}}
    value = {'snapshot': str(target)}

    controller._publish_native_images(registration, value, snapshot, restoring=False)
    assert user.BOOT_MARK_BEGIN.encode() in profile.read_bytes()
    controller._publish_native_images(registration, value, snapshot, restoring=True)
    assert not registration.boot_path.exists()
    if original is None:
        assert not profile.exists()
    else:
        assert profile.read_bytes() == original
        assert profile.stat().st_mode & 0o777 == 0o600


def test_profile_marker_and_foreign_desktop_are_preserved(registration):
    profile = registration.home / '.profile'
    # A marker on its own without the exact command belongs to the owner.
    original = (user.BOOT_MARK_BEGIN + '\nowner-command\n' + user.BOOT_MARK_END + '\n').encode()
    profile.write_bytes(original)
    desktop = registration.home / '.config/autostart/second-brain.desktop'
    desktop.parent.mkdir(parents=True)
    foreign = ('[Desktop Entry]\nExec=' + str(registration.boot_path) + ' --foreign\n').encode()
    desktop.write_bytes(foreign)
    skips = registration._install_boot()
    assert {row['reason'] for row in skips} == {'foreign_profile_block', 'foreign_desktop_entry'}
    assert (profile.read_bytes(), desktop.read_bytes()) == (original, foreign)


def test_exact_legacy_desktop_upgrades_to_quoted_exec_with_safe_mode(registration, tmp_path):
    registration.boot_path = registration.boot_path.with_name('boot $ start')
    desktop = registration.home / '.config/autostart/second-brain.desktop'
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes(registration._desktop_body(legacy=True))
    desktop.chmod(0o600)
    assert not registration._install_boot()
    escaped = str(registration.boot_path).replace('$', '\\\\$')
    assert f'Exec="{escaped}"\n'.encode() in desktop.read_bytes()
    assert desktop.stat().st_mode & 0o777 == 0o600


def test_guarded_hook_write_refuses_parent_swap(registration, tmp_path):
    target = registration.home / '.config/autostart'
    target.mkdir(parents=True)
    images = registration.hook_images()
    desktop = next(image for image in images if image[0].parent == target)
    target.rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        registration._apply_hook(desktop)
    assert list(outside.iterdir()) == []


def test_ordinary_install_adds_crontab_entry_once_and_reports_fault(registration, monkeypatch, tmp_path):
    table = registration.home / 'test-crontab'
    other = b'@reboot /usr/bin/echo "second-brain-user-service" # unrelated\n'
    table.write_bytes(other)
    binary = tmp_path / 'crontab'
    binary.write_text('#!/bin/sh\ncase "$1" in\n'
                      '  -l) cat "$HOME/test-crontab";;\n'
                      '  -) test "${SB_CRON_WRITE_FAIL:-}" != yes && cat > "$HOME/test-crontab";;\n'
                      '  *) exit 2;;\nesac\n', encoding='ascii')
    binary.chmod(0o700)
    monkeypatch.setattr(user, 'shutil_which', lambda name: str(binary))
    monkeypatch.setenv('SB_CRON_WRITE_FAIL', 'yes')
    assert registration._install_boot() == [{'path': 'crontab', 'reason': 'crontab_write_unverified'}]
    assert table.read_bytes() == other
    monkeypatch.delenv('SB_CRON_WRITE_FAIL')
    skips = registration._install_boot()
    assert not skips
    installed = table.read_bytes()
    assert installed.startswith(other) and installed.count(b'@reboot') == 2
    assert not registration._install_boot()
    assert table.read_bytes() == installed
