"""Set up or update Second Brain on this device with one command.

Run it from the verified release folder:

    python -I -B setup_device.py

It inspects the device, asks only what it cannot inspect, then installs or updates,
connects the assistants (connection, hooks, instruction block and skill) and ends with
the read-only acceptance check. --yes accepts every default. Nothing is sent anywhere.
"""
import sys
if __name__=='__main__' and not sys.flags.isolated:
    raise SystemExit('Run this with Python -I from the verified release folder.')
import argparse, json, os, runpy, shutil, subprocess, time
from pathlib import Path

REPO=Path(__file__).resolve().parent
CONTROL='02-Areas/AI-Memory/Wiki/Control.md'
CLIENTS=('claude-code','codex','claude-desktop')


def say(text):print(text,flush=True)


def ask(question,default,assume):
    if assume or not sys.stdin.isatty():return default
    answer=input(question+' ['+default+']: ').strip()
    return answer or default


def run(command,*,capture=False):
    """Children start in the release folder with no inherited runtime or Python overrides."""
    env={key:value for key,value in os.environ.items() if not key.upper().startswith(('BRAIN_','BASIC_MEMORY_','PYTHON'))}
    return subprocess.run([str(part) for part in command],cwd=REPO,env=env,capture_output=capture,text=True,encoding='utf-8')


def detected(home):
    """Assistants found on this device. Claude Desktop is never assumed: it must be closed to be changed."""
    found=[]
    if (home/'.claude.json').exists() or (home/'.claude').is_dir() or shutil.which('claude'):found.append('claude-code')
    if (home/'.codex').is_dir() or shutil.which('codex'):found.append('codex')
    return found


def new_vault(target):
    """Copy the blank starter to a folder that does not exist yet. An existing vault is never overlaid."""
    source=REPO/'starter'
    if target.exists():raise ValueError('The vault folder already exists; an existing vault is used as it is.')
    if not target.parent.is_dir():raise ValueError('Create the parent folder first: '+str(target.parent))
    files=[path for path in sorted(source.rglob('*')) if path.is_file()]
    if not files or any(path.is_symlink() for path in source.rglob('*')):raise ValueError('The starter vault is missing or linked.')
    target.mkdir()
    for path in files:
        output=target/path.relative_to(source)
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('xb') as stream:stream.write(path.read_bytes())


def remotes(vault):
    """Git remotes of the notes folder: none when it is not a repository of its own, None when that cannot be told."""
    if not (Path(vault)/'.git').exists():return []
    result=run(['git','-C',vault,'remote'],capture=True) if shutil.which('git') else None
    return result.stdout.split() if result is not None and result.returncode==0 else None


def own_the_notes(vault,device_id):
    """Name this device as the one maintainer. Never through a link, never over an existing owner."""
    control=vault
    for part in CONTROL.split('/'):
        control=control/part
        if control.is_symlink() or (hasattr(control,'is_junction') and control.is_junction()):
            raise SystemExit('A linked folder on the way to '+CONTROL+' is refused. Make it a plain folder inside the notes and run this again.')
    control.parent.mkdir(parents=True,exist_ok=True)
    try:
        with control.open('x',encoding='utf-8',newline='\n') as output:
            try:output.write('---\nmaintainer_device_id: '+device_id+'\n---\n\n# Shared writer ownership\n\nThis device links saved milestones into daily notes. Additional devices are installed with the capture role.\n');output.flush()
            except BaseException:
                # A half-written ownership note must not stay behind.
                output.close();control.unlink(missing_ok=True);raise
    except FileExistsError:
        raise SystemExit(CONTROL+' appeared while this was running. Another device may own these notes; check it before starting the service.') from None


