"""Native Mac registration boundary under qualification; never PID signal authority.

This helper is intentionally not wired into production control until the loaded
manager contract and complete lifecycle have native qualification receipts.
"""
from __future__ import annotations
import os
from pathlib import Path
import plistlib
import re
import hashlib
import selectors
import stat
import subprocess
import sys
import time


class RegistrationError(RuntimeError):
    pass


class CommandTimeout(RegistrationError):
    pass


DENIED_ENV_PREFIXES=('PYTHON','DYLD_','BRAIN_','BASIC_MEMORY_','MCP_')
SERVICE_ENTRY="import runpy,sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p.parent));sys.argv=sys.argv[1:];runpy.run_path(str(p),run_name='__main__')"


def _decode(raw):
    if not isinstance(raw,bytes) or len(raw)>2_000_000:raise RegistrationError('Native manager response exceeds its bound.')
    try:return raw.decode('utf-8')
    except UnicodeError:raise RegistrationError('Native manager response is unreadable.') from None


def domain_contains(raw,domain,label):
    lines=[line.strip() for line in _decode(raw).splitlines() if line.strip()]
    if not lines or lines[0]!=domain+' = {':raise RegistrationError('Native GUI domain header is unavailable.')
    active=False;closed=False;found=0
    for line in lines[1:]:
        if not active:
            if line=='services = {':active=True
            continue
        if line=='}':closed=True;break
        fields=line.split()
        if (len(fields)!=3 or not fields[0].isdigit()
                or not (fields[1]=='-' or re.fullmatch('-?[0-9]+',fields[1]))):
            raise RegistrationError('Native GUI services inventory grammar is unknown.')
        if fields[2]==label:found+=1
    if not closed or found>1:raise RegistrationError('Native GUI services inventory is incomplete.')
    return found==1


def disabled_state(raw,label):
    lines=[line.strip() for line in _decode(raw).splitlines() if line.strip()]
    if not lines or lines[0]!='disabled services = {' or lines[-1]!='}':
        raise RegistrationError('Native disabled-state grammar is unknown.')
    found=[]
    for line in lines[1:-1]:
        match=re.fullmatch(r'"([^"\r\n]+)"\s*=>\s*(disabled|enabled)',line)
        if not match:raise RegistrationError('Native disabled-state row is unknown.')
        if match.group(1)==label:found.append(match.group(2))
    if len(found)>1:raise RegistrationError('Native disabled-state override is duplicated.')
    return not found or found[0]=='enabled'


def loaded_fields(raw,address):
    lines=[line.strip() for line in _decode(raw).splitlines() if line.strip()]
    if not lines or lines[0]!=address+' = {':raise RegistrationError('Native loaded-service header is unknown.')
    fields={};arguments=None;depth=1;argument_depth=None;environment_depth=None
    critical={'path','type','state','program','working directory','stdout path','stderr path','domain','pid'}
    for line in lines[1:]:
        if line=='}':
            depth-=1
            if depth<0:raise RegistrationError('Native loaded-service blocks are malformed.')
            if argument_depth is not None and depth<argument_depth:argument_depth=None
            if environment_depth is not None and depth<environment_depth:environment_depth=None
            continue
        if depth==0:raise RegistrationError('Native loaded-service trailing data is unknown.')
        key,separator,value=line.partition(' = ')
        if separator and value=='{':
            if key=='arguments' and depth==1:
                if arguments is not None:raise RegistrationError('Native argument block is duplicated.')
                arguments=[];argument_depth=depth+1
            if 'environment' in key:environment_depth=depth+1
            depth+=1;continue
        if argument_depth is not None:
            if depth!=argument_depth or separator:raise RegistrationError('Native arguments are ambiguous.')
            arguments.append(_text(line));continue
        if environment_depth is not None:
            match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=>.*',line)
            if not match:raise RegistrationError('Native environment-key grammar is unknown.')
            entry_environment((match.group(1),));continue
        if separator and depth==1 and key in critical:
            if key in fields:raise RegistrationError('Native loaded-service identity field is duplicated.')
            fields[key]=value
    if depth!=0 or arguments is None:raise RegistrationError('Native loaded-service proof is incomplete.')
    fields['arguments']=arguments
    return fields


