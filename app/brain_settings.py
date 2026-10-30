"""Common configuration; device identity and secrets never belong in Git."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

VERSION = '0.1.8'
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


def validate_search_configuration(value):
    """Only the local provider/model family qualified by this product is used."""
    if (not isinstance(value,dict) or value.get('semantic_embedding_provider','fastembed')!='fastembed'
            or value.get('reranker_enabled',False) is not False):
        raise ValueError('Only local FastEmbed with reranking disabled is supported; preserve and review the configuration.')


def require_local_search_runtime():
    """Before upstream imports, reject unsupported providers and an unsealed SDK."""
    from importlib.util import find_spec
    path=STATE/'config.json'
    if path.is_symlink() or path.is_junction():raise ValueError('Linked search configuration is refused.')
    if path.exists():
        with path.open('rb') as source:raw=source.read(1_000_001)
        if len(raw)>1_000_000:raise ValueError('Search configuration exceeds its bound.')
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise ValueError('Search configuration must be a valid local JSON object.') from None
    else:value={}  # Pinned Basic Memory defaults are FastEmbed and no reranker.
    validate_search_configuration(value)
    if find_spec('litellm') is not None or any(Path(sys.executable).with_name(name).exists() for name in ('litellm.exe','litellm-proxy.exe')):
        raise ValueError('This release excludes LiteLLM; preserve the environment and reprovision from the verified locked release.')

def environment():
    # Basic Memory environment overrides must not defeat the source-safe file
    # config, change the indexed project or silently select a remote provider.
    env = {key:value for key,value in os.environ.items() if not key.startswith('BASIC_MEMORY_')}
    env.update(BRAIN_SYSTEM_ROOT=str(ROOT), BRAIN_VAULT=str(VAULT), BRAIN_STATE=str(STATE),
               BASIC_MEMORY_CONFIG_DIR=str(STATE), BASIC_MEMORY_FORCE_LOCAL='true',
               FASTEMBED_CACHE_PATH=str(ROOT/'models'), HF_HUB_DISABLE_TELEMETRY='1',
               HF_HUB_OFFLINE='1', DO_NOT_TRACK='1',BASIC_MEMORY_DISABLE_PERMALINKS='true',
               BASIC_MEMORY_ENSURE_FRONTMATTER_ON_SYNC='false',BASIC_MEMORY_FORMAT_ON_SAVE='false',
               BASIC_MEMORY_AUTO_UPDATE='false')
    return env

def require_maintainer():
    if ROLE != 'maintainer': raise PermissionError('This device is capture/search only; shared derived writes belong to the designated maintainer.')
    control=VAULT
    for part in ('02-Areas','AI-Memory','Wiki','Control.md'):
        control=control/part
        if control.is_symlink() or os.path.isjunction(control):
            raise PermissionError('Shared wiki ownership could not be verified; linked Control paths are refused.')
    if not control.resolve().is_relative_to(VAULT):
        raise PermissionError('Shared wiki ownership could not be verified; Control leaves the vault.')
    if control.exists():
        import re
        with control.open('rb') as stream: raw=stream.read(2_000_001)
        if len(raw)>2_000_000:
            raise PermissionError('Shared wiki ownership could not be verified; Control exceeds 2 MB.')
        try: ownership=raw.decode('utf-8').replace('\r\n','\n').replace('\r','\n')
        except UnicodeError as exc:
            raise PermissionError('Shared wiki ownership could not be verified; repair Control UTF-8 before an explicit handoff.') from exc
        match=re.search(r'(?m)^maintainer_device_id: ([a-z0-9-]+)$',ownership)
        if not match or match.group(1)!=DEVICE_ID: raise PermissionError('Shared wiki ownership belongs to a different device; perform an explicit handoff.')
