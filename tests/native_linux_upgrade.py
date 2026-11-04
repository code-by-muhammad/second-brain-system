"""Read-only native unit preabsence diagnostic on one fresh Ubuntu24 VM.

Prepare the reviewed isolated SDK/account and synthetic017 deployment files,
then observe native unit preabsence once. No product unit installation,
update, rollback or recovery is executed or qualified.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import types
import uuid


REMOTE='https://github.com/code-by-muhammad/second-brain-system.git'
OLD_TAG='v0.1.7'
OLD_COMMIT='0c29118bb9170048b5e2b534f96a9d13468b17fa'
OLD_MANIFEST='bc9e556632530c660b13da04aa8ec0b583c54e9e3f712b6f09e073add51939fd'
UNIT='second-brain.service'
SOURCE_SAFE=None


def digest(raw):return hashlib.sha256(raw).hexdigest()


def read(path,limit=64*1024*1024):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size>limit:
        raise RuntimeError('Unverified bounded fixture input.')
    with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise RuntimeError('Fixture input exceeds its bound.')
    return raw


def obj(path):return json.loads(read(path))


def plain(path):
    path=Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise RuntimeError('Require an absolute plain fixture path.')
    return path


def write_new(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def module(path,name):
    value=types.ModuleType(name);value.__file__=str(path)
    exec(compile(read(path),str(path),'exec'),value.__dict__)
    return value


def environment(home,temporary,base):
    # Process-local fixture configuration, never manager/global environment edits.
    keep=('PATH','SYSTEMROOT','LANG','LC_ALL','GITHUB_ACTIONS','GITHUB_RUN_ID','GITHUB_SHA','RUNNER_ARCH')
    result={key:os.environ[key] for key in keep if key in os.environ}
    result.update(HOME=str(home),XDG_RUNTIME_DIR='/run/user/'+str(os.geteuid()),TMPDIR=str(temporary),PATH=str(base.parent)+':/usr/bin:/bin:/usr/sbin:/sbin',
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never',
        HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
    return result


def command(argv,env,*,cwd=None,timeout=180):
    return subprocess.run(list(map(str,argv)),env=env,cwd=cwd,stdin=subprocess.DEVNULL,
        capture_output=True,text=True,encoding='utf-8',timeout=timeout,check=False)


def git(tool,env,*arguments,cwd=None):
    result=command([tool,'--no-replace-objects','--no-optional-locks',*(['-c','safe.directory='+str(SOURCE_SAFE),'-c','safe.directory='+str(SOURCE_SAFE/'.git')] if SOURCE_SAFE is not None else []),'-c','core.fsmonitor=false',
        '-c','core.hooksPath=/dev/null',*arguments],env,cwd=cwd)
    if result.returncode:raise RuntimeError('Pinned public fixture Git operation refused.')
    return result.stdout.strip()


def verify_checkout(path,tag,commit,manifest_sha,tool,env):
    if (git(tool,env,'rev-parse','HEAD',cwd=path)!=commit
        or git(tool,env,'rev-parse','refs/tags/'+tag+'^{commit}',cwd=path)!=commit
        or git(tool,env,'remote','get-url','origin',cwd=path)!=REMOTE):
        raise RuntimeError('Official checkout/tag identity differs.')
    raw=read(path/'release-manifest.json');manifest=json.loads(raw)
    if digest(raw)!=manifest_sha or manifest['version']!=tag.removeprefix('v'):
        raise RuntimeError('Reviewed manifest identity differs.')
    payload={**manifest['sha256'],'release-manifest.json':manifest_sha}
    tracked=set(git(tool,env,'ls-files','-z',cwd=path).split('\0'))-{''}
    if tracked!=set(payload):raise RuntimeError('Tracked source set differs.')
    for name,expected in payload.items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or not re.fullmatch('[a-f0-9]{64}',expected):
            raise RuntimeError('Malformed public source inventory.')
        source=plain(path/relative)
        if not source.resolve().is_relative_to(path.resolve()) or digest(read(source))!=expected:
            raise RuntimeError('Public source payload differs.')
    return manifest,payload


def fresh_clone(source,target,tag,tool,env):
    if target.exists():raise RuntimeError('Never reuse a fixture checkout.')
    target.parent.mkdir(parents=True,exist_ok=True)
    git(tool,env,'clone','--no-hardlinks','--no-checkout','--template=','--config','core.autocrlf=false','--',source,target)
    git(tool,env,'remote','set-url','origin',REMOTE,cwd=target)
    git(tool,env,'checkout','--detach',tag,cwd=target)


def app_bytes(path):return {p.name:read(p) for p in path.glob('*.py')}


def interpreter_identity(python,env):
    """Observe a selected venv invocation; this does not inspect a daemon's prefix."""
    result=command([python,'-I','-B','-c',
        'import json,sys;print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix)))'],env)
    if result.returncode:raise RuntimeError('Selected interpreter identity probe refused.')
    value=json.loads(result.stdout)
    if Path(value['prefix']).resolve()!=python.parent.parent.resolve() or value['prefix']==value['base_prefix']:
        raise RuntimeError('Selected interpreter did not retain its own venv prefix.')
    return value


class SyntheticAcceptanceRejection(RuntimeError):pass
class SyntheticAutomaticStopRejection(RuntimeError):pass
class PreexistingNativeRegistration(RuntimeError):pass
class DiagnosticPreabsenceComplete(Exception):pass