def _text(value):
    if (not isinstance(value,str) or not value or value!=value.strip()
            or any(ord(char)<32 or ord(char)==127 or char in '{}' for char in value)):
        raise RegistrationError('Native registration argument is not unambiguous.')
    return value


def _plain(path):
    path=Path(path)
    if not path.is_absolute():raise RegistrationError('Native registration path must be absolute.')
    for part in (path,*path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode):raise RegistrationError('Linked native registration path is refused.')
    return path


def native_tool():
    tool=Path('/bin/launchctl')
    try:
        for part in (tool,*tool.parents):
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise RegistrationError('Native manager tool is not protected by the OS owner.')
        if not stat.S_ISREG(tool.stat().st_mode):raise RegistrationError('Native manager tool is unavailable.')
    except OSError:raise RegistrationError('Native manager tool is unavailable.') from None
    return str(tool)


def command(arguments,*,timeout=15):
    """Private directly owned child, streaming caps, no shell or ambient env."""
    if type(timeout) not in (int,float) or not 0<timeout<=30:raise RegistrationError('Native command timeout is outside its bound.')
    argv=[native_tool(),*map(_text,arguments)]
    try:
        child=subprocess.Popen(argv,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    except OSError:raise RegistrationError('Native manager command is unavailable.') from None
    selector=selectors.DefaultSelector();out=bytearray();err=bytearray();deadline=time.monotonic()+timeout
    try:
        for pipe,bucket in ((child.stdout,out),(child.stderr,err)):
            os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,bucket)
        while selector.get_map():
            if time.monotonic()>=deadline:raise CommandTimeout('Native manager command timed out.')
            for key,_ in selector.select(.1):
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk:selector.unregister(key.fileobj);continue
                key.data.extend(chunk)
                if len(out)>2_000_000 or len(err)>65536:raise RegistrationError('Native manager output exceeds its bound.')
        child.wait(timeout=max(.01,deadline-time.monotonic()))
        return child.returncode,bytes(out),bytes(err)
    except subprocess.TimeoutExpired:raise CommandTimeout('Native manager command timed out.') from None
    except OSError:raise RegistrationError('Native manager command is unavailable.') from None
    finally:
        selector.close()
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()


def read_definition(path,uid):
    """Read exact source-generated bytes, never parse untrusted plist authority."""
    path=_plain(path)
    try:descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    except OSError:raise RegistrationError('Native definition cannot be safely opened.') from None
    try:
        info=os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_nlink!=1
                or info.st_mode&0o022 or info.st_size>65536):
            raise RegistrationError('Native definition ownership/type/size is invalid.')
        payload=os.read(descriptor,65537);after=os.fstat(descriptor)
        identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)
        if len(payload)!=info.st_size or identity(info)!=identity(after):raise RegistrationError('Native definition changed during read.')
        return identity(info),payload
    finally:os.close(descriptor)


def definitions(label,root,selected_python):
    """Current and retained selections authorize only exact generated definitions."""
    if not re.fullmatch(r'local\.second-brain(?:\.test\.[a-f0-9]{32})?',label):raise RegistrationError('Unrecognized native service label.')
    root=_plain(root);result={}
    script=_text(str(root/'app/brain_supervisor.py'))
    for value in selected_python:
        python=Path(value)
        if not python.is_absolute():raise RegistrationError('Native interpreter selection must be absolute.')
        _text(str(python))
        # The no-flags form recognizes the exact previously generated plist.
        for flags in ([],['-I','-B','-c',SERVICE_ENTRY]):
            argv=[str(python),*flags,script]
            definition={'Label':label,'ProgramArguments':argv,'RunAtLoad':True,'KeepAlive':True,
                'WorkingDirectory':str(root/'app'),'StandardOutPath':str(root/'logs/service-supervisor.log'),
                'StandardErrorPath':str(root/'logs/service-supervisor.log')}
            result[plistlib.dumps(definition)]=argv
    return result


