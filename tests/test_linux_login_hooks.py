"""Preserve exact login and autostart images under the native Linux backend."""

from pathlib import Path
import sys

import pytest

import brain_linux_user_service as user
import brain_process as processes
import brain_settings as cfg

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='requires Linux account and directory-handle hook semantics')


@pytest.fixture
def registration(monkeypatch):
    monkeypatch.setattr(user, 'account_home', lambda: Path.home().resolve())
    monkeypatch.setattr(user, 'shutil_which', lambda name: None)
    return user.LinuxUserService('second-brain.service', cfg.ROOT, processes._selected_python(),
                                 current_python=cfg.PYTHON)


def test_exact_profile_bytes_and_mode_round_trip(registration):
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
    assert not registration.remove_boot_hooks()
    assert profile.read_bytes() == original
    assert profile.stat().st_mode & 0o777 == 0o600
    assert not registration.boot_path.exists()


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
    registration.remove_boot_hooks()
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


def test_explicit_crontab_edits_only_exact_owned_entry_and_reports_fault(registration, monkeypatch, tmp_path):
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
    skips = registration._install_boot()
    assert not skips
    installed = table.read_bytes()
    assert installed.startswith(other) and installed.count(b'@reboot') == 2
    assert not registration._install_boot()
    assert table.read_bytes() == installed
    monkeypatch.setenv('SB_CRON_WRITE_FAIL', 'yes')
    assert registration.remove_boot_hooks() == [{'path': 'crontab', 'reason': 'crontab_write_unverified'}]
    assert table.read_bytes() == installed
    monkeypatch.delenv('SB_CRON_WRITE_FAIL')
    assert not registration.remove_boot_hooks()
    assert table.read_bytes() == other
