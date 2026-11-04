"""Unprivileged macOS TCP presence inventory; never process/signal authority.

Output is checked after capture, so the byte limit is an acceptance bound rather
than a streaming memory limit. Unknown native formats always refuse.
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


def native_tool() -> Path:
    tool = Path('/usr/sbin/netstat')
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
    return parse(value.stdout, port)
