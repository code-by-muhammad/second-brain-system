"""Native-table refusal controls; raw fixtures are synthetic, no OS command runs."""
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

import brain_mac_inventory as inventory
import brain_process as processes
import brain_settings as cfg


def table(*rows):
    # Independent observed token contract from native arm64/Intel 984b6ff receipts.
    # Whitespace is a synthetic variant; raw native spacing was not retained.
    headers = ['Active Internet connections (including servers)',
               'Proto  Recv-Q Send-Q    Local Address    Foreign Address   (state)']
    return ('\n'.join(headers+list(rows))+'\n').encode()


@pytest.mark.parametrize('state', ['TIME_WAIT', 'ESTABLISHED', 'CLOSE_WAIT', 'SYN_RCVD', 'CLOSED'])
def test_non_listener_state_does_not_block_empty_runtime(state):
    assert inventory.parse(table(f'tcp4 0 0 127.0.0.1.8765 *.* {state}'), 8765) == ()


def test_native_inventory_preserves_duplicate_and_coexisting_rows():
    raw = table('tcp4 0 0 127.0.0.1.8765 *.* LISTEN', 'tcp4 0 0 *.8765 *.* LISTEN')
    assert inventory.parse(raw, 8765) == (('tcp4', '*', 8765), ('tcp4', '127.0.0.1', 8765))
    duplicate = table('tcp4 0 0 127.0.0.1.8765 *.* LISTEN', 'tcp4 0 0 127.0.0.1.8765 *.* LISTEN')
    assert len(inventory.parse(duplicate, 8765)) == 2


def test_exact_local_port_and_truncated_unrelated_ipv6():
    raw = table('tcp6 0 0 2607:f8b0:4005:8.443 *.* ESTABLISHED',
                'tcp4 0 0 127.0.0.1.18765 127.0.0.1.8765 LISTEN',
                'tcp4 0 0 127.0.0.1.8765 *.* LISTEN')
    assert inventory.parse(raw, 8765) == (('tcp4', '127.0.0.1', 8765),)


@pytest.mark.parametrize('raw', [b'', b'unknown header\n', b'\xff',
    table('udp4 0 0 *.8765 *.* LISTEN'), table('tcp4 0 0 *.8765 LISTEN'),
    table('tcp4 0 0 *.8765 *.* LISTEN extra'), table('tcp4 x 0 *.8765 *.* LISTEN'),
    table('tcp4 0 0 *.8765 *.* UNKNOWN'), table('tcp4 0 0 8765 *.* LISTEN'),
    table('tcp4 0 0 *.* *.* LISTEN'), table('tcp4 0 0 *.999999 *.* LISTEN')])
def test_malformed_inventory_cannot_authorize_absence(raw):
    with pytest.raises(inventory.InventoryError):
        inventory.parse(raw, 8765)


