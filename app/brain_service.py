"""Generate per-user OS service definitions with the same Python core."""
import json, os, plistlib, re, shlex, subprocess, sys, tempfile, time
from contextlib import ExitStack,contextmanager
from pathlib import Path
from filelock import FileLock,Timeout
import brain_settings as cfg
import brain_process as processes

LABEL='local.second-brain'
TASK=cfg.DEVICE.get('service_name','SecondBrainLocalService')
LINUX_UNIT='second-brain.service'

def _state(): return cfg.ROOT/'state'


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
        if owned['birth']<minimum or owned['supervisor_birth']<minimum:
            raise processes.OwnershipError('The current listener predates verified quiescence.')
        heartbeat=_heartbeat(owned,value['started_at_filetime'],allow_legacy and value.get('legacy_heartbeat'))
        if not heartbeat:raise processes.OwnershipError('Fresh supervisor heartbeat is required to finish the transaction.')
        import asyncio
        from brain_cli import mcp_call
        health=asyncio.run(mcp_call('brain_health',{}))
        if health.get('status')!='running' or health.get('version')!=expected_version:
            raise processes.OwnershipError('Authenticated expected-version health did not match; transaction preserved.')
        again=processes.listener_identity()
        if again!=owned:raise processes.OwnershipError('Runtime identity changed during transaction health verification.')
        _atomic(_state()/'service-transactions'/(hold_id+'.json'),{**value,'stage':'complete','completed_filetime':processes.filetime_now(),'authenticated_version':expected_version,'identity':owned,'heartbeat_verified':heartbeat=='verified'})
        (_state()/'service-transaction.json').unlink()
        return {'status':'transaction released','hold_id':hold_id,'identity':owned}


def spawn_allowed():
    value=transaction()
    return value is None or value.get('stage') in ('starting','started')


def cancel_transaction(hold_id):
    """Release only a positively refused pre-End stop, with no runtime switch."""
    processes.validate_environment()
    with _control():
        value=_match_transaction(hold_id)
        if value is None or value.get('stage')!='prepared':raise processes.OwnershipError('Only a prepared, refused stop can be cancelled without recovery.')
        path=_state()/'service-control-status.json'
        status=json.loads(path.read_text(encoding='utf-8'))
        if status.get('status')!='stop failed' or status.get('hold_id')!=hold_id or status.get('end_attempted') is not False:
            raise processes.OwnershipError('No matching pre-End refusal receipt; recovery hold preserved.')
        if sys.platform=='win32':
            task=_windows_task()
            current=task['enabled'] if task is not None else None
            if current!=status.get('prior_task_enabled'):raise processes.OwnershipError('Task state was not restored to its pre-stop state; hold preserved.')
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
    if (value.get('state')=='Disabled')==value['enabled']:
        raise processes.OwnershipError('Task enabled setting disagrees with its native state.')
    value['trigger_count']=len(definition.findall('{*}Triggers/*'))
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
        return plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(cfg.PYTHON),str(script)],'RunAtLoad':True,'KeepAlive':True,
                              'WorkingDirectory':str(cfg.ROOT/'app'),'StandardOutPath':str(cfg.ROOT/'logs/service-supervisor.log'),
                              'StandardErrorPath':str(cfg.ROOT/'logs/service-supervisor.log')}).decode()
    # systemd quoting is not shell quoting. Escape percent specifiers and backslashes.
    def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    return '[Unit]\nDescription=Local Second Brain MCP\nAfter=network.target\n\n[Service]\nType=simple\nExecStart='+quote(cfg.PYTHON)+' '+quote(script)+'\nRestart=on-failure\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n'

def install():
    processes.validate_environment()
    (cfg.ROOT/'logs').mkdir(parents=True,exist_ok=True)
    if sys.platform=='win32':
        with _control():
            previous=_windows_task()
            enabled=transaction() is None and (previous is None or previous['enabled'])
            target=_state()/'service-task.xml';target.write_text(definition(enabled=enabled),encoding='utf-16')
            subprocess.run([_tool('schtasks.exe'),'/Create','/TN',_task_address(),'/XML',str(target),'/F'],check=True,capture_output=True,timeout=30,**cfg.child_options())
    elif sys.platform=='darwin':
        target=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(definition(),encoding='utf-8')
        # Registration is loaded only by explicit start. Installing during an
        # update hold must not run the replacement service under parent locks.
    else:
        target=Path.home()/'.config/systemd/user'/LINUX_UNIT; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(definition(),encoding='utf-8')
        subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
        subprocess.run(['systemctl','--user','enable',LINUX_UNIT],check=True,capture_output=True)
    return {'status':'installed','definition':str(target)}

