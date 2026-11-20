"""Durable launch and exit evidence for the non-systemd Linux service."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import time
import uuid

from filelock import FileLock, Timeout
import psutil

import brain_linux_identity as identity
import brain_process as processes
import brain_settings as cfg


GENERATION = 'user-service-generation.json'
BLOCKER = 'user-service-blocked.json'
ROLES = ('keeper', 'supervisor', 'server', 'maintenance', 'index_bootstrap', 'index_worker')
UUID = re.compile(r'[0-9a-f]{32}')


class LifecycleError(processes.OwnershipError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


def _error(reason='generation_unverified', message='User-service generation ownership could not be verified.'):
    return LifecycleError(reason, message)


def _boot():
    try:
        return identity.boot_id()
    except (identity.IdentityError, OSError) as error:
        raise _error() from error


def _record_matches(record, live):
    return live is not None and all(record.get(key) == live[key] for key in ('pid', 'start_ticks', 'boot_id'))


def _session(pid):
    try:
        return os.getsid(pid)
    except ProcessLookupError:
        return None
    except OSError as error:
        raise _error('session_unverified', 'User-service session membership is unavailable.') from error


def _arguments(pid):
    try:
        raw = identity._read(Path('/proc')/str(pid)/'cmdline', 65536)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (identity.IdentityError, OSError) as error:
        raise _error() from error
    if not raw.endswith(b'\0'):
        return None
    return [os.fsdecode(arg) for arg in raw[:-1].split(b'\0')]


def _command(role, argv, interpreter):
    if not isinstance(argv, list) or not argv or len(argv) > 10 or any(
            not isinstance(arg, str) or not arg or '\0' in arg for arg in argv):
        return False
    if argv[0] != interpreter:
        return False
    app = cfg.ROOT/'app'
    script = str(app/('brain_'+role+'.py'))
    if role == 'keeper':
        return argv[1:] == [str(app/'brain_linux_user_service.py'), 'supervise']
    if role == 'index_bootstrap':
        return argv[1:] in (['-B', script], ['-B', script, '--reconcile'])
    if role == 'index_worker':
        return processes.index_worker_form(argv, script)
    return argv[1:] == [script]


def _read(registration, filename):
    raw = registration._read_file(cfg.STATE/filename)
    if raw is None:
        return None
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise _error() from error
    if not isinstance(result, dict):
        raise _error()
    return result


def _write(registration, filename, record):
    raw = json.dumps(record, ensure_ascii=True, sort_keys=True).encode()
    if len(raw) > 65536 or registration.keeper_path.parent != cfg.STATE:
        raise _error()
    from brain_service import _atomic
    _atomic(cfg.STATE/filename, record)


def generation(registration):
    value = _read(registration, GENERATION)
    if value is None:
        return None
    records = value.get('roles')
    interpreter = value.get('interpreter')
    boot = value.get('boot_id')
    if (value.get('format') != 1 or value.get('root') != str(cfg.ROOT)
            or not isinstance(value.get('generation'), str) or not UUID.fullmatch(value['generation'])
            or not isinstance(boot, str) or not re.fullmatch(r'[0-9a-f-]{36}', boot)
            or not isinstance(interpreter, str) or not Path(interpreter).is_absolute()
            or not isinstance(value.get('definition'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['definition'])
            or value.get('drain') not in ('none', 'requested', 'drained')
            or not isinstance(records, dict) or not {'keeper', 'supervisor'} <= records.keys()
            or not records.keys() <= set(ROLES)):
        raise _error()
    for role, record in records.items():
        if (not isinstance(record, dict) or record.get('role') != role or record.get('boot_id') != boot
                or not _command(role, record.get('argv'), interpreter)
                or any(type(record.get(key)) is not int or record[key] <= 0 for key in ('pid', 'start_ticks', 'sid'))):
            raise _error()
    keeper = records['keeper']
    for role, record in records.items():
        if record['sid'] != (record['pid'] if role == 'index_worker' else keeper['pid']):
            raise _error()
        if record['start_ticks'] < keeper['start_ticks']:
            raise _error()
    if 'index_worker' in records and 'index_bootstrap' not in records:
        raise _error()
    if len({(item['pid'],item['start_ticks']) for item in records.values()}) != len(records):
        raise _error()
    return value


def _binding(registration, value):
    unit = registration._unit_bytes()
    if (unit is None or hashlib.sha256(unit).hexdigest() != value['definition']
            or registration.known.get(unit, (None,))[0] != value['interpreter']):
        raise _error('generation_definition_changed', 'User-service generation definition differs from its launch.')


def _capture(pid, role, argv, sid, parent=None):
    if not _command(role, argv, argv[0]):
        raise _error()
    live = processes._linux_lifetime(pid)
    if not live or _session(pid) != sid or _arguments(pid) != argv:
        raise _error()
    record = {**live, 'role': role, 'argv': argv, 'sid': sid}
    with ExitStack() as stack:
        handle = _retain(stack, record)
        if handle is None:
            raise _error()
        if parent is not None:
            parent_handle = _retain(stack, parent)
            try:
                parent_pid = psutil.Process(pid).ppid()
            except psutil.Error as error:
                raise _error() from error
            if (parent_handle is None or parent_pid != parent['pid'] or not parent_handle.alive()
                    or live['start_ticks'] < parent['start_ticks']):
                raise _error()
        if not handle.alive():
            raise _error()
    return record


class _Handle:
    def __init__(self, fd, pid, role):
        self.fd = fd
        self.pid = pid
        self.role = role

    def alive(self):
        poll = select.poll()
        poll.register(self.fd, select.POLLIN)
        return not poll.poll(0)

    def send(self, signum):
        if self.alive():
            try:
                signal.pidfd_send_signal(self.fd, signum)
            except ProcessLookupError:
                pass
            except OSError as error:
                raise _error('generation_signal_failed', 'A verified user-service process could not be signalled.') from error


def _retain(stack, record, *, verify_role=True):
    pid = record['pid']
    live = processes._linux_lifetime(pid)
    if not _record_matches(record, live):
        return None
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return None
    except (AttributeError, OSError) as error:
        raise _error('pidfd_unavailable', 'A verified process handle is unavailable on this Linux host.') from error
    stack.callback(os.close, fd)
    handle = _Handle(fd, pid, record.get('role'))
    if not handle.alive():
        return None
    if not _record_matches(record, processes._linux_lifetime(pid)):
        if not handle.alive():
            return None
        raise _error()
    if verify_role:
        argv = _arguments(pid)
        if argv != record['argv'] or _session(pid) != record['sid']:
            if not handle.alive():
                return None
            raise _error()
        owned = processes.inspect(pid)
        try:
            if owned is None or owned.role != record['role'] or owned.lifetime != live:
                if not handle.alive():
                    return None
                raise _error()
        finally:
            if owned is not None:
                owned.close()
    return handle if handle.alive() else None


def publish_supervisor(registration, child, keeper):
    argv = list(registration.argv)
    deadline = time.monotonic()+2
    while True:
        try:
            supervisor = _capture(child.pid, 'supervisor', argv, keeper['sid'], keeper)
            break
        except processes.OwnershipError:
            if child.poll() is not None or time.monotonic() >= deadline:
                raise
            time.sleep(.02)
    value = {'format': 1, 'root': str(cfg.ROOT), 'interpreter': argv[0], 'boot_id': keeper['boot_id'],
             'generation': uuid.uuid4().hex, 'definition': hashlib.sha256(registration.installable).hexdigest(),
             'roles': {'keeper': keeper, 'supervisor': supervisor}, 'drain': 'none'}
    try:
        _binding(registration, value)
        _write(registration, GENERATION, value)
    except BaseException:
        end_unreleased_supervisor(child, supervisor)
        raise
    return supervisor


def end_unreleased_supervisor(child, record):
    with ExitStack() as stack:
        handle = _retain(stack,record)
        if handle is not None:
            handle.send(signal.SIGTERM)
            if not GenerationDrain._wait([handle],time.monotonic()+2):
                handle.send(signal.SIGKILL)
        child.wait(timeout=5)


def keeper_record(registration):
    return _capture(os.getpid(), 'keeper', list(registration.keeper_argv), os.getpid())


def _active(registration, value):
    from brain_service import spawn_allowed
    return value is not None and value['boot_id'] == _boot() and value['drain'] == 'none' and (
        registration._enabled() is True and spawn_allowed())


def enroll(registration, role, child):
    value = generation(registration)
    if not _active(registration, value):
        raise _error('launch_admission_closed', 'User-service generation has no active spawn admission.')
    _binding(registration, value)
    parent_role = 'index_bootstrap' if role == 'index_worker' else 'supervisor'
    parent = value['roles'].get(parent_role)
    if parent is None or parent['pid'] != os.getpid():
        raise _error()
    before = value['roles'].get(role)
    if before is not None and _record_matches(before, processes._linux_lifetime(before['pid'])):
        raise _error('previous_role_live', 'An earlier enrolled process is still live.')
    if role == 'index_bootstrap':
        worker = value['roles'].get('index_worker')
        if worker is not None and _record_matches(worker, processes._linux_lifetime(worker['pid'])):
            raise _error('previous_worker_live', 'An earlier enrolled index worker is still live.')
        value['roles'].pop('index_worker', None)
    sid = child.pid if role == 'index_worker' else parent['sid']
    deadline = time.monotonic()+2
    while True:
        try:
            record = _capture(child.pid, role, list(child.args), sid, parent)
            break
        except processes.OwnershipError:
            if child.poll() is not None or time.monotonic() >= deadline:
                raise
            time.sleep(.02)
    value['roles'][role] = record
    _write(registration, GENERATION, value)


def supervisor_allowed(registration):
    value = generation(registration)
    if not _active(registration, value):
        return False
    _binding(registration, value)
    roles = value['roles']
    if roles['supervisor']['pid'] != os.getpid():
        return False
    with ExitStack() as stack:
        return _retain(stack, roles['keeper']) is not None and _retain(stack, roles['supervisor']) is not None


class Launch:
    def __init__(self, registration, value, manual):
        self.registration = registration
        self.generation = None if manual else value['generation']
        self.manual = manual
        self.managed = not manual

    @contextmanager
    def admission(self):
        from brain_index_bootstrap import LaunchDeferred
        from brain_service import _user_space, transaction, user_space_blocker
        try:
            with FileLock(str(cfg.STATE/'service-control.lock'), timeout=0):
                if self.manual:
                    if transaction() is not None or user_space_blocker() is not None:
                        raise LaunchDeferred('launch_admission_closed')
                    path=cfg.STATE/GENERATION
                    if path.is_symlink() or path.exists():
                        value=generation(_user_space())
                        if value is not None and value['boot_id']==_boot() and value['drain']=='requested':
                            raise LaunchDeferred('launch_admission_closed')
                else:
                    value=generation(self.registration)
                    if (not _active(self.registration,value) or value['generation']!=self.generation
                            or value['roles'].get('index_bootstrap',{}).get('pid')!=os.getpid()):
                        raise LaunchDeferred('launch_admission_closed')
                    _binding(self.registration, value)
                    with ExitStack() as stack:
                        if any(_retain(stack, value['roles'][name]) is None
                               for name in ('keeper', 'supervisor', 'index_bootstrap')):
                            raise LaunchDeferred('launch_admission_closed')
                yield
        except Timeout as error:
            raise LaunchDeferred('launch_admission_busy') from error

    def publish(self, child):
        if not self.manual:
            enroll(self.registration, 'index_worker', child)

    def drained(self):
        if self.manual:
            return False
        value = generation(self.registration)
        return bool(value is not None and value['generation'] == self.generation
                    and value['boot_id'] == _boot() and value['drain'] in ('requested', 'drained'))


def launch_for(registration, manual):
    value = generation(registration)
    if not manual and (value is None or value['boot_id'] != _boot()
                       or value['roles'].get('index_bootstrap', {}).get('pid') != os.getpid()):
        raise _error('bootstrap_unenrolled', 'The index bootstrap does not belong to an enrolled generation.')
    return Launch(registration, value, manual)


def drain_recorded(registration, generation_id):
    value = generation(registration)
    if value is None or value['generation'] != generation_id:
        return False
    if value['drain'] == 'requested':
        return None
    return value['drain'] == 'drained'


def blocked(registration):
    value = _read(registration, BLOCKER)
    if (value is None or value.get('format') != 1 or value.get('root') != str(cfg.ROOT)
            or value.get('boot_id') != _boot()):
        return None
    if (not isinstance(value.get('reason'), str) or len(value['reason']) > 80
            or not isinstance(value.get('guidance'), str) or len(value['guidance']) > 400):
        raise _error()
    try:
        current = generation(registration)
    except processes.OwnershipError:
        current = None
    if value.get('generation') != (current['generation'] if current else None):
        return None
    return {'status': 'blocked', 'reason': value['reason'], 'guidance': value['guidance']}


def set_blocked(registration, error):
    try:
        value = generation(registration)
    except processes.OwnershipError:
        value = None
    reason = getattr(error, 'reason', 'generation_cleanup_blocked')
    _write(registration, BLOCKER, {'format': 1, 'root': str(cfg.ROOT), 'boot_id': _boot(),
           'generation': value['generation'] if value else None,
           'reason': reason, 'guidance': str(error)[:400]})


def clear_blocked(registration):
    registration._remove_state_file(cfg.STATE/BLOCKER)


def _legacy_records(registration):
    legacy = registration._keeper_record()
    if legacy is None:
        return {}
    unit = registration._unit_bytes()
    argv = registration.known.get(unit)
    result = {}
    for role in ('keeper', 'supervisor'):
        record = legacy.get(role)
        if record is None:
            continue
        if (not isinstance(record, dict) or any(type(record.get(key)) is not int or record[key] <= 0
                for key in ('pid', 'sid', 'start_ticks')) or not isinstance(record.get('boot_id'), str)):
            raise _error()
        if role == 'keeper':
            if record['pid'] != record['sid']:
                raise _error()
            command = [argv[0], str(cfg.ROOT/'app/brain_linux_user_service.py'), 'supervise'] if argv else None
        else:
            command = list(argv) if argv else None
        if _record_matches(record, processes._linux_lifetime(record['pid'])) and command is None:
            raise _error()
        result[role] = {**record, 'role': role, 'argv': command}
    return result


def _sessions(records, boot):
    sessions = {}
    for record in records.values():
        if record['boot_id'] != boot:
            continue
        sid = record['sid']
        leader = processes._linux_lifetime(sid)
        known = next((item for item in records.values() if item['pid'] == sid), None)
        if known is not None and leader is not None and not _record_matches(known, leader) and _session(sid) == sid:
            # The old session cannot coexist with another live session of the same id.
            continue
        sessions[sid] = min(record['start_ticks'], sessions.get(sid, record['start_ticks']))
    return sessions


def _session_members(records, boot):
    sessions = _sessions(records, boot)
    if not sessions:
        return set()
    result = set()
    for count, pid in enumerate(psutil.pids()):
        if count >= 32768:
            raise _error('process_inventory_limit', 'User-service process inventory exceeds its safety bound.')
        sid = _session(pid)
        if sid in sessions:
            live = processes._linux_lifetime(pid)
            if live is not None and live['boot_id'] == boot and live['start_ticks'] >= sessions[sid]:
                if _session(pid) != sid:
                    raise _error()
                result.add(pid)
    return result


class GenerationDrain:
    """Retain verified lifetimes through control admission, End and final proof."""
    def __init__(self, registration, stack):
        self.registration = registration
        self.stack = stack
        self.value = generation(registration)
        self.boot = _boot()
        self.records = self.value['roles'] if self.value is not None else _legacy_records(registration)
        self.current = self.value is not None and self.value['boot_id'] == self.boot
        if self.current:
            _binding(registration, self.value)
        self.handles = {}
        self.started = bool(self.current and self.value['drain'] == 'requested')

    def inventory(self, *, exclude=None):
        value = generation(self.registration)
        if value != self.value:
            raise _error()
        for record in self.records.values():
            if record['boot_id'] != self.boot:
                continue
            handle = _retain(self.stack, record)
            if handle is not None:
                self.handles[handle.pid] = handle
        for role, record in self.records.items():
            parent = self.records.get({'supervisor':'keeper','index_worker':'index_bootstrap'}.get(role,'supervisor'))
            if role=='keeper' or parent is None:
                continue
            child_handle, parent_handle = self.handles.get(record['pid']), self.handles.get(parent['pid'])
            if child_handle is not None and child_handle.alive() and parent_handle is not None and parent_handle.alive():
                try:
                    parent_pid = psutil.Process(record['pid']).ppid()
                except psutil.NoSuchProcess:
                    if not child_handle.alive():
                        continue
                    raise _error() from None
                except psutil.Error as error:
                    raise _error() from error
                if parent_pid != parent['pid'] and parent_handle.alive() and child_handle.alive():
                    raise _error('generation_ancestry_changed', 'A recorded live process has contradictory ancestry.')
        roots = processes.roots()
        children = []
        try:
            live = {pid: handle for pid, handle in self.handles.items() if handle.alive()}
            for root in roots:
                if root.alive() and root.role == 'index_worker' and (
                        root.pid not in live or live[root.pid].role != 'index_worker' or not self.current):
                    raise _error('unmanaged_index_worker',
                                 'A legacy, manual or unverified reindex process is still active; preserve it before stopping/upgrading.')
                if root.alive() and root.role == 'index_bootstrap' and self.current and root.pid not in live:
                    raise _error('unmanaged_index_bootstrap', 'An unenrolled index bootstrap is still active.')
            # A legacy keeper is not a process-discovery root. Its verified children
            # that can run work descend from the recorded supervisor.
            anchored = [root for root in roots if root.pid in live and root.alive()]
            children = processes.descendants(anchored)
            for child in children:
                if child.pid in live:
                    continue
                handle = _retain(self.stack, {**child.lifetime, 'role': 'descendant'}, verify_role=False)
                if handle is not None:
                    self.handles[handle.pid] = handle
                    live[handle.pid] = handle
            possible = _session_members(self.records, self.boot)
            possible.update(root.pid for root in roots if root.alive())
            if exclude is not None:
                possible.discard(exclude)
            if possible - live.keys():
                raise _error('unverified_generation_survivor', 'An unverified runtime process blocks generation cleanup.')
            return {pid: handle for pid, handle in live.items() if pid != exclude}
        finally:
            for item in children + roots:
                item.close()

    def _set_drain(self, state):
        value = {**self.value, 'drain': state}
        _write(self.registration, GENERATION, value)
        self.value = value

    @staticmethod
    def _wait(handles, deadline):
        while any(handle.alive() for handle in handles) and time.monotonic() < deadline:
            time.sleep(min(.05, max(0, deadline-time.monotonic())))
        return all(not handle.alive() for handle in handles)

    def _end(self, handles, deadline, term_seconds):
        if not handles:
            return
        if time.monotonic() >= deadline:
            raise _error('generation_drain_timeout', 'User-service stop budget expired before process exit.')
        for handle in handles:
            handle.send(signal.SIGTERM)
        if self._wait(handles, min(deadline, time.monotonic()+term_seconds)):
            return
        if time.monotonic() >= deadline:
            raise _error('generation_drain_timeout', 'User-service termination did not finish within its stop budget.')
        for handle in handles:
            handle.send(signal.SIGKILL)
        if not self._wait(handles, min(deadline, time.monotonic()+5)):
            raise _error('generation_drain_timeout', 'User-service escalation did not prove process exit.')

    def _index_handles(self, live):
        worker = self.records.get('index_worker')
        worker_session = worker['sid'] if worker else None
        return [handle for handle in live.values() if handle.role in ('index_bootstrap', 'index_worker')
                or (handle.role == 'descendant' and _session(handle.pid) == worker_session)]

    def drain_index(self, deadline, *, exclude=None):
        live = self.inventory(exclude=exclude)
        if not self.current or self.value['drain'] == 'drained':
            return
        active = self._index_handles(live)
        if active or self.records.get('index_bootstrap') is not None or self.started:
            self.started = True
            self._set_drain('requested')
        bootstrap = next((handle for handle in active if handle.role == 'index_bootstrap'), None)
        escalated = False
        if bootstrap is not None:
            if time.monotonic() >= deadline:
                raise _error('generation_drain_timeout', 'User-service stop budget expired before index cancellation.')
            bootstrap.send(signal.SIGTERM)
            grace = min(deadline, time.monotonic()+8)
            if not self._wait([bootstrap], grace):
                if time.monotonic() >= deadline:
                    raise _error('generation_drain_timeout', 'Enrolled index bootstrap did not finish within its stop budget.')
                for handle in active:
                    handle.send(signal.SIGKILL)
                escalated = True
        if not escalated:
            self._end([handle for handle in active if handle is not bootstrap and handle.alive()], deadline, 2)
        if not self._wait(active, min(deadline, time.monotonic()+5)):
            raise _error('generation_drain_timeout', 'Enrolled index processes did not exit.')
        fresh = self.inventory(exclude=exclude)
        if self._index_handles(fresh):
            raise _error('generation_drain_unresolved', 'An enrolled index process is still active.')
        if self.started:
            self._set_drain('drained')

    def finish(self, deadline, *, exclude=None):
        live = self.inventory(exclude=exclude)
        self._end(list(live.values()), deadline, 5)
        self.prove_quiet(exclude=exclude)
        return bool(live)

    def prove_quiet(self, *, exclude=None, check_port=True):
        if self.inventory(exclude=exclude):
            raise _error('generation_not_quiet', 'A verified user-service process has not exited.')
        if check_port and processes._listeners():
            raise _error('port_not_empty', 'The configured port remains occupied or unattributed.')
        if self.current and self.value['drain'] == 'requested':
            # A controller may have died after signalling. A fresh full proof
            # allows an interrupted receipt to be settled under its own lock.
            self._set_drain('drained')

    def prove_recovery_port(self, deadline):
        server = self.records.get('server') if self.current else None
        proven = False
        for attempt in range(3):
            remaining=deadline-time.monotonic()
            if remaining<=0:
                break
            try:
                rows = processes._listeners()
            except processes.ListenerUncertain:
                rows = None
            if rows==[]:
                return
            if rows:
                known=[row for row in rows if row.lifetime is not None]
                if (any(row.laddr.ip!='127.0.0.1' for row in rows) or len(known)>1
                        or (known and (server is None or not _record_matches(server,known[0].lifetime)))):
                    break
                if len(rows)==1 and known:
                    with ExitStack() as stack:
                        if _retain(stack,server) is not None:
                            if proven:return
                            proven=True
                            continue
            proven=False
            if attempt<2:
                time.sleep(min(.05,max(0,deadline-time.monotonic())))
        raise _error('port_ownership_uncertain', 'The configured port is occupied without verified generation ownership.')


def retain_worker(stack, child):
    """Pin the runner's own Linux child before it is enrolled or released."""
    record = _capture(child.pid, 'index_worker', list(child.args), child.pid)
    return record, _retain(stack, record)


