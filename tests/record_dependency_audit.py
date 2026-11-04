"""Record native audit provenance only; this does not approve a release."""
from datetime import datetime,timezone
import hashlib,json,os,platform,re,runpy,sys,sysconfig
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CI_FIELDS=('GITHUB_REPOSITORY','GITHUB_SERVER_URL','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT',
           'GITHUB_SHA','GITHUB_REF','GITHUB_EVENT_NAME','GITHUB_WORKFLOW_SHA','GITHUB_WORKFLOW_REF',
           'GITHUB_JOB','GITHUB_HEAD_REF','GITHUB_BASE_REF','RUNNER_OS','RUNNER_ARCH',
           'RUNNER_ENVIRONMENT','RUNNER_NAME','ImageOS','ImageVersion','AUDIT_MATRIX_OS','AUDIT_PR_HEAD_SHA','AUDIT_PR_BASE_SHA')


def read(path):
    if path.is_symlink() or path.stat().st_size>16_000_000:
        raise ValueError('Audit evidence is linked or exceeds its bound.')
    return path.read_bytes()


def record(root=ROOT):
    root=Path(root);reports=root/'reports';target=reports/'audit-provenance.json'
    if target.exists():raise ValueError('Keep the first native provenance receipt.')
    runpy.run_path(str(root/'install.py'))['_candidate_platform']()
    gate=runpy.run_path(str(root/'check_dependency_audit.py'))
    policy,machine=gate['profile_path'](sys.platform,platform.machine())
    ci={key:os.environ.get(key,'') for key in CI_FIELDS}
    required=set(CI_FIELDS)-{'GITHUB_HEAD_REF','GITHUB_BASE_REF','AUDIT_PR_HEAD_SHA','AUDIT_PR_BASE_SHA'}
    if any(not ci[key] for key in required) or any(len(value)>4096 or any(ord(c)<32 for c in value) for value in ci.values()):
        raise ValueError('Native CI identity is missing or malformed.')
    if (any(not re.fullmatch('[0-9]+',ci[key]) for key in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'))
        or any(not re.fullmatch('[a-f0-9]{40}',ci[key]) for key in ('GITHUB_SHA','GITHUB_WORKFLOW_SHA'))):
        raise ValueError('Native CI run or source identity is malformed.')
    expected={('win32','AMD64'):('Windows','X64','windows-2025'),('linux','x86_64'):('Linux','X64','ubuntu-24.04'),
              ('darwin','arm64'):('macOS','ARM64','macos-15'),('darwin','x86_64'):('macOS','X64','macos-15-intel')}
    if (ci['RUNNER_OS'],ci['RUNNER_ARCH'],ci['AUDIT_MATRIX_OS'])!=expected.get((sys.platform,machine)):
        raise ValueError('Native runner identity differs from the guarded host profile.')
    commit=read(reports/'checked-out-commit.txt').decode('utf-8-sig').strip()
    source=json.loads(read(reports/'source.json').decode('utf-8-sig'))
    if (not isinstance(source,dict) or not re.fullmatch('[a-f0-9]{40}',commit)
        or source.get('checked_out_commit')!=commit or source.get('commit')!=ci['GITHUB_SHA'] or commit!=ci['GITHUB_SHA']
        or not isinstance(source.get('checked_out_tree'),str) or not re.fullmatch('[a-f0-9]{40}',source['checked_out_tree'])
        or any(source.get(field)!=ci[env] for field,env in (('event','GITHUB_EVENT_NAME'),('ref','GITHUB_REF'),('run_id','GITHUB_RUN_ID')))):
        raise ValueError('Checked-out source differs from the recorded CI identity.')
    pr={key:source.get(key) or '' for key in ('pr_head','pr_base')}
    if ci['GITHUB_EVENT_NAME']=='pull_request' and any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{40}',value) for value in pr.values()):
        raise ValueError('Pull-request source identity is incomplete.')
    if ci['GITHUB_EVENT_NAME']!='pull_request' and any(pr.values()):
        raise ValueError('Unexpected pull-request identity for this event.')
    if pr!={'pr_head':ci['AUDIT_PR_HEAD_SHA'],'pr_base':ci['AUDIT_PR_BASE_SHA']}:
        raise ValueError('Pull-request source differs from the actual workflow event.')
    versions={name:read(reports/file).decode('utf-8-sig').strip() for name,file in
              (('uv','uv-version.txt'),('pip_audit','pip-audit-version.txt'))}
    if not re.fullmatch(r'uv [0-9]+\.[0-9]+\.[0-9]+(?: .+)?',versions['uv']) or versions['pip_audit']!='pip-audit 2.9.0':
        raise ValueError('Observed audit tool versions are unavailable or differ from the scanner request.')
    exit_text=os.environ.get('AUDIT_PROFILE_EXIT_CODE','')
    if not re.fullmatch('[0-9]+',exit_text) or not 0<=int(exit_text)<=255:
        raise ValueError('Actual profile checker exit code is unavailable.')
    files={'pyproject':root/'pyproject.toml','lock':root/'uv.lock','requirements':reports/'audit-requirements.txt',
           'raw_report':reports/'dependency-audit.json','profile':policy,'profile_output':reports/'dependency-profile.txt',
           'manifest':root/'release-manifest.json','recorder':root/'tests/record_dependency_audit.py',
           'workflow':root/'.github/workflows/verify.yml','source':reports/'source.json',
           'checked_out_commit':reports/'checked-out-commit.txt','uv_version':reports/'uv-version.txt',
           'pip_audit_version':reports/'pip-audit-version.txt'}
    hashes={name:hashlib.sha256(read(path)).hexdigest() for name,path in files.items()}
    if source.get('manifest_sha256')!=hashes['manifest']:
        raise ValueError('Source receipt manifest identity differs.')
    value={'recorded_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'native host and exact dependency audit inputs; not service or physical-device acceptance',
        'ci':ci,'source_commit':commit,'source_tree':source['checked_out_tree'],**pr,
        'sys_platform':sys.platform,'machine':machine,'observed_machine':platform.machine(),
        'os_release':platform.release(),'os_version':platform.version(),'uname_machine':os.uname().machine if hasattr(os,'uname') else None,
        'python_version':sys.version,'python_executable':sys.executable,'python_implementation':platform.python_implementation(),
        'python_build_platform':sysconfig.get_platform(),'native_candidate_guard_passed':True,
        'observed_tool_versions':versions,'declared_scanner_requirement':'pip-audit==2.9.0',
        'scanner_interpreter_scope':'CLI version observed; scanner interpreter is separate from the guarded project interpreter.',
        'profile_exit_code':int(exit_text),'files':{name:path.relative_to(root).as_posix() for name,path in files.items()},'sha256':hashes}
    with target.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)
    print(json.dumps({'status':'native audit provenance recorded','platform':sys.platform,'machine':machine}))
    return value


if __name__=='__main__':record()