def make_maintainer(device,root,vault,service):
    """Turn an installed capture device into the one maintainer of notes that have none yet."""
    control=vault/CONTROL
    def owned_by_another():
        if not control.exists():return False
        owner=[line.split(':',1)[1].strip() for line in control.read_text(encoding='utf-8-sig').splitlines() if line.startswith('maintainer_device_id:')]
        return owner!=[device['device_id']]
    refusal='These notes already name another device as maintainer in '+CONTROL+'. Only one device may be; nothing was changed.'
    if owned_by_another():raise SystemExit(refusal)
    if (root/'state/service-transaction.json').exists():raise SystemExit('An update is in progress on this device; nothing was changed.')
    record=json.loads((root/'device.json').read_text(encoding='utf-8-sig'))
    if record.get('device_id')!=device['device_id'] or record.get('role')!='capture':raise SystemExit('The device record is not a capture device; nothing was changed.')
    write=runpy.run_path(str(REPO/'install.py'))['atomic']
    if not service and any((root/'state'/name).exists() for name in ('service-heartbeat.json','service-pids.json','service-control-status.json')):
        # A server that is running, or still starting, keeps the old role until it is restarted. Without the
        # service step the change is made only on a device whose service has never run.
        raise SystemExit('This device has a background service. Run this without --no-service so it can be stopped and started around the change; nothing was changed.')
    created=False
    try:
        if service:
            result=brain(device,'service','stop','--wait-seconds','300',capture=True)
            if result.returncode:
                raise SystemExit('The service could not be stopped cleanly: '+((result.stderr or '').strip().splitlines() or [''])[-1][:300]+'\nThe role was not changed.')
        # Another device may have claimed the notes while the service was stopping.
        if owned_by_another():raise SystemExit(refusal)
        if not control.exists():own_the_notes(vault,device['device_id']);created=True
        write(root/'device.json',json.dumps({**record,'role':'maintainer'},indent=2)+'\n')
    except BaseException:
        # Leave one consistent device. If the record was already replaced the change is complete and stays;
        # otherwise the ownership note made here is withdrawn. Either way the service comes back up, also
        # after a stop that failed late or was interrupted and may already have ended it.
        try:
            try:switched=json.loads((root/'device.json').read_text(encoding='utf-8-sig')).get('role')=='maintainer'
            except (OSError,ValueError):switched=False
            # Only the note this run wrote is withdrawn; one that another device has put there since stays.
            if created and not switched and not owned_by_another():control.unlink(missing_ok=True)
        finally:
            if service:
                say('The service was started again.' if brain(device,'service','start',capture=True).returncode==0
                    else 'The service is stopped. Start it with: "'+device['python']+'" "'+str(root/'app/brain_cli.py')+'" service start')
        raise
    say('This device is now the maintainer of these notes.')


def wiki_programs(name):
    """Programs called claude or codex on PATH, in order. Windows needs the real .exe: launchers are passed over and a link is named by its target."""
    found=[]
    for folder in os.environ.get('PATH','').split(os.pathsep):
        candidate=Path(folder)/(name+('.exe' if os.name=='nt' else ''))
        # A relative PATH entry means the current folder, where a tool may have been planted.
        if not Path(folder).is_absolute() or not candidate.is_file() or not os.access(candidate,os.X_OK):continue
        program=str(candidate.resolve() if os.name=='nt' else candidate)
        if program not in found:found.append(program)
    return found


def brain(device,*arguments,capture=False):
    return run([device['python'],Path(device['root'])/'app/brain_cli.py',*arguments],capture=capture)


def wait_running(device,version,seconds=240):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=brain(device,'health',capture=True)
        try:health=json.loads(result.stdout) if result.returncode==0 else {}
        except ValueError:health={}
        if isinstance(health,dict) and health.get('status')=='running' and health.get('version')==version:return True
        time.sleep(3)
    return False


def start_service(device,version,root,registered=False):
    """Register and start the background service, then wait until it answers on this version."""
    # A registration that already exists is only started; registering over it would be refused.
    if not (registered and brain(device,'service','start',capture=True).returncode==0):
        for action in ('install','start'):
            result=brain(device,'service',action,capture=True)
            if result.returncode:
                reason=((result.stderr or '').strip().splitlines() or [''])[-1][:400]
                raise SystemExit('The background service could not '+action+(': '+reason if reason else '.')+'\nThe device is installed but not running. Fix that cause and run this command again.')
    if not wait_running(device,version):
        raise SystemExit('The service did not report running within four minutes. Check with: "'+device['python']+'" "'+str(root/'app/brain_cli.py')+'" health')


