from pathlib import Path
from types import SimpleNamespace
import pytest
import brain_service as service
import brain_settings as cfg


@pytest.mark.parametrize('registered',[False,True])
def test_mac_start_restores_an_unloaded_agent(brain,monkeypatch,registered):
    monkeypatch.setattr(service.sys,'platform','darwin')
    monkeypatch.setattr(service.os,'getuid',lambda:501,raising=False)
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0 if registered or args[1]!='print' else 1)
    monkeypatch.setattr(service.subprocess,'run',run)
    # This fixture tests only the launchctl registration adapter. Ownership,
    # heartbeat and authentication are independent integration contracts.
    service._start_native()
    assert calls[0]==['launchctl','print','gui/501/'+service.LABEL]
    if registered:
        assert len(calls)==2
    else:
        assert calls[1]==['launchctl','bootstrap','gui/501',str(Path.home()/'Library/LaunchAgents'/f'{service.LABEL}.plist')]
    assert calls[-1]==['launchctl','kickstart','gui/501/'+service.LABEL]
