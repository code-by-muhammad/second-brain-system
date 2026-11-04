"""Unsupported native service actions refuse before state or manager access."""
import os
import struct
from types import SimpleNamespace
import pytest
import brain_cli as cli
import brain_process as processes
import brain_service as service
import brain_settings as cfg


@pytest.mark.parametrize('platform,machine',[('freebsd14','x86_64'),('darwin','arm64e'),('linux','aarch64')])
@pytest.mark.parametrize('action',['install','start','stop','hold','finish','cancel'])
@pytest.mark.parametrize('entry',['direct','cli'])
def test_unsupported_native_mutation_refuses_before_any_service_work(brain,monkeypatch,platform,machine,action,entry):
    before={p.relative_to(cfg.ROOT).as_posix():p.read_bytes() for p in cfg.ROOT.rglob('*') if p.is_file()}
    monkeypatch.setattr(service.sys,'platform',platform)
    monkeypatch.setattr(struct,'calcsize',lambda code:8)
    monkeypatch.setattr(os,'uname',lambda:SimpleNamespace(machine=machine),raising=False)
    def forbidden(*args,**kwargs):pytest.fail('Unqualified action reached service work')
    monkeypatch.setattr(processes,'validate_environment',forbidden)
    monkeypatch.setattr(service,'_control',forbidden)
    monkeypatch.setattr(service,'_start_native',forbidden)
    monkeypatch.setattr(service.subprocess,'run',forbidden)
    with pytest.raises(processes.OwnershipError,match='Unsupported candidate'):
        if entry=='cli':
            monkeypatch.setattr(cli.sys,'argv',['brain_cli.py','service',action,'--hold-id','a'*32,'--snapshot',str(cfg.ROOT/'missing')])
            cli.main()
        elif action=='hold':service.begin_transaction('a'*32,str(cfg.ROOT/'missing'))
        elif action=='finish':service.finish_transaction('a'*32)
        elif action=='cancel':service.cancel_transaction('a'*32)
        else:getattr(service,action)()
    assert {p.relative_to(cfg.ROOT).as_posix():p.read_bytes() for p in cfg.ROOT.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('platform',['darwin','linux'])
def test_definition_rendering_remains_available_without_native_action(brain,monkeypatch,platform):
    monkeypatch.setattr(service.sys,'platform',platform)
    monkeypatch.setattr(service.subprocess,'run',lambda *a,**kw:pytest.fail('Definition launched a process'))
    value=service.definition()
    assert isinstance(value,str) and 'brain_supervisor.py' in value
