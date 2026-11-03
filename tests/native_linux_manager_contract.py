"""Disposable Ubuntu CI systemd-user contract observations, not product acceptance.

Run only as the fresh ordinary account made by the companion CI harness. The
required attestation-only mode creates no UUID unit. Historical fixture code is
unreachable until a separately reviewed CLI change. No notes, credentials,
pre-existing owner manager, process-ID signals, provider or product service is used.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid


MANAGER='/org/freedesktop/systemd1'
INTERFACE='org.freedesktop.systemd1'
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C','SYSTEMD_COLORS':'0','SYSTEMD_PAGER':''}
UNIT_KEYS=('Id','Names','Following','FragmentPath','SourcePath','DropInPaths','LoadState','Transient',
    'NeedDaemonReload','UnitFileState','ActiveState','SubState','InvocationID','Job','TriggeredBy','UpheldBy',
    'BoundBy','RequiredBy','OnFailureOf','OnSuccessOf','WantedBy')
DEPENDENCY_KEYS=('Requires','Requisite','Wants','BindsTo','PartOf','Upholds','Conflicts',
                 'OnFailure','OnSuccess','Triggers','PropagatesStopTo')
UNIT_KEYS=UNIT_KEYS+DEPENDENCY_KEYS
SERVICE_KEYS=('Type','ExecStart','ExecStartEx','ExecStartPre','ExecStartPost','ExecCondition','ExecReload',
    'ExecStop','ExecStopPost','Restart','RestartUSec','KillMode','KillSignal','SendSIGKILL','TimeoutStopUSec',
    'Environment','EnvironmentFiles','PassEnvironment','UnsetEnvironment','WorkingDirectory','RootDirectory',
    'RootImage','User','Group','PAMName','ExecSearchPath','DynamicUser','Slice','ControlGroup','MainPID','ControlPID',
    'Result','ExecMainCode','ExecMainStatus','NRestarts')


def protected(path):
    resolved=Path(path).resolve(strict=True)
    for part in (resolved,*resolved.parents):
        info=part.stat()
        if info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('unprotected-native-tool')
    if not resolved.is_file():raise RuntimeError('missing-native-tool')
    return str(resolved)


def bus(tool,runtime,destination,obj,interface,method,signature=None,*arguments,socket_path=None):
    argv=[tool,'--address=unix:path='+str(socket_path or runtime/'bus'),'--auto-start=no',
          '--allow-interactive-authorization=no','--json=short','--timeout=10s',
          'call',destination,obj,interface,method]
    if signature is not None:argv.extend((signature,*arguments))
    result=subprocess.run(argv,env={**ENV,'XDG_RUNTIME_DIR':str(runtime)},stdin=subprocess.DEVNULL,
                          capture_output=True,timeout=15,check=False)
    if result.returncode:return {'available':False,'exit_code':result.returncode}
    if len(result.stdout)>2_000_000:raise RuntimeError('bus-response-exceeds-bound')
    if not result.stdout.strip():return {'available':True,'type':'','data':[]}
    value=json.loads(result.stdout)
    if not isinstance(value,dict) or set(value)!= {'type','data'}:raise RuntimeError('unknown-bus-json-shape')
    return {'available':True,**value}


def result(value,signature):
    if not value.get('available') or value.get('type')!=signature:raise RuntimeError('required-bus-contract-unavailable')
    return value['data']


def properties(call,obj,interface):
    value=result(call(obj,'org.freedesktop.DBus.Properties','GetAll','s',interface),'a{sv}')
    if not isinstance(value,list) or len(value)!=1 or not isinstance(value[0],dict):
        raise RuntimeError('unknown-getall-shape')
    return value[0]


def selected(props,keys):
    answer={key:props[key] if key in props else {'missing':True} for key in keys}
    for key in ('Environment','UnsetEnvironment'):
        if key in answer and 'data' in answer[key]:
            answer[key]={'type':answer[key]['type'],
                'names_only':[entry.partition('=')[0] for entry in answer[key]['data']]}
    return answer


def value(props,key):
    if key not in props:raise RuntimeError('required-loaded-property-missing')
    return props[key]['data']


def loaded_snapshot(call,obj):
    unit=properties(call,obj,INTERFACE+'.Unit')
    service=properties(call,obj,INTERFACE+'.Service')
    return unit,service,{'unit':selected(unit,UNIT_KEYS),'service':selected(service,SERVICE_KEYS)}


def cgroup_observation(path):
    if ((not path.startswith('/user.slice/') and path not in
         ('/system.slice/dbus.service','/system.slice/dbus-broker.service')) or '..' in path.split('/')):
        raise RuntimeError('invalid-cgroup-observation-path')
    actual=Path('/sys/fs/cgroup')/path.lstrip('/')
    try:
        raw=(actual/'cgroup.events').read_bytes()
    except FileNotFoundError:return {'present':False,'populated':False}
    if len(raw)>1024:raise RuntimeError('cgroup-events-exceeds-bound')
    entries=dict(line.split() for line in raw.decode('ascii').splitlines())
    return {'present':True,'populated':entries.get('populated')=='1'}


def stopped(call,obj,group):
    end=time.monotonic()+15
    while time.monotonic()<end:
        unit,service,snapshot=loaded_snapshot(call,obj)
        if (value(unit,'ActiveState')=='inactive' and value(unit,'SubState')=='dead'
            and value(unit,'Job')==[0,'/'] and value(service,'MainPID')==0
            and value(service,'ControlPID')==0 and not cgroup_observation(group)['populated']):
            return unit,service,snapshot
        time.sleep(.1)
    raise RuntimeError('synthetic-unit-stop-not-empty')


def typed(props,key,signature):
    item=props.get(key)
    if not isinstance(item,dict) or set(item)!={'type','data'} or item['type']!=signature:
        raise RuntimeError('attestation-property-contract-unavailable')
    return item['data']


def peer_identity(raw,uid,pid=None):
    peer=result(raw('org.freedesktop.DBus','/org/freedesktop/DBus',
                    'org.freedesktop.DBus','GetNameOwner','s',INTERFACE),'s')[0]
    if not re.fullmatch(r':[0-9]+\.[0-9]+',peer):raise RuntimeError('manager-unique-name-unavailable')
    credentials=result(raw('org.freedesktop.DBus','/org/freedesktop/DBus',
        'org.freedesktop.DBus','GetConnectionCredentials','s',peer),'a{sv}')[0]
    actual=typed(credentials,'ProcessID','u')
    if (typed(credentials,'UnixUserID','u')!=uid or type(actual) is not int or not 0<actual<0x80000000
        or (pid is not None and actual!=pid)):
        raise RuntimeError('manager-peer-credentials-mismatch')
    return peer,actual,{key:item.get('type') for key,item in credentials.items()}


def root_manager_attestation(tool,runtime,uid,prefix,report):
    """Read-only PID1 attestation; active same-UID/root adversaries are out of scope."""
    report['stage']='system-bus-preconnect'
    groups=[path for path in ('/system.slice/dbus.service','/system.slice/dbus-broker.service')
            if cgroup_observation(path)['populated']]
    if not groups:raise RuntimeError('preexisting-system-bus-not-populated-no-connect')
    socket_path=Path('/run/dbus/system_bus_socket')
    info=socket_path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0:
        raise RuntimeError('system-bus-not-root-owned-socket')
    for directory in socket_path.parents:
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise RuntimeError('system-bus-ancestor-not-protected')
    raw_system=lambda dest,obj,interface,method,sig=None,*argv:bus(
        tool,runtime,dest,obj,interface,method,sig,*argv,socket_path=socket_path)
    raw_user=lambda dest,obj,interface,method,sig=None,*argv:bus(tool,runtime,dest,obj,interface,method,sig,*argv)
    report['stage']='system-manager-peer'
    sys_peer,_,sys_credentials=peer_identity(raw_system,0,1)
    system=lambda obj,interface,method,sig=None,*argv:raw_system(sys_peer,obj,interface,method,sig,*argv)
    variant=result(system(MANAGER,'org.freedesktop.DBus.Properties','Get','ss',INTERFACE+'.Manager','Version'),'v')[0]
    version=typed({'Version':variant},'Version','s')
    release=Path(protected('/etc/os-release')).read_bytes()
    if len(release)>65536:raise RuntimeError('os-release-exceeds-bound')
    os_release={key:val.strip('"') for key,val in (line.partition('=')[::2]
        for line in release.decode('utf-8').splitlines()) if key in ('ID','VERSION_ID')}
    if not isinstance(version,str) or not version.startswith('255.') or os_release!={'ID':'ubuntu','VERSION_ID':'24.04'}:
        raise RuntimeError('attestation-host-outside-ubuntu2404-systemd255')
    name='user@'+str(uid)+'.service'
    obj=result(system(MANAGER,INTERFACE+'.Manager','GetUnit','s',name),'o')[0]
    def snapshot():
        unit=properties(system,obj,INTERFACE+'.Unit')
        service=properties(system,obj,INTERFACE+'.Service')
        report.setdefault('root_user_unit_s1_shape',{
            'unit':selected(unit,('Id','LoadState','ActiveState','SubState','Job','InvocationID','FragmentPath','DropInPaths')),
            'service':selected(service,('User','Type','ControlGroup','MainPID','ExecMainPID','RootDirectory','RootImage','ExecStart'))})
        for key,expected in (('Id',name),('LoadState','loaded'),('ActiveState','active'),('SubState','running')):
            if typed(unit,key,'s')!=expected:raise RuntimeError('root-user-unit-not-loaded-active')
        invocation=typed(unit,'InvocationID','ay')
        if (not isinstance(invocation,list) or len(invocation)!=16 or not any(invocation)
            or any(type(item) is not int or not 0<=item<=255 for item in invocation)
            or typed(unit,'Job','(uo)')!=[0,'/']):raise RuntimeError('root-user-unit-invocation-not-idle')
        main=typed(service,'MainPID','u')
        if (type(main) is not int or not 1<main<0x80000000 or typed(service,'ExecMainPID','u')!=main
            or typed(service,'User','s')!=str(uid) or typed(service,'ControlGroup','s')!=prefix
            or typed(service,'RootDirectory','s') or typed(service,'RootImage','s')):
            raise RuntimeError('root-user-unit-process-context-mismatch')
        execute=typed(service,'ExecStart','a(sasbttttuii)')
        expected='/usr/lib/systemd/systemd'
        if (not isinstance(execute,list) or len(execute)!=1 or not isinstance(execute[0],list)
            or len(execute[0])!=10 or execute[0][:3]!=[expected,[expected,'--user'],False]):
            raise RuntimeError('root-user-unit-executable-context-mismatch')
        protected(expected)
        proof={'invocation':invocation,'main_pid':main,'exec_main_pid':typed(service,'ExecMainPID','u'),
               'active':typed(unit,'ActiveState','s'),'sub':typed(unit,'SubState','s'),
               'job':typed(unit,'Job','(uo)'),'execute':execute}
        observed={'unit':selected(unit,('Id','LoadState','ActiveState','SubState','Job','InvocationID','FragmentPath','DropInPaths')),
                  'service':selected(service,('User','Type','ControlGroup','MainPID','ExecMainPID','RootDirectory','RootImage','ExecStart')),
                  'exec_pid_matches_main':execute[0][7]==main}
        return proof,observed
    report['stage']='user-unit-s1'
    first,observed=snapshot()
    report['stage']='user-peer-bind'
    user_peer,pid,user_credentials=peer_identity(raw_user,uid,first['main_pid'])
    if (Path('/proc')/str(pid)/'cgroup').read_text().splitlines()!=['0::'+prefix+'/init.scope']:
        raise RuntimeError('manager-peer-wrong-cgroup')
    report['stage']='user-unit-s2'
    second,_=snapshot()
    if first!=second:raise RuntimeError('root-user-unit-invocation-changed')
    report['stage']='bracket-close'
    for raw,peer in ((raw_system,sys_peer),(raw_user,user_peer)):
        owner=result(raw('org.freedesktop.DBus','/org/freedesktop/DBus',
                        'org.freedesktop.DBus','GetNameOwner','s',INTERFACE),'s')[0]
        if owner!=peer:raise RuntimeError('attested-manager-owner-changed')
    report['root_attestation']={'scope':'root/OS trusted; active same-UID adversary out of scope',
        'system_version':version,'os_release':os_release,'system_peer':sys_peer,'user_peer':user_peer,
        'system_credential_types':sys_credentials,'user_credential_types':user_credentials,
        'snapshot':observed,'snapshots_equal':True,'system_bus_groups':groups}
    return raw_user,user_peer,pid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-user',required=True)
    parser.add_argument('--renderer-file',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--attestation-only',action='store_true',required=True)
    args=parser.parse_args()
    uid=os.geteuid();account=pwd.getpwuid(uid);home=Path(account.pw_dir)
    receipt=args.receipt.resolve()
    if receipt.exists() or not receipt.is_relative_to(home):raise RuntimeError('create-only-own-home-receipt-required')
    report={'status':'incomplete','scope':'disposable native systemd-user contract observations only',
            'stage':'preflight','recorded_at':time.time(),'unit_created':False,'unavailable':[]}
    path=None;definition=None;call=None;unit_name=None;obj=None;execution_validated=False;group=None
    try:
        if (sys.platform!='linux' or uid==0 or account.pw_name!=args.expected_user
            or not re.fullmatch('sbci[0-9]+',account.pw_name)
            or os.environ.get('GITHUB_ACTIONS')!='true' or not sys.flags.isolated
            or not sys.dont_write_bytecode or sys.flags.optimize):raise RuntimeError('disposable-ci-ordinary-user-required')
        runtime=Path('/run/user')/str(uid)
        if os.environ.get('XDG_RUNTIME_DIR')!=str(runtime):raise RuntimeError('wrong-runtime-directory')
        info=runtime.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&0o077:
            raise RuntimeError('runtime-directory-not-private')
        prefix='/user.slice/user-'+str(uid)+'.slice/user@'+str(uid)+'.service'
        socket_info=(runtime/'bus').lstat()
        if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid!=uid:
            raise RuntimeError('runtime-bus-not-owned-socket')
        bus_groups=[prefix+'/'+slice_name+'/'+unit for slice_name in ('session.slice','app.slice')
                    for unit in ('dbus.service','dbus-broker.service')
                    if cgroup_observation(prefix+'/'+slice_name+'/'+unit).get('populated')]
        if not bus_groups:
            raise RuntimeError('preexisting-bus-not-populated-no-connect')
        report['preexisting_bus_groups']=bus_groups
        report['stage']='native-tool-proof'
        tool=protected('/usr/bin/busctl')
        raw_call,peer,manager_pid=root_manager_attestation(tool,runtime,uid,prefix,report)
        call=lambda obj,interface,method,sig=None,*argv:raw_call(peer,obj,interface,method,sig,*argv)
        report['stage']='manager-properties'
        manager=properties(call,MANAGER,INTERFACE+'.Manager')
        report['stage']='source-provenance'
        report.update(uid=uid,os=platform.platform(),python=sys.version,manager_peer='consistent with unique bus owner',
            manager=selected(manager,('Version','ControlGroup','UnitPath','Environment','NJobs','SystemState')),
            renderer_sha256=hashlib.sha256(args.renderer_file.read_bytes()).hexdigest(),
            probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        report['stage']='manager-controlgroup-unitpath'
        if value(manager,'ControlGroup')!=prefix:raise RuntimeError('manager-controlgroup-mismatch')
        directory=home/'.config/systemd/user'
        if str(directory) not in value(manager,'UnitPath'):raise RuntimeError('home-unit-directory-not-in-search-path')
        if args.attestation_only:
            report.update(status='attestation observed; not qualification',stage='finished',attestation_observed=True)
            return
        report['stage']='manager-introspect'
        introspection=result(call(MANAGER,'org.freedesktop.DBus.Introspectable','Introspect'),'s')[0]
        methods=('LoadUnit','ListUnitFilesByPatterns','GetUnitFileLinks','EnableUnitFiles','DisableUnitFiles',
                 'StartUnit','StopUnit','Reload')
        report['manager_methods']={name:bool(re.search(r'<method\s+name="'+name+r'"',introspection)) for name in methods}
        report['stage']='fixture-materialize'
        unit_name='second-brain-ci-'+uuid.uuid4().hex+'.service'
        root=home/('sb-contract-'+uuid.uuid4().hex)
        (root/'app').mkdir(parents=True,mode=0o700);(root/'state').mkdir(mode=0o700)
        script=root/'app/brain_supervisor.py'
        script.write_text('import json,os,signal,socket,time\nfrom pathlib import Path\n'
            'def stop(*_):\n Path('+repr(str(root/'state/term'))+').write_text("term");raise SystemExit(0)\n'
            'signal.signal(signal.SIGTERM,stop)\n'
            's=socket.socket();s.bind(("127.0.0.1",0));s.listen(1)\n'
            'p=Path('+repr(str(root/'state/ready.json'))+')\n'
            't=p.with_suffix(".tmp");t.write_text(json.dumps({"pid":os.getpid(),"port":s.getsockname()[1]}));os.replace(t,p)\n'
            'time.sleep(45)\ns.close()\n',encoding='utf-8')
        report['stage']='renderer-extract'
        source=args.renderer_file.read_text(encoding='utf-8')
        functions=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='definition']
        if len(functions)!=1:raise RuntimeError('renderer-definition-not-unique')
        function=functions[0]
        namespace={'cfg':SimpleNamespace(ROOT=root,PYTHON=Path(sys.executable)),
                   'LINUX_UNIT':unit_name,'sys':sys,'os':os}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<exact-linux-renderer>','exec'),namespace)
        definition=namespace['definition']('linux').encode()
        report['renderer_function_sha256']=hashlib.sha256(ast.get_source_segment(source,function).encode()).hexdigest()
        report['unit']=unit_name
        report['stage']='uuid-preregistration-check'
        units=result(call(MANAGER,INTERFACE+'.Manager','ListUnits'),'a(ssssssouso)')[0]
        unit_files=call(MANAGER,INTERFACE+'.Manager','ListUnitFilesByPatterns','asas','0','1',unit_name)
        files=result(unit_files,'a(ss)')[0]
        if any(row[0]==unit_name for row in units) or files:raise RuntimeError('uuid-unit-name-already-registered')
        report['before']={'loaded_matches':0,
            'unit_files':unit_files,
            'file_links':call(MANAGER,INTERFACE+'.Manager','GetUnitFileLinks','sb',unit_name,'false')}
        report['stage']='create-owned-definition'
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/unit_name
        with path.open('xb') as output:output.write(definition)
        report['unit_created']=True;report['definition_sha256']=hashlib.sha256(definition).hexdigest()
        report['stage']='load-owned-definition'
        obj=result(call(MANAGER,INTERFACE+'.Manager','LoadUnit','s',unit_name),'o')[0]
        unit,service,snapshot=loaded_snapshot(call,obj);report['loaded']=snapshot
        if (value(unit,'Id')!=unit_name or value(unit,'FragmentPath')!=str(path)
            or value(unit,'LoadState')!='loaded' or value(unit,'NeedDaemonReload') or value(unit,'Transient')
            or value(unit,'DropInPaths') or value(unit,'SourcePath')
            or any(value(unit,k) for k in ('Requisite','BindsTo','PartOf','Upholds','OnFailure','OnSuccess','Triggers','PropagatesStopTo'))
            or any(not n.endswith(('.slice','.target')) for k in ('Requires','Wants') for n in value(unit,k))
            or value(service,'Type')!='simple' or value(service,'EnvironmentFiles')
            or any(value(service,key) for key in ('ExecStartPre','ExecStartPost','ExecCondition','ExecReload','ExecStop','ExecStopPost'))
            or len(value(service,'ExecStart'))!=1
            or value(service,'ExecStart')[0][:3]!=[str(sys.executable),[str(sys.executable),str(script)],False]):
            raise RuntimeError('loaded-fixture-does-not-match-safe-known-execution')
        execution_validated=True
        report['stage']='enable'
        report['enable']=call(MANAGER,INTERFACE+'.Manager','EnableUnitFiles','asbb','1',unit_name,'false','false')
        result(report['enable'],'ba(sss)')
        report['enable_symlinks']=[change[1] for change in result(report['enable'],'ba(sss)')[1] if change[0]=='symlink']
        report['enabled_links']=call(MANAGER,INTERFACE+'.Manager','GetUnitFileLinks','sb',unit_name,'false')
        report['stage']='start'
        report['start']=call(MANAGER,INTERFACE+'.Manager','StartUnit','ss',unit_name,'replace')
        result(report['start'],'o')
        end=time.monotonic()+10
        while time.monotonic()<end:
            unit,service,snapshot=loaded_snapshot(call,obj)
            if value(unit,'ActiveState')=='active' and (root/'state/ready.json').exists():break
            time.sleep(.1)
        else:raise RuntimeError('synthetic-supervisor-not-ready')
        report['active']=snapshot
        group=value(service,'ControlGroup')
        expected_group=prefix+'/'+value(service,'Slice')+'/'+unit_name
        if group!=expected_group:raise RuntimeError('synthetic-unit-unexpected-cgroup')
        ready=json.loads((root/'state/ready.json').read_text())
        if value(service,'MainPID')!=ready['pid']:raise RuntimeError('synthetic-unit-wrong-main-pid')
        report['active_cgroup']=cgroup_observation(group)
        report['stage']='disable-before-stop'
        report['disable']=call(MANAGER,INTERFACE+'.Manager','DisableUnitFiles','asb','1',unit_name,'false')
        result(report['disable'],'a(sss)')
        report['disabled_links']=call(MANAGER,INTERFACE+'.Manager','GetUnitFileLinks','sb',unit_name,'false')
        unit,service,snapshot=loaded_snapshot(call,obj);report['disabled_active']=snapshot
        if value(unit,'ActiveState')!='active' or value(service,'MainPID')!=ready['pid']:
            raise RuntimeError('synthetic-unit-not-active-before-stop')
        report['stage']='stop'
        report['stop']=call(MANAGER,INTERFACE+'.Manager','StopUnit','ss',unit_name,'replace')
        result(report['stop'],'o')
        unit,service,snapshot=stopped(call,obj,group)
        if value(service,'Result')!='success' or value(service,'NRestarts')!=0:
            raise RuntimeError('synthetic-unit-stop-not-successful')
        if not (root/'state/term').is_file():raise RuntimeError('manager-term-not-observed')
        report['stopped']=snapshot;report['stopped_cgroup']=cgroup_observation(group)
        report['stage']='reload'
        started=time.monotonic();report['reload']=call(MANAGER,INTERFACE+'.Manager','Reload')
        result(report['reload'],'')
        report['reload_ms']=round((time.monotonic()-started)*1000,3)
        after=properties(call,MANAGER,INTERFACE+'.Manager')
        report['manager_after_reload']=selected(after,('SystemState','NJobs','NInstalledJobs','NFailedUnits'))
        if value(after,'SystemState')!='running' or value(after,'NJobs')!=0:
            raise RuntimeError('manager-not-idle-after-reload')
        report.update(status='observations completed; not product qualification',stage='finished')
    except Exception as error:
        report['error_type']=type(error).__name__
        if isinstance(error,OSError):report['error_errno']=error.errno
        if type(error) is RuntimeError:report['fixed_error']=str(error)
    finally:
        if report['unit_created'] and call is not None and execution_validated:
            try:
                report['cleanup_disable']=call(MANAGER,INTERFACE+'.Manager','DisableUnitFiles','asb','1',unit_name,'false')
                result(report['cleanup_disable'],'a(sss)')
                report['cleanup_stop']=call(MANAGER,INTERFACE+'.Manager','StopUnit','ss',unit_name,'replace')
                result(report['cleanup_stop'],'o')
                if group is None:
                    _,service,_=loaded_snapshot(call,obj)
                    group=prefix+'/'+value(service,'Slice')+'/'+unit_name
                stopped(call,obj,group)
                if path.read_bytes()!=definition:raise RuntimeError('fixture-definition-changed-preserve')
                path.unlink();report['cleanup_definition_removed']=True
                report['cleanup_reload']=call(MANAGER,INTERFACE+'.Manager','Reload')
                result(report['cleanup_reload'],'')
                files=result(call(MANAGER,INTERFACE+'.Manager','ListUnitFilesByPatterns','asas','0','1',unit_name),'a(ss)')[0]
                if files:raise RuntimeError('cleanup-unit-file-still-registered')
                if any(os.path.lexists(link) for link in report.get('enable_symlinks',[])):
                    raise RuntimeError('cleanup-enable-symlink-remains')
                if any(p.name==unit_name for p in directory.rglob('*')):
                    raise RuntimeError('cleanup-unit-name-remains')
                rows=result(call(MANAGER,INTERFACE+'.Manager','ListUnits'),'a(ssssssouso)')[0]
                if any(row[0]==unit_name and (row[2],row[3])!=('not-found','inactive') for row in rows):
                    raise RuntimeError('cleanup-unit-still-loaded')
                owner=result(raw_call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                                     'GetNameOwner','s',INTERFACE),'s')[0]
                if owner!=peer:raise RuntimeError('manager-owner-changed')
                report['cleanup_verified']=True
            except Exception as error:report['cleanup_error_type']=type(error).__name__
        for label in ('before','enabled_links','disabled_links'):
            observations=report.get(label,{})
            if label=='before':
                if report.get('manager_methods',{}).get('GetUnitFileLinks') is not False:continue
                observations=observations.get('file_links',{})
            if observations.get('available') is False:report['unavailable'].append(label+'-file-links')
        if (not report.get('cleanup_verified') and not report.get('attestation_observed')) or report['unavailable']:
            report['status']='incomplete'
        report['finished_at']=time.time()
        text=json.dumps(report,indent=2)
        with receipt.open('x',encoding='utf-8') as output:output.write(text)
        print(json.dumps({'status':report['status'],'stage':report['stage'],'receipt':str(receipt)}))
        if report['status']=='incomplete':raise SystemExit(1)


if __name__=='__main__':main()
