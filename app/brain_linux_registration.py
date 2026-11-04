"""Verified systemd user-registration boundary; never PID signals.

Candidate admission is separate from full release/native lifecycle qualification.
Callers supply exact known rendered definition bytes and their literal venv argv.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from functools import wraps


class RegistrationError(RuntimeError):
    pass


class CommandTimeout(RegistrationError):
    pass


class ReloadRequired(RegistrationError):
    pass


class NotReady(RegistrationError):
    pass


def _bounded(seconds):
    """One helper's nested native calls share a finite operation budget."""
    def decorate(function):
        @wraps(function)
        def wrapped(self,*args,**kwargs):
            previous=getattr(self,'_deadline',None)
            self._deadline=min(previous or float('inf'),time.monotonic()+seconds)
            try:return function(self,*args,**kwargs)
            except (OSError,UnicodeError) as error:
                failure=RegistrationError('Native filesystem or OS state could not be verified.')
                for key in ('prior_native','bytes_replaced','prior_restored','restore_error'):
                    if hasattr(error,key):setattr(failure,key,getattr(error,key))
                raise failure from None
            finally:self._deadline=previous
        return wrapped
    return decorate


def _variant(properties: dict, name: str, signature: str):
    value = properties.get(name)
    if not isinstance(value, dict) or set(value) != {'type', 'data'} or value['type'] != signature:
        raise RegistrationError('Native loaded property is missing or has an unqualified type.')
    return value['data']