def _start_native():
    if sys.platform=='win32': command=[_tool('schtasks.exe'),'/Run','/TN',_task_address()]
    elif sys.platform=='darwin':
        domain=f'gui/{os.getuid()}'; target=f'{domain}/{LABEL}'
        registered=subprocess.run(['launchctl','print',target],capture_output=True,**cfg.child_options())
        if registered.returncode:
            plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
            subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True,**cfg.child_options())
        command=['launchctl','kickstart',target]
    else: command=['systemctl','--user','start',LINUX_UNIT]
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30,**cfg.child_options())

def write_heartbeat(status):
    if os.name=='nt':
        api=processes._api();birth=processes._birth(api,api.GetCurrentProcess())
    else:birth=int(__import__('psutil').Process().create_time()*10_000_000)+116444736000000000
    _atomic(_state()/'service-heartbeat.json',{'status':status,'pid':os.getpid(),'birth':birth,
            'written_filetime':processes.filetime_now(),'version':cfg.VERSION})


def _heartbeat(identity,attempt,legacy):
    path=_state()/'service-heartbeat.json'
    if path.exists():
        if path.is_symlink() or os.path.isjunction(path) or path.stat().st_size>65536:return None
        try:value=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,RecursionError):return None
        if (isinstance(value,dict) and value.get('status')=='ready' and value.get('pid')==identity['supervisor_pid']
                and value.get('birth')==identity['supervisor_birth'] and value.get('written_filetime',0)>=attempt):return 'verified'
    if legacy and cfg.DEVICE.get('version') in ('0.1.0','0.1.1','0.1.2','0.1.3','0.1.4','0.1.5','0.1.6','0.1.7'):
        return 'legacy-unavailable'
    return None


def _wait_started(attempt,since,legacy,deadline):
    while time.monotonic()<deadline:
        try:identity=processes.listener_identity()
        except (OSError,ValueError):
            # Atomic registry replacement can be briefly unavailable on Windows.
            # OwnershipError remains fatal; never retry a foreign listener.
            time.sleep(.25);continue
        if identity is not None:
            fresh=since is None or (identity['birth']>=since and identity['supervisor_birth']>=since)
            heartbeat=_heartbeat(identity,attempt,legacy)
            if fresh and heartbeat:return identity,heartbeat
        time.sleep(.25)
    raise processes.OwnershipError('Service did not publish an owned ready listener/heartbeat before the deadline.')


