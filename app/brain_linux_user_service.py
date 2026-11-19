"""User-space service for Linux hosts whose PID 1 is not systemd.

The qualified Ubuntu 24.04 user unit stays in brain_linux_registration. This
module is the other Linux backend: a session leader that runs the same
supervisor command, restarts it after a failure, and is started again after
the host returns by a login script, an XDG autostart entry, and a user
crontab when those exist. It never signals a process whose boot-relative
lifetime does not match the record it wrote.
"""
import hashlib, json, os, shlex, signal, stat, subprocess, sys, time
from pathlib import Path

from brain_linux_registration import RegistrationError, definitions

BOOT_MARK_BEGIN='# BEGIN SECOND-BRAIN USER SERVICE'
BOOT_MARK_END='# END SECOND-BRAIN USER SERVICE'
CRON_MARK='second-brain-user-service'
KEEPER_NAME='user-service-keeper.json'


def pid1_comm():
    try:return Path('/proc/1/comm').read_text(encoding='utf-8',errors='replace').strip()
    except OSError:return ''


def systemd_manager_present():
    """True only when systemd itself is PID 1 and the existing backend must be used."""
    comm=pid1_comm()
    if comm=='systemd':return True
    if comm:return False
    return Path('/run/systemd/system').is_dir()


def account_home():
    import pwd
    return Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()


def open_registration(unit,root,selected_python,*,current_python):
    if systemd_manager_present():
        from brain_linux_registration import LinuxRegistration
        return LinuxRegistration(unit,root,selected_python,current_python=current_python)
    return LinuxUserService(unit,root,selected_python,current_python=current_python)


def _lifetime(pid):
    import brain_process as processes
    try:return processes._linux_lifetime(pid)
    except processes.OwnershipError:return None


def _matches(record,pid):
    if not isinstance(record,dict) or type(pid) is not int:return False
    live=_lifetime(pid)
    return (live is not None and live.get('pid')==pid and live.get('start_ticks')==record.get('start_ticks')
            and live.get('boot_id')==record.get('boot_id') and type(record.get('sid')) is int and record['sid']>1
            and _sid(pid)==record['sid'])


def _sid(pid):
    try:return os.getsid(pid)
    except (ProcessLookupError,PermissionError,OSError):return None


def _cmdline(pid):
    try:raw=Path('/proc',str(pid),'cmdline').read_bytes()
    except OSError:return None
    if len(raw)>65536:return None
    return tuple(part.decode('utf-8','surrogateescape') for part in raw.split(b'\0') if part)


