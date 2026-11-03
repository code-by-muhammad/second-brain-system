"""A scoped exception must not hide incomplete audits, changed code or new CVEs."""
from copy import deepcopy
from datetime import date
import json
import hashlib
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('audit_policy',Path(__file__).resolve().parents[1]/'check_dependency_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


@pytest.fixture
def profile(tmp_path):
    source=tmp_path/'profile.py';source.write_bytes(b'# reviewed static local transport\n')
    report={'dependencies':[{'name':'basic-memory','version':'0.23.2','vulns':[]},
                            {'name':'fastmcp','version':'4.0.0b1','vulns':[{'id':'GHSA-vqqp-x5qm-5qp4'}]}]}
    policy={'format':1,'platform':'win32','machine':'AMD64','expected_audit_versions':{'basic-memory':'0.23.2','fastmcp':'4.0.0b1'},
            'source_sha256':{'profile.py':hashlib.sha256(source.read_bytes()).hexdigest()},
            'exceptions':[{'package':'fastmcp','version':'4.0.0b1','id':'GHSA-vqqp-x5qm-5qp4',
                           'review_by':'2026-11-12','disposition':'not_reachable_in_qualified_profile'}]}
    return tmp_path,report,policy


def check(profile):
    root,report,policy=profile
    return audit.assess(report,policy,root,today=date(2026,10,29))


def test_exact_reviewed_exception_is_separate_from_raw_findings(profile):
    result=check(profile)
    assert result['status']=='passed with scoped exception' and result['raw_findings']==1
    assert len(result['exceptions_applied'])==1 and not result['unresolved']


def test_another_advisory_in_same_package_remains_unresolved(profile):
    root,report,policy=profile
    report['dependencies'][1]['vulns'].append({'id':'GHSA-aaaa-bbbb-cccc'})
    result=check(profile)
    assert result['status']=='needs review' and len(result['unresolved'])==1 and result['raw_findings']==2


@pytest.mark.parametrize('change',['missing-package','changed-version','unscanned','duplicate','litellm'])
def test_incomplete_changed_or_unscanned_audit_cannot_pass(profile,change):
    root,report,policy=profile
    if change=='missing-package':report['dependencies'].pop(0)
    elif change=='changed-version':report['dependencies'][1]['version']='4.0.0'
    elif change=='unscanned':report['dependencies'][0]['skip_reason']='lookup failed'
    elif change=='duplicate':report['dependencies'].append(deepcopy(report['dependencies'][0]))
    else:report['dependencies'].append({'name':'litellm','version':'1.91.5','vulns':[]})
    expected={'missing-package':'exact qualified dependency graph','changed-version':'exact qualified dependency graph','unscanned':'Unscanned','duplicate':'Duplicate','litellm':'excluded LiteLLM'}
    with pytest.raises(ValueError,match=expected[change]):check(profile)


def test_expired_exception_requires_review(profile):
    root,report,policy=profile
    with pytest.raises(ValueError,match='expired'):audit.assess(report,policy,root,today=date(2026,11,13))


def test_auth_transport_source_change_requires_review(profile):
    root,report,policy=profile;(root/'profile.py').write_bytes(b'# changed authentication\n')
    with pytest.raises(ValueError,match='Protected security profile changed'):check(profile)


def test_new_basic_memory_cannot_inherit_the_old_exception(profile):
    root,report,policy=profile;report['dependencies'][0]['version']='0.24.1';policy['expected_audit_versions']['basic-memory']='0.24.1'
    with pytest.raises(ValueError,match='Basic Memory dependency changed'):check(profile)


PROFILES=[('win32','Windows','nt','AMD64','security-exceptions.json'),
          ('darwin','Darwin','posix','arm64','security-exceptions-darwin-arm64.json'),
          ('darwin','Darwin','posix','x86_64','security-exceptions-darwin-x86_64.json'),
          ('linux','Linux','posix','x86_64','security-exceptions-linux-x86_64.json')]

@pytest.mark.parametrize('system,system_name,os_name,machine,profile_name',PROFILES)
def test_real_policy_graph_matches_independent_nondev_lock_traversal(system,system_name,os_name,machine,profile_name):
    import tomllib
    from packaging.markers import Marker
    root=Path(__file__).resolve().parents[1];policy=json.loads((root/profile_name).read_bytes())
    lock=tomllib.loads((root/'uv.lock').read_text());packages=lock['package']
    assert set(lock['required-markers'])=={f"platform_machine == '{m}' and sys_platform == '{s}'" for s,_,_,m,_ in PROFILES}
    # Pin every PEP508 environment field; never inherit the test host's OS.
    environment={'sys_platform':system,'os_name':os_name,'platform_system':system_name,
                 'platform_machine':machine,'python_version':'3.12','python_full_version':'3.12.10',
                 'implementation_name':'cpython','implementation_version':'3.12.10',
                 'platform_python_implementation':'CPython','platform_release':'','platform_version':''}
    seen=set();versions={};pending=[('shared-second-brain',None,())]
    while pending:
        name,version,extras=pending.pop();identity=(name,version,extras)
        if identity in seen:continue
        seen.add(identity);choices=[row for row in packages if row['name']==name and (version is None or row['version']==version)]
        assert len(choices)==1;row=choices[0]
        if name!='shared-second-brain':
            if name in versions:assert versions[name]==row['version'],'Conflicting lock versions: '+name
            versions[name]=row['version']
        children=list(row.get('dependencies',[]))
        for extra in extras:children.extend(row.get('optional-dependencies',{}).get(extra,[]))
        for child in children:
            if child.get('marker') and not any(Marker(child['marker']).evaluate({**environment,'extra':extra}) for extra in ('',*extras)):continue
            pending.append((child['name'],child.get('version'),tuple(sorted(child.get('extra',[])))))
    assert versions==policy['expected_audit_versions'] and 'litellm' not in versions
    intel=system=='darwin' and machine=='x86_64'
    assert versions['onnxruntime']==('1.23.2' if intel else '1.31.0')
    assert {'coloredlogs','humanfriendly','sympy','mpmath'}<=versions.keys() if intel else not {'coloredlogs','humanfriendly','sympy','mpmath'}&versions.keys()


def real_report(profile_name='security-exceptions.json'):
    policy=json.loads((audit.ROOT/profile_name).read_bytes())
    report={'dependencies':[{'name':name,'version':version,'vulns':[]} for name,version in policy['expected_audit_versions'].items()]}
    for package in report['dependencies']:
        if package['name'] in ('fastmcp','fastmcp-slim'):package['vulns']=[{'id':'GHSA-vqqp-x5qm-5qp4'}]
    return report,policy


@pytest.mark.parametrize('system,system_name,os_name,machine,profile_name',PROFILES)
def test_real_policy_source_bindings_and_both_advisory_entries(system,system_name,os_name,machine,profile_name):
    report,policy=real_report(profile_name)
    assert {(item['package'],item['version'],item['id'],item['review_by']) for item in policy['exceptions']}=={
        (name,'4.0.0b1','GHSA-vqqp-x5qm-5qp4','2026-11-12') for name in ('fastmcp','fastmcp-slim')}
    # Bind all local modules, including lazy server imports and future additions.
    expected={path.relative_to(audit.ROOT).as_posix() for path in (audit.ROOT/'app').glob('*.py')}
    expected|={'install.py','update.py','setup_runtime.py','pyproject.toml','uv.lock',
               'check_dependency_audit.py','tests/test_security_profile.py'}
    assert set(policy['source_sha256'])==expected
    result=audit.assess(report,policy,audit.ROOT,today=date(2026,10,29),expected_platform=system,expected_machine=machine)
    assert len(result['exceptions_applied'])==2 and not result['unresolved']
    assert audit.assess(report,policy,audit.ROOT,today=date(2026,11,12),expected_platform=system,expected_machine=machine)['status']=='passed with scoped exception'
    with pytest.raises(ValueError,match='expired'):audit.assess(report,policy,audit.ROOT,today=date(2026,11,13),expected_platform=system,expected_machine=machine)


@pytest.mark.parametrize('mode,code',[('good',0),('unresolved',1),('invalid',2)])
@pytest.mark.parametrize('system,system_name,os_name,machine,profile_name',PROFILES)
def test_cli_returns_success_unresolved_and_validation_exit_codes(tmp_path,monkeypatch,mode,code,system,system_name,os_name,machine,profile_name):
    from datetime import datetime,timezone
    class Clock:
        @staticmethod
        def now(zone):return datetime(2026,10,29,tzinfo=timezone.utc)
    monkeypatch.setattr(audit,'datetime',Clock);report,policy=real_report(profile_name)
    monkeypatch.setattr(audit.sys,'platform',system);monkeypatch.setattr(audit.platform,'machine',lambda:machine)
    if mode=='unresolved':report['dependencies'][0]['vulns']=[{'id':'GHSA-aaaa-bbbb-cccc'}]
    elif mode=='invalid':report={}
    path=tmp_path/'audit.json';path.write_text(json.dumps(report));monkeypatch.setattr(audit.sys,'argv',['checker.py',str(path)])
    if code:
        with pytest.raises(SystemExit) as failure:audit.main()
        assert failure.value.code==code
    else:audit.main()


@pytest.mark.parametrize('mutation',['non-dict','missing-date','bad-date','malformed-id','extra-package','unsafe-source'])
def test_malformed_policy_or_new_report_data_fails_closed(profile,mutation):
    root,report,policy=profile
    if mutation=='non-dict':policy['exceptions']=[None]
    elif mutation=='missing-date':policy['exceptions'][0].pop('review_by')
    elif mutation=='bad-date':policy['exceptions'][0]['review_by']='invalid'
    elif mutation=='malformed-id':report['dependencies'][1]['vulns'][0]['id']='invalid'
    elif mutation=='extra-package':report['dependencies'].append({'name':'extra','version':'1','vulns':[]})
    else:policy['source_sha256']={'../outside.py':'0'*64}
    with pytest.raises(ValueError):check(profile)


def test_no_findings_is_reported_without_applying_an_exception(profile):
    root,report,policy=profile;report['dependencies'][1]['vulns']=[]
    result=check(profile)
    assert result['status']=='passed; no findings' and result['raw_findings']==0 and not result['exceptions_applied']
