"""Unprivileged macOS TCP presence inventory; never process/signal authority.

Output is checked after capture, so the byte limit is an acceptance bound rather
than a streaming memory limit. Unknown native formats always refuse.

macOS 27 returns an empty netstat table to callers that are not system binaries.
Only then the calling user's own listeners are read with lsof. Listeners of other
users are not visible on that path, and the port is never probed to find them.
"""
from pathlib import Path
import stat
import subprocess


class InventoryError(RuntimeError):
    pass


HEADERS = (
    ('Active', 'Internet', 'connections', '(including', 'servers)'),
    ('Proto', 'Recv-Q', 'Send-Q', 'Local', 'Address', 'Foreign', 'Address', '(state)'),
)
STATES = frozenset(('CLOSED', 'LISTEN', 'SYN_SENT', 'SYN_RCVD', 'SYN_RECEIVED',
                    'ESTABLISHED', 'CLOSE_WAIT', 'FIN_WAIT_1', 'FIN_WAIT_2',
                    'CLOSING', 'LAST_ACK', 'TIME_WAIT'))


def parse(raw: bytes, port: int) -> tuple[tuple[str, str, int], ...]:
    if not isinstance(raw, bytes) or len(raw) > 2_000_000 or type(port) is not int or not 1 <= port <= 65535:
        raise InventoryError('Native TCP inventory input is outside its bound.')
    malformed = False
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        malformed = True
    if malformed:
        raise InventoryError('Native TCP inventory is unreadable.') from None
    lines = [tuple(line.split()) for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or tuple(lines[:2]) != HEADERS:
        raise InventoryError('Native TCP inventory is unreadable.')
    result = []
    for fields in lines[2:]:
        if (len(fields) != 6 or fields[0] not in ('tcp4', 'tcp6', 'tcp46')
                or not fields[1].isdigit() or not fields[2].isdigit() or fields[-1] not in STATES):
            raise InventoryError('Native TCP inventory is unreadable.')
        host, separator, number = fields[3].rpartition('.')
        if not separator or not host or not (number == '*' or number.isdigit()):
            raise InventoryError('Native TCP inventory is unreadable.')
        if number == '*':
            if fields[-1] == 'LISTEN':
                raise InventoryError('Native TCP inventory is unreadable.')
            continue
        if len(number) > 5 or int(number) > 65535:
            raise InventoryError('Native TCP inventory is unreadable.')
        if fields[-1] == 'LISTEN' and int(number) == port:
            result.append((fields[0], host, port))
    return tuple(sorted(result))


def parse_own(raw: bytes, port: int) -> tuple[tuple[str, str, int], ...]:
    """Read `lsof -Fptn` records: one p line per process, then f, t, n per socket."""
    if not isinstance(raw, bytes) or len(raw) > 2_000_000 or type(port) is not int or not 1 <= port <= 65535:
        raise InventoryError('Native TCP inventory input is outside its bound.')
    malformed = False
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        malformed = True
    result = []
    expected = 'p'
    family = ''
    for line in () if malformed else text.splitlines():
        key, value = line[:1], line[1:]
        if not key or key not in expected:
            malformed = True
        elif key in 'pf':
            malformed = not value.isdigit()
            expected = 'f' if key == 'p' else 't'
        elif key == 't':
            malformed = value not in ('IPv4', 'IPv6')
            family = 'tcp4' if value == 'IPv4' else 'tcp6'
            expected = 'n'
        else:
            host, separator, number = value.rpartition(':')
            if family == 'tcp6' and host.startswith('[') and host.endswith(']'):
                host = host[1:-1]
            malformed = not separator or not host or number != str(port)
            result.append((family, host, port))
            expected = 'pf'
        if malformed:
            break
    if malformed or not result or expected != 'pf':
        raise InventoryError('Native TCP inventory is unreadable.')
    return tuple(sorted(result))


def native_tool(path: str = '/usr/sbin/netstat') -> Path:
    tool = Path(path)
    failed = False
    try:
        for part in (tool, *tool.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                failed = True
                break
        if not failed and not stat.S_ISREG(tool.stat().st_mode):
            failed = True
    except OSError:
        failed = True
    if failed:
        raise InventoryError('Native TCP inventory executable is unavailable or unprotected.') from None
    return tool


def read(port: int, *, timeout: float = 5) -> tuple[tuple[str, str, int], ...]:
    if type(timeout) not in (int, float) or not 0 < timeout <= 5:
        raise InventoryError('Native TCP inventory timeout is outside its bound.')
    tool = native_tool()
    failed = False
    try:
        value = subprocess.run([str(tool), '-an', '-p', 'tcp'],
                               env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'},
                               stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    if failed:
        raise InventoryError('Native TCP inventory is unreadable.') from None
    if value.returncode or value.stderr:
        raise InventoryError('Native TCP inventory is unreadable.')
    if value.stdout:
        return parse(value.stdout, port)
    return _read_own(port, timeout)


def _read_own(port: int, timeout: float) -> tuple[tuple[str, str, int], ...]:
    tool = native_tool('/usr/sbin/lsof')
    failed = False
    try:
        value = subprocess.run([str(tool), '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fptn'],
                               env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'},
                               stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    if failed or value.stderr or value.returncode not in (0, 1):
        raise InventoryError('Native TCP inventory is unreadable.') from None
    if value.returncode:
        # lsof exits 1 with no output when nothing matches.
        if value.stdout:
            raise InventoryError('Native TCP inventory is unreadable.')
        return ()
    return parse_own(value.stdout, port)
