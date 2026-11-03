"""Generate per-user OS service definitions with the same Python core."""
import hashlib, json, os, plistlib, re, shlex, subprocess, sys, tempfile, time
from contextlib import ExitStack,contextmanager
from pathlib import Path
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_process as processes

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
    from brain_linux_registration import LinuxRegistration
    return LinuxRegistration(LINUX_UNIT,cfg.ROOT,processes._selected_python(),current_python=cfg.PYTHON)


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
def _control():
    lock=FileLock(str(_state()/'service-control.lock'),timeout=5)
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
               'stage':'prepared','created_filetime':processes.filetime_now()}
        _atomic(_state()/'service-transaction.json',value)
        return value


def finish_transaction(hold_id,expected_version=None,allow_legacy=False):
    _qualified_native_platform()
    processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None:raise processes.OwnershipError('No matching transaction to finish.')
        if value.get('stage')!='started' or not value.get('empty_at_filetime'):
            raise processes.OwnershipError('Transaction has not completed a verified stop and start.')
        if not isinstance(expected_version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',expected_version) or expected_version!=cfg.DEVICE.get('version'):
            raise processes.OwnershipError('Name the installed expected version before finishing recovery.')
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
        _atomic(_state()/'service-transactions'/(hold_id+'.json'),{**value,'stage':'complete','completed_filetime':processes.filetime_now(),'authenticated_version':expected_version,'identity':owned,'heartbeat_verified':heartbeat=='verified'})
        (_state()/'service-transaction.json').unlink()
        return {'status':'transaction released','hold_id':hold_id,'identity':owned}


def spawn_allowed():
    value=transaction()
    return value is None or value.get('stage') in ('starting','started')


def cancel_transaction(hold_id):
    """Release only a positively refused pre-End stop, with no runtime switch."""
    _qualified_native_platform()
    processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None or value.get('stage')!='prepared':raise processes.OwnershipError('Only a prepared, refused stop can be cancelled without recovery.')
        path=_state()/'service-control-status.json'
        status=(_windows_stop_history(hold_id) if sys.platform=='win32' else
                _native_control_status() if sys.platform=='darwin' else json.loads(path.read_text(encoding='utf-8')))
        if sys.platform=='win32' and status is None:raise processes.OwnershipError('No matching verified stop history; recovery hold preserved.')
        if status.get('status')!='stop failed' or status.get('hold_id')!=hold_id or status.get('end_attempted') is not False:
            raise processes.OwnershipError('No matching pre-End refusal receipt; recovery hold preserved.')
        if sys.platform=='win32':
            task=_windows_task()
            if (task is not None)!=status['prior_task_present']:
                raise processes.OwnershipError('Task presence changed since this stop; recovery hold preserved.')
            if task is not None:
                if _windows_task_identity(task)!=status['prior_task_definition_sha256']:
                    raise processes.OwnershipError('Task definition changed since this stop; recovery hold preserved.')
                if task['enabled']!=status['prior_task_enabled']:raise processes.OwnershipError('Task state was not restored to its pre-stop state; hold preserved.')
        elif sys.platform=='darwin':
            native=_mac_registration().inspect()
            prior=status.get('prior_native')
            if (not isinstance(prior,dict) or type(prior.get('enabled')) is not bool
                    or not isinstance(prior.get('definition_sha256'),str)
                    or prior['enabled']!=status.get('prior_task_enabled')
                    or native['definition_sha256']!=prior['definition_sha256']):
                raise processes.OwnershipError('Mac native definition changed or lacks its prior receipt; hold preserved.')
            if native['enabled']!=status.get('prior_task_enabled'):
                raise processes.OwnershipError('Mac activation was not restored to its pre-stop state; hold preserved.')
        elif sys.platform=='linux':
            native=_linux_registration().inspect(allow_reload=True,prove_main=False)
            prior=status.get('prior_native')
            if (not isinstance(prior,dict) or native['enabled']!=status.get('prior_task_enabled')
                or native['definition_sha256']!=prior.get('definition_sha256')):
                raise processes.OwnershipError('Linux activation or definition was not restored to its pre-stop state; hold preserved.')
        import hashlib
        saved=Path(value['snapshot'])/'snapshot.json'
        if saved.stat().st_size>64*1024*1024 or hashlib.sha256(saved.read_bytes()).hexdigest()!=value.get('snapshot_sha256'):
            raise processes.OwnershipError('Snapshot identity changed; hold preserved.')
        snapshot=json.loads(saved.read_text(encoding='utf-8'))
        device=cfg.ROOT/'device.json'
        if hashlib.sha256(device.read_bytes()).hexdigest()!=snapshot['files'][0]['before_sha256']:
            raise processes.OwnershipError('Device changed since preflight; hold preserved.')
        app=cfg.ROOT/'app'
        payload={path.relative_to(app).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in app.rglob('*') if path.is_file() and '__pycache__' not in path.relative_to(app).parts}
        if payload!=snapshot['app_sha256']:raise processes.OwnershipError('App changed since preflight; hold preserved.')
        record={**value,'stage':'cancelled before runtime switch','cancelled_filetime':processes.filetime_now(),'empty_runtime_verified':False}
        _atomic(_state()/'service-transactions'/(hold_id+'.json'),record)
        (_state()/'service-transaction.json').unlink()
        _atomic(path,{**status,'status':'stop refused; runtime unchanged','transaction_cancelled':True})
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


def _windows_task():
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
    result=subprocess.run([_tool('powershell.exe'),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,**cfg.child_options())
    if result.returncode:raise processes.OwnershipError('Scheduled task ownership could not be queried.')
    value=json.loads(result.stdout)
    if not value.get('exists'):return None
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
    arguments=action.findtext('{*}Arguments',default='')
    # Definitions generated by this application have one quoted script argument.
    expected='"'+str(cfg.ROOT/'app/brain_supervisor.py')+'"'
    if arguments!=expected:raise processes.OwnershipError('The task name belongs to another runtime or action; preserve it.')
    command=action.findtext('{*}Command',default='')
    directory=action.findtext('{*}WorkingDirectory',default='')
    if (processes._path(command) not in processes._launchers() or processes._path(directory)!=processes._path(cfg.ROOT/'app')
            or value.get('owner_sid')!=value.get('current_sid') or not value.get('current_sid')):
        raise processes.OwnershipError('Task interpreter, directory or owner does not match this device.')
    value['enabled']=definition.findtext('{*}Settings/{*}Enabled',default='true')=='true'
    # Activation and an existing running instance are separate native properties.
    state=value.get('state')
    if ((value['enabled'] and state not in ('Ready','Queued','Running'))
            or (not value['enabled'] and state not in ('Disabled','Running'))):
        raise processes.OwnershipError('Task enabled setting disagrees with its supported native state.')
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
    value.pop('xml',None)
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

def install():
    _qualified_native_platform()
    processes.validate_environment()
    (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        with _control():
            previous=_windows_task()
            enabled=transaction() is None and (previous is None or previous['enabled'])
            target=_state()/'service-task.xml';target.write_text(definition(enabled=enabled),encoding='utf-16')
            subprocess.run([_tool('schtasks.exe'),'/Create','/TN',_task_address(),'/XML',str(target),'/F'],check=True,capture_output=True,timeout=30,**cfg.child_options())
    elif sys.platform=='darwin':
        with _control():
            active=transaction()
            if active is not None and (active.get('stage')!='stopped' or type(active.get('empty_at_filetime')) is not int or active['empty_at_filetime']<=0):
                raise processes.OwnershipError('Finish a verified stop before replacing the Mac definition during a hold.')
            registration=_mac_registration();prior=registration.inspect();target=registration.path
            if prior['loaded']:raise processes.OwnershipError('Stop the owned Mac job before replacing its registration.')
            if active is not None and prior['enabled'] is not False:
                raise processes.OwnershipError('Held Mac install requires verified disabled activation.')
            payload=definition().encode('utf-8');expected=hashlib.sha256(payload).hexdigest()
            prior,selected=(prior,False) if active is not None else _native_install_intent(prior,expected)
            if active is None:
                _atomic(_state()/'service-control-status.json',{'status':'installing','phase':'native activation suspension',
                    'prior_native':prior,'activation_disable_attempted':True,'end_attempted':False,
                    'requested_enabled':selected,'expected_native_definition_sha256':expected})
            try:
                registration.install(payload,enabled=selected)
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
            registration=_linux_registration();current=registration.inspect(allow_reload=True,prove_main=False);target=registration.path
            hold=transaction();active_hold=hold is not None
            if active_hold and (hold.get('stage')!='stopped' or not hold.get('empty_at_filetime') or current['enabled'] is not False):
                raise processes.OwnershipError('Held Linux install requires the verified disabled stopped phase.')
            payload=definition('linux').encode('utf-8');expected=hashlib.sha256(payload).hexdigest()
            prior,selected=(current,False) if active_hold else _native_install_intent(current,expected)
            if not active_hold:
                _atomic(_state()/'service-control-status.json',{'status':'installing','phase':'native activation suspension',
                    'prior_native':prior,'activation_disable_attempted':True,'end_attempted':False,
                    'requested_enabled':selected,'expected_native_definition_sha256':expected})
            try:
                registration.install(payload,enabled=selected)
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

def _start_native(*,explicit=False):
    if sys.platform=='win32': command=[_tool('schtasks.exe'),'/Run','/TN',_task_address()]
    elif sys.platform=='darwin':
        return _mac_registration().start(explicit=explicit)
    elif sys.platform=='linux':
        return _linux_registration().start(explicit=explicit)
    else:raise processes.OwnershipError('Unsupported native service platform.')
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30,**cfg.child_options())

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
        try:identity=processes.listener_identity()
        except (OSError,ValueError):
            # Atomic registry replacement can be briefly unavailable on Windows.
            # OwnershipError remains fatal; never retry a foreign listener.
            time.sleep(.25);continue
        if identity is not None:
            fresh=(processes.newer_than_empty(identity,linux_since) if linux_since is not None
                   else since is None or (identity['birth']>=since and identity['supervisor_birth']>=since))
            heartbeat=(_heartbeat(identity,attempt,legacy,linux_attempt) if linux_attempt is not None
                       else _heartbeat(identity,attempt,legacy))
            if fresh and heartbeat:return identity,heartbeat
        time.sleep(.25)
    raise processes.OwnershipError('Service did not publish an owned ready listener/heartbeat before the deadline.')


def start(*,explicit=False,hold_id=None,since=None,allow_legacy=False,deadline=None):
    _qualified_native_platform()
    processes.validate_environment()
    attempt=processes.filetime_now()
    linux_attempt=processes.linux_marker() if sys.platform=='linux' else None
    linux_since=None
    if since is not None and (type(since) is not int or (sys.platform!='linux' and attempt<since)):
        raise processes.OwnershipError('Startup clock predates verified quiescence.')
    # Refuse an occupied/foreign/orphan port before task mutation or credential use.
    processes.listener_identity()
    with _control():
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
        task=(_windows_task() if sys.platform=='win32' else _mac_registration().inspect() if sys.platform=='darwin'
              else _linux_registration().inspect() if sys.platform=='linux' else None)
        if sys.platform=='win32' and task is None:raise processes.OwnershipError('Service task is not installed.')
        if sys.platform=='darwin' and not task['file_present']:raise processes.OwnershipError('The owned Mac registration is not installed.')
        if sys.platform=='linux' and not task['file_present']:raise processes.OwnershipError('The owned Linux registration is not installed.')
        if sys.platform=='darwin':
            listening=processes.listener_identity()
            if listening is not None:_native_listener_binding(listening,task)
        elif sys.platform=='linux':
            listening=processes.listener_identity()
            if listening is not None:_native_listener_binding(listening,task)
        if task is not None and not task['enabled'] and not explicit:
            raise processes.OwnershipError('Service is explicitly stopped; use service start to enable it.')
        try:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'starting','start_attempt_filetime':attempt})
            _atomic(_state()/'service-control-status.json',{'status':'starting','started_at_filetime':attempt,'hold_id':hold_id})
            if sys.platform=='win32' and task is not None and not task['enabled']:
                subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=20,**cfg.child_options())
            if sys.platform in ('darwin','linux'):_start_native(explicit=explicit)
            else:_start_native()
        except BaseException:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'start failed'})
            _atomic(_state()/'service-control-status.json',{'status':'start failed','phase':'native start','hold_id':hold_id})
            raise
    try:
        args=(attempt,since,allow_legacy,deadline or time.monotonic()+60)
        identity,heartbeat=(_wait_started(*args,linux_attempt,linux_since) if linux_attempt is not None else _wait_started(*args))
        result={'status':'started','identity':identity,'heartbeat_verified':heartbeat=='verified','transaction':hold_id,'started_at_filetime':attempt}
        with _control():
            current=_match_transaction(hold_id)
            if current is not None and (current.get('stage')!='starting' or current.get('start_attempt_filetime')!=attempt):
                raise processes.OwnershipError('Another controller superseded this startup attempt.')
            if sys.platform=='darwin':_native_listener_binding(identity,_mac_registration().inspect())
            elif sys.platform=='linux':_native_listener_binding(identity,_linux_registration().inspect())
            if not processes.same_runtime(processes.listener_identity(),identity):raise processes.OwnershipError('Listener changed before recording startup.')
            if current is not None:_atomic(_state()/'service-transaction.json',{**current,'stage':'started','identity':identity,'started_at_filetime':attempt,'legacy_heartbeat':heartbeat!='verified',
                **({'started_boot_id':linux_attempt['boot_id'],'started_boottime_ticks':linux_attempt['boottime_ticks']} if linux_attempt is not None else {})})
            _atomic(_state()/'service-control-status.json',result)
        return result
    except BaseException as error:
        # Do not replace a concurrent controller's newer stop/start receipt.
        try:
            with _control():
                current=transaction()
                if current is not None and current.get('hold_id')==hold_id and current.get('start_attempt_filetime')==attempt and current.get('stage')=='starting':
                    _atomic(_state()/'service-transaction.json',{**current,'stage':'start failed'})
                status_path=_state()/'service-control-status.json'
                status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
                if status.get('started_at_filetime')==attempt:
                    _atomic(status_path,{'status':'start failed','phase':'owned readiness','error_type':type(error).__name__,'hold_id':hold_id,'started_at_filetime':attempt})
        except Exception:pass  # A concurrent controller retains ownership/evidence.
        raise processes.OwnershipError('Startup readiness failed; inspect service-control-status.json and retry the recorded transaction stop before recovery.') from error