def loaded_contract(unit: str, path: Path, argv: tuple[str, ...], prefix: str,
                    unit_properties: dict, service_properties: dict, *, allow_reload=False,prove_main=True) -> dict:
    """Validate a candidate systemd-255 GetAll snapshot; no I/O or authority.

    This pure contract is exercised by fault controls before it may be used by
    the registration/lifecycle backend. Native measurement is still required.
    """
    if (not isinstance(unit, str) or not re.fullmatch(r'second-brain(?:-(?:test|ci)-[a-f0-9]{32})?\.service', unit)
            or not isinstance(path, Path) or not path.is_absolute()
            or not isinstance(argv, tuple) or len(argv) != 2
            or any(not isinstance(arg, str) or not arg or not Path(arg).is_absolute() for arg in argv)
            or not isinstance(prefix, str) or not re.fullmatch(r'/user\.slice/user-[0-9]+\.slice/user@[0-9]+\.service', prefix)):
        raise RegistrationError('Invalid expected loaded-registration boundary.')
    u, s = unit_properties, service_properties
    if not isinstance(u, dict) or not isinstance(s, dict):
        raise RegistrationError('Native loaded properties are not structured maps.')
    exact_unit = {'Id': unit, 'Names': [unit], 'Following': '', 'FragmentPath': str(path),
                  'SourcePath': '', 'DropInPaths': [], 'LoadState': 'loaded',
                  'Transient': False}
    for name, expected in exact_unit.items():
        signature = 'as' if isinstance(expected, list) else 'b' if type(expected) is bool else 's'
        if _variant(u, name, signature) != expected:
            raise RegistrationError('Loaded unit identity/definition differs from the known registration.')
    for name in ('TriggeredBy', 'UpheldBy', 'BoundBy', 'RequiredBy', 'OnFailureOf', 'OnSuccessOf'):
        if _variant(u, name, 'as') != []:
            raise RegistrationError('Loaded unit has a foreign activation dependency.')
    for name in ('Requisite','Wants','BindsTo','PartOf','Upholds','OnFailure','OnSuccess','Triggers','PropagatesStopTo'):
        if _variant(u,name,'as')!=[]:
            raise RegistrationError('Loaded unit has a foreign forward dependency.')
    for name,allowed in (('Requires',{'app.slice','basic.target','sysinit.target'}),('Conflicts',{'shutdown.target'})):
        values=_variant(u,name,'as')
        if not isinstance(values,list) or any(value not in allowed for value in values):
            raise RegistrationError('Loaded unit has an unqualified implicit dependency.')
    wanted = _variant(u, 'WantedBy', 'as')
    if not isinstance(wanted, list) or any(value != 'default.target' for value in wanted) or len(wanted) > 1:
        raise RegistrationError('Loaded unit has an unqualified activation target.')
    enabled = _variant(u, 'UnitFileState', 's')
    if enabled not in ('enabled', 'disabled'):
        raise RegistrationError('Native enablement state is not plain enabled/disabled.')
    active = _variant(u, 'ActiveState', 's')
    if active not in ('active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'):
        raise RegistrationError('Native active state is outside the candidate contract.')
    substate = _variant(u, 'SubState', 's')
    if not isinstance(substate, str) or not substate or len(substate) > 64:
        raise RegistrationError('Native substate is malformed.')
    invocation = _variant(u, 'InvocationID', 'ay')
    if (not isinstance(invocation, list) or len(invocation) not in (0, 16)
            or any(type(value) is not int or not 0 <= value <= 255 for value in invocation)
            or (active == 'active' and (len(invocation) != 16 or not any(invocation)))):
        raise RegistrationError('Native invocation identity is unavailable.')
    job = _variant(u, 'Job', '(uo)')
    if (not isinstance(job, list) or len(job) != 2 or type(job[0]) is not int or job[0] < 0
            or job[1] != ('/' if job[0] == 0 else '/org/freedesktop/systemd1/job/'+str(job[0]))):
        raise RegistrationError('Native job identity is malformed.')
    exact_service = {'Type': 'simple', 'Restart': 'on-failure', 'KillMode': 'control-group',
                     'KillSignal': 15, 'SendSIGKILL': True, 'RestartUSec': 10_000_000,
                     'DynamicUser': False, 'Slice': 'app.slice'}
    for name, expected in exact_service.items():
        signature = 'b' if type(expected) is bool else 'i' if name == 'KillSignal' else 't' if type(expected) is int else 's'
        if _variant(s, name, signature) != expected:
            raise RegistrationError('Loaded service execution policy differs from the known definition.')
    for name in ('ExecStartPre', 'ExecStartPost', 'ExecCondition', 'ExecReload', 'ExecStop', 'ExecStopPost'):
        if _variant(s, name, 'a(sasbttttuii)') != []:
            raise RegistrationError('Loaded service has an unexpected execution hook.')
    for name, signature in (('Environment', 'as'), ('EnvironmentFiles', 'a(sb)'),
                            ('PassEnvironment', 'as'), ('UnsetEnvironment', 'as'), ('ExecSearchPath', 'as')):
        if _variant(s, name, signature) != []:
            raise RegistrationError('Loaded service has an unexpected environment override.')
    for name in ('WorkingDirectory', 'RootDirectory', 'RootImage', 'User', 'Group', 'PAMName'):
        if _variant(s, name, 's') != '':
            raise RegistrationError('Loaded service has an unexpected execution context.')
    execute = _variant(s, 'ExecStart', 'a(sasbttttuii)')
    if (not isinstance(execute, list) or len(execute) != 1 or not isinstance(execute[0], list)
            or len(execute[0]) != 10 or execute[0][:3] != [argv[0], list(argv), False]
            or any(type(value) is not int for value in execute[0][3:])):
        raise RegistrationError('Loaded service argv is not the exact literal expected command.')
    # Newer managers expose command-prefix flags separately. Empty is the only
    # candidate shape: prefixes must not expand the accepted execution authority.
    if 'ExecStartEx' in s:
        extended = _variant(s, 'ExecStartEx', 'a(sasasttttuii)')
        if (not isinstance(extended, list) or len(extended) != 1 or not isinstance(extended[0], list) or len(extended[0]) != 10
                or extended[0][:3] != [argv[0], list(argv), []]):
            raise RegistrationError('Loaded service has unexpected command-prefix flags.')
    timeout = _variant(s, 'TimeoutStopUSec', 't')
    if type(timeout) is not int or not 0 < timeout < 2**64-1:
        raise RegistrationError('Native stop timeout is not finite.')
    group = _variant(s, 'ControlGroup', 's')
    expected_group = prefix+'/app.slice/'+unit
    if group not in (expected_group, '') or (active == 'active' and group != expected_group):
        raise RegistrationError('Loaded service cgroup is not exactly this unit.')
    main, control = (_variant(s, name, 'u') for name in ('MainPID', 'ControlPID'))
    if any(type(pid) is not int or not 0 <= pid <= 0x7fffffff for pid in (main, control)):
        raise RegistrationError('Loaded service process identifiers are malformed.')
    needs_reload=_variant(u,'NeedDaemonReload','b')
    if type(needs_reload) is not bool:raise RegistrationError('Native reload state is malformed.')
    if prove_main and active=='active' and execute[0][7]!=main:
        raise NotReady('Native command PID differs from the main PID.')
    if needs_reload and not allow_reload:
        raise ReloadRequired('Exact native registration requires a verified reload.')
    return {'file_present': True, 'loaded': True, 'enabled': enabled == 'enabled',
            'enablement_state': enabled, 'running': active == 'active',
            'active_state': active, 'sub_state': substate, 'invocation_id': invocation,
            'job': job, 'control_group': group, 'expected_control_group': expected_group,
            'main_pid': main, 'control_pid': control, 'timeout_stop_usec': timeout,
            'needs_reload':needs_reload}


def _plain(path: Path) -> Path:
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise RegistrationError('Linked or relative native registration boundary refused.')
    return path


