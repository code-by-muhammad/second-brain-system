"""Observe launchctl's contract using one private UUID job on disposable Mac CI.

This is not product lifecycle qualification. No product label, notes, credentials,
PID signals, or ambient environment values are used. Native manager grammar is
observed before it is accepted by a product ownership parser.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import pwd
import re
import selectors
import stat
import subprocess
import sys
import time
import uuid


ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C'}


def protected(path):
    path=Path(path)
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise RuntimeError('unprotected-native-tool')
    if not stat.S_ISREG(path.stat().st_mode):raise RuntimeError('missing-native-tool')
    return str(path)


def command(argv):
    """Streaming cap and time bound; only a directly owned unreaped child is killed."""
    child=subprocess.Popen(argv,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out=bytearray();err=bytearray();deadline=time.monotonic()+15
    selector=selectors.DefaultSelector()
    try:
        for pipe,bucket in ((child.stdout,out),(child.stderr,err)):
            os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,bucket)
        while selector.get_map():
            if time.monotonic()>deadline:raise RuntimeError('native-command-timeout')
            for key,_ in selector.select(.1):
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk:selector.unregister(key.fileobj);continue
                key.data.extend(chunk)
                if len(out)>2_000_000 or len(err)>65536:raise RuntimeError('native-command-output-bound')
        child.wait(timeout=max(.01,deadline-time.monotonic()))
        return child.returncode,bytes(out),bytes(err)
    finally:
        selector.close()
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()


def text(data):
    return data.decode('utf-8',errors='strict')


def service_summary(raw,expected):
    """Only fixture-owned expected values, scalar field names and env KEY names."""
    result=[];environment_depth=None;arguments_depth=None;depth=0
    safe={'path','type','state','program','working directory','stdout path','stderr path','domain','pid'}
    for line in text(raw).splitlines():
        value=line.strip()
        if not value:continue
        if value=='}':
            depth-=1
            if environment_depth is not None and depth<environment_depth:environment_depth=None
            if arguments_depth is not None and depth<arguments_depth:arguments_depth=None
            result.append({'depth':depth,'close':True});continue
        key,separator,tail=value.partition(' = ')
        if separator and tail=='{':
            result.append({'depth':depth,'block':key if re.fullmatch(r'[a-zA-Z0-9 /_.-]{1,200}',key) else '<fixture-header>'})
            depth+=1
            if 'environment' in key:environment_depth=depth
            if key=='arguments':arguments_depth=depth
            continue
        if environment_depth is not None:
            name=value.partition('=>')[0].partition('=')[0].strip(' "')
            if re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',name):result.append({'depth':depth,'environment_key':name})
            continue
        if separator:
            entry={'depth':depth,'key':key if re.fullmatch(r'[a-zA-Z0-9 /_.-]{1,200}',key) else '<unknown-key>'}
            if key in safe:entry['value']=tail if len(tail)<1000 else '<over-bound>'
            else:entry['shape']='decimal' if tail.isdigit() else 'scalar'
            result.append(entry)
        elif arguments_depth is not None and depth==arguments_depth and value in expected:result.append({'depth':depth,'argument':value})
        else:result.append({'depth':depth,'unknown_line':True})
    return result


def fixture_identity(raw,target,sleeper):
    fields=service_summary(raw,{sleeper,'60'})
    for key,value in (('path',str(target)),('type','LaunchAgent'),('program',sleeper),('working directory','/'),
                      ('stdout path','/dev/null'),('stderr path','/dev/null')):
        matches=[row for row in fields if row.get('key')==key]
        if len(matches)!=1 or matches[0].get('value')!=value:raise RuntimeError('fixture-loaded-identity-unavailable')
    if [row['argument'] for row in fields if 'argument' in row]!=[sleeper,'60']:
        raise RuntimeError('fixture-loaded-arguments-unavailable')
    return fields


def domain_summary(raw,domain,label):
    lines=text(raw).splitlines()
    if not lines or lines[0].strip()!=domain+' = {':raise RuntimeError('unexpected-domain-header')
    in_services=False;rows=0;present=False;shapes=set();closed=False
    for line in lines[1:]:
        stripped=line.strip()
        if not in_services:
            if stripped=='services = {':in_services=True
            continue
        if stripped=='}':closed=True;break
        fields=stripped.split()
        if not fields:continue
        rows+=1;present|=label in fields
        shapes.add(tuple('decimal' if token.isdigit() else 'signed-decimal' if re.fullmatch('-[0-9]+',token) else 'dash' if token=='-' else 'hex' if re.fullmatch('0x[0-9a-fA-F]+',token) else 'label' if re.fullmatch('[A-Za-z0-9_.-]+',token) else 'other' for token in fields))
    if not closed:raise RuntimeError('domain-services-block-unavailable')
    return {'header_verified':True,'services_block_closed':True,'service_row_count':rows,
            'fixture_label_present':present,'row_shapes':[list(row) for row in sorted(shapes)]}


def disabled_summary(raw,label):
    lines=text(raw).splitlines();found=[]
    for line in lines:
        if label in line:
            match=re.fullmatch(r'\s*"'+re.escape(label)+r'"\s*=>\s*(disabled|enabled|true|false)\s*',line)
            if not match:raise RuntimeError('unexpected-disabled-override-grammar')
            found.append(match.group(1))
    if len(found)>1:raise RuntimeError('duplicate-disabled-override')
    return {'fixture_override':found[0] if found else 'default','header_shape':bool(lines and lines[0].strip().endswith('= {'))}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--receipt',required=True);args=parser.parse_args()
    receipt=Path(args.receipt);report={'scope':'native launchctl contract only; not product/device acceptance',
        'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'arch':platform.machine(),
        'os_version':platform.mac_ver()[0],'observations':[],'commands':[],'cleanup_verified':False,
        'cleanup_scope':'UUID job and file only; enabled override and OS background-task metadata may remain on disposable host'}
    target=None;definition=None;launchctl=None;domain=None;label=None;created=False
    def mutate(stage,*arguments):
        code,out,err=command([launchctl,*arguments])
        message=text(err)
        error_class=('empty' if not message else 'not-found' if 'Could not find service' in message else
                     'domain-unsupported' if 'Domain does not support' in message else
                     'permission' if 'not permitted' in message.lower() or 'denied' in message.lower() else
                     'input-output' if 'Input/output error' in message else 'other')
        report['commands'].append({'stage':stage,'exit_code':code,'stdout_empty':not out,'stderr_empty':not err,'stderr_class':error_class})
    def await_unloaded(address):
        deadline=time.monotonic()+25
        while True:
            code,out,err=command([launchctl,'print',address])
            if code==113 and not out:return
            if time.monotonic()>=deadline:raise RuntimeError('manager-unload-not-observed-before-deadline')
            time.sleep(.25)
    try:
        if sys.platform!='darwin' or os.geteuid()==0 or not sys.flags.isolated or os.environ.get('GITHUB_ACTIONS')!='true':
            raise RuntimeError('disposable-native-ci-required')
        launchctl=protected('/bin/launchctl');sleeper=protected('/bin/sleep')
        home=Path(pwd.getpwuid(os.geteuid()).pw_dir);directory=home/'Library/LaunchAgents'
        for part in (home,home/'Library',directory):
            if not part.exists():part.mkdir(mode=0o700)
            info=part.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
                raise RuntimeError('unsafe-real-launchagents-directory')
        domain='gui/'+str(os.geteuid());label='local.second-brain.test.'+uuid.uuid4().hex;address=domain+'/'+label
        target=directory/(label+'.plist')
        definition=plistlib.dumps({'Label':label,'ProgramArguments':[sleeper,'60'],
            'WorkingDirectory':'/','RunAtLoad':False,'KeepAlive':False,
            'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'})
        expected={sleeper,'60'}
        def observe(stage):
            domain_code,domain_out,domain_err=command([launchctl,'print',domain])
            if domain_code or domain_err:raise RuntimeError('gui-domain-unavailable')
            code,out,err=command([launchctl,'print',address])
            disabled_code,disabled_out,disabled_err=command([launchctl,'print-disabled',domain])
            if disabled_code or disabled_err:raise RuntimeError('disabled-state-unavailable')
            row={'stage':stage,'domain':domain_summary(domain_out,domain,label),'service_exit_code':code,
                'service_stdout_empty':not out,'disabled':disabled_summary(disabled_out,label)}
            if code:
                message=text(err)
                row['not_found_contract']={'contains_fixture_label':label in message,
                    'contains_gui_domain':'gui' in message,'line_count':len(message.splitlines()),
                    'error_class':'not-found' if 'Could not find service' in message else 'unknown'}
            else:
                if err:raise RuntimeError('service-query-stderr')
                row['loaded_fields']=service_summary(out,expected)
            report['observations'].append(row)
            return row,out
        first,_=observe('initial-absent');time.sleep(.25);second,_=observe('initial-absent-recheck')
        if not first['service_exit_code'] or not second['service_exit_code'] or first['domain']['fixture_label_present'] or second['domain']['fixture_label_present']:
            raise RuntimeError('initial-absence-not-observed')
        descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        created=True
        with os.fdopen(descriptor,'wb') as stream:stream.write(definition);stream.flush();os.fsync(stream.fileno())
        mutate('bootstrap','bootstrap',domain,str(target))
        row,raw=observe('loaded-idle')
        if row['service_exit_code'] or not row['domain']['fixture_label_present']:raise RuntimeError('fixture-load-not-observed')
        try:fixture_identity(raw,target,sleeper);row['expected_identity_contract']=True
        except RuntimeError:row['expected_identity_contract']=False
        mutate('kickstart','kickstart',address);time.sleep(.3)
        row,raw=observe('running');fields=service_summary(raw,expected)
        if row['service_exit_code'] or not any(field.get('key')=='state' and field.get('value')=='running' for field in fields):
            raise RuntimeError('fixture-running-query-failed')
        mutate('disable','disable',address);row,_=observe('disabled-running')
        if row['disabled']['fixture_override'] not in ('disabled','true'):raise RuntimeError('disable-not-observed')
        mutate('bootout','bootout',address);await_unloaded(address)
        row,_=observe('disabled-unloaded');time.sleep(.25);again,_=observe('disabled-unloaded-recheck')
        if not row['service_exit_code'] or not again['service_exit_code'] or row['domain']['fixture_label_present'] or again['domain']['fixture_label_present']:
            raise RuntimeError('bootout-absence-not-observed')
        mutate('enable','enable',address);observe('enabled-unloaded')
        report['status']='contract observed; not qualification'
    except Exception as error:
        report.update(status='failed',failure_class=str(error) if type(error) is RuntimeError else type(error).__name__)
    finally:
        if created:
            try:
                if target.exists():
                    info=target.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or target.read_bytes()!=definition:
                        raise RuntimeError('fixture-file-changed-preserved')
                else:report['fixture_file_missing']=True
                address=domain+'/'+label
                code,out,err=command([launchctl,'print',address])
                if code==0:
                    fixture_identity(out,target,protected('/bin/sleep'))
                    mutate('cleanup-disable','disable',address);mutate('cleanup-bootout','bootout',address)
                elif code!=113 or out:raise RuntimeError('cleanup-absence-unknown')
                # Never remove a UUID file while the manager still reports it loaded.
                await_unloaded(address);time.sleep(.25);code,out,err=command([launchctl,'print',address])
                if code!=113 or out:raise RuntimeError('cleanup-unload-unverified')
                mutate('cleanup-enable','enable',address);target.unlink(missing_ok=True);report['cleanup_verified']=True
            except Exception as error:report.update(status='failed',cleanup_failure=str(error) if type(error) is RuntimeError else type(error).__name__)
        else:report['cleanup_verified']=True
        receipt.parent.mkdir(parents=True,exist_ok=True)
        with receipt.open('x',encoding='utf-8') as stream:json.dump(report,stream,sort_keys=True,indent=2)
    return 0 if report.get('status')=='contract observed; not qualification' and report['cleanup_verified'] else 1


if __name__=='__main__':raise SystemExit(main())