def stop(*,hold_id=None,wait_seconds=30):
    _qualified_native_platform()
    processes.validate_environment()
    if type(wait_seconds) is not int or not 1<=wait_seconds<=300:raise ValueError('Use a stop wait between 1 and 300 seconds.')
    owned=[];task=None;disabled=False;end_attempted=False;phase='task preflight';mac=None;linux=None;linux_receipt=None
    native_disable_attempted=False;native_restore_allowed=False
    windows_disable_attempted=False;windows_restore_allowed=False;windows_preflight_verified=False
    with _control():
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
        try:
            if sys.platform=='win32':
                current=_windows_task()
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
                linux=_linux_registration();current_native=linux.inspect(allow_reload=True,prove_main=False)
                if task is None:task=current_native
                if task['definition_sha256']!=current_native['definition_sha256']:
                    raise processes.OwnershipError('Native definition changed since this stop attempt; hold preserved.')
                native_restore_allowed=not end_attempted;native_disable_attempted=True;disabled=True
                _atomic(_state()/'service-control-status.json',{'status':'stopping','phase':'native activation suspension',
                    'prior_native':task,'activation_disable_attempted':True,'end_attempted':end_attempted,'hold_id':hold_id})
                linux.suspend_activation(prior=task)
            phase='writer admission'
            with ExitStack() as locks:
                admission=time.monotonic()+wait_seconds
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
                    live_supervisors=[process for process in owned if process.role=='supervisor' and process.alive()]
                    if any(process.pid!=current_native.get('main_pid') for process in live_supervisors):
                        raise processes.OwnershipError('A manual same-root supervisor is active outside the native unit.')
                    if (current_native['running'] and current_native.get('main_lifetime') is not None
                        and not any(process.pid==current_native['main_pid']
                                    and process.lifetime==current_native['main_lifetime'] for process in live_supervisors)):
                        raise processes.OwnershipError('The native supervisor is missing from the verified role inventory.')
                    linux.verify_processes(owned)
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
                    linux_receipt=linux.native_stop();native_exit=linux_receipt['native_end_exit']
                    if linux_receipt.get('native_stopped') is not True:
                        raise processes.OwnershipError('Linux manager did not verify native quiescence.')
                else:
                    command=([_tool('schtasks.exe'),'/End','/TN',_task_address()] if sys.platform=='win32'
                             else ['systemctl','--user','stop',LINUX_UNIT])
                    native_exit=subprocess.run(command,check=False,capture_output=True,timeout=30,**cfg.child_options()).returncode
                phase='retained process cleanup'
                if sys.platform=='win32':
                    for process in sorted(owned,key=lambda process:(process.role!='supervisor',process.kind=='launcher')):process.terminate()
                deadline=time.monotonic()+wait_seconds
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
                    verified=linux.inspect(allow_reload=True,prove_main=False)
                    if verified['enabled'] or not verified['quiescent']:
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
            elif (sys.platform!='win32' and not end_attempted and disabled and task is not None and task['enabled']
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
                if task is None and not native_disable_attempted and not end_attempted:receipt['status']='stop preflight failed'
            _atomic(_state()/'service-control-status.json',receipt)
            reason=' '+str(error) if isinstance(error,processes.OwnershipError) else ''
            raise processes.OwnershipError('Stop failed during '+phase+'; no empty-runtime receipt.'+reason+' See service-control-status.json; pending work is preserved.') from error
        finally:
            for process in owned:process.close()


def inspect():
    """Read task ownership before an updater creates a durable hold."""
    processes.validate_environment()
    with _control():
        if sys.platform=='darwin':
            native=_mac_registration().inspect()
            listening=processes.listener_identity()
            if listening is not None:_native_listener_binding(listening,native)
            return {'status':'inspected','task_present':native['file_present'] and native['loaded'],
                    'task_enabled':native['enabled'],'native':native,
                    'expected_native_definition_sha256':hashlib.sha256(definition(sys.platform).encode('utf-8')).hexdigest()}
        if sys.platform=='linux':
            native=_linux_registration().inspect()
            listening=processes.listener_identity()
            if listening is not None:_native_listener_binding(listening,native)
            return {'status':'inspected','task_present':native['file_present'] and native['loaded'],
                    'task_enabled':native['enabled'],'native':native,
                    'expected_native_definition_sha256':hashlib.sha256(definition('linux').encode('utf-8')).hexdigest()}
        task=_windows_task() if sys.platform=='win32' else None
        return {'status':'inspected','task_present':task is not None,
                'task_enabled':task['enabled'] if task is not None else None}


def recovery_status():
    processes.validate_environment()
    value=transaction()
    return {'status':'recovery required' if value else 'no transaction','transaction':value,
            'device_version':cfg.DEVICE.get('version'),
            'guidance':'Use the verified new checkout update.py --recover --hold-id UUID; it must acquire update.lock before recovery.'}
