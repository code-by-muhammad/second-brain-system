"""Generate per-user OS service definitions with the same Python core."""
import hashlib, json, os, plistlib, re, shlex, subprocess, sys, tempfile, time
from contextlib import ExitStack,contextmanager
from pathlib import Path
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_process as processes
import brain_release as release_files

LABEL='local.second-brain'
TASK=cfg.DEVICE.get('service_name','SecondBrainLocalService')
LINUX_UNIT='second-brain.service'

def _state(): return cfg.ROOT/'state'


def _candidate_platform():
    """Admit the declared native candidate matrix; this is not release approval."""
    import errno
    import struct
    if struct.calcsize('P')!=8:
        raise ValueError('This candidate requires a native 64-bit supported interpreter; no changes made.')
    if sys.platform=='win32':
        # Query the kernel, not WMI/environment-derived architecture strings.
        try:import ctypes
        except ImportError:raise ValueError('Native Windows architecture support is unavailable; no changes made.') from None
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=ctypes.c_void_p
            query=kernel.IsWow64Process2
            query.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ushort),ctypes.POINTER(ctypes.c_ushort)]
            query.restype=ctypes.c_int
            process_machine,native_machine=ctypes.c_ushort(),ctypes.c_ushort()
            if not query(kernel.GetCurrentProcess(),ctypes.byref(process_machine),ctypes.byref(native_machine)):
                raise OSError('Architecture query failed.')
        except (AttributeError,OSError,ctypes.ArgumentError):
            raise ValueError('Native Windows architecture is unavailable; no changes made.') from None
        if process_machine.value!=0 or native_machine.value!=0x8664:
            raise ValueError('This candidate supports native Windows x64 only; no changes made.')
        return
    if sys.platform not in ('darwin','linux'):
        raise ValueError('Unsupported candidate operating system; no changes made.')
    try:machine=os.uname().machine
    except (AttributeError,OSError):raise ValueError('Native architecture is unavailable; no changes made.') from None
    if machine not in (('arm64','x86_64') if sys.platform=='darwin' else ('x86_64',)):
        raise ValueError('Unsupported candidate architecture; no changes made.')
    if sys.platform=='darwin':
        try:import ctypes
        except ImportError:raise ValueError('Native Mac architecture support is unavailable; no changes made.') from None
        try:
            query=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).sysctlbyname
            query.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
            query.restype=ctypes.c_int
            translated=ctypes.c_int();size=ctypes.c_size_t(ctypes.sizeof(translated))
            result=query(b'sysctl.proc_translated',ctypes.byref(translated),ctypes.byref(size),None,0)
            if result==-1 and ctypes.get_errno()==errno.ENOENT:return
            if result!=0 or size.value!=ctypes.sizeof(translated) or translated.value!=0:
                raise OSError('Translated or uncertain process architecture.')
        except (AttributeError,OSError,ctypes.ArgumentError):
            raise ValueError('Use a native Mac interpreter; Rosetta or uncertain translation is refused before changes.') from None


def _qualified_native_platform():
    try:_candidate_platform()
    except ValueError as error:raise processes.OwnershipError(str(error)) from None


def _mac_registration():
    from brain_mac_registration import MacRegistration
    return MacRegistration(LABEL,cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)


def _linux_registration():
    # systemd as PID 1 keeps the qualified user-unit backend, including its refusals.
    # Any other Linux init (tini, a container, a host without systemd) uses the user-space service.
    from brain_linux_user_service import open_registration
    return open_registration(LINUX_UNIT,cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)


def _native_listener_binding(identity,native):
    """A same-root manual pair cannot stand in for the native manager's job."""
    if (not isinstance(identity,dict) or not isinstance(native,dict) or native.get('loaded') is not True or native.get('running') is not True
            or type(native.get('main_pid')) is not int or type(identity.get('supervisor_pid')) is not int
            or native['main_pid']!=identity['supervisor_pid']):
        raise processes.OwnershipError('Owned listener supervisor does not match the verified native manager job.')
    if sys.platform=='linux':
        lifetime=native.get('main_lifetime')
        if (not isinstance(lifetime,dict) or type(lifetime.get('pid')) is not int
                or lifetime['pid']!=native['main_pid']
                or type(lifetime.get('start_ticks')) is not int or type(identity.get('supervisor_start_ticks')) is not int
                or lifetime['start_ticks']!=identity['supervisor_start_ticks']
                or not isinstance(lifetime.get('boot_id'),str) or not isinstance(identity.get('supervisor_boot_id'),str)
                or lifetime['boot_id']!=identity['supervisor_boot_id']):
            raise processes.OwnershipError('Owned Linux listener lifetime does not match the native manager job.')


def _native_control_status(*,optional=False):
    from brain_markdown import bounded_json_file
    try:value=bounded_json_file(_state()/'service-control-status.json',65536)
    except FileNotFoundError:
        if optional:return None
        raise processes.OwnershipError('Native control receipt is missing; hold preserved.') from None
    except (OSError,ValueError,RecursionError):
        raise processes.OwnershipError('Native control receipt is unreadable; hold preserved.') from None
    if not isinstance(value,dict):raise processes.OwnershipError('Native control receipt is invalid; hold preserved.')
    return value


def _native_stop_history(hold_id):
    if hold_id is None:return None
    value=_native_control_status(optional=True)
    if value is None or value.get('hold_id')!=hold_id or value.get('status') not in ('stopping','stop failed'):return None
    prior=value.get('prior_native')
    if (not isinstance(prior,dict) or type(prior.get('enabled')) is not bool
            or not isinstance(prior.get('definition_sha256'),str)
            or type(value.get('end_attempted')) is not bool
            or type(value.get('activation_disable_attempted')) is not bool):
        raise processes.OwnershipError('Native stop history is incomplete; hold preserved.')
    return value


def _native_install_intent(current,expected):
    """Reuse an incomplete install's intent only for the same exact target."""
    if (not isinstance(current,dict) or type(current.get('file_present')) is not bool
        or (type(current.get('enabled')) is not bool and not
            (current['file_present'] is False and current.get('enabled') is None))
        or (current['file_present'] and not isinstance(current.get('definition_sha256'),str))
        or (not current['file_present'] and current.get('definition_sha256') is not None)):
        raise processes.OwnershipError('Native install state is incomplete; preserve its receipt.')
    prior=current;selected=current['enabled'] if type(current['enabled']) is bool else True
    history=_native_control_status(optional=True)
    if (history is None or history.get('status') not in ('installing','install failed')
        or history.get('expected_native_definition_sha256')!=expected):return prior,selected
    original=history.get('prior_native');intent=history.get('requested_enabled')
    if (not isinstance(original,dict) or type(intent) is not bool
        or type(original.get('file_present')) is not bool
        or (type(original.get('enabled')) is not bool and not
            (original['file_present'] is False and original.get('enabled') is None))
        or (original['file_present'] and not isinstance(original.get('definition_sha256'),str))
        or (not original['file_present'] and original.get('definition_sha256') is not None)
        or current.get('definition_sha256') not in (original.get('definition_sha256'),expected)):
        raise processes.OwnershipError('Native install retry no longer matches its original or target definition.')
    return original,intent


def _windows_task_identity(task):
    if (not isinstance(task,dict) or type(task.get('enabled')) is not bool
            or not isinstance(task.get('definition_sha256'),str)
            or not re.fullmatch('[a-f0-9]{64}',task['definition_sha256'])):
        raise processes.OwnershipError('Windows task has no verified stable definition fingerprint; hold preserved.')
    return task['definition_sha256']


def _windows_stop_fields(task,hold_id,disable_attempted,end_attempted):
    return {'root':str(cfg.ROOT),'task_address':_task_address(),'hold_id':hold_id,
            'prior_task_present':task is not None,'prior_task_enabled':task['enabled'] if task is not None else None,
            'prior_task_definition_sha256':_windows_task_identity(task) if task is not None else None,
            'activation_disable_attempted':disable_attempted,'end_attempted':end_attempted}


def _windows_stop_history(hold_id):
    if hold_id is None:return None
    value=_native_control_status(optional=True)
    if value is None or value.get('hold_id')!=hold_id or value.get('status') not in ('stopping','stop failed'):return None
    if (value.get('root')!=str(cfg.ROOT) or value.get('task_address')!=_task_address()
            or type(value.get('prior_task_present')) is not bool
            or not {'prior_task_enabled','prior_task_definition_sha256'}<=value.keys()
            or type(value.get('activation_disable_attempted')) is not bool
            or type(value.get('end_attempted')) is not bool
            or (value['prior_task_present'] and (type(value['prior_task_enabled']) is not bool
                or not isinstance(value['prior_task_definition_sha256'],str)
                or not re.fullmatch('[a-f0-9]{64}',value['prior_task_definition_sha256'])))
            or (not value['prior_task_present'] and (value['prior_task_enabled'] is not None
                or value['prior_task_definition_sha256'] is not None or value['activation_disable_attempted']))):
        raise processes.OwnershipError('Windows stop history is incomplete; preserve the original controller/hold evidence.')
    return value


def _linux_boundary(record,prefix):
    return {'boot_id':record.get(prefix+'boot_id'),'boottime_ticks':record.get(prefix+'boottime_ticks')}


@contextmanager
def _control(deadline=None):
    budget=5 if deadline is None else min(5,max(0,deadline-time.monotonic()))
    lock=FileLock(str(_state()/'service-control.lock'),timeout=budget)
    try:lock.acquire()
    except Timeout as error:raise processes.OwnershipError('Service control is busy; retry after the current controller completes.') from error
    try:yield
    finally:lock.release()


def _tool(name):
    if os.name!='nt':return name
    import ctypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    function=api.GetSystemDirectoryW
    function.argtypes=[ctypes.c_wchar_p,ctypes.c_uint];function.restype=ctypes.c_uint
    buffer=ctypes.create_unicode_buffer(32768)
    count=function(buffer,len(buffer))
    if not 0<count<len(buffer):raise processes.OwnershipError('Windows system directory cannot be verified.')
    path=Path(buffer.value)/('WindowsPowerShell/v1.0/powershell.exe' if name=='powershell.exe' else name)
    if not path.is_file():raise processes.OwnershipError('Required native system tool is unavailable.')
    return str(path)


def _atomic(path,value):
    if path.is_symlink() or os.path.isjunction(path) or not path.resolve().is_relative_to(_state()):
        raise processes.OwnershipError('Unsafe service control path.')
    for parent in path.parents:
        if parent==_state().parent:break
        if parent.is_symlink() or parent.is_junction():raise processes.OwnershipError('Linked service control directory is refused.')
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.service-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            json.dump(value,output,ensure_ascii=True);output.flush();os.fsync(output.fileno())
        os.replace(name,path)
        release_files.sync_directory(path.parent)
    finally:Path(name).unlink(missing_ok=True)