def start(*,explicit=False,hold_id=None,since=None,allow_legacy=False,deadline=None):
    processes.validate_environment()
    attempt=processes.filetime_now()
    if since is not None and (type(since) is not int or attempt<since):raise processes.OwnershipError('Startup clock predates verified quiescence.')
    # Refuse an occupied/foreign/orphan port before task mutation or credential use.
    processes.listener_identity()
    with _control():
        value=_match_transaction(hold_id)
        if value is not None:
            if not explicit:raise processes.OwnershipError('Automatic start is blocked by the update transaction.')
            if not value.get('empty_at_filetime') or value.get('stage') not in ('stopped','start failed','started'):
                raise processes.OwnershipError('Transaction must verify a stopped runtime before starting.')
            since=max(since or 0,value['empty_at_filetime'])
        task=_windows_task() if sys.platform=='win32' else None
        if sys.platform=='win32' and task is None:raise processes.OwnershipError('Service task is not installed.')
        if task is not None and not task['enabled'] and not explicit:
            raise processes.OwnershipError('Service is explicitly stopped; use service start to enable it.')
        try:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'starting','start_attempt_filetime':attempt})
            _atomic(_state()/'service-control-status.json',{'status':'starting','started_at_filetime':attempt,'hold_id':hold_id})
            if task is not None and not task['enabled']:
                subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=20,**cfg.child_options())
            _start_native()
        except BaseException:
            if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'start failed'})
            _atomic(_state()/'service-control-status.json',{'status':'start failed','phase':'native start','hold_id':hold_id})
            raise
    try:
        identity,heartbeat=_wait_started(attempt,since,allow_legacy,deadline or time.monotonic()+60)
        result={'status':'started','identity':identity,'heartbeat_verified':heartbeat=='verified','transaction':hold_id,'started_at_filetime':attempt}
        with _control():
            current=_match_transaction(hold_id)
            if current is not None and (current.get('stage')!='starting' or current.get('start_attempt_filetime')!=attempt):
                raise processes.OwnershipError('Another controller superseded this startup attempt.')
            if processes.listener_identity()!=identity:raise processes.OwnershipError('Listener changed before recording startup.')
            if current is not None:_atomic(_state()/'service-transaction.json',{**current,'stage':'started','identity':identity,'started_at_filetime':attempt,'legacy_heartbeat':heartbeat!='verified'})
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
    processes.validate_environment()
    if type(wait_seconds) is not int or not 1<=wait_seconds<=300:raise ValueError('Use a stop wait between 1 and 300 seconds.')
    owned=[];task=None;disabled=False;end_attempted=False;phase='task preflight'
    with _control():
        value=_match_transaction(hold_id)
        try:
            if sys.platform=='win32':
                task=_windows_task()
                if task is not None:
                    subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Disable'],check=True,timeout=20,**cfg.child_options())
                    disabled=True
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
                if sys.platform=='win32':command=[_tool('schtasks.exe'),'/End','/TN',_task_address()]
                elif sys.platform=='darwin':command=['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}']
                else:command=['systemctl','--user','stop',LINUX_UNIT]
                phase='native end';end_attempted=True
                native=subprocess.run(command,check=False,capture_output=True,timeout=30,**cfg.child_options())
                phase='retained process cleanup'
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
                empty=processes.filetime_now()
                if value is not None:_atomic(_state()/'service-transaction.json',{**value,'stage':'stopped','empty_at_filetime':empty})
                result={'status':'stopped','empty_at_filetime':empty,'native_end_exit':native.returncode,'task_disabled':disabled,
                        'legacy_scope':'birth-verified exact role pairs and retained descendants; no adoption'}
                _atomic(_state()/'service-control-status.json',result);return result
        except BaseException as error:
            restored=False
            if not end_attempted and disabled and task is not None and task['enabled']:
                try:
                    subprocess.run([_tool('schtasks.exe'),'/Change','/TN',_task_address(),'/Enable'],check=True,timeout=20,**cfg.child_options())
                    restored=True;disabled=False
                except Exception:pass
            receipt={'status':'stop failed','phase':phase,'error_type':type(error).__name__,'end_attempted':end_attempted,
                     'task_disabled':disabled,'prior_enabled_restored':restored,'prior_task_enabled':task['enabled'] if task is not None else None,'hold_id':hold_id,
                     'guidance':'Pending work preserved. No empty-runtime receipt; retry the exact transaction after the active writer finishes or inspect owned-process identity.'}
            _atomic(_state()/'service-control-status.json',receipt)
            reason=' '+str(error) if isinstance(error,processes.OwnershipError) else ''
            raise processes.OwnershipError('Stop failed during '+phase+'; no empty-runtime receipt.'+reason+' See service-control-status.json; pending work is preserved.') from error
        finally:
            for process in owned:process.close()


def inspect():
    """Read task ownership before an updater creates a durable hold."""
    processes.validate_environment()
    with _control():
        task=_windows_task() if sys.platform=='win32' else None
        return {'status':'inspected','task_present':task is not None,
                'task_enabled':task['enabled'] if task is not None else None}


def recovery_status():
    processes.validate_environment()
    value=transaction()
    return {'status':'recovery required' if value else 'no transaction','transaction':value,
            'device_version':cfg.DEVICE.get('version'),
            'guidance':'Use the verified new checkout update.py --recover --hold-id UUID; it must acquire update.lock before recovery.'}
