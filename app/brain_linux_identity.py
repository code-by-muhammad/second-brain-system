"""Boot-relative Linux process lifetimes; never signal or authorize a PID alone."""
from __future__ import annotations

import os
from pathlib import Path
import re
import time


class IdentityError(RuntimeError):
    pass


def _read(path: Path, limit: int) -> bytes:
    descriptor=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        payload=os.read(descriptor,limit+1)
        if len(payload)>limit:raise IdentityError('Linux lifetime metadata exceeds its bound.')
        return payload
    finally:os.close(descriptor)


def boot_id() -> str:
    value=_read(Path('/proc/sys/kernel/random/boot_id'),64).decode('ascii').strip()
    if not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',value):
        raise IdentityError('Linux boot identity is unavailable.')
    return value


def parse_stat(payload: bytes, pid: int) -> dict:
    """Field 22 follows comm, whose text may itself contain spaces or ')'."""
    if type(pid) is not int or not 0<pid<=0x7fffffff or len(payload)>4096:
        raise IdentityError('Linux process lifetime is malformed.')
    try:
        prefix,fields=payload.rsplit(b')',1)
        if not prefix.startswith(str(pid).encode()+b' ('):raise ValueError
        values=fields.split()
        if len(values)<20 or len(values[0])!=1:raise ValueError
        parent=int(values[1]);ticks=int(values[19])
        if not 0<=parent<=0x7fffffff or ticks<=0:raise ValueError
        state=values[0].decode('ascii')
        if state not in ('R','S','D','Z','T','t','X','x','K','W','P','I'):raise ValueError
    except (ValueError,UnicodeError,IndexError):
        raise IdentityError('Linux process lifetime is malformed.') from None
    return {'pid':pid,'start_ticks':ticks,'parent_pid':parent,'state':state}


def process(pid: int) -> dict | None:
    if type(pid) is not int or not 0<pid<=0x7fffffff:
        raise IdentityError('Linux process identifier is invalid.')
    before=boot_id()
    try:raw=_read(Path('/proc')/str(pid)/'stat',4096)
    except (FileNotFoundError,ProcessLookupError):return None
    record=parse_stat(raw,pid)
    if boot_id()!=before:raise IdentityError('Linux boot changed during lifetime inspection.')
    if record['state'] in ('Z','X','x'):return None
    return {'pid':pid,'start_ticks':record['start_ticks'],'boot_id':before}


def marker() -> dict:
    before=boot_id()
    frequency=os.sysconf('SC_CLK_TCK')
    if type(frequency) is not int or frequency<=0:raise IdentityError('Linux tick frequency is unavailable.')
    ticks=time.clock_gettime_ns(time.CLOCK_BOOTTIME)*frequency//1_000_000_000
    if boot_id()!=before:raise IdentityError('Linux boot changed during clock inspection.')
    return {'boot_id':before,'boottime_ticks':ticks}


def fresh(identity: dict, boundary: dict) -> bool:
    """Strictly newer ticks deliberately refuse an ambiguous same-tick start."""
    if (not isinstance(identity,dict) or not isinstance(boundary,dict)
        or type(boundary.get('boottime_ticks')) is not int or boundary['boottime_ticks']<0
        or not isinstance(boundary.get('boot_id'),str)):
        raise IdentityError('Linux quiescence boundary is unavailable.')
    if (type(identity.get('start_ticks')) is not int or type(identity.get('supervisor_start_ticks')) is not int
        or identity.get('boot_id')!=boundary['boot_id'] or identity.get('supervisor_boot_id')!=boundary['boot_id']):
        return False
    return min(identity['start_ticks'],identity['supervisor_start_ticks'])>boundary['boottime_ticks']
