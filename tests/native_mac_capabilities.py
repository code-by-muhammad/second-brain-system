"""Read-only macOS capability probe, not product/device qualification.

Run explicitly with locked Python -I -B. No launchd mutation; one bounded owned
child listens without credentials or user data. This is outside default pytest.
"""
import argparse
import errno
import json
import platform
import select
import socket
import subprocess
import sys
import os
import hashlib
from pathlib import Path

import psutil


def probe(args, report):
    report['stage'] = 'host-preflight'
    if sys.platform != 'darwin' or not sys.flags.isolated or sys.flags.optimize or os.geteuid() == 0:
        raise RuntimeError('Require native non-root macOS with non-optimized Python -I.')
    report['non_root_euid'] = True
    report['stage'] = 'gui-domain'
    gui_result = subprocess.run(['/bin/launchctl', 'print', 'gui/'+str(os.getuid())],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    report['gui_domain_observed'] = gui_result.returncode == 0
    code = ('import socket,json,time\n'
            's=socket.socket();s.bind(("127.0.0.1",0));s.listen(1)\n'
            'print(json.dumps({"port":s.getsockname()[1]}),flush=True)\n'
            'time.sleep(60)\n')
    report['stage'] = 'child-start'
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if not select.select([child.stdout], [], [], 10)[0]: raise RuntimeError('Owned child startup timed out.')
        raw = child.stdout.readline(256)
        value = json.loads(raw)
        port = value['port']
        if type(port) is not int or not 1024 <= port <= 65535: raise RuntimeError('Unexpected fixture port.')
        report['stage'] = 'per-process-listener'
        rows = psutil.Process(child.pid).net_connections(kind='tcp')
        report['other_owned_process_listener_visible'] = any(
            row.status == psutil.CONN_LISTEN and row.laddr.ip == '127.0.0.1' and row.laddr.port == port for row in rows)
        report['stage'] = 'occupied-bind'
        with socket.socket() as check:
            try: check.bind(('127.0.0.1', port))
            except OSError as error: report['occupied_bind_refused'] = error.errno == errno.EADDRINUSE
            else: report['occupied_bind_refused'] = False
        report['stage'] = 'system-wide-inventory'
        try:
            psutil.net_connections(kind='tcp')
        except psutil.AccessDenied:
            report['system_wide_access_denied'] = True
        else:
            report['system_wide_access_denied'] = False
    finally:
        # Popen retains an unreaped direct child: never signal discovered/cached PIDs.
        prior_stage = report['stage']
        report['stage'] = 'owned-child-cleanup'
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        child.stdout.close()
        report['owned_child_reaped'] = child.returncode is not None
        report['stage'] = prior_stage
    report['stage'] = 'prerequisite-checks'
    if report['arch'] != args.expected_arch or not all(report.get(key) for key in (
            'gui_domain_observed', 'other_owned_process_listener_visible', 'occupied_bind_refused', 'owned_child_reaped')):
        raise RuntimeError('Native prerequisites failed; do not claim macOS support.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-arch', choices=['arm64', 'x86_64'], required=True)
    args = parser.parse_args()
    report = {'scope': 'native prerequisites only; no service/install/update/device acceptance',
              'os': platform.system(), 'os_version': platform.mac_ver()[0], 'arch': platform.machine(),
              'python_version': platform.python_version(), 'psutil_version': psutil.__version__,
              'expected_arch': args.expected_arch, 'owned_child_reaped': False,
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'privilege_limit': 'non-root effective UID only; standard-user permissions not established'}
    try:
        probe(args, report)
    except Exception as error:
        report.update(status='failed', failure_stage=report.get('stage'), error_type=type(error).__name__)
        print(json.dumps(report), flush=True)
        return 1
    report.update(status='prerequisites observed', stage='complete')
    print(json.dumps(report), flush=True)
    return 0


if __name__ == '__main__': sys.exit(main())
