"""Read-only ancestor metadata for the failed hosted Linux qualification guard."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys

FAILED_RUN=41823108172
CANDIDATE='afe772bc61fcf747b6a0d8a17ccba67e5104b049'
FAILED_HEAD='fbdf28ccda8fa2b71fd64201d109ebc0a036b877'
PINS={'release-manifest.json':'d219a57ccbb9230964b3e6c8f823c730b26428fcb6fe019dd882bfcbb35f9400',
      'native_linux_upgrade.py':'86115376fefb8f53400743874a583dae18059c94577fe78adbaa6337b904c0ab',
      'prepare_linux_product.sh':'2040d60d2a2c5bb51eebefd8db4919da8b0c59cee3b731881fe75bf20a7581aa'}

def digest(raw):return hashlib.sha256(raw).hexdigest()

def guarded(mode,uid):
    """The unchanged failed harness predicate; this inspector never applies a fix."""
    return uid==0 and not mode&0o022

def chain(path,probe):
    selected=PurePosixPath(path)
    if not selected.is_absolute() or '..' in selected.parts:raise ValueError('fixed absolute path required')
    rows=[probe(str(p)) for p in (selected,*selected.parents)]
    return {'selected_path':str(selected),'ancestors':rows,
            'unsafe_existing_ancestors':[row['path'] for row in rows if row.get('exists') and row.get('guard_uid_mode_ok') is False],
            'complete_existing_chain':all(row.get('exists') and 'error_type' not in row for row in rows)}

def inspect(path):
    row={'path':path}
    try:
        value=os.lstat(path)
        row.update(exists=True,uid=value.st_uid,gid=value.st_gid,mode=oct(stat.S_IMODE(value.st_mode)),
                   type=('symlink' if stat.S_ISLNK(value.st_mode) else 'directory' if stat.S_ISDIR(value.st_mode) else 'regular' if stat.S_ISREG(value.st_mode) else 'other'),
                   guard_uid_mode_ok=(None if stat.S_ISLNK(value.st_mode) else guarded(value.st_mode,value.st_uid)))
        if stat.S_ISLNK(value.st_mode):
            row['symlink_target']=os.readlink(path)
            row['harness_plain_rejects']=True
        followed=os.stat(path)
        row['stat']={'uid':followed.st_uid,'gid':followed.st_gid,'mode':oct(stat.S_IMODE(followed.st_mode)),
                     'guard_uid_mode_ok':guarded(followed.st_mode,followed.st_uid)}
    except FileNotFoundError:row['exists']=False
    except OSError as error:row['error_type']=type(error).__name__
    return row

def bounded(path):
    raw=path.read_bytes()
    if not 0<len(raw)<=1_000_000:raise ValueError('public evidence size refused')
    return raw

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper-sha256',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if (sys.platform!='linux' or platform.machine()!='x86_64' or platform.freedesktop_os_release().get('VERSION_ID')!='24.04'
        or os.geteuid()==0 or not sys.flags.isolated or not sys.dont_write_bytecode
        or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted'
        or os.environ.get('GITHUB_REF')!='refs/heads/feature/posix-linux-ownership-diagnostic'
        or not re.fullmatch('[a-f0-9]{40}',os.environ.get('GITHUB_SHA',''))
        or os.environ.get('GITHUB_SHA')!=os.environ.get('GITHUB_WORKFLOW_SHA')
        or not re.fullmatch('[0-9]+',os.environ.get('GITHUB_RUN_ID',''))
        or not re.fullmatch('[a-f0-9]{64}',args.helper_sha256)):
        raise ValueError('bounded native diagnostic identity required')
    workspace=Path(os.environ['GITHUB_WORKSPACE'])
    own=workspace/'harness/tests/native_linux_ownership.py'
    receipt=workspace/'reports/linux-ownership.json'
    if Path(__file__).absolute()!=own or args.receipt!=receipt or receipt.exists():raise ValueError('fixed create-only report required')
    helper_sha=digest(bounded(own))
    if helper_sha!=args.helper_sha256:raise ValueError('reviewed helper changed')
    evidence={}
    for name,path in [('release-manifest.json',workspace/'software/release-manifest.json'),
                      ('native_linux_upgrade.py',workspace/'harness/tests/native_linux_upgrade.py'),
                      ('prepare_linux_product.sh',workspace/'harness/tests/prepare_linux_product.sh')]:
        observed=digest(bounded(path))
        if observed!=PINS[name]:raise ValueError('failed source pin changed')
        evidence[name]=observed
    selected=[]
    for mode in ('forward','rollback','recovery'):
        base=f'/opt/second-brain-product-{FAILED_RUN}-1-{mode}'
        selected.extend((base+'/software',base+'/software/.venv',base+'/native_linux_upgrade.py',base+'/uv'))
    selected.append('/usr/bin/python3.12')
    cache={}
    def probe(path):
        if path not in cache:cache[path]=inspect(path)
        return cache[path]
    chains=[chain(path,probe) for path in selected]
    resolved=Path('/usr/bin/python3.12').resolve(strict=True)
    resolved_chain=chain(str(resolved),probe)
    for path in ('/','/opt','/usr','/usr/bin','/usr/bin/python3.12'):
        if not probe(path).get('exists') or 'error_type' in probe(path):raise ValueError('required host metadata incomplete')
    value={'status':'read-only host ancestry observed; no native qualification',
           'scope':'New host metadata only. No SDK/product runtime/account/manager/installer/update creation or permission changes. Missing failed-VM paths are not a successful guard execution.',
           'failed_run_id':FAILED_RUN,'failed_harness_commit':FAILED_HEAD,'candidate_commit':CANDIDATE,
           'source_sha256':evidence,'helper_sha256':helper_sha,'workflow_head':os.environ['GITHUB_SHA'],
           'workflow_sha':os.environ['GITHUB_WORKFLOW_SHA'],'workflow_run_id':os.environ['GITHUB_RUN_ID'],
           'architecture':platform.machine(),'ubuntu_version':platform.freedesktop_os_release().get('VERSION_ID'),
           'observer_uid':os.geteuid(),'observer_gid':os.getegid(),
           'image_os':os.environ.get('ImageOS'),'image_version':os.environ.get('ImageVersion'),
           'observer_python':sys.executable,'observer_prefix':sys.prefix,'observer_base_executable':sys._base_executable,
           'failed_guard_order':'source, SDK prefix, resolved SDK base executable, resolved harness, uv; then each selected path and parents',
           'failed_base_executable_scope':'Failed uv log selected /usr/bin/python3.12; failed SDK sys._base_executable was not retained. This report does not recreate that SDK.',
           'selected_chains':chains,'current_system_python_resolved':str(resolved),'resolved_system_python_chain':resolved_chain,
           'unsafe_existing_paths':sorted(path for path,row in cache.items() if row.get('exists') and row.get('guard_uid_mode_ok') is False)}
    raw=(json.dumps(value,indent=2)+'\n').encode()
    if len(raw)>100_000:raise ValueError('bounded report exceeded')
    with receipt.open('xb') as stream:stream.write(raw)
    print(json.dumps({'status':value['status'],'unsafe_existing_paths':value['unsafe_existing_paths']}))

if __name__=='__main__':main()
