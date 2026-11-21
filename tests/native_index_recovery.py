"""Exercise index convergence on the installed service in the disposable setup job."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


REMOTE = 'https://github.com/code-by-muhammad/second-brain-system.git'
PREDECESSOR = '43a36d9ea9bf63f7c23023c935a505fc97af413a'
PREDECESSOR_MANIFEST = '7b283ba308165d7848629e8f16b98a551abeefbdbdffa2b314092ef67bb7acee'


def wait_for(call, predicate, timeout=480):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = call()
        if predicate(current):
            return current
        time.sleep(2)
    raise RuntimeError('The native fixture did not reach the required state.')


def seed_0215(root, vault):
    previous = root / 'releases/v0.2.15'
    previous.mkdir(parents=True)
    git = ['git', '-C', str(previous)]
    subprocess.run(git + ['init', '-q'], check=True)
    subprocess.run(git + ['config', 'core.autocrlf', 'false'], check=True)
    subprocess.run(git + ['remote', 'add', 'origin', REMOTE], check=True)
    subprocess.run(git + ['fetch', '--quiet', '--depth=1', 'origin', PREDECESSOR], check=True)
    subprocess.run(git + ['checkout', '--quiet', '--detach', PREDECESSOR], check=True)
    subprocess.run(git + ['tag', 'v0.2.15', PREDECESSOR], check=True)
    assert hashlib.sha256((previous / 'release-manifest.json').read_bytes()).hexdigest() == PREDECESSOR_MANIFEST
    shutil.copytree(previous / 'starter', vault)
    base = sys.executable
    if sys.platform == 'darwin':
        prefix = subprocess.check_output(['brew', '--prefix', 'python@3.12'], text=True).strip()
        base = str(Path(prefix) / 'bin/python3.12')
    subprocess.run(['uv', 'sync', '--locked', '--no-dev', '--project', str(previous), '--python', base], check=True)
    python = previous / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    subprocess.run([str(python), '-I', '-B', str(previous / 'install.py'), '--root', str(root),
                    '--vault', str(vault), '--role', 'capture', '--reuse-python', str(python),
                    '--download-model', '--install-service', '--configure-clients', '--client',
                    'claude-code', '--include-hooks'], check=True)
    cli = [str(python), '-B', str(root / 'app/brain_cli.py')]
    subprocess.run(cli + ['service', 'start'], check=True)
    assert json.loads(subprocess.check_output(cli + ['health']))['version'] == '0.2.15'


def verify_quiet(python, root):
    probe = """
import json, sys
sys.path.insert(0, sys.argv[1])
import brain_process as processes
import brain_settings as cfg
from filelock import FileLock, Timeout
try:
    registry = json.loads((cfg.STATE / 'service-pids.json').read_bytes())
    receipt = json.loads((cfg.STATE / 'index-bootstrap-status.json').read_bytes())
    if registry.get('bootstrap') is not None or receipt.get('status') == 'indexing':
        raise SystemExit(2)
    with FileLock(str(cfg.STATE / 'index-bootstrap.lock'), timeout=0):
        roots = processes.roots()
        try:
            if any(p.role in ('index_bootstrap', 'index_worker') for p in roots):
                raise SystemExit(2)
        finally:
            for p in roots:
                p.close()
except (FileNotFoundError, Timeout):
    raise SystemExit(2)
"""
    subprocess_args = [python, '-I', '-B', '-c', probe, str(root / 'app')]
    wait_for(lambda: subprocess.run(subprocess_args, timeout=30).returncode, lambda code: code == 0)
    print('PASS   predecessor bootstrap and worker are absent and the index lock is free')


def verify_deferred(root):
    try:
        status = json.loads((root / 'state/upgrade-status.json').read_bytes())
    except FileNotFoundError:
        raise SystemExit('Setup failed before writing an upgrade-status.json receipt; see the setup error above.') from None
    assert status['status'] == 'update deferred; runtime not switched'
    assert not (root / 'state/service-transaction.json').exists()
    print('PASS   exact pre-End deferral permits one quiet retry')


def verify_service_absent(python, root):
    probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import brain_process as processes
home = Path.home()
for absent in (home / '.config/systemd/user/second-brain.service',
               home / '.config/second-brain/second-brain.service',
               home / '.config/second-brain/enabled'):
    assert not absent.exists() and not absent.is_symlink()
assert not processes.roots()
assert not processes._listeners()
"""
    subprocess.run([python, '-I', '-B', '-c', probe, str(root / 'app')], timeout=30, check=True)
    print('PASS   service-free Linux fixture has no registration, process or listener')


def verify_codex(path):
    report = Path(path).read_text(encoding='utf-8-sig')
    for item in ('connection', 'instructions', 'skill'):
        assert re.search(r'(?m)^PASS\s+Codex ' + item + r':', report), item


