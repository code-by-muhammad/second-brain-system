"""User-space service for Linux hosts whose PID 1 is not systemd.

The qualified Ubuntu 24.04 user unit stays in brain_linux_registration. This
module is the other Linux backend: a session leader that runs the same
supervisor command, restarts it after a failure, and is started again after
the host returns by a login script, an XDG autostart entry, and a user
crontab when those exist. It never signals a process whose boot-relative
lifetime does not match the record it wrote.
"""
from contextlib import ExitStack
import hashlib, json, os, shlex, signal, stat, subprocess, sys, time
from contextlib import contextmanager
from pathlib import Path

from brain_linux_registration import RegistrationError, definitions

BOOT_MARK_BEGIN='# BEGIN SECOND-BRAIN USER SERVICE'
BOOT_MARK_END='# END SECOND-BRAIN USER SERVICE'
CRON_MARK='second-brain-user-service'
KEEPER_NAME='user-service-keeper.json'
HOOK_LIMIT = 1024 * 1024


class HookRefusal(RegistrationError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('User service hook: ' + reason)


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
    try:
        with Path('/proc',str(pid),'cmdline').open('rb') as stream:raw=stream.read(65537)
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

    @contextmanager
    def _home_directory(self, path, *, create=False):
        parts = path.relative_to(self.home).parts
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open(self.home, flags)
        try:
            for name in parts:
                try:
                    child = os.open(name, flags, dir_fd=directory)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(name, 0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                    child = os.open(name, flags, dir_fd=directory)
                os.close(directory)
                directory = child
                info = os.fstat(directory)
                if info.st_uid != self.uid or info.st_mode & 0o022:
                    raise RegistrationError('User service directory is unsafe.')
            yield directory
        finally:
            os.close(directory)

    def _file_image(self, path, limit, *, directory=None):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None, None
        except OSError as error:
            raise RegistrationError('User service file cannot be read.') from error
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_size > limit:
                raise RegistrationError('User service file ownership or size differs.')
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise RegistrationError('User service file exceeded its size bound.')
        return data, stat.S_IMODE(info.st_mode)

    def _read_file(self, path, limit=65536):
        if path in (self.path, self.enabled_path, self.boot_path):
            try:
                with self._home_directory(path.parent) as directory:
                    return self._file_image(path.name, limit, directory=directory)[0]
            except FileNotFoundError:
                return None
        return self._file_image(path, limit)[0]

    def _write(self, path, payload, mode, *, expected=None):
        with self._home_directory(path.parent, create=payload is not None) as directory:
            temporary = '.' + path.name + '.' + os.urandom(8).hex()
            try:
                if payload is not None:
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         mode, dir_fd=directory)
                    with os.fdopen(descriptor, 'wb') as stream:
                        os.fchmod(stream.fileno(), mode)
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                current = self._file_image(path.name, HOOK_LIMIT, directory=directory)
                if expected is not None and current != expected:
                    raise HookRefusal('file_changed_after_planning')
                if not os.path.samestat(os.fstat(directory), path.parent.lstat()):
                    raise HookRefusal('parent_changed_after_planning')
                if payload is None:
                    os.unlink(path.name, dir_fd=directory)
                elif current[0] is None:
                    os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                else:
                    os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                if payload is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass

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
        previous = self._hook_file(self.enabled_path, 16)
        self._write(self.enabled_path, b'true\n' if enabled else b'false\n',
                    0o644 if previous[1] is None else previous[1], expected=previous)
        if self._enabled() is not enabled:raise RegistrationError('User service activation was not verified.')
        return self.inspect(prove_main=False)

    def _keeper_record(self):
        data=self._read_file(self.keeper_path)
        if data is None:return None
        try:value=json.loads(data)
        except (ValueError,UnicodeError,RecursionError):raise RegistrationError('User service keeper record is unreadable.') from None
        if not isinstance(value,dict) or value.get('format')!=1:raise RegistrationError('User service keeper record is not this format.')
        return value

    def _remove_state_file(self,path):
        from brain_release import sync_directory
        if path.parent!=self.keeper_path.parent or path.parent.is_symlink():
            raise RegistrationError('User service state path is unsafe.')
        if self._read_file(path) is not None:
            path.unlink()
            sync_directory(path.parent)

    def _supervisor_process(self):
        record=self._keeper_record()
        if record is None:return None
        child=record.get('supervisor')
        if not isinstance(child,dict) or type(child.get('pid')) is not int:return None
        argv=self.known.get(self._unit_bytes(),self.argv)
        if not _matches(child,child['pid']) or _cmdline(child['pid'])!=argv:return None
        return child

    def _keeper_process(self):
        record=self._keeper_record()
        if record is None:return None
        keeper=record.get('keeper')
        if not isinstance(keeper,dict) or type(keeper.get('pid')) is not int:return None
        argv=self.known.get(self._unit_bytes(),self.argv)
        expected=(argv[0],*self.keeper_argv[1:])
        if not _matches(keeper,keeper['pid']) or _cmdline(keeper['pid'])!=expected:return None
        return keeper

    def _quiet(self):
        return self._supervisor_process() is None and self._keeper_process() is None

    def inspect(self,*,allow_reload=False,prove_main=True):
        del allow_reload
        content=self._unit_bytes();enabled=self._enabled()
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
                    if _cmdline(supervisor['pid'])!=self.known[content]:
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

    def native_stop(self,*,drain=None,deadline=None):
        from brain_linux_generation import GenerationDrain
        current=self.inspect(prove_main=False)
        if current['enabled']:raise RegistrationError('Disable user-service activation before ending it.')
        deadline=deadline if deadline is not None else time.monotonic()+20
        with ExitStack() as stack:
            if drain is None:
                drain=GenerationDrain(self,stack)
                drain.drain_index(deadline)
            sent=drain.finish(deadline)
        value=self.inspect(prove_main=False)
        if not value['quiescent'] or value['enabled'] or value['definition_sha256']!=current['definition_sha256']:
            raise RegistrationError('User service did not reach its verified disabled stopped state.')
        return {**value,'native_stopped':True,'native_end_exit':0 if sent else None,'native_end_timed_out':False,'native_end_sent':sent}

    def _environment(self):
        allowed=('HOME','USER','LOGNAME','PATH','LANG','LC_ALL','LC_CTYPE','TERM','SHELL','TMPDIR','TZ','XDG_RUNTIME_DIR','XDG_DATA_HOME','XDG_CONFIG_HOME')
        env={key:os.environ[key] for key in allowed if key in os.environ and not key.startswith(('LD_','PYTHON','BRAIN_','BASIC_MEMORY_'))}
        env['HOME']=str(self.home)
        env.setdefault('PATH','/usr/local/bin:/usr/bin:/bin')
        return env

    def _spawn_keeper(self,deadline):
        if self._keeper_process() is not None:return
        if self._unit_bytes()!=self.installable:raise RegistrationError('User service definition is not the current one.')
        if time.monotonic()>=deadline:raise RegistrationError('User service startup budget expired.')
        log_dir=self.root/'logs';log_dir.mkdir(parents=True,exist_ok=True)
        with (log_dir/'user-service.log').open('ab') as log:
            child=subprocess.Popen(list(self.keeper_argv),cwd=str(self.home),env=self._environment(),stdin=subprocess.DEVNULL,
                                   stdout=log,stderr=log,start_new_session=True)
        deadline=min(deadline,time.monotonic()+15)
        while time.monotonic()<deadline:
            keeper=self._keeper_process()
            if keeper is not None and keeper['pid']==child.pid:return
            if child.poll() is not None:break
            time.sleep(min(.05,max(0,deadline-time.monotonic())))
        raise RegistrationError('User service keeper did not publish its lifetime.')

    def start(self,*,explicit=True,deadline=None,hold_id=None):
        from brain_linux_generation import GenerationDrain, set_blocked
        import brain_service as controller
        from brain_index_bootstrap import settle_interrupted
        from brain_settings import STATE
        from filelock import FileLock
        deadline=deadline if deadline is not None else time.monotonic()+40
        value=self.inspect(prove_main=False)
        if not value['file_present']:raise RegistrationError('Install the exact user-service definition before start.')
        if not explicit and not value['enabled']:raise RegistrationError('User service is persistently disabled.')
        if self._keeper_process() is None:
            try:
                with ExitStack() as stack:
                    drain=GenerationDrain(self,stack)
                    drain.prove_recovery_port(deadline)
                    drain.drain_index(deadline)
                    drain.finish(deadline)
                    with FileLock(str(STATE/'index-bootstrap.lock'),timeout=0):
                        settle_interrupted(lambda generation: _matching_drain(drain,generation))
            except Exception as error:
                set_blocked(self,error)
                raise
        if explicit:self._set_enabled(True)
        controller._match_transaction(hold_id)
        if controller.spawn_allowed() and self._enabled() is True:
            self._spawn_keeper(deadline)
        return self.inspect(prove_main=False)

    def install(self,payload,*,enabled=None):
        if payload!=self.installable:raise RegistrationError('Only the current exact user-service definition may be installed.')
        current=self.inspect(prove_main=False)
        if not current['quiescent']:raise RegistrationError('Stop the user service before changing its definition.')
        if enabled is None and current['file_present']:
            raise RegistrationError('Existing user service definition requires explicit activation intent.')
        selected=True if enabled is None else enabled
        self._write(self.path,payload,0o644)
        if self._unit_bytes()!=payload:raise RegistrationError('User service definition was not verified.')
        skips = self._install_boot()
        return {**self._set_enabled(selected), 'hook_skips': skips}

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

    def _hook_file(self, path, limit):
        try:
            with self._home_directory(path.parent) as directory:
                data, mode = self._file_image(path.name, limit, directory=directory)
        except FileNotFoundError:
            return None, None
        except (OSError, RegistrationError) as error:
            raise HookRefusal('linked_foreign_or_oversized_file') from error
        if mode is not None and mode & 0o7022:
            raise HookRefusal('unsafe_file_mode')
        return data, mode

    def _boot_script(self, python, *, legacy=False):
        argv = [str(python), self.keeper_argv[1], 'boot']
        if legacy:
            return ('#!/bin/sh\nexec ' + shlex.join(argv) + '\n').encode()
        return ('#!/bin/sh\n' + shlex.join(argv) + ' </dev/null >/dev/null 2>&1 &\n').encode()

    def _profile_block(self, *, separator=False, legacy=False):
        command = shlex.quote(str(self.boot_path))
        if legacy:
            command += ' >/dev/null 2>&1 || true'
        else:
            command += ' </dev/null >/dev/null 2>&1 &'
        marker = '# Second Brain added the preceding newline\n' if separator else ''
        return (BOOT_MARK_BEGIN + '\n' + marker + command + '\n' + BOOT_MARK_END + '\n').encode()

    def _profile_change(self, data, *, remove):
        data = data or b''
        begin, end = BOOT_MARK_BEGIN.encode(), BOOT_MARK_END.encode()
        lines = data.split(b'\n')
        if begin not in lines and end not in lines:
            if any(line.removesuffix(b'\r') in (begin, end) for line in lines):
                raise HookRefusal('malformed_profile_block')
            if remove:
                return data
            separator = bool(data and not data.endswith(b'\n'))
            return data + (b'\n' if separator else b'') + self._profile_block(separator=separator)
        if lines.count(begin) != 1 or lines.count(end) != 1:
            raise HookRefusal('malformed_profile_block')
        start = sum(len(line) + 1 for line in lines[:lines.index(begin)])
        for block, separator in ((self._profile_block(legacy=True), False),
                                 (self._profile_block(), False),
                                 (self._profile_block(separator=True), True)):
            if data.startswith(block, start) and (not separator or start > 0):
                stop = start + len(block)
                if remove:
                    # Keep a necessary delimiter when owner content follows the block.
                    return data[:start - (1 if separator and stop == len(data) else 0)] + data[stop:]
                return data[:start] + self._profile_block(separator=separator) + data[stop:]
        raise HookRefusal('foreign_profile_block')

    def _desktop_body(self, *, legacy=False):
        command = str(self.boot_path)
        if not legacy:
            if not command.isascii() or any(part in command for part in ('=', '%', '\n', '\r')):
                raise HookRefusal('unsupported_desktop_exec_path')
            escaped = ''.join('\\' + char if char in ('"', '`', '$', '\\') else char for char in command)
            command = '"' + escaped.replace('\\', '\\\\').replace('\t', '\\t') + '"'
        return ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Second Brain\n'
                'Comment=Start the local Second Brain service\nExec=' + command +
                '\nTerminal=false\nX-GNOME-Autostart-enabled=true\n').encode()

    def hook_images(self, python=None, *, skips=None, remove=False):
        """Plan exact byte and mode transitions. Cron edits are explicit operations."""
        python = self.keeper_argv[0] if python is None else python
        boot, mode = self._hook_file(self.boot_path, HOOK_LIMIT)
        candidate_boot = self._boot_script(python)
        known_boot = {self._boot_script(self.keeper_argv[0], legacy=True),
                      self._boot_script(self.keeper_argv[0]), candidate_boot}
        if boot is not None and boot not in known_boot:
            raise HookRefusal('foreign_boot_start')
        boot_image = (self.boot_path, boot, mode, None if remove else candidate_boot, None if remove else 0o700)
        images = []
        profile = self.home / '.profile'
        desktop = self.home / '.config' / 'autostart' / 'second-brain.desktop'
        for path in (profile, desktop):
            try:
                data, mode = self._hook_file(path, HOOK_LIMIT)
                if path == profile:
                    changed = self._profile_change(data, remove=remove)
                    if data is None and remove:
                        continue
                else:
                    if data is not None and data not in (self._desktop_body(legacy=True), self._desktop_body()):
                        raise HookRefusal('foreign_desktop_entry')
                    changed = None if remove else self._desktop_body()
                new_mode = (0o644 if mode is None else mode) if changed is not None else None
                images.append((path, data, mode, changed, new_mode))
            except (OSError, HookRefusal) as error:
                if skips is not None:
                    skips.append({'path': str(path), 'reason': getattr(error, 'reason', 'inaccessible_file')})
        ordered = [*images, boot_image] if remove else [boot_image, *images]
        return [image for image in ordered if image[1:3] != image[3:5]]

    def _apply_hook(self, image):
        path, before, mode, after, new_mode = image
        self._write(path, after, new_mode, expected=(before, mode))
        if self._hook_file(path, HOOK_LIMIT) != (after, new_mode):
            raise HookRefusal('hook_write_unverified')

    def _install_boot(self):
        skips = []
        for image in self.hook_images(skips=skips):
            self._apply_hook(image)
        self._edit_crontab(remove=False, skips=skips)
        return skips

    def _edit_crontab(self, *, remove, skips):
        binary = shutil_which('crontab')
        if not binary:
            return
        try:
            if Path(os.environ.get('HOME', '')).resolve() != account_home():
                raise HookRefusal('crontab_home_mismatch')
            if '%' in str(self.boot_path) or '\n' in str(self.boot_path):
                raise HookRefusal('unsupported_crontab_path')
            owned = ('@reboot ' + shlex.quote(str(self.boot_path)) + ' # ' + CRON_MARK).encode()
            before = _read_crontab(binary)
            rows = before.splitlines(keepends=True)
            matching = [row for row in rows if row.removesuffix(b'\n') == owned]
            if len(matching) > 1:
                raise HookRefusal('duplicate_crontab_entry')
            if remove:
                after = b''.join(row for row in rows if row not in matching)
            elif matching:
                return
            else:
                after = before + (b'\n' if before and not before.endswith(b'\n') else b'') + owned + b'\n'
            if before == after:
                return
            if _read_crontab(binary) != before:
                raise HookRefusal('crontab_changed_after_planning')
            result = subprocess.run([binary, '-'], input=after, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode or _read_crontab(binary) != after:
                raise HookRefusal('crontab_write_unverified')
        except (OSError, subprocess.SubprocessError, HookRefusal) as error:
            skips.append({'path': 'crontab', 'reason': getattr(error, 'reason', 'crontab_unavailable')})

    def withdraw(self):
        """Withdraw an owned, disabled service after removing its boot entry points."""
        current = self.inspect(prove_main=False)
        if not current['quiescent'] or current['running'] or current['enabled']:
            raise RegistrationError('Disable and stop the user service before withdrawal.')
        skips = self.remove_boot_hooks()
        if any(skip['path'] == 'crontab' for skip in skips):
            raise HookRefusal('crontab_withdrawal_unverified')
        for path in (self.path, self.enabled_path):
            prior = self._hook_file(path, 65536)
            if prior[0] is not None:
                self._write(path, None, None, expected=prior)
        if self.path.exists() or self.enabled_path.exists():
            raise RegistrationError('User service definition was not removed.')
        return skips

    def remove_boot_hooks(self):
        skips = []
        for image in self.hook_images(skips=skips, remove=True):
            self._apply_hook(image)
        self._edit_crontab(remove=True, skips=skips)
        return skips

def _read_crontab(binary):
    import selectors

    env = dict(os.environ, LC_ALL='C')
    data = [bytearray(), bytearray()]
    deadline = time.monotonic() + 10
    with subprocess.Popen([binary, '-l'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        try:
            with selectors.DefaultSelector() as streams:
                for index, pipe in enumerate((child.stdout, child.stderr)):
                    streams.register(pipe, selectors.EVENT_READ, index)
                while streams.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HookRefusal('crontab_read_timeout')
                    for key, _ in streams.select(remaining):
                        buffer = data[key.data]
                        block = os.read(key.fd, min(65_536, HOOK_LIMIT + 1 - len(buffer)))
                        if not block:
                            streams.unregister(key.fileobj)
                        buffer.extend(block)
                        if len(buffer) > HOOK_LIMIT:
                            raise HookRefusal('crontab_size_limit')
            result = child.wait(timeout=max(0, deadline - time.monotonic()))
        finally:
            if child.poll() is None:
                child.kill()
    if result == 0:
        return bytes(data[0])
    if result == 1 and not data[0] and data[1].lower().startswith(b'no crontab for '):
        return b''
    raise HookRefusal('crontab_read_failed')


def shutil_which(name):
    import shutil
    return shutil.which(name)


def _matching_drain(drain,generation):
    value=drain.value
    return bool(value is not None and value['generation']==generation and value['drain']=='drained')


def supervise():
    """Keep the enrolled supervisor running while this exact definition is enabled."""
    from filelock import FileLock, Timeout
    import brain_settings as cfg
    import brain_service as controller
    import brain_linux_generation as lifecycle
    from brain_index_bootstrap import settle_interrupted

    stopping=False

    def stop(signum,frame):
        nonlocal stopping
        stopping=True

    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    service=LinuxUserService('second-brain.service',cfg.ROOT,{str(cfg.PYTHON)},current_python=cfg.PYTHON,keeper=True)
    keeper=lifecycle.keeper_record(service)
    record={'format':1,'keeper':keeper,'supervisor':None}
    lifecycle._write(service,KEEPER_NAME,record)
    child=None
    restart_after=0
    while not stopping:
        if child is not None:
            if child.poll() is None:
                time.sleep(.25);continue
            child=None
            restart_after=time.monotonic()+10
        if time.monotonic()<restart_after:
            time.sleep(.25);continue
        try:
            with FileLock(str(cfg.STATE/'service-control.lock'),timeout=0):
                if stopping:break
                if (service._unit_bytes()!=service.installable or service._enabled() is not True
                        or not controller.spawn_allowed()):
                    continue
                with ExitStack() as stack:
                    drain=lifecycle.GenerationDrain(service,stack)
                    deadline=time.monotonic()+20
                    drain.prove_recovery_port(deadline)
                    drain.drain_index(deadline,exclude=os.getpid())
                    drain.finish(deadline,exclude=os.getpid())
                    with FileLock(str(cfg.STATE/'index-bootstrap.lock'),timeout=0):
                        settle_interrupted(lambda generation: _matching_drain(drain,generation))
                if (service._unit_bytes()!=service.installable or service._enabled() is not True
                        or not controller.spawn_allowed() or stopping):
                    continue
                log=cfg.ROOT/'logs/user-service.log'
                with log.open('ab') as stream:
                    child=subprocess.Popen(list(service.argv),cwd=str(service.home),env=service._environment(),
                                           stdin=subprocess.DEVNULL,stdout=stream,stderr=stream)
                record['supervisor']=lifecycle.publish_supervisor(service,child,keeper)
                try:lifecycle._write(service,KEEPER_NAME,record)
                except BaseException:
                    lifecycle.end_unreleased_supervisor(child,record['supervisor'])
                    raise
                lifecycle.clear_blocked(service)
        except Timeout:
            pass
        except Exception as error:
            lifecycle.set_blocked(service,error)
        finally:
            # A controller waiting for keeper exit holds service-control.lock.
            # Exiting does not require that lock, and the controller owns cleanup.
            if not stopping:time.sleep(.25)


def boot():
    """An eligible login or scheduled trigger can repair a missing keeper."""
    import brain_settings as cfg
    import brain_service as controller
    service=LinuxUserService('second-brain.service',cfg.ROOT,{str(cfg.PYTHON)},current_python=cfg.PYTHON,keeper=True)
    if service._enabled() is not True or controller.transaction() is not None:return
    if (service._keeper_process() is not None and service._supervisor_process() is not None
            and controller.user_space_blocker() is None):
        return
    try:controller.start(explicit=False)
    except Exception as error:
        text=str(error)
        if 'persistently disabled' in text or 'explicitly stopped' in text or 'not installed' in text:return
        raise


if __name__=='__main__':
    if sys.argv[1:]==['supervise']:supervise()
    elif sys.argv[1:]==['boot']:boot()
    else:raise SystemExit('Use supervise or boot.')
