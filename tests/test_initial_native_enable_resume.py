"""Initial-journal/service intent composition with a fully fake native manager."""
import hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import brain_settings as cfg
import brain_service as service
import brain_process as processes

@pytest.fixture
def setup_case(tmp_path,monkeypatch,request):
    platform=request.param
    source=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('native_initial_installer',source/'install.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_candidate_platform',lambda:None)
    monkeypatch.setattr(module,'sys',SimpleNamespace(**{**vars(sys),'platform':platform}))
    root,vault=tmp_path/'runtime',tmp_path/'vault';vault.mkdir()
    support=module.setup_helpers();monkeypatch.setattr(support,'host_context',lambda:None)
    monkeypatch.setattr(support,'service_python',lambda python:None)
    monkeypatch.setattr(module,'setup_helpers',lambda:support)
    monkeypatch.setattr(service,'sys',SimpleNamespace(platform=platform))
    monkeypatch.setattr(service,'_qualified_native_platform',lambda:None)
    monkeypatch.setattr(processes,'validate_environment',lambda:None)
    monkeypatch.setattr(processes,'listener_identity',lambda:None)
    monkeypatch.setattr(cfg,'ROOT',root);monkeypatch.setattr(cfg,'STATE',root/'state')
    payload=b'synthetic current exact known registration\n';sha=hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(service,'definition',lambda *args,**kwargs:payload.decode())
    native=SimpleNamespace(path=root/'state/native-registration',enabled=None,attempts=0)
    native.known={payload:[str(cfg.PYTHON),'synthetic-supervisor.py']}
    def inspect(**kwargs):return {'file_present':native.path.exists(),'loaded':False,'running':False,'main_pid':None,
        'enabled':native.enabled,'definition_sha256':hashlib.sha256(native.path.read_bytes()).hexdigest() if native.path.exists() else None}
    native.inspect=inspect
    def install(raw,*,enabled,**kwargs):
        native.attempts+=1;native.path.write_bytes(raw);native.enabled=False
        if native.attempts==1:raise OSError('Synthetic enable failed after exact file write')
        native.enabled=enabled
    native.install=install
    def restore(prior):
        if prior['file_present']!=native.path.exists():raise ValueError('Prior file presence changed; preserve evidence')
        native.enabled=prior['enabled']
    native.restore_activation=restore
    monkeypatch.setattr(service,'_mac_registration',lambda:native)
    monkeypatch.setattr(service,'_linux_registration',lambda:native)
    calls=[]
    def control(command,**kwargs):
        action=command[-1];calls.append(action)
        result=getattr(service,action)()
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(support,'run',control)
    arguments=['install.py','--root',str(root),'--vault',str(vault),'--reuse-python',sys.executable,'--install-service']
    def run(*extra):
        monkeypatch.setattr(sys,'argv', [*arguments,*extra]);module.sys.argv=sys.argv;module.main()
    return SimpleNamespace(module=module,root=root,native=native,calls=calls,run=run,sha=sha)

@pytest.mark.parametrize('setup_case',['darwin','linux'],indirect=True)
def test_initial_resume_retries_exact_file_after_installer_enable_failure(setup_case):
    h=setup_case
    with pytest.raises(OSError,match='enable failed'):h.run()
    journal_path=h.root/'state/install-transaction.json';journal=json.loads(journal_path.read_text())
    history=json.loads((h.root/'state/service-control-status.json').read_text())
    assert journal['stage']=='service-intent' and history['status']=='install failed'
    assert history['prior_native']['file_present'] is False and history['requested_enabled'] is True
    assert h.native.path.read_bytes() and h.native.enabled is False
    auth=(h.root/'state/client-access.json').read_bytes();device=(h.root/'device.json').read_bytes()
    h.run('--resume-install',journal['install_id'])
    assert h.native.attempts==2 and h.native.enabled is True
    assert json.loads(journal_path.read_text())['stage']=='complete'
    assert (h.root/'state/client-access.json').read_bytes()==auth and (h.root/'device.json').read_bytes()==device
    assert h.calls==['install','inspect','install']


