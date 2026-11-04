"""Host-selection and exception boundaries; no package/network/service activity."""
from datetime import date
import hashlib,importlib.util
from pathlib import Path
import pytest

GATE=Path(__file__).resolve().parents[1]/'check_dependency_audit.py'
spec=importlib.util.spec_from_file_location('portable_audit_proposal',GATE)
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
PROFILES=[('win32','AMD64','security-exceptions.json'),
          ('darwin','arm64','security-exceptions-darwin-arm64.json'),
          ('darwin','x86_64','security-exceptions-darwin-x86_64.json'),
          ('linux','x86_64','security-exceptions-linux-x86_64.json')]

@pytest.mark.parametrize('system,machine,name',PROFILES)
def test_native_selection_and_exact_profile_refusal(tmp_path,system,machine,name):
    selected,canonical=audit.profile_path(system,machine)
    assert selected.name==name and canonical==machine
    source=tmp_path/'static.py';source.write_bytes(b'# local static-token transport\n')
    policy={'format':1,'platform':system,'machine':machine,
            'expected_audit_versions':{'basic-memory':'0.23.2','fastmcp':'4.0.0b1'},
            'source_sha256':{'static.py':hashlib.sha256(source.read_bytes()).hexdigest()},
            'exceptions':[{'package':'fastmcp','version':'4.0.0b1','id':'GHSA-vqqp-x5qm-5qp4',
                           'review_by':'2026-11-12','disposition':'not_reachable_in_qualified_profile'}]}
    report={'dependencies':[{'name':'basic-memory','version':'0.23.2','vulns':[]},
                            {'name':'fastmcp','version':'4.0.0b1','vulns':[{'id':'GHSA-vqqp-x5qm-5qp4'}]}]}
    options={'expected_platform':system,'expected_machine':machine}
    accepted=audit.assess(report,policy,tmp_path,today=date(2026,11,12),**options)
    assert accepted['raw_findings']==1 and len(accepted['exceptions_applied'])==1 and not accepted['unresolved']
    for bad in ({**options,'expected_platform':'unsupported'},{**options,'expected_machine':'unsupported'}):
        with pytest.raises(ValueError,match='Unsupported'):audit.assess(report,policy,tmp_path,**bad)
    with pytest.raises(ValueError,match='expired'):audit.assess(report,policy,tmp_path,today=date(2026,11,13),**options)
    source.write_bytes(b'# changed transport\n')
    with pytest.raises(ValueError,match='Protected security profile'):audit.assess(report,policy,tmp_path,today=date(2026,11,4),**options)

@pytest.mark.parametrize('system,machine',[('linux','aarch64'),('win32','ARM64'),('darwin','i386'),('freebsd','x86_64')])
def test_unknown_architecture_or_os_cannot_select_another_profile(system,machine):
    with pytest.raises(ValueError,match='No reviewed'):audit.profile_path(system,machine)

def test_windows_machine_case_normalization_is_not_an_architecture_alias():
    assert audit.profile_path('win32','amd64')[1]=='AMD64'
    with pytest.raises(ValueError):audit.profile_path('win32','x86_64')