def transaction():
    path=_state()/'service-transaction.json'
    if not path.exists():return None
    if path.is_symlink() or os.path.isjunction(path) or path.stat().st_size>65536:
        raise processes.OwnershipError('Service transaction is unavailable or invalid.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or not re.fullmatch('[a-f0-9]{32}',value.get('hold_id','')) or value.get('root')!=str(cfg.ROOT):
        raise processes.OwnershipError('Service transaction identity is invalid.')
    return value


def _match_transaction(hold_id):
    value=transaction()
    if value is not None and value['hold_id']!=hold_id:
        raise processes.OwnershipError('An update transaction owns this runtime. Inspect service recover; do not override its hold.')
    if hold_id is not None and value is None:
        raise processes.OwnershipError('The named update transaction does not exist.')
    return value


def _held_snapshot(value):
    """Validate the existing sealed images before admitting/mutating a hold."""
    from brain_markdown import bounded_file_bytes
    def read(path,limit):
        if any(part.is_symlink() or part.is_junction() for part in (path,*path.parents)):
            raise processes.OwnershipError('Linked snapshot authority is refused.')
        return bounded_file_bytes(path,limit)
    try:
        target=Path(value.get('snapshot',''))
        if not target.is_absolute() or target.resolve()!=target or not target.is_relative_to(cfg.ROOT/'backups'):
            raise processes.OwnershipError('Snapshot path is outside this runtime.')
        raw=read(target/'snapshot.json',64*1024*1024)
        if hashlib.sha256(raw).hexdigest()!=value.get('snapshot_sha256'):
            raise processes.OwnershipError('Snapshot changed; hold preserved.')
        snapshot=json.loads(raw)
        if (not isinstance(snapshot,dict) or snapshot.get('format')!=3 or snapshot.get('root')!=str(cfg.ROOT)
                or not isinstance(snapshot.get('previous'),dict) or not isinstance(snapshot.get('app_sha256'),dict)
                or not isinstance(snapshot.get('files'),list) or not snapshot['files']):
            raise processes.OwnershipError('A sealed format-3 snapshot is required; hold preserved.')
        registration=snapshot.get('registration')
        keys={'kind','definition_sha256','enabled','interpreter'}|({'xml','owner_sid'} if sys.platform=='win32' else set())
        digest=lambda value:isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None
        absent=(sys.platform=='linux' and registration=={'kind':'linux','backend':'user-space','present':False})
        if not absent and (not isinstance(registration,dict) or set(registration)!=keys or registration.get('kind')!=sys.platform
                or registration.get('enabled') is not True or not digest(registration.get('definition_sha256'))
                or not isinstance(registration.get('interpreter'),str) or not Path(registration['interpreter']).is_absolute()
                or registration['interpreter']!=snapshot['previous'].get('python')):
            raise processes.OwnershipError('Sealed native registration is invalid; hold preserved.')
        if sys.platform=='win32':
            if (not isinstance(registration['xml'],str) or not registration['xml'] or len(registration['xml'].encode('utf-8'))>65536
                    or not isinstance(registration['owner_sid'],str) or not re.fullmatch(r'S-1-[0-9]+(?:-[0-9]+)+',registration['owner_sid'])):
                raise processes.OwnershipError('Sealed Windows XML/SID is invalid; hold preserved.')
            # Snapshot integrity binds username-form UserId to the SID measured
            # by the original getter; explicit SID-form XML is checked directly.
            original=_windows_definition({'xml':registration['xml'],'owner_sid':registration['owner_sid'],
                'current_sid':registration['owner_sid']})
            if (original['definition_sha256']!=registration['definition_sha256'] or not original['enabled']
                    or original['interpreter']!=registration['interpreter']):
                raise processes.OwnershipError('Sealed Windows ownership/fingerprint disagrees; hold preserved.')
        for index,row in enumerate(snapshot['files']):
            if not isinstance(row,dict) or (index==0 and row.get('path')!=str(cfg.ROOT/'device.json')):
                raise processes.OwnershipError('Sealed device/client image identity is invalid.')
            before=row.get('before_sha256')
            if before is not None and (not digest(before) or hashlib.sha256(read(target/str(index),64*1024*1024)).hexdigest()!=before):
                raise processes.OwnershipError('Sealed device/client image changed; hold preserved.')
        if release_files.inventory(target/'app',allow_cache=False)!=release_files.app_map(snapshot['app_sha256']):
            raise processes.OwnershipError('App snapshot has extra, missing or changed payloads; hold preserved.')
        deployment=snapshot.get('deployment')
        if deployment is not None:
            rows=release_files.artifact_rows(target,snapshot)
            release_files.app_map(deployment['target_app_sha256'])
            if (deployment['hold_id']!=value['hold_id'] or absent!=(deployment['service_policy'] in ('first','absent'))
                    or not {'device','launcher'} <= {row['kind'] for row,_,_ in rows if row['phase']=='deploy'}):
                raise processes.OwnershipError('Sealed deployment UUID or policy differs from the active transaction.')
        elif absent:
            raise processes.OwnershipError('Native absence requires sealed deployment images.')
        binding=snapshot.get('previous_binding')
        if (not isinstance(binding,dict) or set(binding)!={'before_sha256','after_sha256'}
                or (binding['before_sha256'] is not None and not digest(binding['before_sha256']))
                or not digest(binding['after_sha256'])):
            raise processes.OwnershipError('Sealed previous binding hashes are invalid.')
        for phase in ('before','after'):
            expected=binding[phase+'_sha256'];path=target/('previous-binding.'+phase)
            if expected is None:
                if path.exists() or path.is_symlink() or path.is_junction():raise processes.OwnershipError('Unexpected prior binding image.')
            elif hashlib.sha256(read(path,65536)).hexdigest()!=expected:
                raise processes.OwnershipError('Sealed previous binding image changed; hold preserved.')
        return snapshot
    except (OSError,ValueError,TypeError,KeyError,RecursionError) as error:
        raise processes.OwnershipError('Snapshot is unreadable or invalid; hold preserved.') from error


def _runtime_app_hashes():
    try:return release_files.inventory(cfg.ROOT/'app')
    except (OSError,ValueError) as error:raise processes.OwnershipError('Runtime app could not be verified.') from error


def _runtime_device_bytes():
    from brain_markdown import bounded_file_bytes
    device=cfg.ROOT/'device.json'
    if any(part.is_symlink() or part.is_junction() for part in (device,*device.parents)):
        raise processes.OwnershipError('Linked device path is refused.')
    return bounded_file_bytes(device,65536)


def _original_runtime(snapshot):
    return (hashlib.sha256(_runtime_device_bytes()).hexdigest()==snapshot['files'][0].get('before_sha256')
            and _runtime_app_hashes()==snapshot['app_sha256'])


def _forward_runtime(snapshot):
    """Recognize only the exact forward device and sealed controller app map."""
    from brain_markdown import bounded_file_bytes
    raw=_runtime_device_bytes()
    if hashlib.sha256(raw).hexdigest()!=snapshot['files'][0].get('after_sha256'):return False
    checkout=Path(snapshot.get('controller_checkout',''));identity=snapshot.get('controller_identity')
    if (not checkout.is_absolute() or checkout.resolve()!=checkout or not isinstance(identity,dict)
            or identity.get('repository')!='code-by-muhammad/second-brain-system'
            or not isinstance(identity.get('manifest_sha256'),str)
            or not re.fullmatch('[a-f0-9]{64}',identity['manifest_sha256'])):
        raise processes.OwnershipError('Sealed forward controller identity is invalid.')
    manifest_path=checkout/'release-manifest.json'
    if any(part.is_symlink() or part.is_junction() for part in (manifest_path,*manifest_path.parents)):
        raise processes.OwnershipError('Linked forward manifest is refused.')
    manifest_bytes=bounded_file_bytes(manifest_path,8*1024*1024)
    if hashlib.sha256(manifest_bytes).hexdigest()!=identity['manifest_sha256']:
        raise processes.OwnershipError('Forward manifest differs from sealed controller identity.')
    manifest=json.loads(manifest_bytes);device=json.loads(raw)
    if (manifest.get('repository')!=identity['repository'] or 'v'+manifest.get('version','')!=identity.get('tag')
            or device.get('release_root')!=str(checkout)):
        raise processes.OwnershipError('Forward app origin differs from sealed controller identity.')
    expected=release_files.manifest_app(manifest,device.get('version'))
    sealed=snapshot.get('deployment')
    return (sealed is None or sealed['target_app_sha256']==expected) and _runtime_app_hashes()==expected


def _sealed_registration_check(value,current,history):
    if value is None or value.get('stage')!='prepared':return
    snapshot=_held_snapshot(value);sealed=snapshot['registration']
    if not _original_runtime(snapshot):raise processes.OwnershipError('Original runtime changed before stop; hold preserved.')
    if sealed.get('present') is False:
        if not _native_absent(current):
            raise processes.OwnershipError('Original user-space registration absence differs; hold preserved.')
        return
    if (current is None or current.get('definition_sha256')!=sealed['definition_sha256']
            or (sys.platform=='win32' and (current.get('owner_sid')!=sealed['owner_sid']
                or current.get('interpreter')!=sealed['interpreter']))):
        raise processes.OwnershipError('Native registration differs from the sealed preflight; hold preserved.')
    attempted=history is not None and (history.get('activation_disable_attempted') is True or history.get('end_attempted') is True)
    if not attempted and current.get('enabled') is not sealed['enabled']:
        raise processes.OwnershipError('Native activation changed since preflight; hold preserved.')


def _held_install(value,hold_id=None):
    if value is None:
        if hold_id is not None:raise processes.OwnershipError('The named install transaction does not exist.')
        return None
    if hold_id is not None and value['hold_id']!=hold_id:
        raise processes.OwnershipError('Held install requires the exact upgrade UUID.')
    if value.get('stage')!='stopped' or type(value.get('empty_at_filetime')) is not int or value['empty_at_filetime']<=0:
        raise processes.OwnershipError('Held install requires a verified stopped phase with disabled activation.')
    snapshot=_held_snapshot(value)
    if 'deployment' in snapshot and value['hold_id']!=hold_id:
        raise processes.OwnershipError('Held install requires the exact upgrade UUID.')
    if not _original_runtime(snapshot) and not _forward_runtime(snapshot):
        raise processes.OwnershipError('Runtime is neither complete sealed original nor complete verified forward app/device; hold preserved.')
    return snapshot


def begin_transaction(hold_id,snapshot):
    _qualified_native_platform()
    processes.validate_environment()
    if not re.fullmatch('[a-f0-9]{32}',hold_id or ''):raise ValueError('Use a transaction UUID.')
    target=Path(snapshot).resolve()
    if not target.is_relative_to(cfg.ROOT/'backups') or not (target/'snapshot.json').is_file():
        raise processes.OwnershipError('Transaction needs a verified local upgrade snapshot.')
    with _control():
        prior=transaction()
        if prior is None and (_state()/'service-transactions'/(hold_id+'.json')).exists():raise processes.OwnershipError('Transaction UUID was already completed; choose a new UUID.')
        if prior is not None:
            if prior['hold_id']!=hold_id or prior.get('snapshot')!=str(target):raise processes.OwnershipError('Another transaction is already recorded.')
            return prior
        import hashlib
        value={'hold_id':hold_id,'root':str(cfg.ROOT),'snapshot':str(target),'snapshot_sha256':hashlib.sha256((target/'snapshot.json').read_bytes()).hexdigest(),
               'stage':'prepared','stop_entered':False,'created_filetime':processes.filetime_now()}
        native=(_windows_task() if sys.platform=='win32' else _mac_registration().inspect() if sys.platform=='darwin'
                else _linux_registration().inspect(allow_reload=True,prove_main=False))
        _sealed_registration_check(value,native,None)
        sealed=_held_snapshot(value)
        if any(release_files.file_image(Path(row['path']))!=before
               for row,before,_ in release_files.artifact_rows(target,sealed)):
            raise processes.OwnershipError('Deployment preimages changed before the hold.')
        _atomic(_state()/'service-transaction.json',value)
        return value


def finish_transaction(hold_id,expected_version=None,allow_legacy=False):
    _qualified_native_platform()
    processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None:raise processes.OwnershipError('No matching transaction to finish.')
        if not isinstance(expected_version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',expected_version) or expected_version!=cfg.DEVICE.get('version'):
            raise processes.OwnershipError('Name the installed expected version before finishing recovery.')
        if value.get('stage') not in ('stopped','started') or not value.get('empty_at_filetime'):
            raise processes.OwnershipError('Transaction has not completed a verified stop.')
        sealed=_held_snapshot(value)
        deployment=sealed.get('deployment')
        restoring=expected_version==sealed['previous'].get('version')
        if deployment is not None:
            _verify_finish_payload(value,sealed,restoring)
            if deployment['service_policy']=='absent' or (deployment['service_policy']=='first' and restoring):
                if value.get('stage')!='stopped' or not value.get('empty_at_filetime'):
                    raise processes.OwnershipError('Service-free completion requires an exact stopped transaction.')
                registration=_linux_registration()
                if not _native_absent(registration.inspect(allow_reload=True,prove_main=False)):
                    raise processes.OwnershipError('Service-free completion lacks native absence.')
                with ExitStack() as locks:
                    deadline=time.monotonic()+30
                    for name in ('wiki-worker.lock','index-bootstrap.lock','maintenance.lock','writer.lock'):
                        locks.enter_context(FileLock(str(_state()/name),timeout=max(0,deadline-time.monotonic())))
                    prove_user_space_quiescence(registration)
                    if not _native_absent(registration.inspect(allow_reload=True,prove_main=False)):
                        raise processes.OwnershipError('Native absence changed during completion.')
                    _verify_finish_payload(value,sealed,restoring)
                    return _release_transaction(value,absent_version=expected_version)
        if value.get('stage')!='started' or not value.get('empty_at_filetime'):
            raise processes.OwnershipError('Transaction has not completed a verified stop and start.')
        owned=processes.listener_identity()
        if not owned:raise processes.OwnershipError('Cannot release a transaction without an owned live server.')
        minimum=value.get('empty_at_filetime')
        fresh=(processes.newer_than_empty(owned,_linux_boundary(value,'empty_')) if sys.platform=='linux'
               else owned['birth']>=minimum and owned['supervisor_birth']>=minimum)
        if not fresh:
            raise processes.OwnershipError('The current listener predates verified quiescence.')
        heartbeat_args=(owned,value['started_at_filetime'],allow_legacy and value.get('legacy_heartbeat'))
        heartbeat=(_heartbeat(*heartbeat_args,_linux_boundary(value,'started_')) if sys.platform=='linux'
                   else _heartbeat(*heartbeat_args))
        if not heartbeat:raise processes.OwnershipError('Fresh supervisor heartbeat is required to finish the transaction.')
        if sys.platform=='darwin':_native_listener_binding(owned,_mac_registration().inspect())
        elif sys.platform=='linux':_native_listener_binding(owned,_linux_registration().inspect())
        import asyncio
        from brain_cli import mcp_call
        health=asyncio.run(mcp_call('brain_health',{}))
        if health.get('status')!='running' or health.get('version')!=expected_version:
            raise processes.OwnershipError('Authenticated expected-version health did not match; transaction preserved.')
        again=processes.listener_identity()
        if not processes.same_runtime(again,owned):raise processes.OwnershipError('Runtime identity changed during transaction health verification.')
        if sys.platform=='darwin':_native_listener_binding(again,_mac_registration().inspect())
        elif sys.platform=='linux':_native_listener_binding(again,_linux_registration().inspect())
        if deployment is not None:_verify_finish_payload(value,sealed,restoring)
        return _release_transaction(value,authenticated_version=expected_version,identity=owned,
                                    heartbeat_verified=heartbeat=='verified')


def _verify_finish_payload(value,sealed,restoring):
    if not (_original_runtime(sealed) if restoring else _forward_runtime(sealed)):
        raise processes.OwnershipError('Expected sealed runtime bytes differ; hold preserved.')
    for row,before,after in release_files.artifact_rows(value['snapshot'],sealed):
        if row['kind']=='activation' and not restoring:after=(b'true\n',after[1])
        if release_files.file_image(Path(row['path']))!=(before if restoring else after):
            raise processes.OwnershipError('Expected sealed deployment file differs; hold preserved: '+row['path'])


def _release_transaction(value,**proof):
    hold_id=value['hold_id']
    _atomic(_state()/'service-transactions'/(hold_id+'.json'),
            {**value,'stage':'complete','completed_filetime':processes.filetime_now(),**proof})
    (_state()/'service-transaction.json').unlink()
    release_files.sync_directory(_state())
    return {'status':'transaction released','hold_id':hold_id,'identity':proof.get('identity'),
            **({'absent_version':proof['absent_version']} if 'absent_version' in proof else {})}


def spawn_allowed():
    value=transaction()
    return value is None or value.get('stage') in ('starting','started')


def cancel_transaction(hold_id):
    """Release only a provably unentered/pre-End stop with original bytes."""
    _qualified_native_platform()
    processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None or value.get('stage')!='prepared' or type(value.get('stop_entered')) is not bool:
            raise processes.OwnershipError('Only a typed prepared hold can be cancelled; preserve recovery.')
        snapshot=_held_snapshot(value)
        status=_native_control_status(optional=True)
        matching=status is not None and status.get('hold_id')==hold_id
        no_entry=value['stop_entered'] is False and not matching
        refusal=(matching and status.get('status') in ('stop failed','stop preflight failed')
                 and status.get('end_attempted') is False
                 and status.get('managed_drain_unresolved') is not True
                 and type(status.get('activation_disable_attempted')) is bool
                 and (status['status']!='stop preflight failed' or status['activation_disable_attempted'] is False))
        if not no_entry and not refusal:
            raise processes.OwnershipError('No unentered or certain pre-End refusal; hold preserved.')
        if not value['stop_entered'] and matching:
            raise processes.OwnershipError('Stop receipt contradicts entry marker; hold preserved.')
        if not _original_runtime(snapshot):raise processes.OwnershipError('Original runtime bytes differ; hold preserved.')
        if 'deployment' in snapshot:_verify_finish_payload(value,snapshot,True)
        from brain_markdown import bounded_file_bytes
        binding=cfg.ROOT/'state/previous-release.json'
        if binding.is_symlink() or binding.is_junction():raise processes.OwnershipError('Linked previous binding is refused.')
        try:current_binding=hashlib.sha256(bounded_file_bytes(binding,65536)).hexdigest()
        except FileNotFoundError:current_binding=None
        if current_binding!=snapshot['previous_binding']['before_sha256']:
            raise processes.OwnershipError('Original previous binding was not restored; hold preserved.')
        try:
            native=(_windows_task() if sys.platform=='win32' else _mac_registration().inspect() if sys.platform=='darwin'
                    else _linux_registration().inspect(allow_reload=True,prove_main=False))
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,SyntaxError,LookupError) as error:
            raise processes.OwnershipError('Native original state cannot be verified; hold preserved.') from error
        sealed=snapshot['registration']
        if sealed.get('present') is False:
            if not _native_absent(native):
                raise processes.OwnershipError('Original native absence differs; hold preserved.')
        elif matching and status.get('status')=='stop failed':
            if sys.platform=='win32':
                prior=_windows_stop_history(hold_id)
                if (prior is None or prior['prior_task_present'] is not True or prior['prior_task_enabled'] is not sealed['enabled']
                        or prior['prior_task_definition_sha256']!=sealed['definition_sha256']):
                    raise processes.OwnershipError('Stop receipt contradicts sealed original intent; hold preserved.')
            else:
                prior=status.get('prior_native')
                if (not isinstance(prior,dict) or prior.get('definition_sha256')!=sealed['definition_sha256']
                        or prior.get('enabled') is not sealed['enabled']):
                    raise processes.OwnershipError('Stop receipt contradicts sealed original intent; hold preserved.')
        if sealed.get('present') is not False and (native is None
                or native.get('definition_sha256')!=sealed['definition_sha256'] or native.get('enabled') is not sealed['enabled']):
            raise processes.OwnershipError('Original native definition/activation differs; hold preserved.')
        record={**value,'stage':'cancelled before runtime switch','cancelled_filetime':processes.filetime_now(),'empty_runtime_verified':False}
        _atomic(_state()/'service-transactions'/(hold_id+'.json'),record)
        (_state()/'service-transaction.json').unlink()
        release_files.sync_directory(_state())
        _atomic(_state()/'service-control-status.json',{**(status if matching else {}),'status':'stop refused; runtime unchanged' if refusal else 'stop not entered; runtime unchanged',
            'hold_id':hold_id,'transaction_cancelled':True})
        return {'status':'cancelled; runtime not switched','hold_id':hold_id,'pending_work':'preserved'}


def _task_name():
    if not re.fullmatch(r'SecondBrainLocalService|SecondBrain-(?:Test|CI)-[a-f0-9]{32}',TASK):
        raise processes.OwnershipError('Unrecognized service task name.')
    return TASK


def _task_address():return '\\'+_task_name()


def _scheduled_module():
    module=Path(_tool('powershell.exe')).parent/'Modules/ScheduledTasks/ScheduledTasks.psd1'
    if not module.is_file():raise processes.OwnershipError('Windows ScheduledTasks module is unavailable; task absence cannot be inferred.')
    return module


def _windows_task(*,include_xml=False,deadline=None):
    """Use structured error categories, never localized text to infer absence."""
    name=_task_name()
    module=str(_scheduled_module()).replace("'","''")
    code=("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
          "Import-Module -Name '"+module+"' -ErrorAction Stop; Get-Command Get-ScheduledTask,Get-ScheduledTaskInfo,Export-ScheduledTask -ErrorAction Stop | Out-Null; "
          "try { $t=Get-ScheduledTask -TaskPath '\\' -TaskName '"+name+"'; "
          "$i=Get-ScheduledTaskInfo -TaskPath '\\' -TaskName '"+name+"'; "
          "$sid=if($t.Principal.UserId -match '^S-1-'){ $t.Principal.UserId }else{ (New-Object System.Security.Principal.NTAccount($t.Principal.UserId)).Translate([System.Security.Principal.SecurityIdentifier]).Value }; "
          "@{exists=$true;state=[string]$t.State;last_result=[long]$i.LastTaskResult;owner_sid=$sid;current_sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;xml=(Export-ScheduledTask -TaskPath '\\' -TaskName '"+name+"')}|ConvertTo-Json -Compress "
          "} catch { if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { '{\"exists\":false}' } else { exit 2 } }")
    result=subprocess.run([_tool('powershell.exe'),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=20 if deadline is None else _startup_budget(deadline,20),**cfg.child_options())
    if result.returncode:raise processes.OwnershipError('Scheduled task ownership could not be queried.')
    value=json.loads(result.stdout)
    if not value.get('exists'):return None
    value=_windows_definition(value)
    # Activation and an existing running instance are separate native properties.
    state=value.get('state')
    if ((value['enabled'] and state not in ('Ready','Queued','Running'))
            or (not value['enabled'] and state not in ('Disabled','Running'))):
        raise processes.OwnershipError('Task enabled setting disagrees with its supported native state.')
    if not include_xml:value.pop('xml',None)
    return value


def _windows_definition(value):
    """Pure identical ownership checks for current exports and sealed restore XML."""
    import xml.etree.ElementTree as ET
    definition=ET.fromstring(value['xml'])
    actions=definition.find('{*}Actions');principals=definition.findall('{*}Principals/{*}Principal')
    if actions is None or len(actions)!=1 or len(principals)!=1 or actions[0].tag.rsplit('}',1)[-1]!='Exec':raise processes.OwnershipError('Existing service task has unexpected actions/principals.')
    action=actions[0];principal=principals[0]
    if not principal.get('id') or actions.get('Context')!=principal.get('id'):
        raise processes.OwnershipError('Task security context does not name the single principal.')
    if principal.findtext('{*}LogonType')!='InteractiveToken':
        raise processes.OwnershipError('Task security context is not interactive.')
    # Task Scheduler omits the least-privilege default when exporting its XML.
    # Empty, duplicated or explicitly elevated values remain refused.
    if len(principal.findall('{*}RunLevel'))>1 or principal.findtext('{*}RunLevel',default='LeastPrivilege')!='LeastPrivilege':
        raise processes.OwnershipError('Task security context is not least-privilege.')
    user=principal.findtext('{*}UserId',default='')
    if user.startswith('S-1-') and user!=value.get('owner_sid'):
        raise processes.OwnershipError('XML principal SID differs from its measured owner.')
    arguments=action.findtext('{*}Arguments',default='')
    # Definitions generated by this application have one quoted script argument.
    expected='"'+str(cfg.ROOT/'app/brain_supervisor.py')+'"'
    if arguments!=expected:raise processes.OwnershipError('The task name belongs to another runtime or action; preserve it.')
    command=action.findtext('{*}Command',default='')
    directory=action.findtext('{*}WorkingDirectory',default='')
    if (processes._path(command) not in processes._launchers() or processes._path(directory)!=processes._path(cfg.ROOT/'app')
            or value.get('owner_sid')!=value.get('current_sid') or not value.get('current_sid')):
        raise processes.OwnershipError('Task interpreter, directory or owner does not match this device.')
    value['interpreter']=str(Path(command).with_name('python.exe')) if Path(command).name.lower()=='pythonw.exe' else str(Path(command))
    value['enabled']=definition.findtext('{*}Settings/{*}Enabled',default='true')=='true'
    value['trigger_count']=len(definition.findall('{*}Triggers/*'))
    # Only activation is excluded. Keep all actions/principals/triggers/settings
    # and security/registration content; native runtime state is outside the XML.
    for settings in definition.findall('{*}Settings'):
        for activation in settings.findall('{*}Enabled'):settings.remove(activation)
    for element in definition.iter():
        if element.text is not None and not element.text.strip():element.text=None
        if element.tail is not None and not element.tail.strip():element.tail=None
    canonical=ET.canonicalize(ET.tostring(definition,encoding='unicode'))
    value['definition_sha256']=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return value


def definition(platform=None,*,enabled=True):
    platform=platform or sys.platform
    script=cfg.ROOT/'app/brain_supervisor.py'
    if platform=='win32':
        import xml.etree.ElementTree as ET
        root=ET.Element('Task',xmlns='http://schemas.microsoft.com/windows/2004/02/mit/task',version='1.2')
        import psutil
        user=psutil.Process().username() if os.name=='nt' else os.environ.get('USERDOMAIN','.')+'\\'+os.environ.get('USERNAME','')
        triggers=ET.SubElement(root,'Triggers')
        if not re.fullmatch(r'SecondBrain-(?:Test|CI)-[a-f0-9]{32}',TASK):
            trigger=ET.SubElement(triggers,'LogonTrigger'); ET.SubElement(trigger,'Enabled').text='true'
            ET.SubElement(trigger,'UserId').text=user
        principal=ET.SubElement(ET.SubElement(root,'Principals'),'Principal',id='Author')
        ET.SubElement(principal,'UserId').text=user
        ET.SubElement(principal,'LogonType').text='InteractiveToken'; ET.SubElement(principal,'RunLevel').text='LeastPrivilege'
        settings=ET.SubElement(root,'Settings')
        for key,value in [('Enabled','true' if enabled else 'false'),('MultipleInstancesPolicy','IgnoreNew'),('ExecutionTimeLimit','PT0S'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true')]: ET.SubElement(settings,key).text=value
        action=ET.SubElement(ET.SubElement(root,'Actions',Context='Author'),'Exec')
        python=cfg.PYTHON.with_name('pythonw.exe')
        ET.SubElement(action,'Command').text=str(python)
        ET.SubElement(action,'Arguments').text='"'+str(script)+'"'
        ET.SubElement(action,'WorkingDirectory').text=str(cfg.ROOT/'app')
        return ET.tostring(root,encoding='unicode')
    if platform=='darwin':
        from brain_mac_registration import definitions
        known=definitions(LABEL,cfg.ROOT,[str(cfg.PYTHON)])
        return next(payload.decode('utf-8') for payload,argv in known.items() if argv[1:3]==['-I','-B'])
    # systemd quoting is not shell quoting. Escape percent specifiers and backslashes.
    def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    return '[Unit]\nDescription=Local Second Brain MCP\nAfter=network.target\n\n[Service]\nType=simple\nExecStart='+quote(cfg.PYTHON)+' '+quote(script)+'\nRestart=on-failure\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n'

def install(hold_id=None):
    _qualified_native_platform()
    processes.validate_environment()
    (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        with _control():
            active=transaction();snapshot=_held_install(active,hold_id)
            previous=_windows_task()
            if active is None and previous is not None and previous.get('state') in ('Running','Queued'):
                raise processes.OwnershipError('Stop the owned Windows Task before replacing its registration.')
            if active is not None and (previous is None or previous['enabled'] or previous.get('state')!='Disabled'):
                raise processes.OwnershipError('Held Windows install requires the exact disabled inactive owned Task.')
            enabled=active is None and (previous is None or previous['enabled'])
            payload=definition(enabled=enabled);expected=None
            if snapshot is not None and _original_runtime(snapshot):
                sealed=snapshot['registration']
                original=_windows_definition({'xml':sealed.get('xml'),'owner_sid':sealed.get('owner_sid'),
                    'current_sid':previous['current_sid']})
                if original['definition_sha256']!=sealed['definition_sha256'] or original['interpreter']!=str(cfg.PYTHON):
                    raise processes.OwnershipError('Sealed Windows XML ownership/interpreter differs; hold preserved.')
                expected=sealed['definition_sha256']
                if previous['definition_sha256']==expected:return {'status':'installed','definition':'retained exact original Task',
                    'restored':'sealed-retained','definition_sha256':expected}
                import xml.etree.ElementTree as ET
                tree=ET.fromstring(sealed['xml']);settings=tree.find('{*}Settings')
                if settings is None:raise processes.OwnershipError('Sealed Windows Settings are missing.')
                flags=settings.findall('{*}Enabled')
                if len(flags)>1:raise processes.OwnershipError('Sealed Windows activation is ambiguous.')
                flag=flags[0] if flags else ET.SubElement(settings,'{http://schemas.microsoft.com/windows/2004/02/mit/task}Enabled')
                flag.text='false';payload=ET.tostring(tree,encoding='unicode')
            target=_state()/'service-task.xml'
            descriptor,name=tempfile.mkstemp(prefix='.service-task-',suffix='.xml',dir=_state())
            try:
                with os.fdopen(descriptor,'w',encoding='utf-16',newline='') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.replace(name,target)
            finally:Path(name).unlink(missing_ok=True)
            subprocess.run([_tool('schtasks.exe'),'/Create','/TN',_task_address(),'/XML',str(target),'/F'],check=True,capture_output=True,timeout=30,**cfg.child_options())
            after=_windows_task()
            if after is None or after['enabled'] is not enabled or (expected is not None and after['definition_sha256']!=expected):
                raise processes.OwnershipError('Windows installed activation/definition was not verified; hold preserved.')
            if expected is not None:return {'status':'installed','definition':str(target),
                'restored':'sealed-restored','definition_sha256':after['definition_sha256']}
    elif sys.platform=='darwin':
        with _control():
            active=transaction();snapshot=_held_install(active,hold_id)
            if active is not None and (active.get('stage')!='stopped' or type(active.get('empty_at_filetime')) is not int or active['empty_at_filetime']<=0):
                raise processes.OwnershipError('Finish a verified stop before replacing the Mac definition during a hold.')
            registration=_mac_registration();prior=registration.inspect();target=registration.path
            if prior['loaded']:raise processes.OwnershipError('Stop the owned Mac job before replacing its registration.')
            if active is not None and prior['enabled'] is not False:
                raise processes.OwnershipError('Held Mac install requires verified disabled activation.')
            payload=definition().encode('utf-8');restore_sha256=None
            if snapshot is not None and _original_runtime(snapshot):
                restore_sha256=snapshot['registration']['definition_sha256']
                choices=[raw for raw,argv in registration.known.items() if hashlib.sha256(raw).hexdigest()==restore_sha256 and argv[0]==str(cfg.PYTHON)]
                if len(choices)!=1:raise processes.OwnershipError('Original known Mac definition is unavailable; hold preserved.')
                payload=choices[0]
                if prior['definition_sha256']==restore_sha256:return {'status':'installed','definition':str(target),
                    'restored':'sealed-retained','definition_sha256':restore_sha256}
            expected=hashlib.sha256(payload).hexdigest()
            prior,selected=(prior,False) if active is not None else _native_install_intent(prior,expected)
            if active is None:
                _atomic(_state()/'service-control-status.json',{'status':'installing','phase':'native activation suspension',
                    'prior_native':prior,'activation_disable_attempted':True,'end_attempted':False,
                    'requested_enabled':selected,'expected_native_definition_sha256':expected})
            try:
                if restore_sha256 is None:registration.install(payload,enabled=selected)
                else:
                    registration.install(payload,enabled=selected,restore_sha256=restore_sha256)
                    restored=registration.inspect()
                    if restored.get('definition_sha256')!=restore_sha256 or restored.get('enabled') is not False or restored.get('loaded') is not False:
                        raise processes.OwnershipError('Original Mac registration was not restored disabled/unloaded.')
                    return {'status':'installed','definition':str(target),'restored':'sealed-restored','definition_sha256':restore_sha256}
                if active is None:
                    _atomic(_state()/'service-control-status.json',{'status':'installed','definition':str(target),
                        'requested_enabled':selected,'expected_native_definition_sha256':expected})
            except BaseException as error:
                restored=False
                if active is None:
                    try:registration.restore_activation(prior);restored=True
                    except Exception:pass
                    _atomic(_state()/'service-control-status.json',{'status':'install failed','error_type':type(error).__name__,
                        'prior_native':prior,'activation_disable_attempted':True,'prior_enabled_restored':restored,'end_attempted':False,
                        'requested_enabled':selected,'expected_native_definition_sha256':expected})
                raise
    elif sys.platform=='linux':
        with _control():
            from brain_linux_user_service import LinuxUserService
            registration=_linux_registration()
            hold=transaction();snapshot=_held_install(hold,hold_id);active_hold=hold is not None
            if snapshot is not None and 'deployment' in snapshot and isinstance(registration,LinuxUserService):
                return _install_held_user_space(registration,hold,snapshot)
            current=registration.inspect(allow_reload=True,prove_main=False);target=registration.path
            if active_hold and (hold.get('stage')!='stopped' or not hold.get('empty_at_filetime')
                    or current['enabled'] is not False or current.get('quiescent') is not True):
                raise processes.OwnershipError('Held Linux install requires the verified disabled stopped phase.')
            payload=definition('linux').encode('utf-8');expected=hashlib.sha256(payload).hexdigest();restoring=False
            if snapshot is not None and _original_runtime(snapshot):
                restoring=True
                if expected!=snapshot['registration']['definition_sha256']:
                    raise processes.OwnershipError('Original Linux definition is not the current exact known definition; hold preserved.')
                if current['definition_sha256']==expected:return {'status':'installed','definition':str(target),
                    'restored':'sealed-retained','definition_sha256':expected}
            prior,selected=(current,False) if active_hold else _native_install_intent(current,expected)
            if not active_hold:
                _atomic(_state()/'service-control-status.json',{'status':'installing','phase':'native activation suspension',
                    'prior_native':prior,'activation_disable_attempted':True,'end_attempted':False,
                    'requested_enabled':selected,'expected_native_definition_sha256':expected})
            try:
                registration.install(payload,enabled=selected)
                if restoring:
                    restored=registration.inspect(allow_reload=True,prove_main=False)
                    if restored.get('definition_sha256')!=expected or restored.get('enabled') is not False or not restored.get('quiescent'):
                        raise processes.OwnershipError('Original Linux registration was not restored disabled/quiescent.')
                    return {'status':'installed','definition':str(target),'restored':'sealed-restored','definition_sha256':expected}
                if not active_hold:
                    _atomic(_state()/'service-control-status.json',{'status':'installed','definition':str(target),
                        'requested_enabled':selected,'expected_native_definition_sha256':expected})
            except BaseException as error:
                if not active_hold:
                    restored=False
                    try:registration.restore_activation(prior);restored=True
                    except Exception:pass
                    _atomic(_state()/'service-control-status.json',{'status':'install failed','error_type':type(error).__name__,
                        'prior_native':prior,'activation_disable_attempted':True,'prior_enabled_restored':restored,'end_attempted':False,
                        'requested_enabled':selected,'expected_native_definition_sha256':expected})
                raise
    else:raise processes.OwnershipError('Unsupported native service platform.')
    return {'status':'installed','definition':str(target)}

def _native_absent(native):
    return (isinstance(native,dict) and native.get('backend')=='user-space'
            and native.get('file_present') is False and native.get('enabled') is None
            and native.get('loaded') is False and native.get('running') is False
            and native.get('quiescent') is True and native.get('main_pid') is None)


def plan_registration(python):
    """Measure the exact user-space definition, activation and hooks for a held update."""
    from brain_linux_user_service import LinuxUserService
    from brain_linux_registration import definitions
    if not python or not Path(python).is_absolute() or not Path(python).is_file():
        raise processes.OwnershipError('Candidate interpreter is unavailable.')
    _qualified_native_platform();processes.validate_environment()
    with _control():
        if transaction() is not None:
            raise processes.OwnershipError('An active transaction already owns registration.')
        registration=_linux_registration()
        if not isinstance(registration,LinuxUserService):
            raise processes.OwnershipError('Exact file planning requires the Linux user-space backend.')
        registration.inspect(allow_reload=True,prove_main=False)
        unit=next(iter(definitions(registration.unit,cfg.ROOT,[python])))
        activation=release_files.file_image(registration.enabled_path,16)
        unit_before=release_files.file_image(registration.path,65536)
        unit_mode=0o644 if unit_before[1] is None else unit_before[1]
        activation_mode=0o644 if activation[1] is None else activation[1]
        measured=[('unit',registration.path,unit_before,(unit,unit_mode)),
                  ('activation',registration.enabled_path,activation,(b'false\n',activation_mode))]
        skipped=[]
        for path,before,mode,after,new_mode in registration.hook_images(python,skips=skipped):
            measured.append(('hook',path,(before,mode),(after,new_mode)))
        def image(value):
            data,mode=value
            return {'hex':None if data is None else data.hex(),'mode':mode}
        return {'status':'registration planned','artifacts':[
            {'kind':kind,'path':str(path),'before':image(before),'after':image(after)}
            for kind,path,before,after in measured],'skipped':skipped}


def _native_images(registration,value,snapshot):
    rows=[(row,before,after) for row,before,after in release_files.artifact_rows(value['snapshot'],snapshot)
          if row['phase']=='native']
    fixed={'unit':{registration.path},'activation':{registration.enabled_path},
           'hook':{registration.boot_path,registration.home/'.profile',
                   registration.home/'.config/autostart/second-brain.desktop'}}
    for row,before,after in rows:
        if Path(row['path']) not in fixed[row['kind']]:
            raise processes.OwnershipError('Sealed native destination does not belong to this service.')
    kinds={row['kind'] for row,_,_ in rows}
    policy=snapshot['deployment']['service_policy']
    if policy=='absent':
        if rows:raise processes.OwnershipError('Service-free update cannot publish native registration.')
    elif not {'unit','activation'} <= kinds:
        raise processes.OwnershipError('Native unit and activation images are required.')
    return rows


def _activation_disabled(image):
    return (b'false\n',image[1]) if image[0] is not None else image


def _publish_native_images(registration,value,snapshot,restoring):
    images=_native_images(registration,value,snapshot)
    ordered=list(reversed(images)) if restoring else images
    changes=[]
    for row,before,after in ordered:
        path=Path(row['path'])
        observed=release_files.file_image(path)
        if row['kind']=='activation':before=_activation_disabled(before)
        if observed not in (before,after):
            raise processes.OwnershipError('Native deployment artifact is outside the sealed phase: '+str(path))
        changes.append((path,before if restoring else after,observed))
    for path,desired,observed in changes:
        release_files.write_image(path,desired,observed)


def _stop_first_user_space(linux,value,wait_seconds):
    """Stop a first-upgrade runtime while both native files are absent or only partly published."""
    snapshot=_held_snapshot(value)
    if snapshot['registration'].get('present') is not False:
        return None
    rows=_native_images(linux,value,snapshot)
    paths={row['kind']:(Path(row['path']),before,after) for row,before,after in rows if row['kind']!='hook'}
    unit=release_files.file_image(linux.path)
    flag=release_files.file_image(linux.enabled_path)
    if value['stage']=='prepared' or snapshot['deployment']['service_policy']=='absent':
        if unit[0] is not None or flag[0] is not None:
            raise processes.OwnershipError('Expected both native registration files absent; hold preserved.')
    else:
        selected_unit=paths['unit'][2];selected_flag=paths['activation'][2]
        if unit not in ((None,None),selected_unit) or flag not in ((None,None),selected_flag,(b'true\n',selected_flag[1])):
            raise processes.OwnershipError('A partial registration differs from its sealed images.')
        if unit[0] is not None and flag[0] is not None:
            return None  # The ordinary native stop verifies this complete candidate.
    prove_user_space_quiescence(linux)
    deadline=time.monotonic()+wait_seconds
    with ExitStack() as locks:
        for name in ('wiki-worker.lock','index-bootstrap.lock','maintenance.lock','writer.lock'):
            locks.enter_context(FileLock(str(_state()/name),timeout=max(0,deadline-time.monotonic())))
        if release_files.file_image(linux.path)!=unit or release_files.file_image(linux.enabled_path)!=flag:
            raise processes.OwnershipError('Partial registration changed during quiescence proof.')
        prove_user_space_quiescence(linux)
        if release_files.file_image(linux.path)!=unit or release_files.file_image(linux.enabled_path)!=flag:
            raise processes.OwnershipError('Partial registration changed before empty receipt.')
        boundary=processes.linux_marker()
        result={'status':'stopped','empty_at_filetime':processes.filetime_now(),
                'empty_boot_id':boundary['boot_id'],'empty_boottime_ticks':boundary['boottime_ticks'],
                'native_end_exit':None,'task_disabled':None,'hold_id':value['hold_id']}
        _atomic(_state()/'service-transaction.json',{**value,**{key:item for key,item in result.items() if key.startswith('empty_')},'stage':'stopped'})
        _atomic(_state()/'service-control-status.json',result)
        return result


def withdraw_first_registration(hold_id):
    """Withdraw only a stopped exact first-registration attempt and prove absence."""
    _qualified_native_platform();processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None or value['stage']!='stopped' or not value.get('empty_at_filetime'):
            raise processes.OwnershipError('Withdrawal requires the exact stopped upgrade UUID.')
        snapshot=_held_snapshot(value)
        if snapshot['registration'].get('present') is not False:
            raise processes.OwnershipError('Only a sealed first registration can be withdrawn.')
        registration=_linux_registration()
        prove_user_space_quiescence(registration)
        retire_user_space_generation(registration)
        _publish_native_images(registration,value,snapshot,restoring=True)
        if not _native_absent(registration.inspect(allow_reload=True,prove_main=False)):
            raise processes.OwnershipError('Native absence was not verified after withdrawal.')
        return {'status':'withdrawn','file_present':False}


def check_stopped_user_space(hold_id, *, restoring=False):
    """Reprove sealed registration, process and port quiescence under the caller's work locks."""
    _qualified_native_platform();processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None or value.get('stage')!='stopped' or not value.get('empty_at_filetime'):
            raise processes.OwnershipError('Fresh quiescence requires the exact stopped update UUID.')
        snapshot=_held_snapshot(value)
        registration=_linux_registration()
        rows=_native_images(registration,value,snapshot)
        unit=release_files.file_image(registration.path)
        flag=release_files.file_image(registration.enabled_path,16)
        absent=(None,None)
        if snapshot['deployment']['service_policy']=='absent' or (
                snapshot['registration'].get('present') is False and not restoring):
            allowed_unit=allowed_flag={absent}
        else:
            images={row['kind']:(before,after) for row,before,after in rows if row['kind']!='hook'}
            allowed_unit=set(images['unit'])
            allowed_flag={_activation_disabled(image) for image in images['activation']}
        if unit not in allowed_unit or flag not in allowed_flag:
            raise processes.OwnershipError('Stopped native registration differs from its sealed images.')
        prove_user_space_quiescence(registration)
        if (release_files.file_image(registration.path),release_files.file_image(registration.enabled_path,16))!=(unit,flag):
            raise processes.OwnershipError('Native registration changed during fresh quiescence proof.')
        return {'status':'quiescent','hold_id':hold_id}


def _install_held_user_space(registration,value,snapshot):
    policy=snapshot['deployment']['service_policy']
    restoring=_original_runtime(snapshot)
    if policy=='absent' or (policy=='first' and restoring):
        raise processes.OwnershipError('Use the exact verified withdrawal flow for this absent registration.')
    prove_user_space_quiescence(registration)
    retire_user_space_generation(registration)
    _publish_native_images(registration,value,snapshot,restoring)
    native=registration.inspect(allow_reload=True,prove_main=False)
    if native['enabled'] is not False or native.get('quiescent') is not True:
        raise processes.OwnershipError('Held user-space definition is not disabled and quiescent.')
    result={'status':'installed','definition':str(registration.path),'definition_sha256':native['definition_sha256']}
    if restoring:
        if result['definition_sha256']!=snapshot['registration']['definition_sha256']:
            raise processes.OwnershipError('Restored Linux definition hash differs from the sealed original.')
        result['restored']='sealed-restored'
    return result

def _start_native(*,explicit=False,deadline=None,registration=None,hold_id=None):
    remaining=30 if deadline is None else min(30,deadline-time.monotonic())
    if remaining<=0:raise processes.OwnershipError('Native startup exceeded the caller deadline.')
    if sys.platform=='win32':command=[_tool('schtasks.exe'),'/Run','/TN',_task_address()]
    elif sys.platform in ('darwin','linux'):
        native=registration or (_mac_registration() if sys.platform=='darwin' else _linux_registration())
        if _user_space(native) is not None:return native.start(explicit=explicit,deadline=deadline,hold_id=hold_id)
        return native.start(explicit=explicit,deadline=deadline) if deadline is not None else native.start(explicit=explicit)
    else:raise processes.OwnershipError('Unsupported native service platform.')
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=remaining,**cfg.child_options())


def _user_space(registration=None):
    if sys.platform!='linux':return None
    from brain_linux_user_service import LinuxUserService, systemd_manager_present
    if registration is not None:return registration if isinstance(registration,LinuxUserService) else None
    return None if systemd_manager_present() else _linux_registration()


def prove_user_space_quiescence(registration=None):
    """Require fresh generation, process, descendant and port proof under the controller locks."""
    user=_user_space(registration)
    if user is None:raise processes.OwnershipError('User-space Linux service is required.')
    from brain_linux_generation import GenerationDrain
    with ExitStack() as stack:
        GenerationDrain(user,stack).prove_quiet()


def retire_user_space_generation(registration=None):
    user=_user_space(registration)
    prove_user_space_quiescence(user)
    from brain_index_bootstrap import settle_interrupted
    from brain_linux_generation import GENERATION, clear_blocked, drain_recorded
    settle_interrupted(lambda generation:drain_recorded(user,generation))
    user._remove_state_file(_state()/GENERATION)
    user._remove_state_file(user.keeper_path)
    clear_blocked(user)


def user_space_blocker():
    if sys.platform!='linux':return None
    from brain_linux_user_service import systemd_manager_present
    if systemd_manager_present():return None
    # A backend can be intentionally absent. Read only the local diagnostic.
    if not (_state()/'user-service-blocked.json').exists():return None
    from brain_linux_generation import blocked
    return blocked(_linux_registration())


def managed_drain_recorded(generation):
    user=_user_space()
    if user is None:return False
    from brain_linux_generation import drain_recorded
    return drain_recorded(user,generation)


def index_launch(*,manual):
    if sys.platform!='linux':return None
    from brain_linux_user_service import systemd_manager_present
    if systemd_manager_present():return None
    from brain_linux_generation import GENERATION, Launch, LifecycleError, launch_for
    if manual:return Launch(None,None,True)
    if not (_state()/GENERATION).exists():
        raise LifecycleError('bootstrap_unenrolled','The index bootstrap does not belong to an enrolled generation.')
    return launch_for(_linux_registration(),manual=False)


def supervisor_spawn_allowed():
    if not spawn_allowed():return False
    user=_user_space()
    if user is None:return True
    from brain_linux_generation import supervisor_allowed
    return supervisor_allowed(user)


def enrol_supervisor_child(role,child):
    user=_user_space()
    if user is None:return
    from brain_linux_generation import enroll
    enroll(user,role,child)


def enrol_index_bootstrap(child,arguments):
    enrol_supervisor_child('index_bootstrap',child)


def _listener(deadline=None):
    return processes.listener_identity(deadline=deadline) if sys.platform=='linux' and deadline is not None else processes.listener_identity()


def _startup_budget(deadline,limit):
    remaining=min(limit,deadline-time.monotonic())
    if remaining<=0:raise processes.OwnershipError('Service startup exceeded the caller deadline.')
    return remaining


def write_heartbeat(status):
    if os.name=='nt':
        api=processes._api();birth=processes._birth(api,api.GetCurrentProcess())
    else:birth=int(__import__('psutil').Process().create_time()*10_000_000)+116444736000000000
    extra={}
    if sys.platform=='linux':
        lifetime=processes._linux_lifetime(os.getpid())
        if lifetime is None:raise processes.OwnershipError('Heartbeat process lifetime is unavailable.')
        extra={**lifetime,'written_boottime_ticks':processes.linux_marker()['boottime_ticks']}
    _atomic(_state()/'service-heartbeat.json',{'status':status,'pid':os.getpid(),'birth':birth,
            'written_filetime':processes.filetime_now(),'version':cfg.VERSION,**extra})


def _heartbeat(identity,attempt,legacy,linux_attempt=None):
    path=_state()/'service-heartbeat.json'
    if path.exists():
        if path.is_symlink() or os.path.isjunction(path) or path.stat().st_size>65536:return None
        try:value=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,RecursionError):return None
        if isinstance(value,dict) and value.get('status')=='ready' and value.get('pid')==identity['supervisor_pid']:
            if sys.platform=='linux':
                if (isinstance(linux_attempt,dict) and type(linux_attempt.get('boottime_ticks')) is int
                    and value.get('boot_id')==identity.get('supervisor_boot_id')==linux_attempt.get('boot_id')
                    and type(value.get('start_ticks')) is int and value['start_ticks']==identity.get('supervisor_start_ticks')
                    and type(value.get('written_boottime_ticks')) is int
                    and value['written_boottime_ticks']>linux_attempt['boottime_ticks']):return 'verified'
            elif value.get('birth')==identity['supervisor_birth'] and value.get('written_filetime',0)>=attempt:return 'verified'
    legacy_versions=('0.1.7',) if sys.platform=='linux' else ('0.1.0','0.1.1','0.1.2','0.1.3','0.1.4','0.1.5','0.1.6','0.1.7')
    if legacy and cfg.DEVICE.get('version') in legacy_versions:
        return 'legacy-unavailable'
    return None


def _wait_started(attempt,since,legacy,deadline,linux_attempt=None,linux_since=None):
    while time.monotonic()<deadline:
        try:identity=_listener(deadline)
        except (OSError,ValueError):
            # Atomic registry replacement can be briefly unavailable on Windows.
            # OwnershipError remains fatal; never retry a foreign listener.
            time.sleep(min(.25,max(0,deadline-time.monotonic())));continue
        if identity is not None:
            fresh=(processes.newer_than_empty(identity,linux_since) if linux_since is not None
                   else since is None or (identity['birth']>=since and identity['supervisor_birth']>=since))
            heartbeat=(_heartbeat(identity,attempt,legacy,linux_attempt) if linux_attempt is not None
                       else _heartbeat(identity,attempt,legacy))
            if fresh and heartbeat:return identity,heartbeat
        time.sleep(min(.25,max(0,deadline-time.monotonic())))
    raise processes.OwnershipError('Service did not publish an owned ready listener/heartbeat before the deadline.')


def start(*,explicit=False,hold_id=None,since=None,allow_legacy=False,deadline=None):
    deadline=time.monotonic()+180 if deadline is None else deadline
    _startup_budget(deadline,180)
    _qualified_native_platform()
    processes.validate_environment()
    attempt=processes.filetime_now()
    linux_attempt=processes.linux_marker() if sys.platform=='linux' else None
    linux_since=None
    if since is not None and (type(since) is not int or (sys.platform!='linux' and attempt<since)):
        raise processes.OwnershipError('Startup clock predates verified quiescence.')
    with _control(deadline):
        _startup_budget(deadline,180)
        value=_match_transaction(hold_id)
        if value is not None:
            if not explicit:raise processes.OwnershipError('Automatic start is blocked by the update transaction.')
            if not value.get('empty_at_filetime') or value.get('stage') not in ('stopped','start failed','started'):
                raise processes.OwnershipError('Transaction must verify a stopped runtime before starting.')
            if sys.platform=='linux' and since is not None and since!=value['empty_at_filetime']:
                raise processes.OwnershipError('Linux --since must name exactly the transaction stop receipt; freshness uses its boot-relative boundary.')
            since=max(since or 0,value['empty_at_filetime'])
            if sys.platform=='linux':
                linux_since=_linux_boundary(value,'empty_')
                if (linux_since['boot_id']!=linux_attempt['boot_id']
                    or type(linux_since['boottime_ticks']) is not int
                    or not 0<=linux_since['boottime_ticks']<=linux_attempt['boottime_ticks']):
                    raise processes.OwnershipError('Linux transaction quiescence boundary is missing or belongs to another boot.')
        elif sys.platform=='linux' and since is not None:
            raise processes.OwnershipError('Linux freshness needs the recorded boot-relative transaction boundary.')
        registration=(_mac_registration() if sys.platform=='darwin' else _linux_registration() if sys.platform=='linux' else None)
        user=_user_space(registration)
        task=(_windows_task(deadline=deadline) if sys.platform=='win32' else
              registration.inspect(deadline=deadline) if registration is not None and user is None else
              registration.inspect() if user is not None else None)
        if sys.platform=='win32' and task is None:raise processes.OwnershipError('Service task is not installed.')
        if registration is not None and not task['file_present']:raise processes.OwnershipError('The owned native registration is not installed.')
        # A user-space generation may enter verified orphan cleanup before
        # ordinary readiness admission. The backend checks every occupied row.
        if user is None or (user._keeper_process() is not None and task['running']):
            listening=_listener(deadline)
            if listening is not None and registration is not None:_native_listener_binding(listening,task)
        if task is not None and not task['enabled'] and not explicit:
            raise processes.OwnershipError('Service is explicitly stopped; use service start to enable it.')
        try:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'starting','start_attempt_filetime':attempt})
            _atomic(_state()/'service-control-status.json',{'status':'starting','started_at_filetime':attempt,'hold_id':hold_id})
            if sys.platform=='win32' and task is not None and not task['enabled']:
                subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=_startup_budget(deadline,20),**cfg.child_options())
            _start_native(explicit=explicit,deadline=deadline,registration=registration,hold_id=hold_id)
        except BaseException:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'start failed'})
            _atomic(_state()/'service-control-status.json',{'status':'start failed','phase':'native start','hold_id':hold_id})
            raise
    try:
        # The supervisor reports readiness on a 20-second cycle and a first start from a new environment is slow.
        args=(attempt,since,allow_legacy,deadline)
        identity,heartbeat=(_wait_started(*args,linux_attempt,linux_since) if linux_attempt is not None else _wait_started(*args))
        result={'status':'started','identity':identity,'heartbeat_verified':heartbeat=='verified','transaction':hold_id,'started_at_filetime':attempt}
        with _control(deadline):
            _startup_budget(deadline,180)
            current=_match_transaction(hold_id)
            if current is not None and (current.get('stage')!='starting' or current.get('start_attempt_filetime')!=attempt):
                raise processes.OwnershipError('Another controller superseded this startup attempt.')
            if registration is not None:
                current_native=registration.inspect() if user is not None else registration.inspect(deadline=deadline)
                _native_listener_binding(identity,current_native)
            if not processes.same_runtime(_listener(deadline),identity):raise processes.OwnershipError('Listener changed before recording startup.')
            if current is not None:_atomic(_state()/'service-transaction.json',{**current,'stage':'started','identity':identity,'started_at_filetime':attempt,'legacy_heartbeat':heartbeat!='verified',
                **({'started_boot_id':linux_attempt['boot_id'],'started_boottime_ticks':linux_attempt['boottime_ticks']} if linux_attempt is not None else {})})
            if user is not None:
                from brain_linux_generation import clear_blocked
                clear_blocked(user)
            _atomic(_state()/'service-control-status.json',result)
        return result
    except BaseException as error:
        # Do not replace a concurrent controller's newer stop/start receipt.
        try:
            with _control(deadline):
                current=transaction()
                if current is not None and current.get('hold_id')==hold_id and current.get('start_attempt_filetime')==attempt and current.get('stage')=='starting':
                    _atomic(_state()/'service-transaction.json',{**current,'stage':'start failed'})
                status_path=_state()/'service-control-status.json'
                status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
                if status.get('started_at_filetime')==attempt:
                    _atomic(status_path,{'status':'start failed','phase':'owned readiness','error_type':type(error).__name__,'hold_id':hold_id,'started_at_filetime':attempt})
        except Exception:pass  # A concurrent controller retains ownership/evidence.
        raise processes.OwnershipError('Startup readiness failed ('+type(error).__name__+': '+str(error)[:200]+'); inspect service-control-status.json and retry the recorded transaction stop before recovery.') from error


