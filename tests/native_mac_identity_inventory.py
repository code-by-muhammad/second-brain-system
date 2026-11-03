"""Disposable CI measurements only: interpreter shape, descriptor rows, foreign UID visibility.

No service/client imports, launchd writes, credentials, foreign connections or PID
signals. Foreign witnesses have a self-enforced alarm; the parent only closes
stdin and waits for the subprocess it created. Never run on an owner machine.
"""
import argparse
import ast
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import select
import socket
import stat
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

ROOT=Path(__file__).resolve().parents[1]
MINIMAL={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'}
CHILD=r'''
import json,os,signal,socket,sys,sysconfig
signal.alarm(40)
mode=sys.argv[1]
listener=None
if mode=='listener':
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
value={'pid':os.getpid(),'uid':os.getuid(),'ppid':os.getppid(),
    'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,
    'base_executable':getattr(sys,'_base_executable',None),'orig_argv':getattr(sys,'orig_argv',sys.argv),
    'framework':sysconfig.get_config_var('PYTHONFRAMEWORK'),
    'isolated':sys.flags.isolated,'no_site':sys.flags.no_site}
if listener:value['port']=listener.getsockname()[1]
print(json.dumps(value),flush=True)
sys.stdin.readline()
if listener:listener.close()
'''


class ProbeFailure(RuntimeError):
    def __init__(self,code,**details):
        super().__init__(code);self.details=details


def protected(path):
    path=Path(path)
    if not path.is_absolute():raise RuntimeError('Relative native tool refused')
    for part in (path,*path.parents):
        info=part.lstat()
        reason=('link' if stat.S_ISLNK(info.st_mode) else
                'owner' if info.st_uid!=0 else 'writable-mode' if info.st_mode&0o022 else None)
        if reason:raise ProbeFailure('unprotected-native-tool',component=str(part),reason=reason)
    if not path.is_file():raise RuntimeError('Missing native tool')
    return str(path)


def canonical_tool(path):
    """Resolve an OS launcher alias, then execute only its protected target."""
    path=Path(path)
    if not path.is_absolute():raise RuntimeError('Relative native tool refused')
    for part in path.parents:
        info=part.lstat()
        reason=('link' if stat.S_ISLNK(info.st_mode) else
                'owner' if info.st_uid!=0 else 'writable-mode' if info.st_mode&0o022 else None)
        if reason:raise ProbeFailure('unprotected-launcher-parent',component=str(part),reason=reason)
    if path.lstat().st_uid!=0:
        raise ProbeFailure('unprotected-launcher-alias',component=str(path),reason='owner')
    return protected(path.resolve(strict=True))


def command(argv,*,environment=None,**kwargs):
    return subprocess.run(argv,env=environment or MINIMAL,stdin=subprocess.DEVNULL,capture_output=True,
                          timeout=5,check=True,**kwargs)


def observe_field(process,name):
    try:
        value=getattr(process,name)()
        return {'status':'observed','value':list(value) if isinstance(value,tuple) else value}
    except (psutil.Error,OSError) as error:return {'status':'unavailable','error_type':type(error).__name__}


def witness(argv,env,report,observe):
    child=subprocess.Popen(argv,env=env,cwd='/',stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    record={'invocation_class':report,'wrapper_pid':child.pid,'startup_observed':False,
            'status':'failed','observation_valid':False,'self_alarm_seconds':40}
    try:
        if not select.select([child.stdout],[],[],10)[0]:raise RuntimeError('Witness startup unavailable')
        raw=child.stdout.readline(32769)
        if not raw:raise RuntimeError('Witness startup unavailable')
        if len(raw)>32768:raise RuntimeError('Witness output exceeds bound')
        value=json.loads(raw)
        if type(value.get('pid')) is not int or type(value.get('uid')) is not int:
            raise RuntimeError('Witness identity malformed')
        process=psutil.Process(value['pid'])
        record.update(startup_observed=True,child=value,
            observed={name:observe_field(process,name) for name in ('name','exe','cmdline','ppid','uids')},
            birth_reads=[psutil.Process(value['pid']).create_time() for _ in range(3)])
        value['orig_argv']=[part if part!=CHILD else '<synthetic-child-code>' for part in value['orig_argv']]
        cmd=record['observed']['cmdline']
        if cmd.get('status')=='observed':cmd['value']=[p if p!=CHILD else '<synthetic-child-code>' for p in cmd['value']]
        try:
            reached=[p for p in psutil.Process(value['ppid']).children() if p.pid==value['pid']]
            record['birth_from_parent_children']=reached[0].create_time() if len(reached)==1 else None
        except psutil.Error as error:record['parent_children_error_type']=type(error).__name__
        record['birth_stable']=len(set(record['birth_reads']))==1
        observe(record,process)
        after=psutil.Process(value['pid'])
        record['witness_alive_after_measurement']=(after.is_running()
            and after.status()!=psutil.STATUS_ZOMBIE and after.create_time()==record['birth_reads'][0])
        record['observation_valid']=record['witness_alive_after_measurement']
        record['status']='observed' if record['observation_valid'] else 'invalid-witness-gone'
    except Exception as error:
        record['error_type']=type(error).__name__
        if isinstance(error,ProbeFailure):record['diagnostic']={'code':str(error),**error.details}
        # Only our constant diagnostic messages; never native stderr/argv/addresses.
        if type(error) is RuntimeError:record['fixed_error']=str(error)
    finally:
        if child.stdin:child.stdin.close()
        try:record['wrapper_exit']=child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            record['cleanup']='unverified; self-alarm or disposable CI teardown only; no PID signal'
            record['status']='failed';record['observation_valid']=False
        finally:
            os.set_blocking(child.stderr.fileno(),False)
            try:record['stderr_bytes_observed']=len(os.read(child.stderr.fileno(),32769))
            except BlockingIOError:record['stderr_bytes_observed']=0
            child.stdout.close();child.stderr.close()
    if 'wrapper_exit' in record:
        record['cleanup']='wrapper reaped after stdin close; no external signal'
        if record['wrapper_exit']!=0:record['status']='failed';record['observation_valid']=False
    return record


def identity_cases(folder,bucket):
    # Copy the product environment FUNCTION without importing settings or a service.
    source=(ROOT/'app/brain_settings.py').read_bytes()
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='environment')
    namespace={'os':os,'ROOT':folder/'runtime','STATE':folder/'runtime/state','VAULT':folder/'vault'}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<product environment function>','exec'),namespace)
    env=namespace['environment']()
    script=folder/'runtime/app/brain_server.py'
    script.parent.mkdir(parents=True)
    script.write_text(CHILD.replace("mode=sys.argv[1]","mode='identity'"),encoding='utf-8')
    names=[name for name in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','__PYVENV_LAUNCHER__') if name in env]
    cases=bucket.setdefault('cases',[])
    bucket['environment_function_sha256']=hashlib.sha256(ast.get_source_segment(source.decode(),function).encode()).hexdigest()
    bucket['configured_path_scope']='likely installed venv alias; device configuration was not read'
    choices=[('configured',ROOT/'.venv/bin/python'),('python3-alias',ROOT/'.venv/bin/python3'),
             ('versioned-alias',ROOT/'.venv/bin/python3.12'),('base-auxiliary',Path(sys._base_executable))]
    for label,executable in choices:
        if not executable.is_file():
            cases.append({'case':label,'status':'missing executable'});continue
        metadata=executable.resolve().stat()
        for flags in ([],['-I','-B']):
            record=witness([str(executable),*flags,str(script)],env,
                label+('/no-flags' if not flags else '/isolated-control'),lambda *_:None)
            record.update(case=label,logical_executable=str(executable),resolved_executable=str(executable.resolve()),
                owner_uid=metadata.st_uid,mode=oct(stat.S_IMODE(metadata.st_mode)),ambient_python_variable_names=names)
            # argv source is our known child string, not user text. Keep shape only.
            cases.append(record)
    return bucket


def host_class(value):
    host=value.rpartition('.')[0]
    if host in ('*','::','0.0.0.0'):return 'wildcard'
    try:
        address=ipaddress.ip_address(host.split('%')[0])
        return ('loopback'+str(address.version)) if address.is_loopback else 'other'
    except ValueError:return 'other-or-truncated'


def inventory_observation(module,port):
    started=time.monotonic()
    record={}
    try:
        rows=module.read(port)
        record.update(product_read='observed',matching_listen_count=len(rows),
                      matching_families=[row[0] for row in rows])
    except module.InventoryError:
        record['product_read']='refused'
    record['read_ms']=round((time.monotonic()-started)*1000,3)
    raw=command([protected('/usr/sbin/netstat'),'-an','-p','tcp']).stdout
    if len(raw)>2_000_000:raise RuntimeError('Diagnostic table exceeds bound')
    lines=[line.split() for line in raw.decode('ascii',errors='strict').splitlines() if line.strip()]
    classes={};bad=None
    record['header_positions']=[i for i,row in enumerate(lines) if tuple(row) in module.HEADERS]
    for index,fields in enumerate(lines[2:],2):
        shape=(fields[0] if fields and fields[0] in ('tcp4','tcp6','tcp46') else '<other>',
               fields[-1] if fields and fields[-1] in module.STATES else '<other>',len(fields),
               host_class(fields[3]) if len(fields)>3 else 'missing-address',
               bool(len(fields)>3 and fields[3].rpartition('.')[2]==str(port)))
        classes[str(shape)]=classes.get(str(shape),0)+1
        if bad is None:
            one=(' '.join(module.HEADERS[0])+'\n'+' '.join(module.HEADERS[1])+'\n'+' '.join(fields)+'\n').encode()
            try:module.parse(one,port)
            except module.InventoryError:bad={'index':index,'shape':shape}
    record.update(row_class_counts=classes,first_rejected_data_row=bad,
                  row_classes_scope='second independent netstat snapshot, not product read snapshot')
    return record


def own_socket_cases(module):
    cases=[]
    modes=[('single',False,None),('duplicate-fd',False,None)]
    modes.extend((prefix+'-'+kind,reuse,kind) for prefix,reuse in (('both-opt-in',True),('product-reuseaddr',False))
                 for kind in ('same-address','wildcard','dualstack'))
    for mode,reuse,kind in modes:
        sockets=[]
        record={'case':mode,'first_reuseaddr':True,'first_reuseport':reuse,'status':'incomplete'}
        try:
            first=socket.socket();sockets.append(first)
            first.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            if reuse:first.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,1)
            first.bind(('127.0.0.1',0));first.listen(1);port=first.getsockname()[1]
            record['port']=port
            if mode=='duplicate-fd':sockets.append(socket.socket(fileno=os.dup(first.fileno())))
            elif kind:
                try:
                    family=socket.AF_INET6 if kind=='dualstack' else socket.AF_INET
                    second=socket.socket(family);sockets.append(second)
                    second.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,1)
                    second.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    if family==socket.AF_INET6:second.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,0)
                    address='::' if family==socket.AF_INET6 else ('0.0.0.0' if kind=='wildcard' else '127.0.0.1')
                    second.bind((address,port));second.listen(1);record['second_listener']='bound'
                except OSError as error:record.update(second_listener='refused',bind_errno=error.errno)
            rows=[r for r in psutil.Process().net_connections(kind='tcp') if r.status==psutil.CONN_LISTEN and r.laddr.port==port]
            record.update(own_psutil_listen_rows=len(rows),own_fds=[r.fd for r in rows],
                          global_inventory=inventory_observation(module,port),status='observed')
        except (OSError,RuntimeError) as error:record['error_type']=type(error).__name__
        finally:
            for sock in reversed(sockets):sock.close()
            cases.append(record)
    return cases


