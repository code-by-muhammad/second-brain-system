"""Disposable macOS socket measurements, not a product security acceptance.

Only this process's sockets/credential-free connections are used. Wildcard binds
are deliberate test listeners on a disposable CI VM; never run on an owner host.
"""
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import time


def native_listen_inventory(port):
    """Measure an OS inventory; retain counts/classes, never raw socket rows."""
    tool = Path('/usr/sbin/netstat')
    for part in (tool, *tool.parents):
        metadata = part.stat()
        if part.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError('Native inventory executable is not OS-protected.')
    if not stat.S_ISREG(tool.stat().st_mode):
        raise RuntimeError('Native inventory executable is not regular.')
    result = subprocess.run([str(tool), '-an', '-p', 'tcp'],
                            env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'},
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=False)
    if result.returncode or result.stderr or len(result.stdout) > 2_000_000:
        raise RuntimeError('Native inventory query failed or exceeded bound.')
    expected_headers = [['Active', 'Internet', 'connections', '(including', 'servers)'],
                        ['Proto', 'Recv-Q', 'Send-Q', 'Local', 'Address', 'Foreign', 'Address', '(state)']]
    headers = []
    matches = []
    time_wait_rows = 0
    for line in result.stdout.decode('ascii', errors='strict').splitlines():
        fields = line.split()
        if not fields:
            continue
        if not fields[0].startswith('tcp'):
            if fields not in expected_headers:
                raise RuntimeError('Native inventory header is outside the diagnostic contract.')
            headers.append(fields)
            continue
        if len(fields) != 6 or fields[0] not in ('tcp4', 'tcp6', 'tcp46'):
            raise RuntimeError('Native TCP inventory layout is outside this diagnostic contract.')
        if fields[3].rsplit('.', 1)[-1] != str(port):
            continue
        if fields[-1] == 'TIME_WAIT':
            time_wait_rows += 1
        if fields[-1] == 'LISTEN':
            matches.append({'family': fields[0],
                            'specific_loopback_v4': fields[3] == '127.0.0.1.'+str(port),
                            'specific_loopback_v6': fields[3] == '::1.'+str(port),
                            'wildcard': fields[3] in ('*.'+str(port), '::.'+str(port))})
    if headers != expected_headers:
        raise RuntimeError('Native inventory header sequence is outside the diagnostic contract.')
    return {'header_tokens': headers, 'matching_listen_rows': len(matches), 'matching_time_wait_rows': time_wait_rows,
            'classes': matches}


def zero_byte_probe(port):
    """Diagnostic only: one credential-free IPv4 handshake, no reads/writes."""
    started = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.25)
        try:
            probe.connect(('127.0.0.1', port))
        except ConnectionRefusedError:
            value = {'outcome': 'connection refused'}
        except OSError as error:
            value = {'outcome': 'inconclusive', 'error_type': type(error).__name__, 'errno': error.errno}
        else:
            value = {'outcome': 'connected', 'self_connect': probe.getsockname() == ('127.0.0.1', port)}
    return {**value, 'elapsed_ms': round((time.monotonic()-started)*1000, 3)}