def main():
    windows=os.name=='nt';home=Path.home()
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root',default='C:/Second-Brain-System' if windows else str(home/'.local/share/second-brain'),help='Runtime folder.')
    parser.add_argument('--vault',help='Notes folder. A folder that does not exist is created from the blank starter.')
    parser.add_argument('--role',choices=['capture','maintainer'],help='Default: maintainer for the first device of a brain, capture for an additional one. On an installed capture device, maintainer makes it the maintainer if the notes have none.')
    parser.add_argument('--clients',help='Comma-separated: '+', '.join(CLIENTS)+'. Default: the assistants found on this device.')
    parser.add_argument('--wiki',choices=['claude','codex'],help='Maintainer only: select which assistant program builds the wiki pages. Nothing is built by this.')
    parser.add_argument('--no-hooks',action='store_true',help='Leave out the Claude Code session hooks.')
    parser.add_argument('--timezone',help='IANA name such as Europe/Berlin. Default: the system timezone.')
    parser.add_argument('--no-service',action='store_true',help='Install without registering or starting the background service.')
    parser.add_argument('--no-model',action='store_true',help='Skip the search model download (search stays unavailable until it is present).')
    parser.add_argument('--local-only',action='store_true',help='The owner wants no Git remote for these notes; the check fails if one exists.')
    parser.add_argument('--check',action='store_true',help='Only run the acceptance check.')
    parser.add_argument('--yes',action='store_true',help='Accept every default without asking.')
    args=parser.parse_args()
    root=Path(args.root).expanduser().resolve()
    version=runpy.run_path(str(REPO/'update.py'))['verify'](REPO)['version']
    say('Second Brain '+version+': release files verified.')
    selected=[name for name in (args.clients.split(',') if args.clients is not None else detected(home)) if name]
    if set(selected)-set(CLIENTS):parser.error('Unknown client; choose from '+', '.join(CLIENTS))
    client_flags=[flag for name in selected for flag in ('--client',name)]
    if 'claude-code' in selected and not args.no_hooks:client_flags.append('--include-hooks')
    device=json.loads((root/'device.json').read_text(encoding='utf-8-sig')) if (root/'device.json').is_file() else None

    if device is None and args.check:parser.error('Nothing is installed at '+str(root))
    if device is None:
        default='C:/Second-Brain' if windows else str(home/'Documents/Second-Brain')
        vault=Path(args.vault or ask('Notes folder (created if it does not exist)',default,args.yes)).expanduser().resolve()
        created=not vault.exists()
        role=args.role or ('capture' if (vault/CONTROL).is_file() else 'maintainer')
        if not created and not args.role:
            # Two maintainers on one set of notes would both link captures; only the first device is one.
            role=ask('Role: maintainer for the first device using these notes, capture for every additional device',role,args.yes)
            if role not in ('capture','maintainer'):parser.error('Role must be capture or maintainer.')
        uv=shutil.which('uv')
        if not uv:raise SystemExit('uv is required. Install it from https://docs.astral.sh/uv/ and run this again.')
        say('New device.\n  Runtime: '+str(root)+'\n  Notes:   '+str(vault)+(' (new, from the blank starter)' if created else ' (existing folder, used as it is)')
            +'\n  Role:    '+role+'\n  Assistants: '+(', '.join(selected) or 'none found'))
        if ask('Continue?','yes',args.yes).lower() not in ('y','yes'):raise SystemExit('Nothing was changed.')
        if args.local_only and remotes(vault)!=[]:
            raise SystemExit('--local-only was given, but these notes have a Git remote or it could not be checked ('+', '.join(remotes(vault) or ['Git did not answer'])+'). Remove the remote or drop --local-only. Nothing was installed.')
        if created:new_vault(vault)
        command=[sys.executable,'-I','-B',REPO/'install.py','--root',root,'--vault',vault,'--role',role,'--uv',Path(uv).resolve()]
        if args.timezone:command+=['--timezone',args.timezone]
        if not args.no_model:command.append('--download-model')
        if selected:command+=['--configure-clients',*client_flags]
        if run(command).returncode:
            # The installer keeps its child's output private; its last error line is the reason and names no secret.
            reports=sorted((root/'logs').glob('bootstrap-failure-*.json'),key=lambda path:path.stat().st_mtime)
            reason=(json.loads(reports[-1].read_text(encoding='utf-8')).get('stderr_tail','').strip().splitlines() or [''])[-1] if reports else ''
            raise SystemExit('The installer stopped'+(': '+reason[:400] if reason else '.')+'\nKeep the folders as they are and see the troubleshooting section of the setup guide.')
        device=json.loads((root/'device.json').read_text(encoding='utf-8-sig'))
        # The one device allowed to link captures and write shared summaries names itself before the first start.
        if role=='maintainer' and not (vault/CONTROL).exists():own_the_notes(vault,device['device_id'])
        device['root']=str(root)
        if not args.no_service:start_service(device,version,root)
    elif not args.check:
        vault=Path(device['vault'])
        if args.local_only and remotes(vault)!=[]:
            raise SystemExit('--local-only was given, but these notes have a Git remote or it could not be checked ('+', '.join(remotes(vault) or ['Git did not answer'])+'). Remove the remote or drop --local-only. Nothing was changed.')
        if device.get('version')!=version:
            release=root/'releases'/('v'+version)
            if REPO!=release:raise SystemExit('To update this device the release must be at exactly '+str(release)+'. Clone the tag there and run its setup_device.py.')
            say('Updating this device from '+str(device.get('version'))+' to '+version+'. The service stops only after every check has passed.')
            command=[device['python'],'-I','-B',REPO/'update.py','--root',root,'--version','v'+version]
            if 'claude-desktop' not in selected:command.append('--skip-desktop-refresh')
            if run(command).returncode:
                raise SystemExit('The update did not finish. If it said "deferred", nothing was switched and the old version is still running. Read the reason above before trying again.')
            device=json.loads((root/'device.json').read_text(encoding='utf-8-sig'))
        if selected:
            # An update refreshes only what already existed; this adds whatever a selected assistant still lacks.
            command=[device['python'],'-I','-B',REPO/'install.py','--root',root,'--vault',vault,'--reuse-python',device['python'],'--configure-clients','--clients-only',*client_flags]
            result=run(command,capture=True)
            if result.returncode or '"deferred": []' not in result.stdout:
                reason=((result.stderr or '').strip().splitlines() or ['a settings file changed while it was being written'])[-1]
                say('An assistant could not be completed: '+reason[:300]+'\nThe check below shows what is missing. Close that assistant and run this command again.')
            else:
                # A shared file that was left alone on purpose is named, so the check result below makes sense.
                for row in json.loads(result.stdout).get('client_result',{}).get('skipped',[]):
                    if row.get('reason')!='shared file already planned':say('Left alone: '+str(row.get('path'))+': '+str(row.get('reason'))[:300])
        device['root']=str(root)
        if args.role and args.role!=device.get('role'):
            if args.role!='maintainer':raise SystemExit('A maintainer cannot be turned into a capture device here; nothing was changed.')
            make_maintainer(device,root,vault,not args.no_service)
            device['role']='maintainer'
        # A first run that stopped before the service was up is finished here.
        if not args.no_service and not wait_running(device,version,seconds=15):start_service(device,version,root,registered=True)
    device['root']=str(root)
    if args.wiki and not args.check:
        reason=args.wiki+' was not found as a program on this device. Install it, check that "'+args.wiki+' --version" answers in a new terminal, and run this again.'
        for program in wiki_programs(args.wiki):
            result=brain(device,'wiki','configure-model','--executable',program,capture=True)
            if result.returncode==0:
                say('Wiki assistant selected: '+program+'. No page is built until you run: brain_cli.py wiki run');break
            reason=program+': '+((result.stderr or '').strip().splitlines() or [''])[-1][:300]
        else:say('The wiki assistant was not selected. '+reason)

    command=[device['python'],'-I','-B',REPO/'check_setup.py','--root',root,'--clients',','.join(selected),'--expect-version',version,'--release',REPO]
    if args.no_hooks:command.append('--no-hooks')
    if args.local_only:command.append('--local-only')
    if args.no_service:command.append('--no-service')
    say('\nAcceptance check:')
    failed=run(command).returncode
    say('\nNext: open a NEW chat in your assistant and ask it to call brain_context. After a restart of this computer, run this again with --check.')
    raise SystemExit(failed)


if __name__=='__main__':main()
