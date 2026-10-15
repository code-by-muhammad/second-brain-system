"""Exercise native startup adapters with unique synthetic jobs, never the owner's service."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import pytest
import brain_service as service
import brain_settings as cfg


def prepare_sleeper(monkeypatch):
    monkeypatch.setattr(cfg, 'PYTHON', Path(sys.executable))
    app = cfg.ROOT / 'app'
    app.mkdir()
    script = app / 'brain_supervisor.py'
    script.write_text(
        "import json,os,time\nfrom pathlib import Path\n"
        "p=Path(__file__).resolve().parents[1]/'state/native-starts.json'\n"
        "v=json.loads(p.read_text()) if p.exists() else []\n"
        "v.append(os.getpid()); p.write_text(json.dumps(v))\ntime.sleep(180)\n",
        encoding='utf-8',
    )
    return script, cfg.STATE / 'native-starts.json'


def wait_starts(marker, count):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            pids = json.loads(marker.read_text())
            if len(pids) >= count:
                return pids
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise AssertionError('The unique native service did not publish its start marker.')


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows task lifecycle.')
def test_native_windows_task_stop_start(brain, monkeypatch):
    task = 'SecondBrain-CI-' + uuid.uuid4().hex
    monkeypatch.setattr(service, 'TASK', task)
    script, marker = prepare_sleeper(monkeypatch)
    try:
        service.install()
        service.start()
        first = wait_starts(marker, 1)
        service.stop()
        service.start()
        second = wait_starts(marker, 2)
        assert len(second) == 2 and first[0] != second[1]
    finally:
        for command in (['schtasks.exe', '/End', '/TN', task],
                        ['schtasks.exe', '/Delete', '/TN', task, '/F']):
            subprocess.run(command, capture_output=True, **cfg.child_options())
        if marker.exists():
            for pid in json.loads(marker.read_text()):
                quoted = str(script).replace("'", "''")
                command = (
                    f"$testProcess = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
                    f"if ($testProcess -and $testProcess.CommandLine -and "
                    f"$testProcess.CommandLine.Contains('{quoted}')) "
                    f"{{ & taskkill.exe /PID {int(pid)} /T /F | Out-Null }}"
                )
                subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                               capture_output=True, **cfg.child_options())


@pytest.mark.skipif(sys.platform != 'linux', reason='Native Linux user-unit lifecycle.')
def test_native_linux_unit_stop_start(brain, monkeypatch):
    ready = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True)
    if ready.returncode:
        if os.environ.get('CI'):
            pytest.fail('CI must prepare its user service bus before the native lifecycle test.')
        pytest.skip('This local host has no active systemd user bus.')
    unit = 'second-brain-ci-' + uuid.uuid4().hex + '.service'
    monkeypatch.setattr(service, 'LINUX_UNIT', unit)
    script, marker = prepare_sleeper(monkeypatch)
    target = Path.home() / '.config/systemd/user' / unit
    try:
        service.install()
        service.start()
        first = wait_starts(marker, 1)
        service.stop()
        service.start()
        second = wait_starts(marker, 2)
        assert len(second) == 2 and first[0] != second[1]
    finally:
        subprocess.run(['systemctl', '--user', 'disable', '--now', unit], capture_output=True)
        if target.exists():
            target.unlink()
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        if marker.exists():
            for pid in json.loads(marker.read_text()):
                process = subprocess.run(['ps', '-p', str(pid), '-o', 'args='],
                                         capture_output=True, text=True)
                if str(script) in process.stdout:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
