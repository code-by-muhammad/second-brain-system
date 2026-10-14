"""Common configuration; device identity and secrets never belong in Git."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

VERSION = '0.1.5'
ROOT = Path(os.environ.get('BRAIN_SYSTEM_ROOT', str(Path(__file__).resolve().parents[1]))).resolve()
DEVICE_PATH = ROOT / 'device.json'
DEVICE = json.loads(DEVICE_PATH.read_text(encoding='utf-8')) if DEVICE_PATH.exists() else {}
VAULT = Path(os.environ.get('BRAIN_VAULT', DEVICE.get('vault', str(Path.home()/'Documents'/'Second-Brain')))).expanduser().resolve()
STATE = Path(os.environ.get('BRAIN_STATE', str(ROOT/'state'))).resolve()
ROLE = DEVICE.get('role', 'capture')
DEVICE_ID = DEVICE.get('device_id', 'unconfigured')
PORT = int(os.environ.get('BRAIN_PORT', DEVICE.get('port', 8765)))
PYTHON = Path(DEVICE.get('python', sys.executable))
if PYTHON.name.lower() == 'pythonw.exe': PYTHON = PYTHON.with_name('python.exe')

def child_options():
    import subprocess
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

def environment():
    env = os.environ.copy()
    env.update(BRAIN_SYSTEM_ROOT=str(ROOT), BRAIN_VAULT=str(VAULT), BRAIN_STATE=str(STATE),
               BASIC_MEMORY_CONFIG_DIR=str(STATE), BASIC_MEMORY_FORCE_LOCAL='true',
               FASTEMBED_CACHE_PATH=str(ROOT/'models'), HF_HUB_DISABLE_TELEMETRY='1',
               HF_HUB_OFFLINE='1', DO_NOT_TRACK='1')
    return env

def require_maintainer():
    if ROLE != 'maintainer': raise PermissionError('This device is capture/search only; shared derived writes belong to the designated maintainer.')
    control=VAULT/'02-Areas/AI-Memory/Wiki/Control.md'
    if control.exists():
        import re
        match=re.search(r'(?m)^maintainer_device_id: ([a-z0-9-]+)$',control.read_text(encoding='utf-8'))
        if not match or match.group(1)!=DEVICE_ID: raise PermissionError('Shared wiki ownership belongs to a different device; perform an explicit handoff.')