def foreign_cases(module,bucket):
    cases=bucket.setdefault('cases',[])
    sudo=protected('/usr/bin/sudo')
    clt=Path('/Library/Developer/CommandLineTools/usr/bin/python3')
    if clt.is_file():launcher=canonical_tool(clt)
    else:
        resolved=command([protected('/usr/bin/xcrun'),'-f','python3'],
            environment={**MINIMAL,'DEVELOPER_DIR':'/Library/Developer/CommandLineTools'}).stdout.decode().strip()
        launcher=canonical_tool(resolved)
    preflight=witness([launcher,'-I','-S','-B','-c',CHILD,'identity'],MINIMAL,
                      'auxiliary-interpreter-preflight',lambda *_:None)
    bucket['auxiliary_preflight']=preflight
    image=preflight.get('observed',{}).get('exe',{})
    if not preflight.get('observation_valid') or image.get('status')!='observed':
        raise ProbeFailure('auxiliary-native-image-unverified')
    interpreter=protected(image['value'])
    bucket.update(auxiliary_launcher=launcher,logical_auxiliary_launcher=str(clt),resolved_auxiliary_interpreter=interpreter,
        sudo_version_line=command([sudo,'-V']).stdout.decode(errors='replace').splitlines()[0],
        sudo_use_pty='not established; child alarm is independent of EOF/pty relay')
    for account in ('nobody','root'):
        argv=[sudo,'-n','-u',account,'--',interpreter,'-I','-S','-B','-c',CHILD,'listener']
        def observe(record,process):
            observed=record['observed']['exe']
            record['witness_executable_observed']=observed.get('status')=='observed'
            if observed.get('status')!='observed' or protected(observed['value'])!=interpreter:
                raise ProbeFailure('foreign-native-image-unverified')
            record['witness_executable_protected']=True
            record['global_inventory']=inventory_observation(module,record['child']['port'])
            try:
                rows=process.net_connections(kind='tcp')
                record['foreign_perprocess']={'status':'observed','matching_listeners':sum(
                    r.status==psutil.CONN_LISTEN and r.laddr.port==record['child']['port'] for r in rows)}
            except psutil.Error as error:record['foreign_perprocess']={'status':'refused','error_type':type(error).__name__}
        record=witness(argv,MINIMAL,'foreign-uid-'+account,observe)
        record['account']=account
        uid_observed=record.get('observed',{}).get('uids',{})
        claimed=record.get('child',{}).get('uid')
        if uid_observed.get('status')!='observed' or uid_observed.get('value',[None])[0]!=claimed:
            record.update(status='failed',observation_valid=False,fixed_error='Witness UID not independently observed')
        if record.get('child',{}).get('uid')==os.getuid():
            record.update(status='failed',observation_valid=False,fixed_error='Witness UID is not foreign')
        cases.append(record)
    return bucket


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-arch',required=True);parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    destination=args.receipt.resolve()
    if (not destination.is_relative_to(ROOT/'reports') or destination.exists()
        or any(p.is_symlink() for p in (args.receipt,*args.receipt.parents))):
        raise RuntimeError('Receipt must be create-only beneath CI reports')
    report={'scope':'native disposable CI observations only; not product lifecycle/security acceptance',
            'status':'incomplete','stage':'preflight','recorded_at':time.time()}
    try:
        if (sys.platform!='darwin' or os.geteuid()==0 or not sys.flags.isolated or not sys.dont_write_bytecode
            or sys.flags.optimize or os.environ.get('GITHUB_ACTIONS')!='true'
            or platform.machine()!=args.expected_arch):raise RuntimeError('Require isolated nonroot disposable native Mac CI')
        report.update(os=platform.platform(),architecture=platform.machine(),uid=os.getuid(),groups=os.getgroups(),
            python=sys.version,probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            commit=command(['/usr/bin/git','-C',str(ROOT),'rev-parse','HEAD']).stdout.decode().strip(),
            tree=command(['/usr/bin/git','-C',str(ROOT),'rev-parse','HEAD^{tree}']).stdout.decode().strip())
        report['tracked_git_status']=command(['/usr/bin/git','-C',str(ROOT),'status','--porcelain','--untracked-files=no']).stdout.decode()
        if report['tracked_git_status']:raise RuntimeError('Tracked source changed in CI')
        translated=subprocess.run([protected('/usr/sbin/sysctl'),'-in','sysctl.proc_translated'],env=MINIMAL,
                                  stdin=subprocess.DEVNULL,capture_output=True,timeout=5)
        report['rosetta']={'returncode':translated.returncode,'value':translated.stdout.decode().strip()}
        origin=json.loads((ROOT/'tests/mac_inventory_candidate_origin.json').read_bytes())
        path=ROOT/origin['copied_payload'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=origin['sha256']:raise RuntimeError('Parser payload hash differs')
        if command(['/usr/bin/git','-C',str(ROOT),'hash-object',str(path)]).stdout.decode().strip()!=origin['git_blob']:
            raise RuntimeError('Parser payload Git blob differs')
        report['parser_provenance']=origin
        spec=importlib.util.spec_from_file_location('diagnostic_candidate_inventory',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        report['stage']='identity'
        report['identity']={}
        with tempfile.TemporaryDirectory(prefix='second-brain-mac-measure-') as folder:
            identity_cases(Path(folder),report['identity'])
        report['stage']='owned-sockets';report['socket_cases']=own_socket_cases(module)
        report['stage']='foreign-uid';report['foreign']={};foreign_cases(module,report['foreign'])
        observations=[r['global_inventory'] for r in report['socket_cases'] if 'global_inventory' in r]
        observations.extend(r['global_inventory'] for r in report['foreign']['cases'] if 'global_inventory' in r)
        times=[r['read_ms'] for r in observations]
        report['timing']={'product_reads':len(times),'p50_ms':statistics.median(times),'max_ms':max(times)}
        report['candidate_blockers']=[]
        if any(r['product_read']!='observed' for r in observations):report['candidate_blockers'].append('product parser/read refused native table')
        if any(not r.get('observation_valid') for r in report['foreign']['cases']):
            report['candidate_blockers'].append('foreign witness invalid; visibility not established')
        if any(r.get('observation_valid') and r.get('global_inventory',{}).get('matching_listen_count')!=1 for r in report['foreign']['cases']):
            report['candidate_blockers'].append('foreign UID listener not globally visible exactly once')
        if any(r.get('global_inventory',{}).get('matching_listen_count')!=(2 if r.get('second_listener')=='bound' else 1)
               for r in report['socket_cases']):report['candidate_blockers'].append('coexisting listeners missing from global table')
        if any(not r.get('birth_stable',True) for r in report['identity']['cases']):report['candidate_blockers'].append('unstable process birth')
        for case in report['identity']['cases']:
            if case.get('case')=='configured' and case.get('invocation_class')=='configured/no-flags':
                name=case.get('observed',{}).get('name',{}).get('value','').lower()
                if name not in ('python','python3','python.exe','pythonw.exe'):
                    report['candidate_blockers'].append('configured process name outside current roots allowlist')
                if case.get('birth_from_parent_children')!=case.get('birth_reads',[None])[0]:
                    report['candidate_blockers'].append('configured cross-path process birth unverified')
        if any(r.get('birth_from_parent_children') is not None and
               r['birth_from_parent_children']!=r.get('birth_reads',[None])[0] for r in report['identity']['cases']):
            report['candidate_blockers'].append('process birth differs across ownership paths')
        report['incomplete_cases']=[r.get('invocation_class',r.get('case')) for r in
            report['identity']['cases']+report['socket_cases']+report['foreign']['cases']
            if r.get('status')!='observed']
        if report['incomplete_cases']:raise RuntimeError('One or more measurement cases incomplete')
        report.update(status='observations completed; not qualification',stage='finished')
    except Exception as error:
        report.update(error_type=type(error).__name__)
        if isinstance(error,ProbeFailure):report['diagnostic']={'code':str(error),**error.details}
        elif type(error) is RuntimeError:report['fixed_error']=str(error)
    finally:
        report['finished_at']=time.time()
        with destination.open('x',encoding='utf-8') as output:json.dump(report,output,indent=2)
    print(json.dumps({'status':report['status'],'stage':report['stage'],'receipt':str(args.receipt),
        'candidate_blockers':report.get('candidate_blockers',[]),
        'candidate_blocker_count':len(report.get('candidate_blockers',[]))}))
    if report['status']=='incomplete':raise SystemExit(1)


if __name__=='__main__':main()