@pytest.mark.parametrize('outcome', ['timeout', 'missing', 'stderr', 'nonzero', 'oversize'])
def test_native_transport_refuses_without_raw_error_context(brain, monkeypatch, outcome):
    monkeypatch.setattr(inventory, 'native_tool', lambda: Path('synthetic-netstat'))
    sentinel = b'PRIVATE_RAW_SENTINEL'
    def run(arguments, **options):
        assert arguments == ['synthetic-netstat', '-an', '-p', 'tcp']
        assert options['stdin'] == subprocess.DEVNULL and options['shell'] is False
        assert options['timeout'] == 5
        assert options['env'] == {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'}
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(arguments, 5, output=sentinel, stderr=sentinel)
        if outcome == 'missing':
            raise FileNotFoundError('PRIVATE_RAW_SENTINEL')
        return SimpleNamespace(returncode=int(outcome == 'nonzero'),
                               stdout=b'x'*2_000_001 if outcome == 'oversize' else table(),
                               stderr=sentinel if outcome == 'stderr' else b'')
    monkeypatch.setattr(inventory.subprocess, 'run', run)
    with pytest.raises(inventory.InventoryError) as error:
        inventory.read(cfg.PORT)
    assert 'PRIVATE_RAW_SENTINEL' not in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None


def own_user_run(monkeypatch, returncode, stdout, stderr=b''):
    monkeypatch.setattr(inventory, 'native_tool', lambda path='/usr/sbin/netstat': Path(path).name)
    def run(arguments, **options):
        assert options['stdin'] == subprocess.DEVNULL and options['shell'] is False
        if arguments[0] == 'netstat':
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        assert arguments == ['lsof', '-nP', '-iTCP:8765', '-sTCP:LISTEN', '-Fptn']
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(inventory.subprocess, 'run', run)


def test_hidden_native_table_reads_the_users_own_listeners(monkeypatch):
    # Observed on macOS 27.2: netstat prints nothing for a non-system interpreter.
    own_user_run(monkeypatch, 0, b'p842\nf10\ntIPv4\nn127.0.0.1:8765\n')
    assert inventory.read(8765) == (('tcp4', '127.0.0.1', 8765),)
    own_user_run(monkeypatch, 1, b'')
    assert inventory.read(8765) == ()
    own_user_run(monkeypatch, 0, b'p1\nf3\ntIPv4\nn127.0.0.1:8765\nf4\ntIPv6\nn[::1]:8765\np2\nf5\ntIPv6\nn*:8765\n')
    assert inventory.read(8765) == (('tcp4', '127.0.0.1', 8765), ('tcp6', '*', 8765), ('tcp6', '::1', 8765))


@pytest.mark.parametrize('returncode,stdout,stderr', [
    (0, b'', b''), (1, b'p1\n', b''), (2, b'', b''), (1, b'', b'lsof: warning'),
    (0, b'f3\ntIPv4\nn127.0.0.1:8765\n', b''), (0, b'p1\nf3\ntIPv4\n', b''),
    (0, b'p1\nf3\nn127.0.0.1:8765\n', b''), (0, b'p1\nf3\ntUDP\nn127.0.0.1:8765\n', b''),
    (0, b'px\nf3\ntIPv4\nn127.0.0.1:8765\n', b''), (0, b'p1\nf3\ntIPv4\nn127.0.0.1:9999\n', b''),
    (0, b'p1\nf3\ntIPv4\nn127.0.0.1\n', b''), (0, b'p1\nf3\ntIPv4\nn127.0.0.1:8765\nc python\n', b''),
    (0, b'p1\nf3\ntIPv4\nn127.0.0.1:1->10.0.0.1:8765\n', b''), (0, b'p1\nf3\ntIPv4\nn[::1]:8765\n', b''),
    (0, b'p1\nf3\ntIPv6\nn::1:8765\n', b''), (0, b'p1\nf3\ntIPv4\nnjunk junk:8765\n', b''),
    (0, b'p1\nf3\ntIPv4\nn127.0.0.1:8765\r\n', b''), (0, b'p1\nf3\ntIPv4\nn127.0.0.1:8765', b''),
    (0, b'\xff', b'')])
def test_malformed_own_listener_output_cannot_authorize_absence(monkeypatch, returncode, stdout, stderr):
    own_user_run(monkeypatch, returncode, stdout, stderr)
    with pytest.raises(inventory.InventoryError) as error:
        inventory.read(8765)
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize('fault', ['symlink', 'foreign_owner', 'writable', 'directory'])
def test_unprotected_native_tool_refuses_before_exec(monkeypatch, fault):
    mode = stat.S_IFDIR | 0o755 if fault == 'directory' else stat.S_IFREG | 0o755
    if fault == 'symlink': mode = stat.S_IFLNK | 0o777
    if fault == 'writable': mode |= 0o020
    info = SimpleNamespace(st_mode=mode, st_uid=501 if fault == 'foreign_owner' else 0)
    fake = SimpleNamespace(parents=(), lstat=lambda: info, stat=lambda: info)
    monkeypatch.setattr(inventory, 'Path', lambda _: fake)
    monkeypatch.setattr(inventory.subprocess, 'run', lambda *_, **__: pytest.fail('Unprotected tool ran'))
    with pytest.raises(inventory.InventoryError): inventory.read(8765)


def test_inventory_resamples_startup_transition_before_accepting(brain, monkeypatch):
    row = SimpleNamespace(pid=7, birth=100, laddr=SimpleNamespace(ip='127.0.0.1', port=cfg.PORT))
    observed = iter(((), (('tcp4', '127.0.0.1', cfg.PORT),),
                     (('tcp4', '127.0.0.1', cfg.PORT),), (('tcp4', '127.0.0.1', cfg.PORT),)))
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: next(observed))
    monkeypatch.setattr(processes, '_mac_socket_snapshot', lambda: ([row], True))
    assert processes._mac_listeners() == [row]