def bind_attempt(address, port, *, reuseaddr=False, reuseport=False, listen=False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    state = {'bind_ok': False, 'listen_attempted': listen}
    try:
        if reuseaddr: sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if reuseport: sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind((address, port))
        state['bind_ok'] = True
        if listen:
            sock.listen(4)
            state['listen_ok'] = True
        return state, sock
    except OSError as error:
        sock.close()
        return {**state, 'error_type': type(error).__name__, 'errno': error.errno}, None


def accepted_by(listeners, port):
    def v4_endpoint(value):
        address = ipaddress.ip_address(value[0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped is None:
                raise RuntimeError('Expected only an IPv4 or IPv4-mapped synthetic peer.')
            address = address.ipv4_mapped
        return str(address), value[1]
    with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
        # No application bytes; capture only our own source endpoint for attribution.
        expected_peer = connection.getsockname()
    names = []
    for name, listener in listeners:
        listener.settimeout(.5)
        try: stream, _ = listener.accept()
        except TimeoutError: continue
        with stream:
            if v4_endpoint(stream.getpeername()) != expected_peer or v4_endpoint(stream.getsockname()) != ('127.0.0.1', port):
                raise RuntimeError('Synthetic accepted connection endpoint attribution differs.')
        names.append(name)
    return names


def time_wait_port():
    with socket.socket() as owner:
        owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        owner.bind(('127.0.0.1', 0)); owner.listen(1)
        port = owner.getsockname()[1]
        with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
            stream, _ = owner.accept()
            stream.close()
            connection.settimeout(2)
            if connection.recv(1) != b'': raise RuntimeError('Expected synthetic EOF.')
    return port


def measure():
    report = {'scope': 'BSD IPv4/IPv6 same-user same-process measurements only; no product/lifecycle/device acceptance',
              'os_version': platform.mac_ver()[0], 'arch': platform.machine(),
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'stage': 'host-preflight', 'cases': []}
    try:
        if sys.platform != 'darwin' or os.geteuid() == 0 or not sys.flags.isolated or sys.flags.optimize:
            raise RuntimeError('Require a non-root native disposable macOS CI VM and Python -I.')
        if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_OS') != 'macOS':
            raise RuntimeError('Refusing execution outside the declared disposable CI scope.')
        socket.setdefaulttimeout(5)
        report['inventory_scope'] = 'unprivileged OS TCP row counts only; same-process synthetic listeners; no PID attribution or standard-user acceptance'
        report['stage'] = 'reuse-and-routing'
        for address in ('127.0.0.1', '0.0.0.0'):
            for reuseaddr, reuseport in ((False, False), (True, False), (False, True), (True, True)):
                owner = socket.socket()
                other = None
                try:
                    # Controlled reuse options, not FastMCP/uvicorn observations.
                    owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    owner.bind(('127.0.0.1', 0)); owner.listen(4)
                    port = owner.getsockname()[1]
                    outcome, other = bind_attempt(address, port, reuseaddr=reuseaddr, reuseport=reuseport, listen=True)
                    case = {'address': address, 'reuseaddr': reuseaddr, 'reuseport': reuseport,
                            'owner_reuseaddr': True, 'owner_reuseport': False,
                            'owner_address': '127.0.0.1', **outcome,
                            'native_inventory': native_listen_inventory(port)}
                    if other is not None:
                        case['accepted_by'] = accepted_by((('owner', owner), ('other', other)), port)
                    report['cases'].append(case)
                except OSError as error:
                    report['cases'].append({'address': address, 'reuseaddr': reuseaddr, 'reuseport': reuseport,
                                            'error_type': type(error).__name__, 'errno': error.errno})
                finally:
                    if other is not None: other.close()
                    owner.close()
        report['stage'] = 'same-process-family-coexistence'
        for family, address, v6only in ((socket.AF_INET, '0.0.0.0', None),
                                        (socket.AF_INET6, '::', False),
                                        (socket.AF_INET6, '::', True),
                                        (socket.AF_INET6, '::1', True)):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as controlled:
                    controlled.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if v6only is not None:
                        controlled.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, int(v6only))
                    controlled.bind((address, 0)); controlled.listen(1)
                    port = controlled.getsockname()[1]
                    outcome, check = bind_attempt('127.0.0.1', port)
                    if check is not None: check.close()
                    listening, check = bind_attempt('127.0.0.1', port, listen=True)
                    try:
                        listeners = [('controlled', controlled)]
                        if check is not None: listeners.append(('specific_v4', check))
                        try: routed = accepted_by(listeners, port)
                        except OSError as error: routed = {'connect_error_type': type(error).__name__, 'errno': error.errno}
                        report['cases'].append({'kind': 'same-process controlled family coexistence',
                                                 'address': address, 'ipv6_only': v6only,
                                                 'bare_bind': outcome, 'competing_listener': listening,
                                                 'accepted_by': routed,
                                                 'native_inventory': native_listen_inventory(port)})
                    finally:
                        if check is not None: check.close()
            except OSError as error:
                report['cases'].append({'address': address, 'ipv6_only': v6only,
                                        'error_type': type(error).__name__, 'errno': error.errno})
        report['stage'] = 'time-wait'
        port = time_wait_port()
        started = time.monotonic()
        first, check = bind_attempt('127.0.0.1', port)
        if check is not None: check.close()
        report['time_wait_listen_inventory'] = native_listen_inventory(port)
        report['time_wait_zero_byte_probe'] = zero_byte_probe(port)
        reuse_port = time_wait_port()
        reuse_inventory = native_listen_inventory(reuse_port)
        plain_control, check = bind_attempt('127.0.0.1', reuse_port)
        if check is not None: check.close()
        reuse_control, check = bind_attempt('127.0.0.1', reuse_port, reuseaddr=True, listen=True)
        if check is not None: check.close()
        report['time_wait_reuseaddr_control'] = {**reuse_control, 'different_port': reuse_port != port,
                                                'before_rebind_inventory': reuse_inventory,
                                                'before_rebind_plain_bind': plain_control,
                                                'rebinds_same_port_as_its_own_time_wait': True}
        attempts = 0
        while True:
            outcome, check = bind_attempt('127.0.0.1', port)
            attempts += 1
            if check is not None: check.close()
            elapsed = time.monotonic()-started
            if outcome['bind_ok'] or elapsed >= 65:
                report['time_wait'] = {'initial_bind': first, 'final_bind': outcome,
                                       'seconds_observed': round(elapsed, 3), 'attempts': attempts,
                                       'time_bound_seconds': 65}
                break
            if outcome.get('errno') != errno.EADDRINUSE:
                raise RuntimeError('Unexpected bind error in synthetic TIME_WAIT observation.')
            time.sleep(1)
        report.update(status='measurements complete', stage='complete')
        return report, 0
    except Exception as error:
        report.update(status='measurement failed', failure_stage=report['stage'], error_type=type(error).__name__)
        if isinstance(error, OSError): report['errno'] = error.errno
        return report, 1


if __name__ == '__main__':
    measured, code = measure()
    print(json.dumps(measured), flush=True)
    sys.exit(code)