def clean_worker(stack, child, retained, pending):
    """Close a managed pass through verified handles, even if enrolment failed."""
    if retained is None:
        # A child that could not be pinned was never given the start line.
        # Closing stdin makes it exit, and admission stays held until it does.
        child.wait(timeout=5)
        return
    record, worker = retained
    handles = []
    owner = processes.inspect(child.pid) if worker is not None and worker.alive() else None
    children = []
    try:
        if owner is not None and owner.lifetime and _record_matches(record, owner.lifetime):
            children = processes.descendants([owner])
            for process in children:
                handle = _retain(stack, {**process.lifetime, 'role': 'descendant'}, verify_role=False)
                if handle is not None:
                    handles.append(handle)
    finally:
        if owner is not None:
            owner.close()
        for process in children:
            process.close()
    if worker is not None:
        handles.insert(0, worker)
    try:
        for handle in handles:
            handle.send(signal.SIGTERM)
        if not GenerationDrain._wait(handles,time.monotonic()+2):
            for handle in handles:
                handle.send(signal.SIGKILL)
        child.wait(timeout=5)
        if any(handle.alive() for handle in handles):
            raise _error('worker_exit_unproved', 'A retained index worker process did not exit.')
    except (OSError, subprocess.TimeoutExpired) as error:
        pending()
        raise _error('worker_exit_unproved', 'A retained index worker process did not exit.') from error
