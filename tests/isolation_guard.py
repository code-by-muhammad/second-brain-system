"""Test-only process guard. Deliberately absent from installed application code."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit,unquote
from urllib.request import url2pathname

_POPEN = subprocess.Popen
SANDBOX = Path(os.environ['SB_TEST_SANDBOX']).resolve()
LIVE = ('e:/second-brain', 'e:/second-brain-system')


def validate_sandbox(value, real_home, repo):
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor):
        raise AssertionError('Test sandbox must be a dedicated absolute directory.')
    path = path.resolve()
    for boundary in (Path(real_home).resolve(), Path(repo).resolve(), *(Path(root).resolve() for root in LIVE)):
        if path.is_relative_to(boundary) or boundary.is_relative_to(path):
            raise AssertionError('Test sandbox overlaps an owner, source or live directory.')
    return path


def canonical(value):
    value = os.fsdecode(value).replace('\\', '/').removeprefix('//?/')
    value = value.removeprefix('//./')
    value = re.sub(r'^//(?:localhost|127\.0\.0\.1)/([a-z])\$/', r'\1:/', value, flags=re.I)
    if os.name == 'nt' and re.match(r'^[a-z]:/', value, re.I):
        path = Path(value)
        tail = []
        while not path.exists() and path != path.parent:
            tail.append(path.name)
            path = path.parent
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetLongPathNameW(str(path), buffer, len(buffer)):
            value = str(Path(buffer.value).joinpath(*reversed(tail))).replace('\\', '/').removeprefix('//?/')
    return value.casefold().rstrip('/')


def live_path(value):
    text = canonical(value)
    candidates = [text] + [canonical(token) for token in re.findall(r'[a-z]:[/\\][^;"\r\n]*', os.fsdecode(value), re.I)]
    return any(re.search(r'(?<![a-z0-9_.-])'+re.escape(root)+r'(?=$|[/;"\s])', candidate)
               for candidate in candidates for root in LIVE)


def inside(value):
    try:
        path=Path(canonical(value)) if os.name=='nt' else Path(value)
        return path.is_absolute() and path.resolve().is_relative_to(SANDBOX)
    except (ValueError, OSError):
        return False


def _local_url(value,repo):
    if value.startswith('file:'):
        parsed=urlsplit(value)
        if parsed.netloc not in ('','localhost') or parsed.query or parsed.fragment: return False
        value=url2pathname(unquote(parsed.path))
    elif '://' in value or (':' in value and not re.match(r'^[a-z]:[/\\]',value,re.I)):
        return False
    return inside(value) if Path(value).is_absolute() else inside(repo/value)


def _git_context(args,position,cwd):
    repo=Path(cwd or Path.cwd()).resolve(); options=[]; cursor=1
    while cursor<position:
        value=args[cursor]
        if value in ('--literal-pathspecs','--no-optional-locks','--no-replace-objects'):
            options.append(value);cursor+=1
        elif value=='-C' and cursor+1<position:
            repo=(repo/args[cursor+1]).resolve();cursor+=2
        elif value=='-c' and cursor+1<position:
            key,separator,setting=args[cursor+1].partition('='); key=key.casefold()
            if not separator or key.startswith(('alias.','protocol.','include','url.','remote.')) or key=='core.sshcommand':
                raise AssertionError('Test guard refuses an unsafe Git command override.')
            if key=='core.hookspath' and setting!=os.devnull and not _local_url(setting,repo):
                raise AssertionError('Test guard refuses non-sandbox hooks.')
            options.extend(args[cursor:cursor+2]);cursor+=2
        else: raise AssertionError('Test guard refuses unsupported Git global options.')
    return repo,options


def _git_read(executable,options,arguments,repo,env):
    with _POPEN([executable,*options,*arguments],cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
                **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {})) as process:
        output,_=process.communicate(timeout=10)
        if process.returncode not in (0,1): raise AssertionError('Test guard cannot verify Git remote.')
        return output.decode('utf-8').splitlines()


def _git_network(args,position,cwd,env):
    action=args[position]; repo,options=_git_context(args,position,cwd)
    effective=os.environ if env is None else env
    if effective.get('GIT_ALLOW_PROTOCOL') not in ('file','') or any(key in effective for key in ('GIT_DIR','GIT_CONFIG_PARAMETERS','GIT_CONFIG_COUNT')):
        raise AssertionError('Test guard refuses an unisolated Git network environment.')
    tokens=args[position+1:];targets=[];cursor=0;literal=False
    while cursor<len(tokens):
        value=tokens[cursor]
        if value=='--' and not literal: literal=True;cursor+=1;continue
        if not literal and value.startswith('-'):
            if action=='clone':
                if value in ('--no-local','--no-hardlinks','--no-checkout','--single-branch','--bare','--quiet','-q'):
                    cursor+=1;continue
                key,separator,setting=value.partition('=')
                if key in ('--depth','--branch','--filter','--config'):
                    if not separator:
                        cursor+=1
                        if cursor>=len(tokens): raise AssertionError('Test guard refuses an incomplete clone option.')
                        setting=tokens[cursor]
                    if key=='--config' and setting not in ('core.autocrlf=true','core.autocrlf=false'):
                        raise AssertionError('Test guard refuses unsafe clone configuration.')
                    if key=='--filter' and setting!='blob:none': raise AssertionError('Test guard refuses unsupported clone filtering.')
                    cursor+=1;continue
            elif action=='push' and value in ('-u','--set-upstream','--quiet','-q','--porcelain'):
                cursor+=1;continue
            elif action=='pull' and value in ('--ff-only','--quiet','-q'):
                cursor+=1;continue
            elif action in ('fetch','ls-remote') and value in ('--quiet','-q'):
                cursor+=1;continue
            raise AssertionError('Test guard refuses unsupported Git remote options.')
        targets.append(value);cursor+=1
    if action in ('clone','ls-remote'):
        if not targets or not _local_url(targets[0],repo): raise AssertionError('Test guard requires a sandbox-local Git source.')
        if action=='clone' and (len(targets)!=2 or not _local_url(targets[1],repo)):
            raise AssertionError('Test guard requires a sandbox-local clone destination.')
        return
    if not inside(repo): raise AssertionError('Test guard requires a sandbox-local Git worktree.')
    if targets:
        remote=targets[0]
        if Path(remote).is_absolute() or remote.startswith('file:'):
            if not _local_url(remote,repo): raise AssertionError('Test guard refuses a remote outside the sandbox.')
            return
    else:
        names=_git_read(args[0],options,['remote'],repo,env)
        if names!=['origin']: raise AssertionError('Test guard requires a single configured origin for implicit operations.')
        overrides=_git_read(args[0],options,['config','--get-regexp',r'^(remote\.push[Dd]efault|branch\..*\.(remote|push[Rr]emote))$'],repo,env)
        if any(line.split(None,1)[-1]!='origin' for line in overrides):
            raise AssertionError('Test guard refuses an implicit remote override.')
        remote='origin'
    urls=[]
    for direction in ([],['--push']):
        urls.extend(_git_read(args[0],options,['remote','get-url','--all',*direction,remote],repo,env))
    if not urls or not all(_local_url(value,repo) for value in urls):
        raise AssertionError('Test guard refuses a remote outside the sandbox.')


def check(command, *, cwd=None, env=None):
    if isinstance(command, (str, bytes)):
        raise AssertionError('Test guard refuses shell/string process commands.')
    args = [os.fsdecode(part) for part in command]
    if not args:
        raise AssertionError('Test guard refuses an empty command.')
    values = args + ([str(cwd)] if cwd else [])
    values += list((os.environ if env is None else env).values())
    if any(live_path(value) for value in values):
        raise AssertionError('Test guard refuses a live Second Brain path.')
    for value in args:
        if any(name in value.casefold() for name in ('secondbrainlocalservice', 'local.second-brain', 'second-brain.service')):
            raise AssertionError('Test guard refuses a production service identity.')
    executable = Path(args[0]).name.casefold().removesuffix('.exe')
    if executable in ('uv', 'uvx'):
        raise AssertionError('Test guard refuses dependency/model provisioning in tests.')
    if (executable in ('schtasks', 'launchctl', 'systemctl', 'sc')
            or (executable in ('powershell', 'pwsh') and any('scheduledtask' in value.casefold() or 'schtasks' in value.casefold() for value in args))):
        raise AssertionError('Test guard refuses native service mutation without an explicit reviewed run.')
    scripts = {Path(value).name.casefold() for value in args}
    if '-c' in args:
        position=args.index('-c')
        code=args[position+1] if position+1<len(args) else ''
        lowered=code.casefold()
        if any(name in lowered for name in ('brain_cli','brain_client','brain_supervisor','brain_service','install.py','update.py')) or any(statement in lowered for statement in ('import install','from install ','import update','from update ')):
            raise AssertionError('Test guard refuses an inline service-capable product entrypoint.')
    if '-m' in args:
        position=args.index('-m')
        module=args[position+1] if position+1<len(args) else ''
        if module.casefold() in ('brain_cli','brain_client','brain_supervisor','brain_service','install','update'):
            raise AssertionError('Test guard refuses a module service-capable product entrypoint.')
    def fixture_script(name):
        return next((index for index,value in enumerate(args) if Path(value).name.casefold()==name and inside(value)),None)
    help_index=next((index for name in ('install.py','update.py') if (index:=fixture_script(name)) is not None and args[index+1:]==['--help']),None)
    install_index=fixture_script('install.py')
    def fixture_option(name):
        return name in args and args.index(name)+1<len(args) and inside(args[args.index(name)+1])
    fixture_install=(install_index is not None and fixture_option('--reuse-python') and fixture_option('--root') and fixture_option('--vault')
                     and ('--validate-only' in args or '--install-service' not in args))
    if (('brain_cli.py' in scripts and 'service' in args)
            or scripts & {'brain_client.py', 'brain_supervisor.py'}
            or ('update.py' in scripts and help_index is None)
            or ('install.py' in scripts and help_index is None and not fixture_install)):
        raise AssertionError('Test guard refuses an unreviewed service-capable child entrypoint.')
    if Path(args[0]).name.casefold() not in ('git', 'git.exe'):
        return
    if ('submodule' in args and 'update' in args) or ('remote' in args and 'update' in args):
        raise AssertionError('Test guard refuses an implicit multi-remote operation.')
    # Config writes in synthetic repositories may record an https origin as inert
    # identity evidence; network-capable commands must only use local remotes.
    action = next((value for value in args[1:] if value in ('push', 'fetch', 'clone', 'ls-remote', 'pull')), None)
    if action is None:
        return
    if any(any(marker in value.casefold() for marker in ('https://', 'http://', 'ssh://', 'git@', 'github.com')) for value in args):
        raise AssertionError('Test guard refuses a network Git operation.')
    _git_network(args,args.index(action),cwd,env)


class GuardedPopen(_POPEN):
    def __init__(self, args, *positional, **kwargs):
        if positional or kwargs.get('shell'):
            raise AssertionError('Test guard requires explicit subprocess keyword arguments without a shell.')
        check(args, cwd=kwargs.get('cwd'), env=kwargs.get('env'))
        super().__init__(args, **kwargs)


def install():
    subprocess.Popen = GuardedPopen
    if os.name == 'nt':
        # Git uses CreateProcessW directly for Job containment, bypassing Popen.
        import brain_windows_process
        native_run = brain_windows_process.run
        def guarded_native(command, **kwargs):
            check(command, cwd=kwargs.get('cwd'), env=kwargs.get('env'))
            return native_run(command, **kwargs)
        brain_windows_process.run = guarded_native