def _text(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise RegistrationError('Native registration path/identity contains control characters.')
    return value


def _tool(name: str) -> str:
    return _protected(Path('/usr/bin')/name)


def _protected(path: Path) -> str:
    resolved = path.resolve(strict=True)
    for part in (resolved, *resolved.parents):
        info = part.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RegistrationError('Native manager tool is not protected by the OS owner.')
    if not resolved.is_file():
        raise RegistrationError('Native manager tool is unavailable.')
    return str(resolved)


def runtime_directory() -> Path:
    uid = os.geteuid()
    if uid == 0:
        raise RegistrationError('This backend requires a non-root user manager.')
    path = _plain(Path('/run/user')/str(uid))
    configured = os.environ.get('XDG_RUNTIME_DIR')
    if configured and Path(configured) != path:
        raise RegistrationError('The inherited runtime directory is not this user session.')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise RegistrationError('User runtime directory ownership/privacy is unavailable.')
    bus = _plain(path/'bus')
    info = bus.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise RegistrationError('The expected user bus socket is unavailable.')
    return path


def preexisting_bus_observed() -> bool:
    """Observe an already populated known bus cgroup BEFORE any bus connection.

    This conservative diagnostic precondition is not manager identity or a
    race-free no-activation guarantee. Unknown unit layouts remain refused.
    """
    uid = os.geteuid()
    base = Path('/sys/fs/cgroup/user.slice')/('user-'+str(uid)+'.slice')/('user@'+str(uid)+'.service/session.slice')
    for unit in ('dbus.service', 'dbus-broker.service'):
        path = base/unit/'cgroup.events'
        try:
            with path.open('rb') as source:
                payload = source.read(1025)
        except FileNotFoundError:
            continue
        except OSError:
            raise RegistrationError('Existing user-bus cgroup state is unavailable.') from None
        if len(payload) > 1024:
            raise RegistrationError('Existing user-bus cgroup state exceeds its bound.')
        if b'populated 1' in payload.splitlines():
            return True
    raise RegistrationError('No preexisting populated user-bus cgroup observed; refusing a socket-activation side effect.')


def definitions(unit,root,selected_python):
    if not re.fullmatch(r'second-brain(?:-ci-[a-f0-9]{32})?\.service',unit):
        raise RegistrationError('Unrecognized native service unit.')
    root=_plain(Path(root));script=_plain(root/'app/brain_supervisor.py')
    def quote(value):return '"'+_text(str(value)).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    result={}
    for value in selected_python:
        python=Path(value)
        if not python.is_absolute():raise RegistrationError('Native interpreter must be absolute.')
        argv=(str(python),str(script))
        payload=('[Unit]\nDescription=Local Second Brain MCP\nAfter=network.target\n\n[Service]\nType=simple\nExecStart='
                 +quote(python)+' '+quote(script)+'\nRestart=on-failure\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n').encode()
        result[payload]=argv
    if not 1<=len(result)<=4:raise RegistrationError('Native interpreter selections exceed their bound.')
    return result


class LinuxRegistration:
    """One exact user unit; Ubuntu24.04/systemd255 authority under qualification.

    Root/OS is trusted; active same-UID adversaries are outside this boundary.
    No public service qualification guard is changed by this helper.
    """
    def __init__(self,unit,root,selected_python,*,current_python):
        if sys.platform!='linux' or os.geteuid()==0:
            raise RegistrationError('A native non-root Linux user manager is required.')
        import pwd
        import brain_settings as cfg
        import brain_process as processes
        selected_python=set(map(str,selected_python))
        if (Path(root).resolve()!=cfg.ROOT or selected_python!=processes._selected_python()
            or Path(current_python)!=cfg.PYTHON):
            raise RegistrationError('Native definition selections disagree with this controller device.')
        self.uid=os.geteuid();self.unit=unit;self.runtime=runtime_directory()
        self.home=_plain(Path(pwd.getpwuid(self.uid).pw_dir))
        self.directory=self.home/'.config/systemd/user';self.path=self.directory/unit
        self.prefix='/user.slice/user-'+str(self.uid)+'.slice/user@'+str(self.uid)+'.service'
        self.group=self.prefix+'/app.slice/'+unit
        self.known=definitions(unit,root,selected_python)
        current=(str(current_python),str(Path(root)/'app/brain_supervisor.py'))
        matches=[payload for payload,argv in self.known.items() if argv==current]
        if len(matches)!=1:raise RegistrationError('Current native definition is unavailable.')
        self.installable=matches[0];self.tool=_tool('busctl')
        self.peer=None;self.system_peer=None;self.manager_pid=None

    def _cgroup_empty(self,group):
        if group not in (self.group,'/system.slice/dbus.service','/system.slice/dbus-broker.service'):
            raise RegistrationError('Unexpected native cgroup boundary.')
        try:
            with (Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events').open('rb') as source:raw=source.read(1025)
        except FileNotFoundError:return True
        if len(raw)>1024:raise RegistrationError('Native cgroup state exceeds its bound.')
        lines=raw.splitlines()
        if b'populated 0' in lines:return True
        if b'populated 1' in lines:return False
        raise RegistrationError('Native cgroup state is unavailable.')

    def _preconnect(self,system):
        if not system:
            runtime_directory();preexisting_bus_observed();return self.runtime/'bus'
        if all(self._cgroup_empty(path) for path in ('/system.slice/dbus.service','/system.slice/dbus-broker.service')):
            raise RegistrationError('No preexisting system bus; refusing socket activation.')
        path=Path('/run/dbus/system_bus_socket');info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0:
            raise RegistrationError('System bus socket is not OS-owned.')
        for parent in path.parents:
            info=parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise RegistrationError('System bus directory is not OS-protected.')
        return path

    def _call(self,destination,obj,interface,method,signature=None,*arguments,system=False):
        path=self._preconnect(system)
        remaining=(self._deadline-time.monotonic()) if getattr(self,'_deadline',None) is not None else 20
        if remaining<=.05:raise CommandTimeout('Native operation budget expired.')
        timeout=min(15,remaining)
        command=[self.tool,'--address=unix:path='+str(path),'--auto-start=no',
                 '--allow-interactive-authorization=no','--json=short','--timeout='+str(max(1,int(timeout*1000)))+'ms',
                 'call',destination,obj,interface,method]
        if signature is not None:command.extend((signature,*arguments))
        failed=False;timed_out=False
        try:
            reply=subprocess.run(command,env={'PATH':'/usr/bin:/bin','LC_ALL':'C','SYSTEMD_COLORS':'0',
                'SYSTEMD_PAGER':'','XDG_RUNTIME_DIR':str(self.runtime)},stdin=subprocess.DEVNULL,
                capture_output=True,timeout=min(20,remaining),check=False)
        except subprocess.TimeoutExpired:timed_out=True
        except OSError:failed=True
        if timed_out:raise CommandTimeout('Native manager transport timed out.')
        if failed:raise RegistrationError('Native manager transport is unavailable.')
        if reply.returncode or len(reply.stdout)>2_000_000:
            raise RegistrationError('Native manager query failed or exceeded its bound.')
        if not reply.stdout.strip():return {'type':'','data':[]}
        malformed=False
        try:value=json.loads(reply.stdout)
        except (ValueError,UnicodeError,RecursionError):malformed=True
        if malformed:raise RegistrationError('Native manager returned unreadable structured data.')
        if not isinstance(value,dict) or set(value)!={'type','data'}:
            raise RegistrationError('Native manager response contract is unknown.')
        return value

    @staticmethod
    def _result(value,signature):
        expected=0 if signature=='' else 2 if signature=='ba(sss)' else 1
        if value['type']!=signature or not isinstance(value['data'],list) or len(value['data'])!=expected:
            raise RegistrationError('Native method reply type is unknown.')
        return value['data']

    def _getall(self,peer,obj,interface,*,system=False):
        rows=self._result(self._call(peer,obj,'org.freedesktop.DBus.Properties','GetAll','s',
                          'org.freedesktop.systemd1.'+interface,system=system),'a{sv}')
        if len(rows)!=1 or not isinstance(rows[0],dict):raise RegistrationError('Native properties are unavailable.')
        return rows[0]

    def _owner(self,*,system=False):
        rows=self._result(self._call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                          'GetNameOwner','s','org.freedesktop.systemd1',system=system),'s')
        if len(rows)!=1 or not isinstance(rows[0],str) or not re.fullmatch(r':[0-9]+\.[0-9]+',rows[0]):
            raise RegistrationError('Native unique manager owner is unavailable.')
        return rows[0]

    def _credentials(self,peer,uid,pid=None,*,system=False):
        rows=self._result(self._call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                          'GetConnectionCredentials','s',peer,system=system),'a{sv}')
        if len(rows)!=1 or not isinstance(rows[0],dict):raise RegistrationError('Native peer credentials are unavailable.')
        actual=_variant(rows[0],'ProcessID','u')
        if (_variant(rows[0],'UnixUserID','u')!=uid or type(actual) is not int or not 0<actual<0x80000000
            or (pid is not None and actual!=pid)):
            raise RegistrationError('Native manager peer identity differs.')
        return actual

    def _manager(self):
        root=self._owner(system=True);self._credentials(root,0,1,system=True)
        version=self._result(self._call(root,'/org/freedesktop/systemd1','org.freedesktop.DBus.Properties',
            'Get','ss','org.freedesktop.systemd1.Manager','Version',system=True),'v')
        if len(version)!=1:raise RegistrationError('Native system manager version is unavailable.')
        version=_variant({'Version':version[0]},'Version','s')
        path=Path(_protected(Path('/etc/os-release')))
        with path.open('rb') as source:raw=source.read(65537)
        if len(raw)>65536:raise RegistrationError('Native OS identity exceeds its bound.')
        malformed=False
        try:release={key:value.strip('"') for key,value in (line.partition('=')[::2]
                     for line in raw.decode('utf-8').splitlines()) if key in ('ID','VERSION_ID')}
        except UnicodeError:malformed=True
        if malformed:raise RegistrationError('Native OS identity is unreadable.')
        if release!={'ID':'ubuntu','VERSION_ID':'24.04'} or not isinstance(version,str) or not version.startswith('255.'):
            raise RegistrationError('This native backend requires Ubuntu24.04/systemd255.')
        name='user@'+str(self.uid)+'.service'
        obj=self._result(self._call(root,'/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager',
                                   'GetUnit','s',name,system=True),'o')
        if len(obj)!=1:raise RegistrationError('The existing OS user-manager unit is unavailable.')
        def snapshot():
            u=self._getall(root,obj[0],'Unit',system=True);s=self._getall(root,obj[0],'Service',system=True)
            for key,expected in (('Id',name),('LoadState','loaded'),('ActiveState','active'),('SubState','running')):
                if _variant(u,key,'s')!=expected:raise RegistrationError('OS user-manager unit is not active and loaded.')
            invocation=_variant(u,'InvocationID','ay');pid=_variant(s,'MainPID','u')
            if (not isinstance(invocation,list) or len(invocation)!=16 or not any(invocation)
                or any(type(value) is not int or not 0<=value<=255 for value in invocation)
                or _variant(u,'Job','(uo)')!=[0,'/'] or type(pid) is not int or not 1<pid<0x80000000
                or _variant(s,'ExecMainPID','u')!=pid or _variant(s,'User','s')!=str(self.uid)
                or _variant(s,'Type','s')!='notify-reload' or _variant(s,'ControlGroup','s')!=self.prefix
                or _variant(s,'RootDirectory','s') or _variant(s,'RootImage','s')):
                raise RegistrationError('OS user-manager invocation or execution context differs.')
            execute=_variant(s,'ExecStart','a(sasbttttuii)');binary='/usr/lib/systemd/systemd'
            if (not isinstance(execute,list) or len(execute)!=1 or not isinstance(execute[0],list)
                or len(execute[0])!=10 or execute[0][:3]!=[binary,[binary,'--user'],False] or execute[0][7]!=pid):
                raise RegistrationError('OS user-manager executable differs from its attested command.')
            _protected(Path(binary))
            return invocation,pid,execute
        first=snapshot();peer=self._owner();pid=self._credentials(peer,self.uid,first[1])
        with (Path('/proc')/str(pid)/'cgroup').open('rb') as source:groups=source.read(65537)
        if len(groups)>65536 or groups.decode('ascii').splitlines()!=['0::'+self.prefix+'/init.scope']:
            raise RegistrationError('User-manager peer is outside its exact OS cgroup.')
        if snapshot()!=first or self._owner(system=True)!=root or self._owner()!=peer:
            raise RegistrationError('Native manager changed during authority proof.')
        manager=self._getall(peer,'/org/freedesktop/systemd1','Manager')
        environment=_variant(manager,'Environment','as')
        paths=_variant(manager,'UnitPath','as')
        if (not isinstance(paths,list) or not 1<=len(paths)<=64
            or any(not isinstance(path,str) or not Path(_text(path)).is_absolute() for path in paths)):
            raise RegistrationError('Native manager search paths are malformed.')
        if (not isinstance(environment,list) or any(not isinstance(value,str) for value in environment)
            or any(value.partition('=')[0].startswith(('LD_','PYTHON','BRAIN_','BASIC_MEMORY_','MCP_')) for value in environment)
            or _variant(manager,'Version','s')!=version or _variant(manager,'ControlGroup','s')!=self.prefix
            or str(self.directory) not in paths):
            raise RegistrationError('Native manager environment/version/search path differs.')
        self.peer=peer;self.system_peer=root;self.manager_pid=pid;self.manager_version=version
        self.unit_paths=tuple(map(Path,paths))
        return root,peer,pid,first[0]

    def _read_unit(self):
        _plain(self.path)
        for parent in (self.home,self.home/'.config',self.home/'.config/systemd',self.directory):
            try:info=parent.lstat()
            except FileNotFoundError:continue
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o022:
                raise RegistrationError('Native registration directory ownership/privacy differs.')
        try:descriptor=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        except FileNotFoundError:return None
        except OSError:raise RegistrationError('Native definition cannot be safely opened.') from None
        try:
            info=os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=self.uid or info.st_nlink!=1
                or info.st_mode&0o022 or info.st_size>65536):raise RegistrationError('Native definition ownership/type differs.')
            content=os.read(descriptor,65537);after=os.fstat(descriptor)
            identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)
            if len(content)!=info.st_size or identity(info)!=identity(after):raise RegistrationError('Native definition changed during read.')
            return content,identity(info)
        finally:os.close(descriptor)

    def _method(self,name,signature=None,*arguments):
        return self._call(self.peer,'/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager',name,signature,*arguments)

    def _disk_boundary(self):
        """A reload must not adopt a pending fragment shadow or new drop-in."""
        paths=self.unit_paths;priority=paths.index(self.directory)
        stem=self.unit.removesuffix('.service');parts=stem.split('-')
        names={'service.d',self.unit+'.d'}
        names.update('-'.join(parts[:index])+'-.service.d' for index in range(1,len(parts)))
        for index,path in enumerate(paths):
            _plain(path)
            if index<priority and os.path.lexists(path/self.unit):
                raise RegistrationError('A higher-priority native fragment appeared before reload.')
            for name in names:
                directory=path/name
                if directory.is_symlink():raise RegistrationError('Linked native drop-in directory refused.')
                try:
                    with os.scandir(directory) as entries:
                        for count,entry in enumerate(entries):
                            if count>=256 or entry.name.endswith('.conf'):
                                raise RegistrationError('Pending native drop-in configuration refused before reload.')
                except FileNotFoundError:continue

    def _reload(self):
        self._disk_boundary()
        self._result(self._method('Reload'),'')

    def _links(self,enabled):
        rows=self._result(self._method('GetUnitFileLinks','sb',self.unit,'false'),'as')
        if len(rows)!=1 or not isinstance(rows[0],list):raise RegistrationError('Native activation links are unavailable.')
        expected=self.directory/'default.target.wants'/self.unit
        links=rows[0]
        if links!=([str(expected)] if enabled else []):raise RegistrationError('Native activation links differ from this exact unit.')
        if enabled:
            _plain(expected.parent);info=expected.lstat()
            if not stat.S_ISLNK(info.st_mode) or info.st_uid!=self.uid or expected.resolve(strict=True)!=self.path:
                raise RegistrationError('Native activation link does not name the owned definition.')

    @staticmethod
    def _quiet(value):
        return (not value['loaded'] or (value['active_state'] in ('inactive','failed')
                and value['job']==[0,'/'] and not value['main_pid'] and not value['control_pid']))

    def _loaded_state(self,obj,content,*,prove_main=True):
        if not isinstance(obj,str) or not obj.startswith('/org/freedesktop/systemd1/unit/'):
            raise RegistrationError('Native loaded object path is unavailable.')
        u=self._getall(self.peer,obj,'Unit');s=self._getall(self.peer,obj,'Service')
        needs=_variant(u,'NeedDaemonReload','b');argv=self.known[content]
        if needs:
            execute=_variant(s,'ExecStart','a(sasbttttuii)')
            choices=[known for known in self.known.values() if isinstance(execute,list) and len(execute)==1
                     and isinstance(execute[0],list) and execute[0][:3]==[known[0],list(known),False]]
            if len(choices)!=1:raise RegistrationError('Dirty loaded command is not an exact retained definition.')
            argv=choices[0]
        value=loaded_contract(self.unit,self.path,argv,self.prefix,u,s,allow_reload=True,prove_main=prove_main)
        value['object_path']=obj;value['arguments']=list(argv)
        if prove_main and value['main_pid']:
            import brain_process as processes
            owned=None;unready=False
            try:
                owned=processes.inspect(value['main_pid'],required=True)
                if owned is None or owned.role!='supervisor':unready=True
                else:
                    current=processes.psutil.Process(owned.pid)
                    mismatch=(current.cmdline()!=list(argv) or processes._path(current.cwd())!=processes._path(self.home)
                        or owned.exe!=processes._path(argv[0]))
                    if not owned.alive():unready=True
                    elif mismatch:raise RegistrationError('Native supervisor argv/cwd/executable differs from its loaded command.')
                    self.verify_processes([owned])
                    if not owned.alive():unready=True
                    else:value['main_lifetime']=dict(owned.lifetime)
            except (processes.OwnershipError,processes.psutil.Error):unready=True
            finally:
                if owned is not None:owned.close()
            if unready:raise NotReady('Native MainPID has not reached its verified supervisor lifetime.')
        elif value['main_pid']:
            import brain_process as processes
            try:value['main_lifetime']=processes._linux_lifetime(value['main_pid'])
            except processes.OwnershipError:raise NotReady('Native MainPID exited during its lifetime read.') from None
        again_u=self._getall(self.peer,obj,'Unit');again_s=self._getall(self.peer,obj,'Service')
        if (any(again_u.get(key)!=u.get(key) for key in ('InvocationID','ActiveState','SubState','Job','NeedDaemonReload','UnitFileState'))
            or any(again_s.get(key)!=s.get(key) for key in ('MainPID','ControlPID','ExecStart'))):
            raise NotReady('Native unit changed during its lifetime proof.')
        return value

    def verify_processes(self,owned):
        """Every retained role must belong to this unit; never signal an orphan."""
        for process in owned:
            if not process.alive():continue
            try:
                with (Path('/proc')/str(process.pid)/'cgroup').open('rb') as source:raw=source.read(65537)
            except FileNotFoundError:
                if not process.alive():continue
                raise RegistrationError('Native role cgroup is unavailable.') from None
            if process.alive() and (len(raw)>65536 or raw.decode('ascii').splitlines()!=['0::'+self.group]):
                raise RegistrationError('A retained role is outside its exact native unit cgroup.')

    def _inspect(self,*,allow_reload=False,prove_main=True):
        token=self._manager();before=self._read_unit();content=before[0] if before else None
        if content is not None and content not in self.known:raise RegistrationError('Native definition belongs to another configuration.')
        units=self._result(self._method('ListUnits'),'a(ssssssouso)')
        files=self._result(self._method('ListUnitFilesByPatterns','asas','0','1',self.unit),'a(ss)')
        if (not isinstance(units[0],list) or len(units[0])>32768
            or any(not isinstance(row,list) or len(row)!=10 for row in units[0])
            or not isinstance(files[0],list) or len(files[0])>32768
            or any(not isinstance(row,list) or len(row)!=2 for row in files[0])):
            raise RegistrationError('Native unit inventory is unavailable.')
        matches=[row for row in units[0] if row[0]==self.unit]
        if len(matches)>1:raise RegistrationError('Native unit inventory is duplicated.')
        if content is None:
            if matches or files[0]:raise RegistrationError('Native unit exists outside its owned definition.')
            value={'file_present':False,'loaded':False,'enabled':None,'running':False,'main_pid':None,'needs_reload':False}
        else:
            if len(files[0])!=1 or files[0][0][0]!=str(self.path) or files[0][0][1] not in ('enabled','disabled'):
                raise RegistrationError('Native unit file inventory differs from its exact path.')
            enabled=files[0][0][1]=='enabled';self._links(enabled)
            if matches:
                value=self._loaded_state(matches[0][6],content,prove_main=prove_main)
                if value['enabled']!=enabled and not value['needs_reload']:
                    raise RegistrationError('Loaded and file activation states disagree.')
                value['enabled']=enabled
            else:value={'file_present':True,'loaded':False,'enabled':enabled,'running':False,'main_pid':None,'needs_reload':False}
        if self._read_unit()!=before or self._manager()!=token:
            raise RegistrationError('Native definition or manager changed during inspection.')
        import hashlib
        value['definition_sha256']=hashlib.sha256(content).hexdigest() if content is not None else None
        value['expected_control_group']=self.group;value['manager_version']=self.manager_version
        value['quiescent']=self._quiet(value) and self._cgroup_empty(self.group)
        if value['needs_reload'] and not allow_reload:
            raise ReloadRequired('Exact known native registration requires reconciliation.')
        return value

    @_bounded(60)
    def inspect(self,*,allow_reload=False,prove_main=True):
        return self._inspect(allow_reload=allow_reload,prove_main=prove_main)

    def _reconcile(self,*,prior=None,quiescent=False,prove_main=True):
        value=self._inspect(allow_reload=True,prove_main=prove_main)
        if prior is not None and value['definition_sha256']!=prior.get('definition_sha256'):
            raise RegistrationError('Native definition changed before activation restoration.')
        if value['needs_reload']:
            if quiescent and not value['quiescent']:
                raise RegistrationError('A running native unit cannot be reconciled for install.')
            if not value['quiescent']:
                file=self._read_unit()
                if file is None or value.get('arguments')!=list(self.known[file[0]]):
                    raise ReloadRequired('Stop the retained running command before loading the changed definition.')
            self._reload()
            value=self.inspect(prove_main=prove_main)
        return value

    def _set_enabled(self,enabled,*,defer_reload=False):
        if defer_reload and enabled:raise RegistrationError('Activation enable cannot defer its reload.')
        current=self.inspect(allow_reload=defer_reload,prove_main=False)
        if not current['file_present']:raise RegistrationError('Cannot change activation without the exact definition.')
        if current['enabled'] is enabled:return current
        if enabled:self._result(self._method('EnableUnitFiles','asbb','1',self.unit,'false','false'),'ba(sss)')
        else:self._result(self._method('DisableUnitFiles','asb','1',self.unit,'false'),'a(sss)')
        # Native enable/disable invalidates loaded states manager-wide, even if
        # this exact inactive unit has already been garbage-collected.
        if not defer_reload:self._reload()
        result=self.inspect(allow_reload=defer_reload,prove_main=False)
        if result['enabled'] is not enabled:raise RegistrationError('Native activation change was not verified.')
        return result

    @_bounded(90)
    def suspend_activation(self,prior=None):
        if prior is not None and not isinstance(prior,dict):raise RegistrationError('Durable prior state is malformed.')
        current=self.inspect(allow_reload=True,prove_main=False)
        if prior is None:prior=current
        if (current['definition_sha256']!=prior.get('definition_sha256') or current['enabled'] not in (prior.get('enabled'),False)):
            raise RegistrationError('Native state changed after the durable prior snapshot.')
        file=self._read_unit() if current['needs_reload'] else None
        defer=bool(current['needs_reload'] and not current['quiescent']
                   and (file is None or current.get('arguments')!=list(self.known[file[0]])))
        if current['needs_reload'] and not defer:current=self._reconcile(prior=prior,prove_main=False)
        try:
            if current['file_present']:self._set_enabled(False,defer_reload=defer)
        except BaseException as error:
            error.prior_native=prior
            raise
        return prior

    @_bounded(90)
    def restore_activation(self,prior):
        if not isinstance(prior,dict):raise RegistrationError('Prior activation state is malformed.')
        current=self._reconcile(prior=prior,prove_main=False)
        if not current['file_present'] and prior.get('enabled') is None:return current
        if type(prior.get('enabled')) is not bool:raise RegistrationError('Prior native activation state is unavailable.')
        return self._set_enabled(prior['enabled'])

    @_bounded(120)
    def native_stop(self):
        """A transport timeout means unknown delivery, not a systemd stop timeout."""
        value=self.inspect(allow_reload=True,prove_main=False)
        prior=value;end_sent=False
        if value['enabled']:raise RegistrationError('Disable native activation before ending the unit.')
        timed_out=False;native_exit=None
        if value['loaded']:
            if not self._quiet(value) and value['timeout_stop_usec']/1_000_000+5>self._deadline-time.monotonic():
                raise RegistrationError('Native stop budget is shorter than the manager stop timeout.')
            try:
                end_sent=True
                self._result(self._method('StopUnit','ss',self.unit,'replace'),'o');native_exit=0
            except CommandTimeout:timed_out=True
        deadline=self._deadline
        while True:
            try:
                value=self.inspect(allow_reload=True,prove_main=False)
                if self._quiet(value) and not value['enabled'] and self._cgroup_empty(self.group):
                    value=self._reconcile(quiescent=True,prove_main=False)
                    return {**value,'native_stopped':True,'native_end_exit':native_exit,'native_end_timed_out':timed_out,
                            'native_end_sent':end_sent,'pre_stop_invocation_id':prior.get('invocation_id'),
                            'pre_stop_main_lifetime':prior.get('main_lifetime')}
            except RegistrationError:
                if time.monotonic()>=deadline:raise
            if time.monotonic()>=deadline:raise RegistrationError('Native unit did not reach verified disabled quiescence.')
            time.sleep(.1)

    def _load(self):
        token=self._manager();before=self._read_unit()
        if before is None or before[0] not in self.known:raise RegistrationError('Exact native definition is unavailable.')
        obj=self._result(self._method('LoadUnit','s',self.unit),'o')[0]
        # A separate ListUnits call may observe GC. The returned path is allowed
        # to reload only this already-verified exact file for explicit start.
        value=self._loaded_state(obj,before[0])
        if value['needs_reload']:raise ReloadRequired('Explicit native load needs reconciliation.')
        if self._read_unit()!=before or self._manager()!=token:
            raise RegistrationError('Native definition or manager changed during explicit load.')
        return value

    @_bounded(60)
    def start(self,*,explicit=True):
        value=self._reconcile(quiescent=True)
        if not value['file_present']:raise RegistrationError('Install the exact native definition before start.')
        if not explicit and not value['enabled']:raise RegistrationError('Native unit is persistently disabled.')
        if explicit:value=self._set_enabled(True)
        if not value['loaded']:value=self._load()
        if not value['running']:self._result(self._method('StartUnit','ss',self.unit,'replace'),'o')
        deadline=min(self._deadline,time.monotonic()+30)
        while True:
            try:
                value=self.inspect()
                if value['running'] and value.get('main_pid'):return value
            except NotReady:
                if time.monotonic()>=deadline:raise
            if time.monotonic()>=deadline:raise RegistrationError('Native supervisor did not reach its running state.')
            time.sleep(.1)

    def _replace(self,payload,before):
        for directory in (self.home/'.config',self.home/'.config/systemd',self.directory):
            try:directory.mkdir(mode=0o700)
            except FileExistsError:pass
            _plain(directory);info=directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o022:
                raise RegistrationError('Unsafe native registration directory.')
        temporary=self.directory/('.second-brain-'+os.urandom(16).hex()+'.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        try:
            with os.fdopen(fd,'wb') as output:output.write(payload);output.flush();os.fsync(output.fileno())
            if self._read_unit()!=before:raise RegistrationError('Native definition changed before replacement.')
            os.replace(temporary,self.path)
            self._replace_committed=True
            directory=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:temporary.unlink(missing_ok=True)

    @_bounded(120)
    def install(self,payload,*,enabled=None):
        if payload!=self.installable:raise RegistrationError('Only the current exact native definition may be installed.')
        if enabled is None and self._read_unit() is not None:
            raise RegistrationError('Existing native definition requires explicit durable activation intent.')
        prior=self._reconcile(quiescent=True)
        if not self._quiet(prior) or not self._cgroup_empty(self.group):
            raise RegistrationError('Stop the exact native unit before changing its definition.')
        if prior['file_present'] and type(enabled) is not bool:
            raise RegistrationError('Existing native definition requires explicit durable activation intent.')
        selected=True if enabled is None else enabled
        if type(selected) is not bool:raise RegistrationError('Native activation intent must be explicit.')
        before=self._read_unit()
        import hashlib
        if (hashlib.sha256(before[0]).hexdigest() if before is not None else None)!=prior['definition_sha256']:
            raise RegistrationError('Native file changed after the prior installation snapshot.')
        if before is not None and before[0]==payload:return self._set_enabled(selected)
        self._replace_committed=False
        try:
            if prior['file_present']:self._set_enabled(False)
            current=self.inspect()
            if not self._quiet(current) or not self._cgroup_empty(self.group):
                raise RegistrationError('Native unit changed during activation suspension.')
            self._replace(payload,before)
            # Fresh file-only registration is success; explicit start owns load.
            current=self._reconcile(quiescent=True);after=self._read_unit()
            if not current['file_present'] or after is None or after[0]!=payload:
                raise RegistrationError('Native definition replacement was not verified.')
            return self._set_enabled(selected)
        except BaseException as error:
            error.prior_native=prior
            error.bytes_replaced=self._replace_committed;error.prior_restored=False;error.restore_error=None
            if not self._replace_committed and prior['file_present']:
                deadline=self._deadline;self._deadline=time.monotonic()+30
                try:self.restore_activation(prior);error.prior_restored=True
                except Exception as restoration:error.restore_error=type(restoration).__name__
                finally:self._deadline=deadline
            raise
