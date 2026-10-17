"""Actual checkout entrypoints; all homes, software, venvs and deploys are synthetic."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap
import venv

import pytest


SOURCE = Path(os.environ.get('R153_SOURCE', Path(__file__).resolve().parents[1])).resolve()
PARENT = r'''
import json, os, runpy, subprocess, sys
from pathlib import Path
release, root, vault, events, route = map(Path, sys.argv[1:])
route = str(route)
namespace = runpy.run_path(str(release / 'install.py'))
real_run = subprocess.run
def record(value):
    with events.open('a', encoding='utf-8') as output:
        output.write(json.dumps(value) + '\n')
def run(arguments, **kwargs):
    if arguments[0] == 'uv':
        assert route == 'bootstrap-child', 'Reuse must not run uv.'
        assert arguments == ['uv', 'sync', '--locked', '--no-dev', '--project', str(release), '--python', '3.12']
        record({'kind': 'uv-intercepted', 'argv': arguments})
        return subprocess.CompletedProcess(arguments, 0)
    if str(release / 'install.py') in arguments or '-c' in arguments:
        record({'kind': 'actual-child', 'argv': arguments})
    else:
        # Actual installer ACL operations are allowed only on disposable fixture paths.
        assert os.name == 'nt' and Path(arguments[0]).name.lower() == 'icacls.exe'
        assert Path(arguments[1]).resolve().is_relative_to(root.resolve())
    return real_run(arguments, **kwargs)
subprocess.run = run
sys.argv = [str(release / 'install.py'), '--root', str(root), '--vault', str(vault), '--role', 'capture']
if route == 'model-child':
    selected = release / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    sys.argv.extend(['--reuse-python', str(selected), '--download-model'])
namespace['main']()
'''
MODEL = r'''
import json, os, sys
from pathlib import Path
class TextEmbedding:
    def __init__(self, **arguments):
        Path(os.environ['R153_MODEL_EVENT']).write_text(json.dumps({
            'arguments': arguments, 'python': sys.executable, 'module': __file__,
            'json_module': json.__file__, 'isolated': sys.flags.isolated}), encoding='utf-8')
'''


def hashes(folder):
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()}


def documented_prefix(route, release, python):
    """Use the actual documentation, including its Python flags; don't invent -I."""
    groups = []
    for name in ('README.md', 'docs/DEVICE-SETUP.md'):
        choices = []
        for line in (SOURCE / name).read_text(encoding='utf-8').splitlines():
            if route == 'updater-help' and '/update.py --root ' in line:
                prefix = line.split(' --root ', 1)[0]
                prefix = prefix.replace('<candidate v0.1.7 Python>', shlex.quote(python.as_posix()))
                prefix = prefix.replace('<verified v0.1.7 checkout>', shlex.quote(release.as_posix()))
                choices.append(shlex.split(prefix))
            elif line.startswith('uv run --no-project --python 3.12 '):
                tail = line.split('uv run --no-project --python 3.12 ', 1)[1]
                if route == 'manifest-verifier' and ' -c ' in tail:
                    arguments = shlex.split(tail)
                    assert arguments[0] == 'python'
                    choices.append([str(python), *arguments[1:]])
                elif route == 'installer-help' and 'install.py --' in tail:
                    arguments = shlex.split(tail.split('install.py', 1)[0] + 'install.py')
                    if arguments[0] == 'python':
                        arguments = arguments[1:]
                    assert arguments[-1] == 'install.py'
                    choices.append([str(python), *arguments[:-1], str(release / 'install.py')])
        assert choices, 'Missing actual supported entrypoint in ' + name
        assert all(choice == choices[0] for choice in choices), 'Platform examples disagree in ' + name
        groups.append(choices[0])
    assert groups[0] == groups[1], 'README/device guide entrypoint prefixes disagree.'
    return groups[0]