class LinuxUserService:
    """One exact definition file and one session, with the registration methods the controller calls."""

    def __init__(self,unit,root,selected_python,*,current_python,keeper=False):
        if sys.platform!='linux' or os.geteuid()==0:
            raise RegistrationError('A non-root Linux user service is required.')
        if systemd_manager_present():
            raise RegistrationError('systemd is PID 1; the user-unit backend owns this host.')
        import brain_settings as cfg
        import brain_process as processes
        selected_python=set(map(str,selected_python))
        # The keeper is this same program. It adopts the controller's interpreter set
        # (including a retained previous release) instead of being handed that set.
        if keeper:selected_python=processes._selected_python()
        if (Path(root).resolve()!=cfg.ROOT or selected_python!=processes._selected_python()
                or Path(current_python)!=cfg.PYTHON):
            raise RegistrationError('User service selections disagree with this controller device.')
        home=account_home()
        if Path.home().resolve()!=home:
            raise RegistrationError('The inherited home directory is not this user account.')
        self.uid=os.geteuid();self.unit=unit;self.root=cfg.ROOT;self.home=home
        self.directory=home/'.config'/'second-brain';self.path=self.directory/unit
        self.enabled_path=self.directory/'enabled';self.keeper_path=cfg.STATE/KEEPER_NAME
        self.boot_path=self.directory/'boot-start'
        self.known=definitions(unit,root,selected_python)
        current=(str(cfg.PYTHON),str(Path(root)/'app/brain_supervisor.py'))
        matches=[payload for payload,argv in self.known.items() if argv==current]
        if len(matches)!=1:raise RegistrationError('Current user-service definition is unavailable.')
        self.installable=matches[0];self.argv=current;self.keeper_argv=(str(cfg.PYTHON),str(Path(root)/'app/brain_linux_user_service.py'),'supervise')

    def _private_dir(self,path):
        path.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o022 or path.is_symlink():
            raise RegistrationError('User service directory is not a private directory of this account.')

    def _read_file(self,path,limit=65536):
        if path.is_symlink():raise RegistrationError('User service file is a link.')
        try:descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        except FileNotFoundError:return None
        except OSError:raise RegistrationError('User service file cannot be read.') from None
        try:
            info=os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=self.uid or info.st_size>limit:
                raise RegistrationError('User service file ownership or size differs.')
            return os.read(descriptor,limit+1)
        finally:os.close(descriptor)

    def _write(self,path,payload,mode):
        self._private_dir(path.parent)
        temporary=path.with_name('.'+path.name+'.'+os.urandom(8).hex())
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(payload);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink(missing_ok=True)

    def _unit_bytes(self):
        data=self._read_file(self.path)
        return data

    def _enabled(self):
        data=self._read_file(self.enabled_path,16)
        if data is None:return None
        if data==b'true\n':return True
        if data==b'false\n':return False
        raise RegistrationError('User service activation flag is not true or false.')

    def _set_enabled(self,enabled):
        if type(enabled) is not bool:raise RegistrationError('User service activation must be explicit.')
        self._write(self.enabled_path,b'true\n' if enabled else b'false\n',0o644)
        if self._enabled() is not enabled:raise RegistrationError('User service activation was not verified.')
        return self.inspect(prove_main=False)

    def _keeper_record(self):
        data=self._read_file(self.keeper_path)
        if data is None:return None
        try:value=json.loads(data)
        except (ValueError,UnicodeError):raise RegistrationError('User service keeper record is unreadable.') from None
        if not isinstance(value,dict) or value.get('format')!=1:raise RegistrationError('User service keeper record is not this format.')
        return value

    def _supervisor_process(self):
        record=self._keeper_record()
        if record is None:return None
        child=record.get('supervisor')
        if not isinstance(child,dict) or type(child.get('pid')) is not int:return None
        if not _matches(child,child['pid']) or _cmdline(child['pid'])!=self.argv:return None
        return child

    def _keeper_process(self):
        record=self._keeper_record()
        if record is None:return None
        keeper=record.get('keeper')
        if not isinstance(keeper,dict) or type(keeper.get('pid')) is not int:return None
        if not _matches(keeper,keeper['pid']) or _cmdline(keeper['pid'])!=self.keeper_argv:return None
        return keeper

    def _quiet(self):
        return self._supervisor_process() is None and self._keeper_process() is None

    def inspect(self,*,allow_reload=False,prove_main=True):
        del allow_reload
        content=self._unit_bytes();enabled=self._enabled() if content is not None else None
        if content is None:
            if enabled is not None:raise RegistrationError('User service activation exists without its definition.')
            value={'file_present':False,'loaded':False,'enabled':None,'running':False,'main_pid':None,
                   'definition_sha256':None,'needs_reload':False,'quiescent':self._quiet(),'backend':'user-space'}
        else:
            if content not in self.known:raise RegistrationError('User service definition belongs to another configuration.')
            if enabled is None:raise RegistrationError('User service definition has no activation flag.')
            supervisor=self._supervisor_process();keeper=self._keeper_process()
            running=supervisor is not None
            value={'file_present':True,'loaded':keeper is not None or running,'enabled':enabled,'running':running,
                   'main_pid':supervisor['pid'] if running else None,
                   'definition_sha256':hashlib.sha256(content).hexdigest(),'needs_reload':False,
                   'quiescent':not running and keeper is None,'backend':'user-space'}
            if prove_main and running:
                import brain_process as processes
                owned=processes.inspect(supervisor['pid'],required=True)
                try:
                    if owned is None or owned.role!='supervisor' or not owned.alive() or not owned.lifetime:
                        raise RegistrationError('User service supervisor is not the verified role.')
                    if _cmdline(supervisor['pid'])!=self.argv:
                        raise RegistrationError('User service supervisor command differs.')
                    value['main_lifetime']={'pid':owned.lifetime['pid'],'start_ticks':owned.lifetime['start_ticks'],'boot_id':owned.lifetime['boot_id']}
                    if value['main_lifetime']['start_ticks']!=supervisor['start_ticks'] or value['main_lifetime']['boot_id']!=supervisor['boot_id']:
                        raise RegistrationError('User service supervisor lifetime changed during inspection.')
                finally:
                    if owned is not None:owned.close()
        if content is not None and self._unit_bytes()!=content:raise RegistrationError('User service definition changed during inspection.')
        return value

    def verify_processes(self,owned):
        supervisor=self._supervisor_process();keeper=self._keeper_process()
        session=supervisor['sid'] if supervisor is not None else keeper['sid'] if keeper is not None else None
        for process in owned:
            if not process.alive():continue
            if session is None or _sid(process.pid)!=session:
                raise RegistrationError('A retained role is outside the user service session.')

    def _signal_session(self,record,number):
        if not _matches(record,record['pid']):return False
        if _sid(record['pid'])!=record['sid']:raise RegistrationError('User service session does not match its record.')
        try:os.killpg(record['sid'],number)
        except ProcessLookupError:return False
        return True

    def native_stop(self):
        current=self.inspect(prove_main=False)
        if current['enabled']:raise RegistrationError('Disable user-service activation before ending it.')
        sent=False
        for _ in range(2):
            supervisor=self._supervisor_process();keeper=self._keeper_process()
            target=supervisor or keeper
            if target is None:break
            self._signal_session(target,signal.SIGTERM);sent=True
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and (self._supervisor_process() or self._keeper_process()):
                time.sleep(.1)
            target=self._supervisor_process() or self._keeper_process()
            if target is not None:self._signal_session(target,signal.SIGKILL)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not self._quiet():time.sleep(.1)
            if self._quiet():break
        value=self.inspect(prove_main=False)
        if not value['quiescent'] or value['enabled']:
            raise RegistrationError('User service did not reach a disabled stopped state.')
        return {**value,'native_stopped':True,'native_end_exit':0 if sent else None,'native_end_timed_out':False,'native_end_sent':sent}

    def _environment(self):
        allowed=('HOME','USER','LOGNAME','PATH','LANG','LC_ALL','LC_CTYPE','TERM','SHELL','TMPDIR','TZ','XDG_RUNTIME_DIR','XDG_DATA_HOME','XDG_CONFIG_HOME')
        env={key:os.environ[key] for key in allowed if key in os.environ and not key.startswith(('LD_','PYTHON','BRAIN_','BASIC_MEMORY_'))}
        env['HOME']=str(self.home)
        env.setdefault('PATH','/usr/local/bin:/usr/bin:/bin')
        return env

    def _spawn_keeper(self):
        if self._keeper_process() is not None:return
        if self._unit_bytes()!=self.installable:raise RegistrationError('User service definition is not the current one.')
        log_dir=self.root/'logs';log_dir.mkdir(parents=True,exist_ok=True)
        subprocess.Popen(list(self.keeper_argv),cwd=str(self.home),env=self._environment(),stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if self._keeper_process() is not None:return
            time.sleep(.1)
        raise RegistrationError('User service keeper did not publish its lifetime.')

    def start(self,*,explicit=True):
        value=self.inspect(prove_main=False)
        if not value['file_present']:raise RegistrationError('Install the exact user-service definition before start.')
        if not explicit and not value['enabled']:raise RegistrationError('User service is persistently disabled.')
        if explicit:value=self._set_enabled(True)
        self._install_boot()
        if self._keeper_process() is None and not value['running']:self._spawn_keeper()
        import brain_process as processes
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            try:value=self.inspect()
            except (RegistrationError,processes.OwnershipError):
                if time.monotonic()>=deadline:raise
                time.sleep(.1);continue
            if value['running'] and value.get('main_pid') and value.get('main_lifetime'):return value
            time.sleep(.1)
        raise RegistrationError('User service supervisor did not reach its running state.')

    def install(self,payload,*,enabled=None):
        if payload!=self.installable:raise RegistrationError('Only the current exact user-service definition may be installed.')
        current=self.inspect(prove_main=False)
        if not current['quiescent']:raise RegistrationError('Stop the user service before changing its definition.')
        if enabled is None and current['file_present']:
            raise RegistrationError('Existing user service definition requires explicit activation intent.')
        selected=True if enabled is None else enabled
        self._write(self.path,payload,0o644)
        if self._unit_bytes()!=payload:raise RegistrationError('User service definition was not verified.')
        self._install_boot()
        return self._set_enabled(selected)

    def suspend_activation(self,prior=None):
        current=self.inspect(prove_main=False)
        if prior is not None and (not isinstance(prior,dict) or current.get('definition_sha256')!=prior.get('definition_sha256')):
            raise RegistrationError('User service definition changed after the durable prior snapshot.')
        if current['file_present']:self._set_enabled(False)
        return prior or current

    def restore_activation(self,prior):
        if not isinstance(prior,dict):raise RegistrationError('Prior activation state is malformed.')
        current=self.inspect(prove_main=False)
        if current.get('definition_sha256')!=prior.get('definition_sha256') and prior.get('file_present') is not False:
            raise RegistrationError('User service definition changed before activation restoration.')
        if not current['file_present'] and prior.get('enabled') is None:return current
        if type(prior.get('enabled')) is not bool:raise RegistrationError('Prior user-service activation is unavailable.')
        return self._set_enabled(prior['enabled'])

    def _install_boot(self):
        """Login, desktop autostart, and crontab each call the same script. A missing crontab is skipped."""
        script='#!/bin/sh\nexec '+shlex.join([*self.keeper_argv[:2],'boot'])+'\n'
        self._write(self.boot_path,script.encode(),0o700)
        os.chmod(self.boot_path,0o700)
        self._install_profile()
        self._install_autostart()
        self._install_crontab()

    def _install_profile(self):
        path=self.home/'.profile'
        if path.is_symlink():return
        block=BOOT_MARK_BEGIN+'\n'+shlex.quote(str(self.boot_path))+' >/dev/null 2>&1 || true\n'+BOOT_MARK_END+'\n'
        existing=self._read_file(path,1024*1024) if path.exists() else b''
        if existing is None:return
        text=existing.decode('utf-8',errors='replace')
        if BOOT_MARK_BEGIN in text and BOOT_MARK_END in text:
            start,end=text.index(BOOT_MARK_BEGIN),text.index(BOOT_MARK_END)
            if end<start:raise RegistrationError('User service login block is out of order.')
            text=text[:start]+block+text[end+len(BOOT_MARK_END):].lstrip('\n')
        else:text=text.rstrip('\n')+('\n\n' if text.strip() else '')+block
        self._write(path,text.encode(),0o644)

    def _install_autostart(self):
        path=self.home/'.config'/'autostart'/'second-brain.desktop'
        if path.is_symlink():return
        body='[Desktop Entry]\nType=Application\nVersion=1.0\nName=Second Brain\nComment=Start the local Second Brain service\nExec='+str(self.boot_path)+'\nTerminal=false\nX-GNOME-Autostart-enabled=true\n'
        self._write(path,body.encode(),0o644)

    def _install_crontab(self):
        """Best effort. A host without crontab, including the Grok Bot computer, still has the login script."""
        try:
            binary=shutil_which('crontab')
            if not binary:return False
            if Path(os.environ.get('HOME','')).resolve()!=account_home():return False
            listed=subprocess.run([binary,'-l'],capture_output=True,text=True,timeout=10)
            if listed.returncode and 'no crontab' not in (listed.stderr or '').lower():return False
            rows=[] if listed.returncode else [row for row in listed.stdout.splitlines() if CRON_MARK not in row]
            rows.append('@reboot '+shlex.quote(str(self.boot_path))+' # '+CRON_MARK)
            subprocess.run([binary,'-'],input='\n'.join(rows)+'\n',text=True,timeout=10,check=True)
            return True
        except (OSError,subprocess.SubprocessError):return False

    def withdraw(self):
        """Delete this definition only when nothing is running. The vault and runtime state stay."""
        current=self.inspect(prove_main=False)
        if not current['quiescent'] or current.get('running'):
            raise RegistrationError('User service is not stopped; the definition was not removed.')
        if self.path.exists() and not self.path.is_symlink():self.path.unlink()
        if self.enabled_path.exists() and not self.enabled_path.is_symlink():self.enabled_path.unlink()
        self.remove_boot_hooks()
        if self.path.exists() or self.enabled_path.exists():
            raise RegistrationError('User service definition was not removed.')

    def remove_boot_hooks(self):
        """Drop the login, autostart and crontab hooks. The definition file stays until a later install replaces it."""
        profile=self.home/'.profile'
        data=self._read_file(profile,1024*1024) if profile.exists() and not profile.is_symlink() else None
        if data is not None and BOOT_MARK_BEGIN.encode() in data and BOOT_MARK_END.encode() in data:
            text=data.decode('utf-8',errors='replace')
            start,end=text.index(BOOT_MARK_BEGIN),text.index(BOOT_MARK_END)
            if end>start:self._write(profile,(text[:start]+text[end+len(BOOT_MARK_END):].lstrip('\n')).encode(),0o644)
        desktop=self.home/'.config'/'autostart'/'second-brain.desktop'
        current=self._read_file(desktop,65536) if desktop.exists() and not desktop.is_symlink() else None
        if current is not None and str(self.boot_path).encode() in current:desktop.unlink()
        binary=shutil_which('crontab')
        if binary and Path(os.environ.get('HOME','')).resolve()==account_home():
            listed=subprocess.run([binary,'-l'],capture_output=True,text=True,timeout=10)
            if listed.returncode==0 and CRON_MARK in listed.stdout:
                rows=[row for row in listed.stdout.splitlines() if CRON_MARK not in row]
                subprocess.run([binary,'-'],input=('\n'.join(rows)+'\n') if rows else '',text=True,timeout=10,check=True)


def shutil_which(name):
    import shutil
    return shutil.which(name)


def _publish(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+os.urandom(8).hex())
    temporary.write_text(json.dumps(value),encoding='utf-8')
    os.replace(temporary,path)


def _session_others(sid,own):
    found=[]
    for name in os.listdir('/proc'):
        if not name.isdigit():continue
        pid=int(name)
        if pid==own:continue
        if _sid(pid)==sid:found.append(pid)
    return found


def supervise():
    """Session leader: run the supervisor, and after it exits stop the rest of the session and start it again."""
    import brain_settings as cfg
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    stopping=False
    def _stop(signum,frame):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,_stop);signal.signal(signal.SIGINT,_stop)
    service=LinuxUserService('second-brain.service',cfg.ROOT,{str(cfg.PYTHON)},current_python=cfg.PYTHON,keeper=True)
    identity=_lifetime(os.getpid())
    if identity is None:raise SystemExit(1)
    record={'format':1,'keeper':{'pid':os.getpid(),'sid':os.getsid(0),'start_ticks':identity['start_ticks'],'boot_id':identity['boot_id']},'supervisor':None}
    if record['keeper']['sid']!=os.getpid():raise SystemExit(1)
    _publish(service.keeper_path,record)
    child=None
    while not stopping:
        enabled=service._enabled()
        if service._unit_bytes()!=service.installable or enabled is not True:
            time.sleep(1);continue
        if child is None or child.poll() is not None:
            if child is not None:
                for pid in _session_others(record['keeper']['sid'],os.getpid()):
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                time.sleep(1)
                for pid in _session_others(record['keeper']['sid'],os.getpid()):
                    try:os.kill(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                record['supervisor']=None;_publish(service.keeper_path,record)
                for _ in range(10):
                    if stopping or service._enabled() is not True:break
                    time.sleep(1)
                if stopping or service._enabled() is not True:continue
            log=cfg.ROOT/'logs'/'user-service.log'
            log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('ab') as stream:
                child=subprocess.Popen(list(service.argv),cwd=str(service.home),env=service._environment(),
                                       stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=False)
            live=_lifetime(child.pid)
            if live is not None:
                record['supervisor']={'pid':child.pid,'sid':_sid(child.pid),'start_ticks':live['start_ticks'],'boot_id':live['boot_id']}
                _publish(service.keeper_path,record)
        elif record.get('supervisor') is None:
            live=_lifetime(child.pid)
            if live is not None and _sid(child.pid)==record['keeper']['sid']:
                record['supervisor']={'pid':child.pid,'sid':record['keeper']['sid'],'start_ticks':live['start_ticks'],'boot_id':live['boot_id']}
                _publish(service.keeper_path,record)
        time.sleep(1)
    if child is not None and child.poll() is None:
        for pid in _session_others(record['keeper']['sid'],os.getpid()):
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            for pid in _session_others(record['keeper']['sid'],os.getpid()):
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass


def boot():
    """Start only when the definition is enabled and nothing is running.

    A shell that sources the login script must return immediately when the
    keeper is already up. A deliberate stop stays stopped.
    """
    import brain_settings as cfg
    service=LinuxUserService('second-brain.service',cfg.ROOT,{str(cfg.PYTHON)},current_python=cfg.PYTHON,keeper=True)
    if service._enabled() is not True:return
    if service._keeper_process() is not None or service._supervisor_process() is not None:return
    import brain_service as controller
    try:controller.start(explicit=False)
    except Exception as error:
        text=str(error)
        if 'persistently disabled' in text or 'explicitly stopped' in text or 'not installed' in text:return
        raise


if __name__=='__main__':
    if sys.argv[1:]==['supervise']:supervise()
    elif sys.argv[1:]==['boot']:boot()
    else:raise SystemExit('Use supervise or boot.')