def frame_only(stderr):
    for line in stderr.splitlines():
        if len(line)>8192:continue
        try:value=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(value,dict) or set(value)!={"native_inspection_frames"}:continue
        frames=value["native_inspection_frames"]
        if not isinstance(frames,list) or not 1<=len(frames)<=32:continue
        if all(isinstance(row,dict) and set(row)=={"filename","function","line"}
               and all(isinstance(row[k],str) and re.fullmatch(r"[A-Za-z0-9_.<> -]{1,80}",row[k]) for k in ("filename","function"))
               and type(row["line"]) is int and 1<=row["line"]<=10_000_000 for row in frames):return frames
    return []


def path_metadata_only(stderr,home,uid):
    allowed=[PurePosixPath(str(home)),PurePosixPath("/run/user/"+str(uid))]+[PurePosixPath(p) for p in ("/etc/xdg/systemd","/etc/systemd","/run/systemd","/usr/local/share/systemd","/usr/share/systemd","/usr/local/lib/systemd","/usr/lib/systemd","/usr/lib64/systemd","/lib/systemd","/lib64/systemd")]
    def text(value):return isinstance(value,str) and 0<len(value)<=1024 and not any(ord(c)<32 or ord(c)==127 or c=="\\" for c in value)
    def selected(value):
        p=PurePosixPath(value)
        return text(value) and p.is_absolute() and ".." not in p.parts and len(p.parents)<32 and any(p.is_relative_to(root) for root in allowed)
    for line in stderr.splitlines():
        if len(line)>131072:continue
        try:wrapped=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(wrapped,dict) or set(wrapped)!={"native_unit_path_metadata"}:continue
        value=wrapped["native_unit_path_metadata"]
        if not isinstance(value,dict) or set(value)!={"unit_paths","ancestors","complete","omitted_unit_path_indexes"} or type(value["complete"]) is not bool:continue
        paths=value["unit_paths"];rows=value["ancestors"]
        omitted=value["omitted_unit_path_indexes"]
        if not isinstance(omitted,list) or len(omitted)>64 or any(type(i) is not int or not 0<=i<64 for i in omitted) or omitted!=sorted(set(omitted)) or (omitted and value["complete"]):continue
        if not isinstance(paths,list) or not 0<=len(paths)<=64 or not all(isinstance(p,str) and selected(p) for p in paths):continue
        if len(paths)+len(omitted)>64 or any(i>=len(paths)+len(omitted) for i in omitted):continue
        if not isinstance(rows,list) or len(rows)>256 or (value["complete"] and not paths):continue
        expected={str(p) for raw in paths for p in (PurePosixPath(raw),*PurePosixPath(raw).parents)}
        seen=set();valid=True
        for row in rows:
            if not isinstance(row,dict) or not {"path","status"}<=set(row) or not isinstance(row["path"],str) or row["path"] not in expected or row["path"] in seen:valid=False;break
            seen.add(row["path"])
            if row["status"] in ("missing","unavailable"):
                if set(row)!={"path","status"} or (value["complete"] and row["status"]=="unavailable"):valid=False;break
                continue
            if row["status"]!="exists" or not {"uid","gid","mode","type"}<=set(row):valid=False;break
            if any(type(row[k]) is not int or not 0<=row[k]<=4_294_967_295 for k in ("uid","gid")) or type(row["mode"]) is not int or not 0<=row["mode"]<=0o7777 or row["type"] not in ("directory","symlink","regular","other"):valid=False;break
            fields={"path","status","uid","gid","mode","type"}
            if row["type"]=="symlink":
                if "symlink_target" in row and text(row["symlink_target"]):fields.add("symlink_target")
                elif row.get("target_status") in ("unavailable","over-bound") and not value["complete"]:fields.add("target_status")
                else:valid=False;break
            if set(row)!=fields:valid=False;break
        if valid and (not value["complete"] or seen==expected):return value
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',required=True,choices=('forward','rollback','recovery'))
    parser.add_argument('--source-repository',required=True,type=Path)
    parser.add_argument('--candidate-tag',required=True)
    parser.add_argument('--candidate-commit',required=True)
    parser.add_argument('--candidate-manifest-sha256',required=True)
    parser.add_argument('--reviewed-harness-sha256',required=True)
    parser.add_argument('--workflow-sha',required=True)
    parser.add_argument('--expected-arch',required=True,choices=('x86_64',))
    parser.add_argument('--expected-ubuntu-version',required=True,choices=('24.04',))
    parser.add_argument('--expected-user',required=True)
    parser.add_argument('--uv-sha256',required=True)
    parser.add_argument('--uv',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--execute-reviewed-native',action='store_true')
    args=parser.parse_args();receipt=plain(args.receipt)
    if receipt.exists():raise RuntimeError('Preserve the first receipt; never overwrite or silently retry.')
    previous_umask=os.umask(0o022)
    if previous_umask!=0o022:raise RuntimeError('The reviewed wrapper must pin umask022 before execution.')
    if (args.mode!='forward' or not args.execute_reviewed_native or sys.platform!='linux' or os.geteuid()==0
        or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted'
        or not sys.flags.isolated or not sys.dont_write_bytecode
        or sys.flags.optimize or sys.version_info[:2]!=(3,12)):
        raise RuntimeError('Require reviewed execution on a fresh disposable non-root Ubuntu CI VM, Python3.12 -I -B.')
    if (digest(Path(__file__).read_bytes())!=args.reviewed_harness_sha256
        or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',args.candidate_tag)
        or not re.fullmatch('[a-f0-9]{40}',args.candidate_commit)
        or not re.fullmatch('[a-f0-9]{64}',args.candidate_manifest_sha256)
        or not re.fullmatch('[a-f0-9]{64}',args.uv_sha256)
        or not re.fullmatch('[a-f0-9]{40}',args.workflow_sha)
        or os.environ.get('GITHUB_WORKFLOW_SHA')!=args.workflow_sha
        or platform.machine()!=args.expected_arch or platform.freedesktop_os_release().get('ID')!='ubuntu'
        or platform.freedesktop_os_release().get('VERSION_ID')!=args.expected_ubuntu_version):
        raise RuntimeError('Reviewed harness/candidate identity is required.')
    import pwd
    account=pwd.getpwuid(os.geteuid())
    if account.pw_name!=args.expected_user or not re.fullmatch(r'sbci[0-9]+[fbc]',args.expected_user):
        raise RuntimeError('Require the exact newly created qualification account.')
    if os.environ.get('XDG_RUNTIME_DIR')!='/run/user/'+str(os.geteuid()):
        raise RuntimeError('Require the exact prepopulated account runtime bus.')
    home=plain(Path(account.pw_dir));source=plain(args.source_repository)
    global SOURCE_SAFE
    SOURCE_SAFE=source
    for protected in (source,Path(sys.prefix),Path(sys._base_executable).resolve(),Path(__file__).resolve(),args.uv):
        protected=plain(protected)
        for part in (protected,*protected.parents):
            if part.stat().st_uid!=0 or part.stat().st_mode&0o022:
                raise RuntimeError('Parent source/SDK/harness/tools require root-owned protected boundaries.')
    uv=plain(args.uv);tool=plain(Path(shutil.which('git') or '/usr/bin/git'))
    if not uv.is_file() or not tool.is_file():raise RuntimeError('Absolute trusted tools are required.')
    if digest(read(uv))!=args.uv_sha256:raise RuntimeError('Selected uv differs from its supplied tool pin.')
    if Path(sys.prefix).resolve()!=(source/'.venv').resolve() or sys.prefix==sys.base_prefix:
        raise RuntimeError('Use the official candidate checkout locked SDK .venv as the parent interpreter.')
    boundary=Path(tempfile.mkdtemp(prefix='second-brain-native-linux-',dir='/tmp'))
    plain(boundary);root=boundary/'runtime with spaces';root.mkdir();vault=boundary/'vault';vault.mkdir()
    temporary=boundary/'tmp';temporary.mkdir();fake_home=boundary/'client-home';fake_home.mkdir()
    env=environment(home,temporary,Path(sys._base_executable))
    env['PATH']=str(tool.parent)+':'+env['PATH']
    state={'status':'in progress','mode':args.mode,'unit':UNIT,'scope':'Diagnostic first native preabsence only; fresh account/manager and synthetic runtime files prepared, no product unit install/update/lifecycle acceptance.',
        'harness_sha256':args.reviewed_harness_sha256,'candidate_commit':args.candidate_commit,'candidate_tag':args.candidate_tag,
        'manifest_sha256':args.candidate_manifest_sha256,'baseline_commit':OLD_COMMIT,'root':str(root),
        'workflow_head':os.environ.get('GITHUB_SHA'),'workflow_sha':args.workflow_sha,'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),
        'architecture':platform.machine(),'os_version':platform.freedesktop_os_release().get('VERSION_ID'),'parent_interpreter':sys.executable,
        'account':account.pw_name,'uid':os.geteuid(),'wrapper_metadata_scope':'GitHub markers, exact account, umask022 and populated bus are supplied by the reviewed root wrapper.',
        'started_at':time.time(),'stage':'source preflight','observations':[],'controller_calls':[],'cleanup':{},
        'baseline_setup':'Actual017 deploy() writes synthetic app/config/auth files only; native service install is not executed.',
        'controller_call_scope':'No updater/controller lifecycle calls; one actual read-only native preabsence observation only.',
        'product_unit_registration_attempted':False,
        'limits':['One fresh ordinary account and populated manager bus are prepared by the unchanged reviewed wrapper.',
        'Synthetic017 deploy writes only fixture files; native unit install/update/rollback/recovery never run.',
        'Exception detail is limited to frame basenames, function names and positive line numbers; no messages or raw stderr.',
        'Verified UnitPath and lstat/readlink metadata are limited to exact synthetic-account and allowlisted OS paths.',
        'A failed observation preserves the fixture and yields no lifecycle qualification.',
        'A successful preabsence observation is read-only diagnostic evidence, not a native service pass.',
        'Original failed-host conditions are not reconstructed or normalized.',
        'No owner-device, GUI, local index/model, reboot/autostart or fresh onboarding acceptance.']}
    created=False;success=False;updater=None;old_payload=None;new_payload=None
    old=boundary/'baseline-v0.1.7';target=root/'releases'/args.candidate_tag
    original_env=dict(os.environ);original_cwd=Path.cwd()
    try:
        state['tools']={name:{'path':str(path),'sha256':digest(read(path)),
            'version':command([path,'--version'],env).stdout.strip()} for name,path in (('uv',uv),('git',tool))}
        # No locally invented tags: independently compare the supplied official tag.
        advertised=git(tool,env,'ls-remote','--tags',REMOTE,'refs/tags/'+args.candidate_tag,'refs/tags/'+args.candidate_tag+'^{}').splitlines()
        refs={line.split()[1]:line.split()[0] for line in advertised}
        if refs.get('refs/tags/'+args.candidate_tag+'^{}',refs.get('refs/tags/'+args.candidate_tag))!=args.candidate_commit:
            raise RuntimeError('Public candidate tag differs from the reviewed commit.')
        manifest,new_payload=verify_checkout(source,args.candidate_tag,args.candidate_commit,args.candidate_manifest_sha256,tool,env)
        locked={row['name']:row['version'] for row in tomllib.loads(read(source/'uv.lock').decode())['package']}
        state['parent_sdk']={'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'packages':{}}
        for name in ('fastmcp','httpx','mcp','psutil','filelock'):
            version=importlib.metadata.version(name)
            if version!=locked[name]:raise RuntimeError('Official SDK dependency differs from the candidate lock.')
            state['parent_sdk']['packages'][name]=version
        if 'linux' not in manifest.get('qualified_service_platforms',[]):
            raise RuntimeError('Candidate has no explicit native Linux admission; no service work started.')
        fresh_clone(str(source),target,args.candidate_tag,tool,env)
        verify_checkout(target,args.candidate_tag,args.candidate_commit,args.candidate_manifest_sha256,tool,env)
        fresh_clone(REMOTE,old,OLD_TAG,tool,env)
        _,old_payload=verify_checkout(old,OLD_TAG,OLD_COMMIT,OLD_MANIFEST,tool,env)
        updater=module(target/'update.py','native_linux_candidate_updater')
        updater.verify(target,version=args.candidate_tag)
        updater._candidate_platform();updater._qualified_platform()  # Actual unmodified admission guards.
        guard=command([sys.executable,'-I','-B','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import brain_service as s;s._qualified_native_platform()',str(source/'app')],env)
        if guard.returncode:raise RuntimeError('Actual service guard refused before baseline deployment.')
        os.environ.clear();os.environ.update(env);os.chdir(boundary)
        support=updater.installer().setup_helpers();support.host_context()
        state['stage']='retained017 provisioning and deploy'
        old_python=Path(support.provision(old,root,str(uv)))
        if old_python.parent.parent.parent!=old:raise RuntimeError('Retained baseline venv is not in the external017 checkout.')
        state['baseline_interpreter']=interpreter_identity(old_python,env)
        if (target/'.venv').exists():raise RuntimeError('Do not prebuild the target venv; the public updater owns it.')
        old_installer=module(old/'install.py','native_linux_017_installer')
        old_installer.deploy(root,old_python,vault,role='capture',configure_clients=False)
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        if port==8765:raise RuntimeError('Never use the production port.')
        device=obj(root/'device.json');device['port']=port
        old_installer.atomic(root/'device.json',json.dumps(device,indent=2)+'\n')
        config=obj(root/'state/config.json');config.update(semantic_search_enabled=False)
        old_installer.atomic(root/'state/config.json',json.dumps(config))
        old_installer.atomic(root/'state/index-bootstrap-status.json',json.dumps({'status':'needs attention','fixture_only':True}))
        note=vault/'01-Projects/Source.md';write_new(note,('# Synthetic native Linux fixture\nMarker-'+uuid.uuid4().hex+'\n').encode())
        note_before=read(note);auth_before={n:digest(read(root/'state'/n)) for n in ('access.json','client-access.json')}
        source_app=target/'app'

        def observe(stage,*,preabsence=False):
            # Read actual product authority and native argv; do not normalize a mismatch in the fixture.
            code=r'''import json,os,sys,time
def _native_frame_only(kind,error,tb):
    rows=[];pending=[(error,tb)];seen=set();walked=0
    while pending and len(seen)<4 and len(rows)<32 and walked<64:
        current,trace=pending.pop(0)
        if id(current) in seen:continue
        seen.add(id(current))
        while trace is not None and len(rows)<32 and walked<64:
            walked+=1;code=trace.tb_frame.f_code
            if type(trace.tb_lineno) is int and trace.tb_lineno>0:
                rows.append({"filename":os.path.basename(code.co_filename)[:80],"function":code.co_name[:80],"line":trace.tb_lineno})
            trace=trace.tb_next
        for related in (current.__cause__,current.__context__):
            if related is not None:pending.append((related,related.__traceback__))
    payload=json.dumps({"native_inspection_frames":rows})
    while len(payload)>8192:
        rows.pop();payload=json.dumps({"native_inspection_frames":rows})
    sys.stderr.write(payload+"\n")
sys.excepthook=_native_frame_only
from pathlib import Path
def _unit_path_metadata(registration):
    import stat
    allowed=[registration.home,registration.runtime]+[Path(p) for p in ("/etc/xdg/systemd","/etc/systemd","/run/systemd","/usr/local/share/systemd","/usr/share/systemd","/usr/local/lib/systemd","/usr/lib/systemd","/usr/lib64/systemd","/lib/systemd","/lib64/systemd")]
    value={"unit_paths":[],"ancestors":[],"complete":True,"omitted_unit_path_indexes":[]};seen=set()
    paths=list(registration.unit_paths)
    if not 1<=len(paths)<=64:return {"unit_paths":[],"ancestors":[],"complete":False,"omitted_unit_path_indexes":list(range(min(len(paths),64)))}
    for index,selected in enumerate(paths):
        if len(str(selected))>1024 or len(selected.parents)>=32 or ".." in selected.parts or not any(selected.is_relative_to(root) for root in allowed):
            value["complete"]=False;value["omitted_unit_path_indexes"].append(index);continue
        value["unit_paths"].append(str(selected))
        for path in (selected,*selected.parents):
            name=str(path)
            if name in seen:continue
            if len(seen)>=256:value["complete"]=False;continue
            seen.add(name);row={"path":name}
            try:
                info=os.lstat(path)
                kind="symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "regular" if stat.S_ISREG(info.st_mode) else "other"
                row.update(status="exists",uid=info.st_uid,gid=info.st_gid,mode=stat.S_IMODE(info.st_mode),type=kind)
                if kind=="symlink":
                    try:
                        target=os.readlink(path)
                        if 0<len(target)<=1024 and not any(ord(c)<32 or ord(c)==127 or c=="\\" for c in target):row["symlink_target"]=target
                        else:row["target_status"]="over-bound";value["complete"]=False
                    except OSError:row["target_status"]="unavailable";value["complete"]=False
            except FileNotFoundError:row["status"]="missing"
            except OSError:row["status"]="unavailable";value["complete"]=False
            value["ancestors"].append(row)
    if len(json.dumps({"native_unit_path_metadata":value}))>131072:return {"unit_paths":[],"ancestors":[],"complete":False,"omitted_unit_path_indexes":list(range(len(paths)))}
    return value
sys.path.insert(0,sys.argv[1])
import brain_service as s,brain_process as p,brain_linux_registration as m
s._qualified_native_platform()
r=s._linux_registration();deadline=time.monotonic()+3;retries=0
while True:
 try:v=r.inspect();break
 except m.NotReady:
  if time.monotonic()>=deadline:raise
  retries+=1;time.sleep(.1)
token=r._manager()
try:metadata=_unit_path_metadata(r)
except Exception:metadata={"unit_paths":[],"ancestors":[],"complete":False,"omitted_unit_path_indexes":[]}
sys.stderr.write(json.dumps({"native_unit_path_metadata":metadata})+"\n")
r._disk_boundary()
if sys.argv[2]=='preabsence':
 paths=[r.directory/'default.target.wants'/r.unit,r.directory/(r.unit+'.d')]
 if (v['file_present'] or v['loaded'] or v['enabled'] is not None
     or any(path.exists() or path.is_symlink() for path in paths)
     or (r.directory.exists() and any(r.directory.glob('.second-brain-*.tmp')))):
  print(json.dumps({'refusal':'preexisting-default-unit-or-artifact'}));raise SystemExit(24)
if r._manager()!=token:raise RuntimeError('native-manager-changed-during-observation')
v['notready_retries']=retries
if v['running']:
 import psutil
 owned=p.inspect(v['main_pid'],required=True)
 try:
  actual=psutil.Process(v['main_pid']).cmdline()
  if actual!=v['arguments']:raise RuntimeError('actual-product-argv-mismatch')
  v['actual_supervisor_argv']=actual;v['actual_supervisor_identity']=owned.record()
 finally:owned.close()
v['listener']=p.listener_identity()
if v['listener'] is not None:s._native_listener_binding(v['listener'],v)
roles=p.roots()
try:v['runtime_role_count']=len(roles);v['runtime_roles']=[role.record() for role in roles]
finally:
 for role in roles:role.close()
if not v['running'] and p._listeners():raise RuntimeError('fixture-port-not-empty')
print(json.dumps(v))
'''
            arguments=[*support.isolated_python(sys.executable,root),'-c',code,str(source_app),'preabsence' if preabsence else 'observe']
            result=support.run(arguments,source_app=source_app,env=updater.cli_environment(root),capture_output=True,text=True,encoding='utf-8',timeout=60,check=False)
            try:metadata=path_metadata_only(result.stderr,home,os.geteuid())
            except Exception:metadata=None
            state['native_unit_path_metadata_status']='accepted' if metadata is not None else 'absent-or-rejected'
            if metadata is not None:state['native_unit_path_metadata']=metadata
            if preabsence and result.returncode==24 and json.loads(result.stdout)=={'refusal':'preexisting-default-unit-or-artifact'}:
                raise PreexistingNativeRegistration('Preexisting default unit or artifact is preserved.')
            if result.returncode:
                state['native_inspection_failure_frames']=frame_only(result.stderr)
                raise RuntimeError('Actual native product inspection refused at '+stage+'. Preserve the fixture; do not adapt authority.')
            value=json.loads(result.stdout);state['observations'].append({'stage':stage,**value});return value

        state['stage']='native default-unit preabsence'
        observe('initial default-unit preabsence',preabsence=True)
        raise DiagnosticPreabsenceComplete
        # Legacy install uses real HOME; only synthetic client discovery later uses fake HOME.
        created=True;state['native_registration_attempted']=True;state['stage']='actual017 native service installation'
        updater.controller(old_python,old,root,'service','install')
        baseline=updater.wait_health(sys.executable,root,'0.1.7',checkout=target)
        first=observe('first actual legacy product prefix/argv')
        if not first['running'] or not first['listener']:raise RuntimeError('Actual legacy product identity is unavailable.')
        state['baseline_health_version']=baseline['version']
        original_native_sha=first.get('definition_sha256')
        if not isinstance(original_native_sha,str) or not re.fullmatch('[a-f0-9]{64}',original_native_sha):
            raise RuntimeError('Original native definition hash is unavailable.')
        state['original_native_definition_sha256']=original_native_sha
        env['HOME']=str(fake_home);os.environ.clear();os.environ.update(env)
        (fake_home/'.codex').mkdir();(fake_home/'.claude').mkdir()
        claude=fake_home/'.claude.json';codex=fake_home/'.codex/config.toml'
        write_new(claude,json.dumps({'owner_option':'keep','mcpServers':{'unrelated':{'command':'dummy'},'second-brain':{'command':str(old_python),'args':[str(root/'app/brain_client.py')]}}}).encode())
        write_new(codex,("# retained comment\nowner_option='keep'\n[mcp_servers.unrelated]\ncommand='dummy'\n[mcp_servers.second-brain]\ncommand="+json.dumps(str(old_python))+"\nargs=["+json.dumps(str(root/'app/brain_client.py'))+"]\n").encode())
        original_clients={p:read(p) for p in (claude,codex)};original_device=read(root/'device.json');original_app=app_bytes(root/'app')
        if (root/'state/previous-release.json').exists():raise RuntimeError('Fresh017 previous metadata must be observed absent.')
        nonce=uuid.uuid4().hex;faulted=False;rejected=False;actual_wait=updater.wait_health;actual_controller=updater.controller
        acceptance_error=SyntheticAcceptanceRejection('Synthetic target acceptance rejection nonce='+nonce)
        stop_error=SyntheticAutomaticStopRejection('Synthetic automatic stop refusal nonce='+nonce)

        def reject_acceptance(python,selected,version,timeout=120,*,checkout=None):
            nonlocal faulted
            health=actual_wait(python,selected,version,timeout=timeout,checkout=checkout)
            if args.mode!='forward' and not faulted and version==args.candidate_tag.removeprefix('v'):
                if Path(selected)!=root or Path(checkout)!=target:raise RuntimeError('Fault root/controller mismatch.')
                observed=observe('actual target health/prefix before synthetic rejection')
                transaction=obj(root/'state/service-transaction.json')
                if not observed['loaded'] or not observed['running'] or not observed['listener'] or health.get('status')!='running' or transaction['stage']!='started':
                    raise RuntimeError('No real target-health/started-transaction fault precondition.')
                faulted=True;state['synthetic_acceptance_fault']={'nonce_sha256':digest(nonce.encode()),'real_health_version':health['version'],'listener_verified':True}
                raise acceptance_error
            return health

        def reject_automatic_stop(python,checkout,selected,cmd,action=None,*flags):
            nonlocal rejected
            call={'python':str(python),'checkout':str(checkout),'root':str(selected),'command':cmd,'action':action,'flags':list(flags)}
            state['controller_calls'].append(call)
            if Path(python)!=target/'.venv/bin/python' or Path(checkout)!=target or Path(selected)!=root:
                raise RuntimeError('Parent updater selected a different controller interpreter/checkout/root.')
            if args.mode=='recovery' and faulted and not rejected and cmd=='service' and action=='stop':
                tx=obj(root/'state/service-transaction.json')
                if Path(selected)!=root or Path(checkout)!=target or tx['stage']!='started' or flags!=('--hold-id',tx['hold_id'],'--wait-seconds','300'):
                    raise RuntimeError('Automatic-stop fault precondition differs.')
                rejected=True;state['synthetic_automatic_stop_fault']={'underlying_stop_called':False,'nonce_sha256':digest(nonce.encode())}
                call['underlying_controller_called']=False
                raise stop_error
            call['underlying_controller_called']=True
            try:
                result=actual_controller(python,checkout,selected,cmd,action,*flags);call['returned']=True;return result
            except BaseException as error:call['error_type']=type(error).__name__;raise

        updater.wait_health=reject_acceptance
        updater.controller=reject_automatic_stop
        captured=io.StringIO();update_error=None;update_exception=None;state['stage']='actual public update with controlled fault mode'
        previous_argv=sys.argv
        try:
            sys.argv=[str(target/'update.py'),'--root',str(root),'--version',args.candidate_tag,'--uv',str(uv),'--skip-desktop-refresh']
            with contextlib.redirect_stdout(captured):updater.main()
        except Exception as error:update_error=type(error).__name__;update_exception=error
        finally:sys.argv=previous_argv;updater.wait_health=actual_wait;updater.controller=actual_controller
        private_output=boundary/'updater-private-output.txt';write_new(private_output,captured.getvalue().encode())
        status=obj(root/'state/upgrade-status.json');new_python=target/'.venv/bin/python'
        state['target_interpreter']=interpreter_identity(new_python,env)
        initial_status=read(root/'state/upgrade-status.json');saved_status=boundary/'initial-upgrade-status.json';write_new(saved_status,initial_status)
        state['updater_receipt']={'snapshot_path':str(saved_status),'sha256':digest(initial_status),'status':status['status'],
            'private_output_path':str(private_output),'private_output_sha256':digest(read(private_output))}
        if args.mode=='forward':
            if update_error or status['status']!='updated':raise RuntimeError('Forward update did not verify.')
            cc=obj(claude);cx=tomllib.loads(read(codex).decode());expected_client_args=[str(root/'app/brain_client.py')]
            if (cc['mcpServers']['second-brain']['command']!=str(new_python) or cc['mcpServers']['second-brain']['args']!=expected_client_args
                or cx['mcp_servers']['second-brain']['command']!=str(new_python) or cx['mcp_servers']['second-brain']['args']!=expected_client_args
                or cc['owner_option']!='keep' or cc['mcpServers']['unrelated']!={'command':'dummy'}
                or cx['owner_option']!='keep' or cx['mcp_servers']['unrelated']!={'command':'dummy'} or not read(codex).startswith(b'# retained comment\n')):
                raise RuntimeError('Actual selected client refresh differs.')
            if status['start']['heartbeat_verified'] is not True or status['finish']['status']!='transaction released':
                raise RuntimeError('Fresh target heartbeat/transaction completion did not verify.')
            deployed=obj(root/'device.json');expected_app={Path(n).name:h for n,h in new_payload.items() if n.startswith('app/') and n.endswith('.py')}
            if ({n:digest(raw) for n,raw in app_bytes(root/'app').items()}!=expected_app
                or deployed['python']!=str(new_python) or deployed['version']!=args.candidate_tag.removeprefix('v')
                or deployed['release_root']!=str(target) or status['clients']['deferred'] or status['client_overwrites_after_health']
                or set(status['clients']['changed'])!={str(claude),str(codex)}):
                raise RuntimeError('Forward payload/device/client completion differs.')
        else:
            if not faulted or not update_error:raise RuntimeError('Expected controlled post-health rejection was not observed.')
            pending=[update_exception];chain=[]
            while pending:
                error=pending.pop()
                if error is None or any(error is prior for prior in chain):continue
                chain.append(error);pending.extend((error.__cause__,error.__context__))
            if acceptance_error not in chain or status['error_type']!='SyntheticAcceptanceRejection':
                raise RuntimeError('Observed error chain/receipt does not contain the exact nonce-bound acceptance error.')
            state['synthetic_acceptance_fault']['exact_exception_chain_verified']=True
            if args.mode=='rollback' and status['status']!='update failed; previous runtime verified':raise RuntimeError('Automatic rollback did not verify.')
            if args.mode=='recovery':
                if not rejected or stop_error not in chain or status['status']!='recovery required' or status['recovery_error_type']!='SyntheticAutomaticStopRejection':
                    raise RuntimeError('Exact held recovery error chain/state was not established.')
                state['synthetic_automatic_stop_fault']['exact_exception_chain_verified']=True
                state['stage']='actual wrong/exact UUID recovery controls'
                hold=status['hold_id'];saved=Path(status['snapshot']);snapshot=obj(saved/'snapshot.json')
                if snapshot['controller_python']!=str(new_python) or snapshot['controller_checkout']!=str(target):raise RuntimeError('Recorded recovery binding differs.')
                forward_binding=read(root/'state/previous-release.json')
                def fingerprint():
                    names=('device.json','state/service-transaction.json','state/upgrade-status.json','state/previous-release.json','state/service-control-status.json')
                    native=observe('wrong UUID no-write native projection')
                    return {'files':{n:digest(read(root/n)) if (root/n).exists() else None for n in names},
                        'app':{n:digest(v) for n,v in app_bytes(root/'app').items()},'clients':{str(p):digest(read(p)) for p in original_clients},
                        'history':{p.name:digest(read(p)) for p in (root/'state/upgrade-history').glob('*.json')},
                        'snapshot':{p.relative_to(saved).as_posix():digest(read(plain(p))) for p in saved.rglob('*') if p.is_file()},
                        'lock_exists':(root/'state/update.lock').exists(),'import_cache_exists':(root/'state/import-cache').exists(),
                        'native':{n:native.get(n) for n in ('definition_sha256','enabled','loaded','main_pid','main_lifetime','invocation_id')}}
                before=fingerprint();wrong=uuid.uuid4().hex
                wrong_result=command([new_python,'-I','-B',target/'update.py','--root',root,'--recover','--hold-id',wrong],updater.cli_environment(root),cwd=target,timeout=None)
                if wrong_result.returncode!=1 or 'Recovery UUID/root mismatch; no changes made.' not in wrong_result.stderr or fingerprint()!=before:
                    raise RuntimeError('Actual wrong-UUID no-write refusal differs.')
                exact=command([new_python,'-I','-B',target/'update.py','--root',root,'--recover','--hold-id',hold],updater.cli_environment(root),cwd=target,timeout=None)
                if exact.returncode or obj(root/'state/upgrade-status.json')['status']!='recovered':raise RuntimeError('Actual exact-UUID recovery did not verify.')
                if (root/'state/previous-release.json').exists():raise RuntimeError('Explicit recovery did not restore the original absent previous binding.')
                state['explicit_recovery']={'wrong_uuid_exit':wrong_result.returncode,'exact_uuid_exit':exact.returncode,
                    'wrong_uuid_no_write_projection_sha256':digest(json.dumps(before,sort_keys=True).encode()),
                    'wrong_uuid_projection':'Selected state/control files, app, clients, history, sealed snapshot files, lock/cache presence and native definition/loaded/enabled/PID/lifetime/invocation only; excludes volatile logs/heartbeats.',
                    'inner_service_argv_instrumented':False,'original_previous_binding_restored':True,
                    'original_previous_binding_absent':True,'interrupted_forward_binding_sha256':digest(forward_binding)}
            if read(root/'device.json')!=original_device or app_bytes(root/'app')!=original_app or any(read(p)!=v for p,v in original_clients.items()):
                raise RuntimeError('Exact baseline app/device/selected-client restoration differs.')
            if (root/'state/previous-release.json').exists():raise RuntimeError('Original absent previous metadata was not restored.')
            recovered_status=obj(root/'state/upgrade-status.json')['recovery']
            if recovered_status['start']['heartbeat_verified'] is not False or recovered_status['finish']['status']!='transaction released':
                raise RuntimeError('Legacy heartbeat/transaction completion scope differs.')
        if (root/'state/service-transaction.json').exists():raise RuntimeError('A live transaction remains; preserve it.')
        state['stage']='final actual MCP health/read and preservation'
        wanted=args.candidate_tag.removeprefix('v') if args.mode=='forward' else '0.1.7'
        final_health=updater.wait_health(new_python,root,wanted,checkout=target);final_native=observe('final actual product prefix/argv')
        expected_python=new_python if args.mode=='forward' else old_python
        if not final_native.get('arguments') or final_native['arguments'][0]!=str(expected_python):
            raise RuntimeError('Final native manager interpreter differs from the selected release.')
        if args.mode!='forward':
            if final_native['enabled']!=first['enabled'] or final_native['loaded']!=first['loaded']:
                raise RuntimeError('Rollback/recovery native enablement differs from the original registration.')
            if final_native['definition_sha256']!=original_native_sha:
                raise RuntimeError('Rollback/recovery native definition differs from the sealed original registration.')
            state['original_native_definition_restored']=True
            state['original_native_enablement_restored']=True
            state['original_previous_binding_restored']=True
        observed_read=updater.controller(new_python,target,root,'read','01-Projects/Source.md')
        marker=note_before.decode().splitlines()[1]
        if digest(note_before)!=digest(read(note)) or marker not in json.dumps(observed_read,ensure_ascii=False):raise RuntimeError('Original note/MCP read did not verify.')
        if any(digest(read(root/'state'/n))!=h for n,h in auth_before.items()):raise RuntimeError('Authentication bytes changed.')
        state.update(final_health_version=final_health['version'],note_and_auth_preserved=True,named_client_acceptance='Synthetic selected-file refresh/restoration only; no actual GUI reload.',update_error_type=update_error)
        success=True
    except DiagnosticPreabsenceComplete:
        success=True
    except BaseException as error:
        state['error_type']=type(error).__name__
        state['failure_scope']='Preserve current fixture, loaded definition, transactions and private environments. No guessed cleanup or PID kill.'
    finally:
        try:
            if success and created and updater is not None and not (root/'state/service-transaction.json').exists():
                state['stage']='conditional exact native fixture teardown'
                stopped=updater.controller(target/'.venv/bin/python',target,root,'service','stop','--wait-seconds','300')
                value=observe('verified stopped fixture before conditional file deletion')
                if (stopped.get('status')!='stopped' or not stopped.get('empty_at_filetime')
                    or stopped.get('native',{}).get('native_stopped') is not True or stopped.get('task_disabled') is not True
                    or value['enabled'] is not False or not value['quiescent'] or value['runtime_role_count']):
                    raise RuntimeError('No exact stopped/empty manager proof; preserve registration.')
                # Single-actor disposable fixture; no hostile same-UID CAS claim.
                code='''import hashlib,json,sys,time
sys.path.insert(0,sys.argv[1])
import brain_service as s,brain_linux_registration as m
s._qualified_native_platform()
r=s._linux_registration();r._deadline=time.monotonic()+35
v=r.inspect();token=r._manager();r._disk_boundary()
expected=next(iter(m.definitions(r.unit,s.cfg.ROOT,[sys.argv[2]])))
if (v['enabled'] is not False or not v['quiescent'] or v.get('needs_reload')
    or (v['loaded'] and (v['active_state']!='inactive' or v['main_pid']!=0
        or v['control_pid']!=0 or v['job']!=[0,'/'])) or not r._cgroup_empty(r.group)):
 raise RuntimeError('native-cleanup-state-changed-or-failed-unit-preserved')
before=r._read_unit()
if (before is None or before[0]!=expected or hashlib.sha256(before[0]).hexdigest()!=sys.argv[3]
    or r._read_unit()!=before or r._manager()!=token):raise RuntimeError('native-definition-CAS-refused')
r._links(False)
r.path.unlink()
if r._manager()!=token:raise RuntimeError('native-manager-changed-before-reload')
r._reload()
deadline=time.monotonic()+10
while True:
 if r._manager()!=token or r._read_unit() is not None:raise RuntimeError('native-manager-or-file-changed-after-reload')
 units=r._result(r._method('ListUnits'),'a(ssssssouso)')[0]
 files=r._result(r._method('ListUnitFilesByPatterns','asas','0','1',r.unit),'a(ss)')[0]
 if not any(row[0]==r.unit for row in units) and not files:break
 if time.monotonic()>=deadline:raise RuntimeError('native-unit-garbage-collection-not-observed')
 time.sleep(.1)
r._disk_boundary()
paths=[r.directory/'default.target.wants'/r.unit,r.directory/(r.unit+'.d')]
if any(path.exists() or path.is_symlink() for path in paths) or any(r.directory.glob('.second-brain-*.tmp')):
 raise RuntimeError('native-cleanup-artifact-remains')
after=r.inspect()
if after['file_present'] or after['loaded'] or after['enabled'] is not None or not after['quiescent']:
 raise RuntimeError('native-cleanup-not-verified')
print(json.dumps({'exact_owned_definition_removed':True,'manager_disabled_inactive_cgroup_empty':True,
 'post_reload_registry_absence_verified':True,'pre_delete_expected_definition_sha256':sys.argv[3]}))
'''
                expected_python=target/'.venv/bin/python' if args.mode=='forward' else old_python
                result=support.run([*support.isolated_python(sys.executable,root),'-c',code,str(target/'app'),str(expected_python),value['definition_sha256']],
                    source_app=target/'app',env=updater.cli_environment(root),capture_output=True,text=True,encoding='utf-8',timeout=60,check=False)
                if result.returncode:raise RuntimeError('Conditional exact native definition removal refused.')
                state['cleanup']=json.loads(result.stdout)
                observe('postcleanup exact unit absence control',preabsence=True)
                state['cleanup']['postcleanup_absence_verified']=True
        except BaseException as error:state['cleanup']['error_type']=type(error).__name__;success=False
        try:
            for checkout,payload in ((old,old_payload),(target,new_payload)):
                if payload is not None and any(digest(read(checkout/n))!=h for n,h in payload.items()):
                    state['source_payload_changed']=True;success=False
        except BaseException as error:state['source_final_check_error']=type(error).__name__;success=False
        try:
            for name,path in (('uv',uv),('git',tool)):
                if name in state.get('tools',{}) and digest(read(path))!=state['tools'][name]['sha256']:
                    state['tool_bytes_changed']=True;success=False
        except BaseException as error:state['tool_final_check_error']=type(error).__name__;success=False
        os.environ.clear();os.environ.update(original_env);os.chdir(original_cwd)
        state['ended_at']=time.time();state['status']='read-only native preabsence observed' if success and not created else 'needs review'
        if state['status']=='read-only native preabsence observed':state['stage']='diagnostic complete; no native unit installation'
        write_new(receipt,(json.dumps(state,indent=2)+'\n').encode())
        print(json.dumps({'status':state['status'],'mode':args.mode,'receipt':str(receipt),'error_type':state.get('error_type'),'cleanup':state['cleanup']}))
    return 0 if state['status']=='read-only native preabsence observed' else 1


if __name__=='__main__':raise SystemExit(main())
