"""Qualify the mixed v0.2.15 predecessor in disposable non-systemd containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_index_recovery import (
    PREDECESSOR, PREDECESSOR_MANIFEST, REMOTE, verify_codex, verify_deferred, verify_explicit_retry, verify_quiet, wait_for,
)

RETAINED = 'f7d3eb9d4f0df4af6274f7371b904b305dabc070'
RETAINED_MANIFEST = '6217dedb15689f9b65c5eeea9f9ab234dd5cbf209105139be588c63185ed0f5e'
MODEL_REPO = 'Qdrant/bge-small-en-v1.5-onnx-Q'
MODEL_REVISION = 'aa8f8b060edb00e03bfdd08813a2949946c8ba55'
MODEL_HASHES = {
    'config.json': '13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd',
    'model_optimized.onnx': '51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431',
    'special_tokens_map.json': '5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a',
    'tokenizer.json': 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66',
    'tokenizer_config.json': '0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53',
}


def run(*argv, check=True, timeout=600, env=None, cwd=None):
    return subprocess.run([str(value) for value in argv], check=check, timeout=timeout,
                          stdout=subprocess.PIPE, text=True, encoding='utf-8', env=env, cwd=cwd)


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def git(path, *args):
    return run('git', '-C', path, *args).stdout.strip()


def checkout(source, path, commit, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The clone's upload-pack checks the source .git and drops command-scope Git config.
    with tempfile.TemporaryDirectory(dir=path.parent) as temporary:
        config = Path(temporary) / 'gitconfig'
        run('git', 'config', '--file', config, 'safe.directory', source / '.git')
        run('git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', source, path,
            env=dict(os.environ, GIT_CONFIG_GLOBAL=str(config)))
    git(path, 'config', 'core.autocrlf', 'false')
    git(path, 'remote', 'set-url', 'origin', REMOTE)
    git(path, 'checkout', '--quiet', '--detach', commit)
    git(path, 'tag', '-f', version, commit)
    assert git(path, 'rev-parse', 'HEAD') == commit


def seed_model(python, cache):
    script = '''
import json, sys
from huggingface_hub import snapshot_download
print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], cache_dir=sys.argv[3],
                        allow_patterns=json.loads(sys.argv[4])))
'''
    snapshot = Path(run(python, '-I', '-B', '-c', script, MODEL_REPO, MODEL_REVISION, cache,
                        json.dumps(list(MODEL_HASHES))).stdout.strip())
    for name, expected in MODEL_HASHES.items():
        assert sha(snapshot / name) == expected
    ref = snapshot.parent.parent / 'refs/main'
    ref.parent.mkdir(exist_ok=True)
    ref.write_text(MODEL_REVISION, encoding='ascii')


def app_map(root):
    return {path.name: sha(path) for path in (root / 'app').glob('*.py')}


def old_images(root, home):
    conf = home / '.config/second-brain'
    finish = [root / 'device.json', root / 'bin/second-brain-mcp', home / '.profile',
              home / '.config/autostart/second-brain.desktop', conf / 'boot-start',
              conf / 'enabled', conf / 'second-brain.service']
    return {str(p): (sha(p), p.stat().st_mode & 0o777) for p in finish}


def cli_for(python, root):
    def call(*args, timeout=120):
        return json.loads(run(python, '-B', root / 'app/brain_cli.py', *args, timeout=timeout).stdout)
    return call


def auth_ready(call, version):
    def query():
        try:
            return call('health', timeout=45)
        except (ValueError, subprocess.CalledProcessError):
            return {}
    return wait_for(query, lambda row: row.get('status') == 'running' and row.get('version') == version, 120)


def verify_legacy_deferral(python, root, wizard):
    import_gate = '''
import os, signal, subprocess, sys, time
import psutil
from filelock import FileLock, Timeout
from pathlib import Path
python, root = sys.argv[1:]
root = Path(root)
bootstrap = root/'app/brain_index_bootstrap.py'
child = subprocess.Popen([python, '-B', bootstrap], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fd = None
try:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and child.poll() is None:
        for worker in psutil.Process(child.pid).children():
            try:
                argv = worker.cmdline()
                if argv[-3:] != ['reindex', '-p', 'brain'] or str(Path(python).parent/'bm') not in argv:
                    continue
                if os.getsid(worker.pid) != worker.pid:
                    continue
                fd = os.pidfd_open(worker.pid)
                if psutil.Process(worker.pid).ppid() != child.pid:
                    raise RuntimeError('Legacy worker parent changed.')
                signal.pidfd_send_signal(fd, signal.SIGSTOP)
                break
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
        if fd is not None:
            break
        time.sleep(.02)
    if fd is None:
        raise RuntimeError('Old bootstrap did not spawn a separate-session Basic Memory child.')
    try:
        with FileLock(str(root/'state/index-bootstrap.lock'), timeout=0):
            raise RuntimeError('Legacy bootstrap does not hold the index lock.')
    except Timeout:
        pass
    print('ready', flush=True)
    sys.stdin.readline()
finally:
    if fd is not None:
        try:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        except ProcessLookupError:
            pass
        os.close(fd)
    child.wait(timeout=180)
'''
    process = subprocess.Popen([str(python), '-I', '-B', '-c', import_gate, str(python), str(root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        import select
        assert select.select([process.stdout], [], [], 60)[0]
        assert process.stdout.readline().strip() == 'ready'
        result = wizard()
        assert result.returncode != 0
        status = json.loads((root / 'state/upgrade-status.json').read_bytes())
        assert status['status'] == 'update deferred; runtime not switched'
        assert not (root / 'state/service-transaction.json').exists()
        auth_ready(cli_for(python, root), '0.2.15')
    finally:
        process.stdin.close()
        assert process.wait(timeout=220) == 0


def inject_post_health_failure(target, root, previous_python):
    """Run the exact updater and reject the candidate only after real authenticated health."""
    update = types.ModuleType('_test_update')
    path = target / 'update.py'
    update.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), update.__dict__)
    original = update.wait_health
    observed = []

    class CandidateRejected(Exception):
        pass

    def reject(python, selected_root, version, *args, **kwargs):
        health = original(python, selected_root, version, *args, **kwargs)
        if version == '0.2.16':
            tx = json.loads((root / 'state/service-transaction.json').read_bytes())
            assert tx['stage'] == 'started' and health['status'] == 'running'
            observed.append(version)
            raise CandidateRejected('Synthetic failure after real candidate health')
        return health

    update.wait_health = reject
    argv = sys.argv
    try:
        sys.argv = [str(path), '--root', str(root), '--version', 'v0.2.16', '--clients',
                    'claude-code,codex', '--skip-desktop-refresh']
        try:
            update.main()
        except Exception as error:
            assert isinstance(error.__cause__, CandidateRejected)
        else:
            raise AssertionError('The injected acceptance error did not reach the held updater.')
    finally:
        sys.argv = argv
    assert observed == ['0.2.16']
    status = json.loads((root / 'state/upgrade-status.json').read_bytes())
    assert status['status'] == 'update failed; previous runtime verified'
    assert not (root / 'state/service-transaction.json').exists()
    state = cli_for(previous_python, root)('service', 'inspect')
    assert state['status'] == 'inspected' and state['native']['enabled'] is True
    auth_ready(cli_for(previous_python, root), '0.2.15')


def cold_stdio(python, root, home, version):
    call = cli_for(python, root)
    assert call('service', 'stop', '--wait-seconds', '60')['status'] == 'stopped'
    # Simulate a later boot with persistent activation enabled but no running keeper.
    activation = home / '.config/second-brain/enabled'
    assert activation.read_bytes() == b'false\n'
    activation.write_bytes(b'true\n')
    script = '''
import asyncio, sys
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
async def probe():
    async with Client(StdioTransport(command=sys.argv[1], args=[]), timeout=75, init_timeout=65) as client:
        methods = {tool.name for tool in await client.list_tools()}
        assert {'brain_context', 'brain_read', 'brain_health'} <= methods
asyncio.run(probe())
'''
    if version == '0.2.15':
        run(home / '.config/second-brain/boot-start', timeout=90)
        auth_ready(call, version)
    run(python, '-I', '-B', '-c', script, root / 'bin/second-brain-mcp', timeout=90)
    auth_ready(call, version)


def install_mock_crontab(home):
    folder = home / 'fixture-bin'
    folder.mkdir()
    table = home / 'fixture-crontab'
    table.write_text('# Unrelated entry stays byte-identical\n', encoding='utf-8')
    executable = folder / 'crontab'
    executable.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        '  -l) cat "$HOME/fixture-crontab";;\n'
        '  -) cat > "$HOME/fixture-crontab";;\n'
        '  *) exit 2;;\nesac\n',
        encoding='utf-8',
    )
    executable.chmod(0o700)
    os.environ['PATH'] = str(folder) + os.pathsep + os.environ['PATH']
    return table


def headless_repair(python, root):
    record = root / 'state/user-service-keeper.json'
    before = json.loads(record.read_bytes())['keeper']
    fd = os.pidfd_open(before['pid'])
    try:
        stat_line = Path(f"/proc/{before['pid']}/stat").read_bytes()
        start_ticks = int(stat_line.rsplit(b') ', 1)[1].split()[19])
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        command = Path(f"/proc/{before['pid']}/cmdline").read_bytes().split(b'\0')[:-1]
        assert start_ticks == before['start_ticks'] and boot_id == before['boot_id']
        assert command == [os.fsencode(python), os.fsencode(root / 'app/brain_linux_user_service.py'), b'supervise']
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        os.close(fd)
    run(python, '-B', root / 'app/brain_linux_user_service.py', 'boot', timeout=90)
    auth_ready(cli_for(python, root), '0.2.16')
    after = json.loads(record.read_bytes())['keeper']
    assert (after['pid'], after['start_ticks']) != (before['pid'], before['start_ticks'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit')
    parser.add_argument('--model-cache', type=Path)
    parser.add_argument('--scenario', choices=('forward', 'rollback', 'legacy-deferral', 'first-registration'))
    args = parser.parse_args()
    if args.model_cache:
        assert os.environ.get('GITHUB_ACTIONS') == 'true' and args.model_cache.is_absolute()
        seed_model(sys.executable, args.model_cache)
        return
    if not args.commit or not args.scenario:
        parser.error('--commit and --scenario are required for the native scenario.')
    home = Path.home()
    source = Path('/candidate')
    assert (source / 'release-manifest.json').is_file()
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.geteuid() != 0
    assert home == Path(pwd.getpwuid(os.geteuid()).pw_dir) == Path('/home/sb fixture')
    assert Path('/proc/1/comm').read_text().strip() != 'systemd'
    root, vault = home / 'runtime', home / 'synthetic notes'
    old, target = (root / 'releases' / version for version in ('v0.2.15', 'v0.2.16'))
    checkout(source, old, PREDECESSOR, 'v0.2.15')
    checkout(source, target, args.commit, 'v0.2.16')
    assert sha(old / 'release-manifest.json') == PREDECESSOR_MANIFEST
    run('uv', 'sync', '--locked', '--no-dev', '--project', old, '--python', sys.executable)
    previous_python = old / '.venv/bin/python'
    seed_model(previous_python, root / 'models')
    os.environ['HF_HUB_OFFLINE'] = '1'
    shutil.copytree(old / 'starter', vault)
    (home / '.codex').mkdir()
    (home / '.claude').mkdir()
    (home / '.profile').write_bytes(b'# synthetic login fixture\n# preserved byte \xff')
    (home / '.profile').chmod(0o600)
    cron = install_mock_crontab(home)
    registration = [] if args.scenario == 'first-registration' else ['--install-service']
    run(previous_python, '-I', '-B', old / 'install.py', '--root', root, '--vault', vault,
        '--role', 'capture', '--reuse-python', previous_python, '--configure-clients',
        '--client', 'claude-code', '--include-hooks', *registration)
    cron_before = sha(cron)
    original = app_map(root)
    expected = json.loads((old / 'release-manifest.json').read_bytes())['sha256']
    assert original == {name.removeprefix('app/'): value for name, value in expected.items() if name.startswith('app/')}
    call = cli_for(previous_python, root)
    preimages = None
    if registration:
        call('service', 'start')
        auth_ready(call, '0.2.15')
        verify_quiet(str(previous_python), root)
        preimages = old_images(root, home)
    git(old, 'checkout', '--quiet', '--detach', RETAINED)
    git(old, 'tag', '-f', 'v0.2.15', RETAINED)
    assert sha(old / 'release-manifest.json') == RETAINED_MANIFEST
    assert app_map(root) == original and previous_python.is_file()

    def wizard():
        return run(sys.executable, '-I', '-B', target / 'setup_device.py', '--root', root,
                   '--yes', '--role', 'capture', check=False)

    if args.scenario == 'legacy-deferral':
        verify_legacy_deferral(previous_python, root, wizard)
        assert app_map(root) == original and old_images(root, home) == preimages
    elif args.scenario == 'rollback':
        verify_quiet(str(previous_python), root)
        script = '''
import runpy, sys
from pathlib import Path
harness, target, root = map(Path, sys.argv[1:])
inject = runpy.run_path(str(harness))['inject_post_health_failure']
inject(target, root, Path(sys.executable))
'''
        run(previous_python, '-I', '-B', '-c', script, Path(__file__).resolve(), target, root, cwd=target)
        assert app_map(root) == original and old_images(root, home) == preimages
        cold_stdio(previous_python, root, home, '0.2.15')
    else:
        if registration:
            verify_quiet(str(previous_python), root)
        result = wizard()
        if result.returncode and registration:
            verify_deferred(root)
            verify_quiet(str(previous_python), root)
            result = wizard()
        result.check_returncode()
        report = home / 'wizard.txt'
        report.write_text(result.stdout, encoding='utf-8')
        verify_codex(report)
        candidate_python = target / '.venv/bin/python'
        call = cli_for(candidate_python, root)
        auth_ready(call, '0.2.16')
        verify_explicit_retry(call, vault)
        cold_stdio(candidate_python, root, home, '0.2.16')
        headless_repair(candidate_python, root)
        acceptance = run(sys.executable, '-I', '-B', target / 'setup_device.py', '--root', root,
                         '--check', '--selftest', '--clients', 'claude-code,codex')
        assert sum(line.startswith('PASS   self-test ') for line in acceptance.stdout.splitlines()) == 6
    assert sha(cron) == cron_before
    print(json.dumps({'scenario': args.scenario, 'candidate_commit': args.commit,
                      'candidate_tree': git(target, 'rev-parse', 'HEAD^{tree}'),
                      'candidate_manifest_sha256': sha(target / 'release-manifest.json'),
                      'installed_predecessor_manifest_sha256': PREDECESSOR_MANIFEST,
                      'retained_predecessor_manifest_sha256': RETAINED_MANIFEST,
                      'model_revision': MODEL_REVISION, 'status': 'verified'}))


if __name__ == '__main__':
    main()