def stop(*,hold_id=None,wait_seconds=30):
    _qualified_native_platform()
    processes.validate_environment()
    if type(wait_seconds) is not int or not 1<=wait_seconds<=300:raise ValueError('Use a stop wait between 1 and 300 seconds.')
    deadline=time.monotonic()+wait_seconds
    drain=None
    owned=[];task=None;disabled=False;end_attempted=False;phase='task preflight';mac=None;linux=None;linux_receipt=None
    native_disable_attempted=False;native_restore_allowed=False
    windows_disable_attempted=False;windows_restore_allowed=False;windows_preflight_verified=False
    with _control(deadline), ExitStack() as retained:
        value=_match_transaction(hold_id)
        windows_history=_windows_stop_history(hold_id) if sys.platform=='win32' else None
        if windows_history is not None:
            task=({'enabled':windows_history['prior_task_enabled'],'definition_sha256':windows_history['prior_task_definition_sha256']}
                  if windows_history['prior_task_present'] else None)
            end_attempted=windows_history['end_attempted'];windows_disable_attempted=windows_history['activation_disable_attempted']
        history=_native_stop_history(hold_id) if sys.platform in ('darwin','linux') else None
        if history is not None:
            task=history['prior_native'];end_attempted=history['end_attempted']
            native_disable_attempted=history['activation_disable_attempted'];disabled=native_disable_attempted
        if value is not None:
            if type(value.get('stop_entered')) is not bool:raise processes.OwnershipError('Stop-entry marker is invalid; hold preserved.')
            value={**value,'stop_entered':True}
            _atomic(_state()/'service-transaction.json',value)
        try:
            if sys.platform=='win32':
                current=_windows_task()
                _sealed_registration_check(value,current,windows_history)
                if current is None:
                    if task is not None:raise processes.OwnershipError('The held Windows task disappeared; preserve the hold.')
                    windows_preflight_verified=True
                else:
                    if windows_history is not None and not windows_history['prior_task_present']:
                        raise processes.OwnershipError('A task appeared after verified absence; preserve the hold.')
                    fingerprint=_windows_task_identity(current)
                    if task is None:task=current
                    if _windows_task_identity(task)!=fingerprint:raise processes.OwnershipError('Windows task definition changed since this stop attempt; hold preserved.')
                    windows_preflight_verified=True;windows_restore_allowed=not end_attempted;windows_disable_attempted=True
                    _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'task activation suspension',
                        **_windows_stop_fields(task,hold_id,windows_disable_attempted,end_attempted)})
                    disabled=None  # A failed native call may have partially changed activation.
                    subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Disable'],check=True,timeout=20,**cfg.child_options())
                    current=_windows_task()
                    if _windows_task_identity(current)!=fingerprint or current['enabled']:
                        raise processes.OwnershipError('Windows task disable/definition was not verified; hold preserved.')
                    disabled=True
            elif sys.platform=='darwin':
                mac=_mac_registration();current_native=mac.inspect()
                _sealed_registration_check(value,current_native,history)
                if not isinstance(current_native.get('definition_sha256'),str) or type(current_native.get('enabled')) is not bool:
                    raise processes.OwnershipError('Mac native definition is missing or incomplete; no activation change attempted.')
                if task is None:task=current_native
                if task['definition_sha256']!=current_native['definition_sha256']:
                    raise processes.OwnershipError('Native definition changed since this stop attempt; hold preserved.')
                native_restore_allowed=not end_attempted;native_disable_attempted=True;disabled=True
                _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'native activation suspension',
                    'prior_native':task,'activation_disable_attempted':True,'end_attempted':end_attempted,'hold_id':hold_id})
                mac.suspend_activation()
            elif sys.platform=='linux':
                linux=_linux_registration()
                if value is not None:
                    first_stop=_stop_first_user_space(linux,value,max(0,deadline-time.monotonic()))
                    if first_stop is not None:return first_stop
                current_native=linux.inspect(allow_reload=True,prove_main=False)
                _sealed_registration_check(value,current_native,history)
                if task is None:task=current_native
                if task['definition_sha256']!=current_native['definition_sha256']:
                    raise processes.OwnershipError('Native definition changed since this stop attempt; hold preserved.')
                native_restore_allowed=not end_attempted;native_disable_attempted=True;disabled=True
                _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'native activation suspension',
                    'prior_native':task,'activation_disable_attempted':True,'end_attempted':end_attempted,'hold_id':hold_id})
                linux.suspend_activation(prior=task)
                if _user_space(linux) is not None:
                    from brain_linux_generation import GenerationDrain
                    phase='managed index preflight and drain'
                    drain=GenerationDrain(linux,retained)
                    drain.drain_index(deadline)
            phase='writer admission'
            with ExitStack() as locks:
                admission=deadline
                for name in ('wiki-worker.lock','index-bootstrap.lock','maintenance.lock','writer.lock'):
                    locks.enter_context(FileLock(str(_state()/name),timeout=max(0,admission-time.monotonic())))
                phase='process identity'
                owned=processes.roots()
                if any(process.role=='index_worker' and process.alive() for process in owned):
                    raise processes.OwnershipError('A legacy reindex process is still active; preserve it and wait before stopping/upgrading.')
                owned.extend(processes.descendants(owned))
                if mac is not None:
                    current_native=mac.inspect()
                    if any(process.role=='supervisor' and process.alive() and process.pid!=current_native.get('main_pid') for process in owned):
                        raise processes.OwnershipError('A manual same-root supervisor is active; preserve it before native stop.')
                    _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'native end',
                        'prior_native':task,'activation_disable_attempted':True,'end_attempted':True,'hold_id':hold_id})
                elif linux is not None:
                    current_native=linux.inspect(allow_reload=True,prove_main=False)
                    if (current_native['definition_sha256']!=task['definition_sha256'] or current_native['enabled']):
                        raise processes.OwnershipError('Linux definition or activation changed before native End.')
                    if drain is not None:
                        drain.inventory()
                    live_supervisors=[process for process in owned if process.role=='supervisor' and process.alive()]
                    if any(process.pid!=current_native.get('main_pid') for process in live_supervisors):
                        raise processes.OwnershipError('A manual same-root supervisor is active outside the native unit.')
                    if (current_native['running'] and current_native.get('main_lifetime') is not None
                        and not any(process.pid==current_native['main_pid']
                                    and process.lifetime==current_native['main_lifetime'] for process in live_supervisors)):
                        raise processes.OwnershipError('The native supervisor is missing from the verified role inventory.')
                    if drain is None:linux.verify_processes(owned)
                    # Stop may repair an orphan within this exact cgroup; it
                    # neither authenticates to that listener nor signals PIDs.
                    _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'native end',
                        'prior_native':task,'activation_disable_attempted':True,'end_attempted':True,'hold_id':hold_id})
                phase='native end'
                if sys.platform=='win32':
                    disabled=None
                    current=_windows_task()
                    if (current is None)!=(task is None) or (task is not None and
                            (_windows_task_identity(current)!=_windows_task_identity(task) or current['enabled'])):
                        raise processes.OwnershipError('Windows task changed before End; hold preserved.')
                    disabled=task is not None
                    end_attempted=True  # Sticky intent must be durable before entering the OS boundary.
                    _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':phase,
                        **_windows_stop_fields(task,hold_id,windows_disable_attempted,end_attempted)})
                end_attempted=True
                if sys.platform=='darwin':native_exit=mac.native_stop()['native_end_exit']
                elif sys.platform=='linux':
                    linux_receipt=(linux.native_stop(drain=drain,deadline=deadline) if drain is not None else linux.native_stop())
                    native_exit=linux_receipt['native_end_exit']
                    if linux_receipt.get('native_stopped') is not True:
                        raise processes.OwnershipError('Linux manager did not verify native quiescence.')
                else:
                    command=([_tool('schtasks.exe'),'/End','/TN',_task_address()] if sys.platform=='win32'
                             else ['systemctl','--user','stop',LINUX_UNIT])
                    native_exit=subprocess.run(command,check=False,capture_output=True,timeout=30,**cfg.child_options()).returncode
                phase='retained process cleanup'
                if sys.platform=='win32':
                    for process in sorted(owned,key=lambda process:(process.role!='supervisor',process.kind=='launcher')):process.terminate()
                if drain is None:deadline=time.monotonic()+wait_seconds
                while time.monotonic()<deadline:
                    # Job/TerminateProcess exits are asynchronous. Never inspect
                    # orphan-shaped base processes while retained peers are exiting.
                    if any(process.alive() for process in owned):time.sleep(.1);continue
                    extra=[]
                    try:
                        extra=processes.roots()
                        if any(process.role=='index_worker' and process.alive() for process in extra):raise processes.OwnershipError('Late legacy reindex remains active; no empty receipt.')
                        extra.extend(processes.descendants(extra))
                        if sys.platform=='win32':
                            for process in extra:process.terminate()
                        owned.extend(extra);extra=[]
                        if not any(process.alive() for process in owned) and not processes._listeners():break
                    except processes.OwnershipError:
                        # Retrying inspection never authorizes an unknown kill.
                        pass
                    finally:
                        for process in extra:process.close()
                    time.sleep(.1)
                else:raise processes.OwnershipError('Stop could not verify empty runtime/port; task remains stopped. Inspect exact process identity before recovery; do not kill cached PIDs.')
                if mac is not None:
                    verified=mac.inspect()
                    if verified['loaded'] or verified['enabled']:raise processes.OwnershipError('Mac activation/unloaded proof changed before empty receipt.')
                elif linux is not None:
                    if drain is not None:
                        drain.prove_quiet()
                        from brain_index_bootstrap import settle_interrupted
                        from brain_linux_generation import clear_blocked, drain_recorded
                        settle_interrupted(lambda generation:drain_recorded(linux,generation))
                        clear_blocked(linux)
                    verified=linux.inspect(allow_reload=True,prove_main=False)
                    if (verified['enabled'] or not verified['quiescent']
                            or verified['definition_sha256']!=task['definition_sha256']):
                        raise processes.OwnershipError('Linux disabled cgroup quiescence changed before empty receipt.')
                    disabled=True if verified['enabled'] is False else None
                empty=processes.filetime_now()
                linux_empty=processes.linux_marker() if sys.platform=='linux' else None
                extra=({'empty_boot_id':linux_empty['boot_id'],'empty_boottime_ticks':linux_empty['boottime_ticks']} if linux_empty is not None else {})
                if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'stopped','empty_at_filetime':empty,**extra})
                result={'status':'stopped','empty_at_filetime':empty,'native_end_exit':native_exit,'task_disabled':disabled,
                        'legacy_scope':'birth-verified exact role pairs and retained descendants; no adoption',**extra}
                if linux_receipt is not None:result['native']=linux_receipt
                if sys.platform=='win32':result.update(_windows_stop_fields(task,hold_id,windows_disable_attempted,end_attempted))
                _atomic(_state()/'service-control-status.json',result);return result
        except BaseException as error:
            restored=False
            unresolved_drain=(drain is not None and drain.started and drain.value['drain']!='drained')
            if (sys.platform=='win32' and not end_attempted and windows_disable_attempted
                    and windows_restore_allowed and task is not None and task['enabled']):
                try:
                    disabled=None;current=_windows_task()
                    if _windows_task_identity(current)!=_windows_task_identity(task):raise processes.OwnershipError('Changed task cannot restore activation.')
                    if not current['enabled']:
                        subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=20,**cfg.child_options())
                    current=_windows_task()
                    if _windows_task_identity(current)!=_windows_task_identity(task) or not current['enabled']:
                        raise processes.OwnershipError('Original Windows activation was not verified.')
                    restored=True;disabled=False
                except Exception:pass
            elif (sys.platform!='win32' and not end_attempted and not unresolved_drain and disabled and task is not None and task['enabled']
                    and (sys.platform not in ('darwin','linux') or native_restore_allowed)):
                try:
                    if mac is not None:mac.restore_activation(task)
                    elif linux is not None:linux.restore_activation(task)
                    else:subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=20,**cfg.child_options())
                    restored=True;disabled=False
                except Exception:pass
            receipt={'status':'stop failed','phase':phase,'error_type':type(error).__name__,'end_attempted':end_attempted,
                     'task_disabled':disabled,'prior_enabled_restored':restored,'prior_task_enabled':task['enabled'] if task is not None else None,'hold_id':hold_id,
                     'guidance':'Pending work preserved. No empty-runtime receipt; retry the exact transaction after the active writer finishes or inspect owned-process identity.'}
            if sys.platform=='win32':
                if windows_preflight_verified or windows_history is not None:
                    receipt.update(_windows_stop_fields(task,hold_id,windows_disable_attempted,end_attempted))
                else:
                    receipt.update(root=str(cfg.ROOT),task_address=_task_address(),hold_id=hold_id,
                        prior_task_present=None,prior_task_enabled=None,prior_task_definition_sha256=None,
                        activation_disable_attempted=False,end_attempted=False)
                    receipt['status']='stop preflight failed'
            if sys.platform in ('darwin','linux'):
                receipt.update(prior_native=task,activation_disable_attempted=native_disable_attempted)
                if drain is not None:
                    receipt['managed_drain_unresolved']=unresolved_drain
                    if getattr(error,'reason',None):receipt['reason']=error.reason
                if task is None and not native_disable_attempted and not end_attempted:receipt['status']='stop preflight failed'
            _atomic(_state()/'service-control-status.json',receipt)
            reason=' '+str(error) if isinstance(error,processes.OwnershipError) else ''
            raise processes.OwnershipError('Stop failed during '+phase+'; no empty-runtime receipt.'+reason+' See service-control-status.json; pending work is preserved.') from error
        finally:
            for process in owned:process.close()