@pytest.mark.parametrize('route,shadow', [
    ('updater-help', True), ('updater-help', False),
    ('manifest-verifier', True), ('manifest-verifier', False),
    ('installer-help', True), ('installer-help', False),
    ('bootstrap-child', True), ('bootstrap-child', False),
    ('model-child', True), ('model-child', False),
], ids=[
    'updater-help-untracked-json', 'updater-help-clean-control',
    'manifest-verifier-untracked-json', 'manifest-verifier-clean-control',
    'installer-help-untracked-json', 'installer-help-clean-control',
    'bootstrap-child-untracked-json', 'bootstrap-child-clean-control',
    'model-child-untracked-json', 'model-child-clean-control',
])
def test_supported_software_entrypoint_ignores_untracked_checkout_module(tmp_path, route, shadow):
    release, root, vault = tmp_path / 'software with spaces', tmp_path / 'runtime', tmp_path / 'vault'
    home, appdata, scratch = tmp_path / 'home', tmp_path / 'appdata', tmp_path / 'process temp'
    for path in (release / 'app', root, vault, home / '.codex', home / '.claude', appdata, scratch):
        path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        tmp_path.chmod(0o700)
    copied = ['install.py', 'update.py', 'pyproject.toml', 'uv.lock']
    copied += [p.relative_to(SOURCE).as_posix() for p in (SOURCE / 'app').glob('*.py')]
    for name in copied:
        shutil.copy2(SOURCE / name, release / name)
    software = {name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in copied}
    (release / 'release-manifest.json').write_text(json.dumps({
        'version': '0.1.7', 'repository': 'code-by-muhammad/second-brain-system',
        'sha256': software}), encoding='utf-8')
    (home / '.codex/config.toml').write_text('model = "synthetic-preserved"\n', encoding='utf-8')
    (home / '.claude/settings.json').write_text('{"owner_metadata":["keep"]}\n', encoding='utf-8')
    original_device = {'vault': str(vault.resolve()), 'role': 'capture', 'device_id': 'r153-fixture',
                       'owner_metadata': {'keep': ['synthetic', 17]}}
    (root / 'device.json').write_text(json.dumps(original_device), encoding='utf-8')
    owner_before = (hashes(home), hashes(appdata))
    device_before = (root / 'device.json').read_bytes()
    environment = release / '.venv'
    venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = environment / ('Lib/site-packages' if os.name == 'nt' else
                           'lib/python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor) + '/site-packages')
    (site / 'fastembed.py').write_text(textwrap.dedent(MODEL), encoding='utf-8')
    marker, model_event, events = tmp_path / 'untracked-json-imported', tmp_path / 'model-event.json', tmp_path / 'child-events.jsonl'
    if shadow:
        (release / 'json.py').write_text('from pathlib import Path\nPath(' + repr(str(marker))
            + ').write_bytes(b"harmless synthetic import marker")\n'
              'raise RuntimeError("Untracked synthetic json module executed.")\n', encoding='utf-8')
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('BRAIN_', 'BASIC_MEMORY_', 'PYTHON', 'GIT_', 'R133_', 'R148_', 'R149_', 'R150_', 'R153_'))}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(appdata), XDG_CONFIG_HOME=str(home / 'config'),
               TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch), HF_HUB_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', R153_MODEL_EVENT=str(model_event))
    probe = subprocess.run([str(python), '-I', '-B', '-c',
        'import json,platform,sys; print(json.dumps({"python":sys.executable,"prefix":sys.prefix,'
        '"version":platform.python_version(),"isolated":sys.flags.isolated,"json_module":json.__file__}))'],
        env=env, cwd=release, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    interpreter = json.loads(probe.stdout)
    assert Path(interpreter['python']).absolute() == python.absolute()
    assert Path(interpreter['prefix']).resolve() == environment.resolve()
    if route in ('updater-help', 'manifest-verifier', 'installer-help'):
        command = documented_prefix(route, release, python)
        if route.endswith('-help'):
            command.append('--help')
    else:
        parent = tmp_path / 'isolated-installer-parent.py'
        parent.write_text(textwrap.dedent(PARENT), encoding='utf-8')
        command = [str(python), '-I', '-B', str(parent), str(release), str(root), str(vault), str(events), route]
    process = subprocess.run(command, env=env, cwd=release, capture_output=True,
                             text=True, encoding='utf-8', timeout=45, check=False)
    child_events = [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()] if events.exists() else []
    receipt = {'route': route, 'shadow': shadow, 'source': str(SOURCE), 'software_sha256': software,
               'documentation_sha256': {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
                                        for name in ('README.md', 'docs/DEVICE-SETUP.md')},
               'argv': command, 'interpreter_probe': interpreter, 'returncode': process.returncode,
               'stdout_sha256': hashlib.sha256(process.stdout.encode('utf-8')).hexdigest(),
               'stderr_sha256': hashlib.sha256(process.stderr.encode('utf-8')).hexdigest(),
               'marker_executed': marker.exists(), 'child_events': child_events}
    (tmp_path / 'entrypoint-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    assert software == {name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in copied}
    assert (hashes(home), hashes(appdata)) == owner_before
    assert not marker.exists(), 'Allowed untracked json.py executed in supported ' + route
    assert process.returncode == 0, 'Synthetic supported entrypoint failed: ' + process.stderr[-1500:]
    if route.endswith('-help'):
        assert 'usage:' in process.stdout and not events.exists()
        assert (root / 'device.json').read_bytes() == device_before
    elif route == 'manifest-verifier':
        assert process.stdout.strip() == '0.1.7' and not events.exists()
        assert (root / 'device.json').read_bytes() == device_before
    else:
        installed = json.loads((root / 'device.json').read_text(encoding='utf-8'))
        assert installed['owner_metadata'] == original_device['owner_metadata']
        assert installed['role'] == 'capture' and installed['device_id'] == 'r153-fixture'
        assert installed['python'] == str(python)
        assert all((root / 'app' / Path(name).name).read_bytes() == (release / name).read_bytes()
                   for name in copied if name.startswith('app/'))
        assert '"status": "installed"' in process.stdout
        children = [event['argv'] for event in child_events if event['kind'] == 'actual-child']
        assert len(children) == 1 and children[0][0] == str(python)
        if route == 'bootstrap-child':
            assert len([event for event in child_events if event['kind'] == 'uv-intercepted']) == 1
            assert children[0].count('--reuse-python') == 1 and children[0][-1] == str(python)
            assert not model_event.exists()
        else:
            assert not any(event['kind'] == 'uv-intercepted' for event in child_events)
            observed = json.loads(model_event.read_text(encoding='utf-8'))
            assert observed['python'] == str(python) and Path(observed['module']).resolve() == (site / 'fastembed.py').resolve()
            assert not Path(observed['json_module']).resolve().is_relative_to(release.resolve())
            assert observed['arguments'] == {'model_name': 'BAAI/bge-small-en-v1.5', 'cache_dir': str(root / 'models'), 'threads': 4}