def record_source(root, output):
    device = json.loads((root / 'device.json').read_bytes())
    scenario = os.environ['QUAL_SCENARIO']
    release_ref = os.environ['SECOND_BRAIN_REF'] if scenario == 'fresh' else 'v0.2.16'
    release = root / 'releases' / release_ref
    assert device['version'] == '0.2.16', device['version']
    assert Path(device['release_root']).resolve() == release, device['release_root']

    def revision(ref):
        return subprocess.check_output(['git', '-C', str(release), 'rev-parse', ref],
                                       encoding='ascii').strip()

    data = {
        'version': device['version'],
        'candidate_commit': revision('HEAD'),
        'candidate_tree': revision('HEAD^{tree}'),
        'candidate_manifest_sha256': hashlib.sha256((release / 'release-manifest.json').read_bytes()).hexdigest(),
        'scenario': scenario,
        'runner': os.environ['RUNNER_OS'],
    }
    assert data['candidate_commit'] == os.environ['SECOND_BRAIN_COMMIT']
    Path(output).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def verify_explicit_retry(call, vault):
    wait_for(lambda: call('index', 'status'), lambda row: row['status'] == 'verified')
    assert call('service', 'stop', '--wait-seconds', '60', timeout=90)['status'] == 'stopped'
    note = vault / 'native-explicit-index.md'
    note.write_text('# Native explicit retry\n\nA synthetic post-stop note.\n', encoding='utf-8')
    drift = call('index', 'status')
    assert drift['status'] == 'needs review'
    result = call('index', 'retry', timeout=480)
    assert result['status'] == 'verified' and result['process_exit'] == 0
    assert result['inventory_unchanged_during_run'] is True
    after = call('index', 'status')
    assert after['status'] == 'verified' and after['inventory_stable']
    assert after['coverage']['manifest_sha256'] == drift['coverage']['manifest_sha256']
    assert call('service', 'start', timeout=200)['status'] == 'started'
    assert call('health')['version'] == '0.2.16'
    print('PASS   installed candidate verified explicit retry and returned to authenticated health')


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seed-0215', action='store_true')
    mode.add_argument('--quiet-predecessor', action='store_true')
    mode.add_argument('--assert-deferred', action='store_true')
    mode.add_argument('--service-absent', action='store_true')
    mode.add_argument('--explicit', action='store_true')
    mode.add_argument('--assert-codex')
    mode.add_argument('--record-source')
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("This test requires the disposable native setup job.")
    scratch = Path(os.environ["RUNNER_TEMP"]).resolve()
    root = Path(os.environ["SECOND_BRAIN_ROOT"]).resolve()
    vault = (scratch / "second brain notes").resolve()
    if root != scratch / "second-brain-runtime" or vault != scratch / "second brain notes":
        raise SystemExit("The runtime and vault must be under the native runner scratch directory.")
    if args.record_source:
        record_source(root, args.record_source)
        return
    if args.assert_deferred:
        verify_deferred(root)
        return
    if args.seed_0215:
        seed_0215(root, vault)
        return
    if args.assert_codex:
        verify_codex(args.assert_codex)
        return
    device = json.loads((root / "device.json").read_text(encoding="utf-8"))
    if Path(device["vault"]).resolve() != vault:
        raise SystemExit("The installed service does not belong to the disposable vault.")

    cli = [device["python"], "-B", str(root / "app/brain_cli.py")]
    if Path(cli[0]).name.lower() == "pythonw.exe":
        cli[0] = str(Path(cli[0]).with_name("python.exe"))

    def call(*args, timeout=120):
        output = subprocess.run(
            cli + list(args), check=True, capture_output=True, encoding="utf-8",
            timeout=timeout,
        ).stdout
        return json.loads(output)

    if args.quiet_predecessor:
        verify_quiet(cli[0], root)
        return
    if args.service_absent:
        verify_service_absent(cli[0], root)
        return
    if args.explicit:
        verify_explicit_retry(call, vault)
        return

    baseline = call("index", "status")
    assert baseline["status"] == "verified", baseline
    assert call("service", "stop", "--wait-seconds", "60", timeout=90)["status"] == "stopped"

    edited = vault / "Maps/Start.md"
    former = vault / "Templates/project-brief.md"
    moved = former.with_name("native-index-renamed-brief.md")
    large = vault / "native-index-large.md"
    stat = edited.stat()
    original = edited.read_bytes()
    edited_bytes = original.replace(b"Capture something", b"zirconiumcanary!!", 1)
    assert edited_bytes != original and len(edited_bytes) == len(original)
    moved_bytes = former.read_bytes()
    large_bytes = ("# Native index large fixture\n\n" + "".join(
        f"- Quartz shard {number:03d} contains one synthetic recovery fact.\n"
        for number in range(265)
    )).encode("utf-8")
    edited.write_bytes(edited_bytes)
    os.utime(edited, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert edited.stat().st_mtime_ns == stat.st_mtime_ns
    former.rename(moved)
    large.write_bytes(large_bytes)

    drift = call("index", "status")
    assert drift["status"] == "needs review", drift
    assert all(drift["coverage"][key] > 0 for key in ("missing", "stale", "extra")), drift
    assert call("service", "start", timeout=200)["status"] == "started"

    deadline = time.monotonic() + 480
    current = drift
    while (remaining := deadline - time.monotonic()) > 0:
        current = call("index", "status", timeout=min(120, remaining))
        if current["status"] == "verified":
            break
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    assert current["status"] == "verified", current
    assert current["inventory_stable"], current
    assert current["coverage"]["manifest_sha256"] == drift["coverage"]["manifest_sha256"], current
    assert current["coverage"]["eligible"] == baseline["coverage"]["eligible"] + 1, current
    assert current["vectors"]["chunks"] - baseline["vectors"]["chunks"] > 256, current
    assert not former.exists()
    assert edited.read_bytes() == edited_bytes
    assert moved.read_bytes() == moved_bytes
    assert large.read_bytes() == large_bytes

    search = call("search", "zirconiumcanary", "--limit", "15")
    assert any(
        hit["path"] == "Maps/Start.md" and "zirconiumcanary" in hit["snippet"]
        for branch in search["indexed"] for hit in branch["results"]
    ), search["indexed"]
    print("PASS   native index recovered same-size/mtime edit, rename and 260+ new chunks")


if __name__ == "__main__":
    main()