@pytest.mark.parametrize('setup_case',['darwin','linux'],indirect=True)
@pytest.mark.parametrize('change',['no-receipt','completed-receipt','requested-disabled','service-done','wrong-uuid','changed-source'])
def test_resume_preserves_disabled_or_unmatched_initial_setup(setup_case,monkeypatch,change):
    h=setup_case
    with pytest.raises(OSError,match='enable failed'):h.run()
    journal_path=h.root/'state/install-transaction.json';journal=json.loads(journal_path.read_text())
    status=h.root/'state/service-control-status.json'
    if change=='no-receipt':status.unlink()
    elif change in ('completed-receipt','requested-disabled'):
        history=json.loads(status.read_text())
        if change=='completed-receipt':history['status']='installed'
        else:history['requested_enabled']=False
        status.write_text(json.dumps(history))
    elif change=='service-done':journal['stage']='service-done';journal_path.write_text(json.dumps(journal))
    elif change=='changed-source':monkeypatch.setattr(h.module,'_fresh_source',lambda:{'changed':'f'*64})
    before={str(path.relative_to(h.root)):path.read_bytes() for path in h.root.rglob('*') if path.is_file()}
    with pytest.raises(ValueError):h.run('--resume-install','f'*32 if change=='wrong-uuid' else journal['install_id'])
    assert h.native.attempts==1 and h.native.enabled is False
    assert {str(path.relative_to(h.root)):path.read_bytes() for path in h.root.rglob('*') if path.is_file()}==before
    assert h.calls==(['install'] if change in ('wrong-uuid','changed-source') else ['install','inspect'])


@pytest.mark.parametrize('setup_case',['darwin','linux'],indirect=True)
@pytest.mark.parametrize('history_kind',['matching','none','completed','other-target','prior-present-disabled','prior-present-enabled','requested-disabled'])
def test_inspection_exposes_only_exact_pending_fresh_enable_intent(setup_case,history_kind):
    h=setup_case;(h.root/'state').mkdir(parents=True)
    payload=next(iter(h.native.known));h.native.path.write_bytes(payload);h.native.enabled=False
    history={'status':'install failed','prior_native':{'file_present':False,'enabled':None,'definition_sha256':None},
        'requested_enabled':True,'expected_native_definition_sha256':h.sha}
    if history_kind=='completed':history['status']='installed'
    elif history_kind=='other-target':history['expected_native_definition_sha256']='e'*64
    elif history_kind.startswith('prior-present'):
        history['prior_native']={'file_present':True,'enabled':history_kind.endswith('-enabled'),'definition_sha256':h.sha}
    elif history_kind=='requested-disabled':history['requested_enabled']=False
    if history_kind!='none':(h.root/'state/service-control-status.json').write_text(json.dumps(history))
    before={p:p.read_bytes() for p in (h.root/'state').iterdir() if p.is_file()}
    result=service.inspect()
    assert result['pending_initial_install_enable'] is (history_kind=='matching')
    assert result['expected_native_definition_sha256']==h.sha
    assert 'prior_native' not in result and 'requested_enabled' not in result
    assert all(path.read_bytes()==raw for path,raw in before.items())


@pytest.mark.parametrize('setup_case',['darwin','linux'],indirect=True)
@pytest.mark.parametrize('damage',['malformed','non-dict','unreadable','matching-invalid'])
def test_damaged_native_intent_preserves_inspection_and_refuses_enable_resume(setup_case,monkeypatch,damage):
    import brain_markdown
    h=setup_case
    with pytest.raises(OSError,match='enable failed'):h.run()
    journal=json.loads((h.root/'state/install-transaction.json').read_bytes())
    status=h.root/'state/service-control-status.json'
    if damage=='malformed':status.write_bytes(b'invalid synthetic history JSON')
    elif damage=='non-dict':status.write_text('[]')
    elif damage=='matching-invalid':
        value=json.loads(status.read_bytes());value['requested_enabled']='invalid-bool';status.write_text(json.dumps(value))
    else:
        reader=brain_markdown.bounded_json_file
        def denied(path,*args,**kwargs):
            if path==status:raise PermissionError('Synthetic native receipt read denied')
            return reader(path,*args,**kwargs)
        monkeypatch.setattr(brain_markdown,'bounded_json_file',denied)
    before={p:p.read_bytes() for p in h.root.rglob('*') if p.is_file()}
    result=service.inspect()
    assert result['status']=='inspected' and result['native']['file_present'] is True
    assert result['native']['enabled'] is False and result['native']['definition_sha256']==h.sha
    assert result['expected_native_definition_sha256']==h.sha and result['pending_initial_install_enable'] is False
    assert {p:p.read_bytes() for p in h.root.rglob('*') if p.is_file()}==before
    with pytest.raises(ValueError,match='disabled'):h.run('--resume-install',journal['install_id'])
    assert h.native.attempts==1 and h.native.enabled is False and h.calls==['install','inspect']
    assert {p:p.read_bytes() for p in h.root.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('setup_case',['darwin','linux'],indirect=True)
def test_native_inspect_does_not_swallow_unexpected_intent_errors(setup_case,monkeypatch):
    h=setup_case;(h.root/'state').mkdir(parents=True)
    h.native.path.write_bytes(next(iter(h.native.known)));h.native.enabled=False
    def unexpected(*args):raise RuntimeError('Synthetic unexpected intent fault')
    monkeypatch.setattr(service,'_native_install_intent',unexpected)
    with pytest.raises(RuntimeError,match='unexpected intent fault'):service.inspect()
    assert h.native.attempts==0 and h.native.enabled is False