def entry_environment(names):
    """The actual started process observes its complete inherited key set."""
    if any(name.startswith(DENIED_ENV_PREFIXES) for name in names):
        raise RegistrationError('Native service environment contains an overriding key.')


class MacRegistration:
    """Real account path binding; loaded ownership parser is still being measured."""
    def __init__(self,label,root,selected_python,*,current_python):
        if sys.platform!='darwin' or os.geteuid()==0:raise RegistrationError('A native non-root Mac GUI session is required.')
        import pwd
        self.uid=os.geteuid();self.domain='gui/'+str(self.uid);self.label=label
        self.known=definitions(label,root,selected_python)
        current=[str(Path(current_python)),'-I','-B','-c',SERVICE_ENTRY,str(Path(root)/'app/brain_supervisor.py')]
        matches=[payload for payload,argv in self.known.items() if argv==current]
        if len(matches)!=1:raise RegistrationError('Current isolated interpreter definition is unavailable.')
        self.installable=matches[0]
        self.home=_plain(Path(pwd.getpwuid(self.uid).pw_dir))
        self.directory=self.home/'Library/LaunchAgents';self.path=self.directory/(label+'.plist')
        for part in (self.home,self.home/'Library',self.directory):
            _plain(part)
            try:info=part.lstat()
            except FileNotFoundError:continue
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o022:
                raise RegistrationError('Native user registration directory ownership/privacy is invalid.')

    def inspect(self):
        before=read_definition(self.path,self.uid)
        if before is not None and before[1] not in self.known:raise RegistrationError('Existing native definition belongs to another configuration.')
        def observe():
            code,domain_raw,err=command(['print',self.domain])
            if code or err:raise RegistrationError('Native GUI domain is unavailable.')
            present=domain_contains(domain_raw,self.domain,self.label)
            code,raw,err=command(['print',self.domain+'/'+self.label])
            disabled_code,disabled_raw,disabled_err=command(['print-disabled',self.domain])
            if disabled_code or disabled_err:raise RegistrationError('Native disabled state is unavailable.')
            enabled=disabled_state(disabled_raw,self.label)
            if code:
                message=_decode(err)
                if (present or code!=113 or raw or 'Could not find service' not in message
                        or '"'+self.label+'"' not in message or 'gui' not in message):
                    raise RegistrationError('Native service absence is not proven.')
                return {'file_present':before is not None,'loaded':False,'enabled':enabled,'running':False}
            if err or not present or before is None:raise RegistrationError('Native loaded service and file/domain inventory disagree.')
            fields=loaded_fields(raw,self.domain+'/'+self.label);argv=self.known[before[1]]
            root=Path(argv[-1]).parent.parent
            expected={'path':str(self.path),'type':'LaunchAgent','program':argv[0],
                'working directory':str(root/'app'),'stdout path':str(root/'logs/service-supervisor.log'),
                'stderr path':str(root/'logs/service-supervisor.log'),'arguments':argv}
            if any(fields.get(key)!=value for key,value in expected.items()):raise RegistrationError('Native loaded definition does not match its exact owned file.')
            if not re.fullmatch(re.escape(self.domain)+r' \[[0-9]+\]',fields.get('domain','')):
                raise RegistrationError('Native loaded GUI domain does not match this account.')
            state=fields.get('state')
            if state not in ('running','not running'):raise RegistrationError('Native loaded state is unknown.')
            pid=fields.get('pid')
            if state=='running':
                if not pid or not pid.isdigit() or not 0<int(pid)<=0x7fffffff:raise RegistrationError('Native running service PID is unavailable.')
                import brain_process as processes
                try:owned=processes.inspect(int(pid),required=True)
                except processes.OwnershipError:raise RegistrationError('Native manager process ownership is unavailable.') from None
                try:
                    if owned is None or owned.role!='supervisor':raise RegistrationError('Native manager PID is not the exact owned supervisor.')
                finally:
                    if owned is not None:owned.close()
            elif pid is not None:raise RegistrationError('Native inactive service still reports a process.')
            return {'file_present':True,'loaded':True,'enabled':enabled,'running':state=='running',
                'main_pid':int(pid) if pid else None,'arguments':argv}
        value=observe()
        if not value['loaded']:
            time.sleep(.25)
            if observe()!=value:raise RegistrationError('Native absence changed during recheck.')
        if read_definition(self.path,self.uid)!=before:raise RegistrationError('Native definition changed during manager inspection.')
        value['definition_sha256']=hashlib.sha256(before[1]).hexdigest() if before else None
        return value

    def _set_enabled(self,enabled):
        command(['enable' if enabled else 'disable',self.domain+'/'+self.label])
        current=self.inspect()
        if current['enabled'] is not enabled:raise RegistrationError('Native activation override was not verified.')
        return current

    def suspend_activation(self):
        prior=self.inspect();self._set_enabled(False);return prior

    def restore_activation(self,prior):
        current=self.inspect()
        if current['definition_sha256']!=prior.get('definition_sha256') or type(prior.get('enabled')) is not bool:
            raise RegistrationError('Pre-stop native definition changed; preserve activation hold.')
        return self._set_enabled(prior['enabled'])

    def native_stop(self):
        value=self.inspect()
        if value['enabled']:raise RegistrationError('Disable native activation before ending the job.')
        native_exit=None;timed_out=False
        if value['loaded']:
            try:native_exit=command(['bootout',self.domain+'/'+self.label],timeout=30)[0]
            except CommandTimeout:timed_out=True
        deadline=time.monotonic()+30
        while True:
            try:value=self.inspect()
            except RegistrationError:
                if time.monotonic()>=deadline:raise
                time.sleep(.25);continue
            if not value['loaded'] and not value['enabled']:
                return {**value,'native_end_exit':native_exit,'native_end_timed_out':timed_out}
            if time.monotonic()>=deadline:raise RegistrationError('Native job did not reach verified disabled absence.')
            time.sleep(.25)

    def start(self,*,explicit=True):
        value=self.inspect()
        if not value['file_present']:raise RegistrationError('Install the exact native definition before start.')
        if not explicit and not value['enabled']:raise RegistrationError('Native service is persistently disabled.')
        if explicit:value=self._set_enabled(True)
        if not value['loaded']:
            command(['bootstrap',self.domain,str(self.path)])
            value=self.inspect()
            if not value['loaded']:raise RegistrationError('Native bootstrap was not verified.')
        if not value['running']:command(['kickstart',self.domain+'/'+self.label])
        deadline=time.monotonic()+30
        while True:
            value=self.inspect()
            if value['running']:return value
            if time.monotonic()>=deadline:raise RegistrationError('Native exact supervisor did not start.')
            time.sleep(.25)

    def install(self,payload,*,enabled=None):
        if payload!=self.installable:raise RegistrationError('Only the current isolated native definition may be installed.')
        prior=self.inspect()
        if prior['loaded']:raise RegistrationError('Stop the native job before changing its definition.')
        before=read_definition(self.path,self.uid)
        selected=prior['enabled'] if enabled is None else enabled
        if type(selected) is not bool:raise RegistrationError('Native activation choice must be explicit.')
        current=self._set_enabled(False)
        if current['loaded'] or current['definition_sha256']!=prior['definition_sha256']:
            raise RegistrationError('Native job or definition changed during activation suspension.')
        for part in (self.home/'Library',self.directory):
            try:part.mkdir(mode=0o700)
            except FileExistsError:pass
            _plain(part);info=part.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=self.uid or info.st_mode&0o022:
                raise RegistrationError('Unsafe native registration directory.')
        temporary=self.directory/('.second-brain-'+os.urandom(16).hex()+'.tmp')
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
            if read_definition(self.path,self.uid)!=before:raise RegistrationError('Native definition changed before replacement.')
            os.replace(temporary,self.path)
            directory=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(directory)
            finally:os.close(directory)
            after=read_definition(self.path,self.uid)
            if after is None or after[1]!=payload:raise RegistrationError('Native definition replacement was not verified.')
        finally:temporary.unlink(missing_ok=True)
        return self._set_enabled(selected)
