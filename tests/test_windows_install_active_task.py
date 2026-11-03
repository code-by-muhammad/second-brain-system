"""Pure scheduler boundaries for refusing replacement of an active owned Task."""
from pathlib import Path
from types import SimpleNamespace
import pytest
import brain_service as service
import brain_settings as cfg
import brain_process as processes
from test_windows_stop_durability import windows

@pytest.mark.parametrize('state,enabled',[('Running',True),('Running',False),('Queued',True)])
def test_unheld_install_refuses_active_task_before_definition_or_create(windows,monkeypatch,state,enabled):
    task,seen,calls=windows;task.update(state=state,enabled=enabled)
    target=cfg.STATE/'service-task.xml';target.write_bytes(b'keep existing snapshot')
    def forbidden(*args,**kwargs):pytest.fail('Active Task reached definition/tempfile/Create boundary')
    monkeypatch.setattr(service,'definition',forbidden)
    monkeypatch.setattr(service.tempfile,'mkstemp',forbidden)
    with pytest.raises(processes.OwnershipError,match='Stop the owned Windows Task'):
        service.install()
    assert calls==[] and target.read_bytes()==b'keep existing snapshot'
    assert not (cfg.STATE/'service-transaction.json').exists()
    assert not (cfg.STATE/'service-control-status.json').exists()

@pytest.mark.parametrize('state,enabled',[('Ready',True),('Disabled',False),(None,None)])
def test_unheld_install_preserves_inactive_or_fresh_activation(windows,monkeypatch,state,enabled):
    task,seen,calls=windows
    if state is None:
        prior=None;expected=True
    else:
        task.update(state=state,enabled=enabled);prior=dict(task);expected=enabled
    inspected=[prior]
    def inspect(**kwargs):return inspected[-1]
    monkeypatch.setattr(service,'_windows_task',inspect)
    def create(command,**kwargs):
        assert '/Create' in command and '/F' in command
        assert '/XML' in command
        xml=Path(command[command.index('/XML')+1]).read_text(encoding='utf-16')
        after=service._windows_definition({'xml':xml,'owner_sid':'S-1-5-21-123','current_sid':'S-1-5-21-123'})
        assert after['enabled'] is expected
        after['state']='Ready' if expected else 'Disabled';inspected.append(after);calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess,'run',create)
    result=service.install()
    assert result['status']=='installed' and len(calls)==1
    assert inspected[-1]['enabled'] is expected
