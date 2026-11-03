"""Unqualified native service actions refuse before state or manager access."""
import pytest
import brain_cli as cli
import brain_process as processes
import brain_service as service
import brain_settings as cfg


@pytest.mark.parametrize('platform',['darwin','linux'])
@pytest.mark.parametrize('action',['install','start','stop'])
@pytest.mark.parametrize('entry',['direct','cli'])
def test_unqualified_native_mutation_refuses_before_any_service_work(brain,monkeypatch,platform,action,entry):
    before={p.relative_to(cfg.ROOT).as_posix():p.read_bytes() for p in cfg.ROOT.rglob('*') if p.is_file()}
    monkeypatch.setattr(service.sys,'platform',platform)
    def forbidden(*args,**kwargs):pytest.fail('Unqualified action reached service work')
    monkeypatch.setattr(processes,'validate_environment',forbidden)
    monkeypatch.setattr(service,'_control',forbidden)
    monkeypatch.setattr(service,'_start_native',forbidden)
    monkeypatch.setattr(service.subprocess,'run',forbidden)
    with pytest.raises(processes.OwnershipError,match='qualified for Windows only'):
        if entry=='cli':
            monkeypatch.setattr(cli.sys,'argv',['brain_cli.py','service',action])
            cli.main()
        else:getattr(service,action)()
    assert {p.relative_to(cfg.ROOT).as_posix():p.read_bytes() for p in cfg.ROOT.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('platform',['darwin','linux'])
def test_definition_rendering_remains_available_without_native_action(brain,monkeypatch,platform):
    monkeypatch.setattr(service.sys,'platform',platform)
    monkeypatch.setattr(service.subprocess,'run',lambda *a,**kw:pytest.fail('Definition launched a process'))
    value=service.definition()
    assert isinstance(value,str) and 'brain_supervisor.py' in value