def test_owned_socket_without_global_presence_never_means_absence(brain, monkeypatch):
    row = SimpleNamespace(pid=7, birth=100, laddr=SimpleNamespace(ip='127.0.0.1', port=cfg.PORT))
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: ())
    monkeypatch.setattr(processes, '_mac_socket_snapshot', lambda: ([row], True))
    with pytest.raises(processes.OwnershipError, match='changed'):
        processes._mac_listeners()


def test_coexisting_global_listener_refuses_even_with_owned_socket(brain, monkeypatch):
    row = SimpleNamespace(pid=7, birth=100, laddr=SimpleNamespace(ip='127.0.0.1', port=cfg.PORT))
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: (('tcp4', '*', cfg.PORT), ('tcp4', '127.0.0.1', cfg.PORT)))
    monkeypatch.setattr(processes, '_mac_socket_snapshot', lambda: ([row], True))
    with pytest.raises(processes.OwnershipError, match='multiple'):
        processes._mac_listeners()


def test_global_inventory_failure_is_distinct_and_has_no_raw_context(brain, monkeypatch):
    def unreadable(*args, **kwargs):
        raise inventory.InventoryError('PRIVATE_RAW_SENTINEL')
    monkeypatch.setattr(inventory, 'read', unreadable)
    with pytest.raises(processes.OwnershipError, match='unreadable') as error:
        processes._mac_listeners()
    assert 'PRIVATE_RAW_SENTINEL' not in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None


def test_global_inventory_persistent_transition_never_returns_absence(brain, monkeypatch):
    observed = iter(((), (('tcp4', '127.0.0.1', cfg.PORT),))*3)
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: next(observed))
    monkeypatch.setattr(processes, '_mac_socket_snapshot', lambda: ([], False))
    with pytest.raises(processes.OwnershipError, match='changed'):
        processes._mac_listeners()


@pytest.mark.parametrize('family,host', [('tcp6', '2607:f8b0:4005:8'), ('tcp6', 'fe80::1%lo0'), ('tcp46', '*')])
def test_unqualified_listening_address_cannot_match_owned_ipv4(brain, monkeypatch, family, host):
    monkeypatch.setattr(inventory, 'read', lambda *_, **__: ((family, host, cfg.PORT),))
    row = SimpleNamespace(pid=7, birth=100, laddr=SimpleNamespace(ip='127.0.0.1', port=cfg.PORT))
    monkeypatch.setattr(processes, '_mac_socket_snapshot', lambda: ([row], True))
    with pytest.raises(processes.OwnershipError, match='foreign'):
        processes._mac_listeners()


@pytest.mark.parametrize('fault', [None, 'parent_writable', 'parent_foreign', 'parent_symlink'])
def test_native_tool_accepts_only_protected_full_parent_chain(monkeypatch, fault):
    parent_mode = stat.S_IFLNK | 0o777 if fault == 'parent_symlink' else stat.S_IFDIR | 0o755
    if fault == 'parent_writable': parent_mode |= 0o002
    parent_info = SimpleNamespace(st_mode=parent_mode, st_uid=501 if fault == 'parent_foreign' else 0)
    parent = SimpleNamespace(lstat=lambda: parent_info)
    file_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
    tool = SimpleNamespace(parents=(parent,), lstat=lambda: file_info, stat=lambda: file_info)
    monkeypatch.setattr(inventory, 'Path', lambda _: tool)
    monkeypatch.setattr(inventory.subprocess, 'run', lambda *_, **__: pytest.fail('No execution in metadata control'))
    if fault:
        with pytest.raises(inventory.InventoryError): inventory.native_tool()
    else:
        assert inventory.native_tool() is tool
