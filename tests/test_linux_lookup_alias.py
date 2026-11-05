"""Observed Ubuntu lookup alias over fake metadata; no OS link or manager changes."""
from pathlib import PurePosixPath
from types import SimpleNamespace
import stat

import pytest
import brain_linux_registration as native

ALIAS='/etc/xdg/systemd/user'
TARGET='/etc/systemd/user'
OWN='/home/fixture/.config/systemd/user'

@pytest.fixture
def fs(monkeypatch):
    rows={p:SimpleNamespace(st_uid=0,st_gid=0,st_mode=stat.S_IFDIR|0o755)
          for p in ('/','/etc','/etc/xdg','/etc/xdg/systemd','/etc/systemd',TARGET)}
    rows[ALIAS]=SimpleNamespace(st_uid=0,st_gid=0,st_mode=stat.S_IFLNK|0o777)
    links={ALIAS:'../../systemd/user'};entries={};exists=set();scanned=[]
    class MetadataPath(PurePosixPath):
        def lstat(self):
            if str(self) not in rows:raise FileNotFoundError(str(self))
            return rows[str(self)]
        def is_symlink(self):
            try:return stat.S_ISLNK(self.lstat().st_mode)
            except FileNotFoundError:return False
    class Scan:
        def __init__(self,path):self.path=str(path)
        def __enter__(self):
            scanned.append(self.path)
            return iter(SimpleNamespace(name=name) for name in entries.get(self.path,[]))
        def __exit__(self,*args):return False
    monkeypatch.setattr(native,'Path',MetadataPath)
    monkeypatch.setattr(native,'os',SimpleNamespace(readlink=lambda path:links[str(path)],scandir=Scan,
        path=SimpleNamespace(lexists=lambda path:str(path) in exists or str(path) in rows)))
    def unit(paths=None):
        helper=object.__new__(native.LinuxRegistration)
        helper.unit='second-brain.service';helper.directory=MetadataPath(OWN)
        helper.unit_paths=tuple(MetadataPath(p) for p in (paths or (OWN,ALIAS,TARGET)))
        return helper
    return SimpleNamespace(rows=rows,links=links,entries=entries,exists=exists,scanned=scanned,Path=MetadataPath,unit=unit)

def test_observed_root_owned_alias_admits_disk_boundary_and_checks_target(fs):
    fs.unit()._disk_boundary()
    assert TARGET+'/service.d' in fs.scanned
    assert not any(path.startswith(ALIAS+'/') for path in fs.scanned)

def test_existing_plain_lookup_behavior_is_unchanged(fs):
    fs.rows[ALIAS]=SimpleNamespace(st_uid=0,st_gid=0,st_mode=stat.S_IFDIR|0o755)
    fs.unit()._disk_boundary()
    assert ALIAS+'/service.d' in fs.scanned

@pytest.mark.parametrize('damage',[ 'link-owner','source-parent-owner','source-parent-writable','source-parent-link',
    'target-owner','target-writable','target-link','target-missing','target-parent-owner','target-parent-writable',
    'target-parent-link','root-writable','wrong-relative-target','absolute-target','user-target'])
def test_observed_alias_requires_protected_exact_source_and_target(fs,damage):
    if damage=='link-owner':fs.rows[ALIAS].st_uid=1001
    elif damage=='source-parent-owner':fs.rows['/etc/xdg/systemd'].st_uid=1001
    elif damage=='source-parent-writable':fs.rows['/etc/xdg'].st_mode|=0o020
    elif damage=='source-parent-link':fs.rows['/etc/xdg'].st_mode=stat.S_IFLNK|0o777
    elif damage=='target-owner':fs.rows[TARGET].st_uid=1001
    elif damage=='target-writable':fs.rows[TARGET].st_mode|=0o002
    elif damage=='target-link':fs.rows[TARGET].st_mode=stat.S_IFLNK|0o777
    elif damage=='target-missing':del fs.rows[TARGET]
    elif damage=='target-parent-owner':fs.rows['/etc/systemd'].st_uid=1001
    elif damage=='target-parent-writable':fs.rows['/etc/systemd'].st_mode|=0o020
    elif damage=='target-parent-link':fs.rows['/etc/systemd'].st_mode=stat.S_IFLNK|0o777
    elif damage=='root-writable':fs.rows['/'].st_mode|=0o002
    elif damage=='wrong-relative-target':fs.links[ALIAS]='../systemd/user'
    elif damage=='absolute-target':fs.links[ALIAS]=TARGET
    else:fs.links[ALIAS]='/home/fixture/controlled'
    with pytest.raises(native.RegistrationError):fs.unit()._disk_boundary()

def test_unknown_user_lookup_alias_is_still_refused(fs):
    alias='/home/fixture/untrusted-lookup'
    fs.rows[alias]=SimpleNamespace(st_uid=1001,st_gid=1001,st_mode=stat.S_IFLNK|0o777)
    fs.links[alias]=TARGET
    with pytest.raises(native.RegistrationError):fs.unit((OWN,alias))._disk_boundary()

def test_even_root_owned_unknown_lookup_alias_is_still_refused(fs):
    alias='/etc/other-user-lookup'
    fs.rows[alias]=SimpleNamespace(st_uid=0,st_gid=0,st_mode=stat.S_IFLNK|0o777)
    fs.links[alias]=TARGET
    with pytest.raises(native.RegistrationError):fs.unit((OWN,alias))._disk_boundary()

def test_alias_does_not_hide_higher_priority_fragment(fs):
    fs.exists.add(TARGET+'/second-brain.service')
    with pytest.raises(native.RegistrationError,match='higher-priority'):
        fs.unit((ALIAS,OWN))._disk_boundary()

@pytest.mark.parametrize('directory',['service.d','second-.service.d','second-brain.service.d'])
def test_alias_does_not_hide_applicable_dropins(fs,directory):
    fs.entries[TARGET+'/'+directory]=['10-foreign.conf']
    with pytest.raises(native.RegistrationError,match='drop-in'):
        fs.unit()._disk_boundary()

def test_alias_does_not_allow_owned_definition_to_traverse_link(fs):
    helper=fs.unit();helper.path=fs.Path(ALIAS+'/second-brain.service')
    with pytest.raises(native.RegistrationError,match='Linked'):
        helper._read_unit()

def test_general_plain_guard_is_not_relaxed_for_known_lookup_alias(fs):
    with pytest.raises(native.RegistrationError,match='Linked'):
        native._plain(fs.Path(ALIAS))