def _registration_descriptor(native,registration=None):
    if native is None or (sys.platform!='win32' and not native.get('file_present')):return None
    interpreter=native.get('interpreter')
    if registration is not None:
        matches=[argv[0] for raw,argv in registration.known.items() if hashlib.sha256(raw).hexdigest()==native['definition_sha256']]
        if len(matches)!=1:raise processes.OwnershipError('Native interpreter definition is ambiguous.')
        interpreter=matches[0]
    result={'kind':sys.platform,'definition_sha256':native['definition_sha256'],
            'enabled':native['enabled'],'interpreter':interpreter}
    if sys.platform=='win32':result.update(xml=native['xml'],owner_sid=native['owner_sid'])
    return result


def inspect():
    """Read actual ownership; updater separately requires present+enabled intent."""
    processes.validate_environment()
    with _control():
        if sys.platform in ('darwin','linux'):
            registration=_mac_registration() if sys.platform=='darwin' else _linux_registration()
            native=registration.inspect()
            blocker=user_space_blocker()
            if blocker is None:
                listening=processes.listener_identity()
                if listening is not None:_native_listener_binding(listening,native)
            expected=hashlib.sha256(definition(sys.platform).encode('utf-8')).hexdigest()
            pending_initial_enable=False
            if native['file_present'] and native['enabled'] is False and native['definition_sha256']==expected:
                try:
                    prior,requested=_native_install_intent(native,expected)
                    pending_initial_enable=prior['file_present'] is False and requested is True
                except processes.OwnershipError:pass
            return {'status':'inspected','task_present':native['file_present'] and native['loaded'],
                    'task_enabled':native['enabled'],'native':native,'registration':_registration_descriptor(native,registration),
                    'expected_native_definition_sha256':expected,'pending_initial_install_enable':pending_initial_enable,
                    **({'blocked':blocker} if blocker is not None else {})}
        task=_windows_task(include_xml=True) if sys.platform=='win32' else None
        return {'status':'inspected','task_present':task is not None,
                'task_enabled':task['enabled'] if task is not None else None,'registration':_registration_descriptor(task)}


def recovery_status():
    processes.validate_environment()
    value=transaction()
    return {'status':'recovery required' if value else 'no transaction','transaction':value,
            'device_version':cfg.DEVICE.get('version'),
            'guidance':'Use the verified new checkout update.py --recover --hold-id UUID; it must acquire update.lock before recovery.'}
