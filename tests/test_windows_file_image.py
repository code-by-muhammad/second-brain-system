import importlib.util
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

import brain_release


@pytest.mark.parametrize('reader', ['setup', 'release'])
@pytest.mark.parametrize('change', [None, 'opened file ID', 'descriptor change time', 'final pathname file ID'])
def test_windows_file_image_stat_identity(tmp_path, monkeypatch, reader, change):
    path = tmp_path / 'fixture.cmd'
    data = b'file image\n'
    path.write_bytes(data)
    reads = 0
    shared = {'st_dev': 7, 'st_size': len(data), 'st_mtime_ns': 30, 'st_birthtime_ns': 10}

    def pathname_stat(name, **kwargs):
        assert Path(name) == path
        inode = 12 if change == 'final pathname file ID' and reads >= 2 else 11
        return SimpleNamespace(**shared, st_ino=inode, st_mode=stat.S_IFREG | 0o777, st_ctime_ns=10)

    def descriptor_stat(descriptor):
        nonlocal reads
        reads += 1
        inode = 12 if change == 'opened file ID' else 11
        ctime = 21 if change == 'descriptor change time' and reads >= 2 else 20
        return SimpleNamespace(**shared, st_ino=inode, st_mode=stat.S_IFREG | 0o666, st_ctime_ns=ctime)

    # Simulate pathname and descriptor metadata locally without modifying the platform seen by pathlib.
    windows_os = SimpleNamespace(name='nt', stat=pathname_stat, fstat=descriptor_stat,
                                 open=os.open, fdopen=os.fdopen, O_RDONLY=os.O_RDONLY,
                                 O_BINARY=getattr(os, 'O_BINARY', 0))
    if reader == 'setup':
        source = Path(__file__).resolve().parents[1] / 'setup_runtime.py'
        spec = importlib.util.spec_from_file_location('windows_image_setup', source)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        original_lstat = Path.lstat

        def lstat(target):
            return pathname_stat(target) if target == path else original_lstat(target)

        monkeypatch.setattr(Path, 'lstat', lstat)
        monkeypatch.setattr(setup, 'os', windows_os)
        file_image, error = setup.client_image, setup.ConcurrentWriteError
    else:
        monkeypatch.setattr(brain_release, 'os', windows_os)
        file_image, error = brain_release.file_image, ValueError

    if change is None:
        assert file_image(path) == (data, 0o666)
    else:
        with pytest.raises(error, match='changed while|changed during'):
            file_image(path)
