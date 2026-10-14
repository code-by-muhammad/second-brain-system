import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
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
    service.start()
    assert calls[0]==['launchctl','print','gui/501/'+service.LABEL]
    if registered:
        assert len(calls)==2
    else:
        assert calls[1]==['launchctl','bootstrap','gui/501',str(Path.home()/'Library/LaunchAgents'/f'{service.LABEL}.plist')]
    assert calls[-1]==['launchctl','kickstart','gui/501/'+service.LABEL]


@pytest.mark.skipif(sys.platform!='darwin',reason='Native launchd lifecycle requires macOS.')
def test_native_mac_stop_start_registration(brain,monkeypatch):
    domain=f'gui/{os.getuid()}'
    if subprocess.run(['launchctl','print',domain],capture_output=True).returncode:
        pytest.skip('The CI host has no logged-in GUI launchd domain.')
    label='local.second-brain.ci.'+uuid.uuid4().hex
    monkeypatch.setattr(service,'LABEL',label)
    monkeypatch.setattr(cfg,'PYTHON',Path(sys.executable))
    app=cfg.ROOT/'app'; app.mkdir()
    marker=cfg.STATE/'native-starts.json'
    # A synthetic sleeper exercises real launchd registration without private notes,
    # network, model requests or another wiki-maintenance schedule.
    script="import json,os,time\nfrom pathlib import Path\np=Path(__file__).resolve().parents[1]/'state/native-starts.json'\nv=json.loads(p.read_text()) if p.exists() else []\nv.append(os.getpid()); p.write_text(json.dumps(v))\ntime.sleep(180)\n"
    (app/'brain_supervisor.py').write_text(script,encoding='utf-8')
    plist=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    def wait_starts(count):
        end=time.monotonic()+40
        while time.monotonic()<end:
            try:
                pids=json.loads(marker.read_text())
                if len(pids)>=count: return pids
            except (OSError,ValueError): pass
            time.sleep(.2)
        raise AssertionError('Native launchd agent did not start after registration.')
    try:
        service.install(); service.start()
        first=wait_starts(1)
        service.stop()
        assert subprocess.run(['launchctl','print',domain+'/'+label],capture_output=True).returncode!=0
        service.start()
        second=wait_starts(2)
        assert len(second)==2 and second[0]!=second[1]
    finally:
        subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
        # Only terminate this test's interpreter if it survived bootout.
        if marker.exists():
            for pid in json.loads(marker.read_text()):
                p=subprocess.run(['ps','-p',str(pid),'-o','args='],capture_output=True,text=True)
                if str(app/'brain_supervisor.py') in p.stdout:
                    try: os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError: pass
        if plist.exists(): plist.unlink()
