"""Read-only acceptance check for one Second Brain device.

Changes nothing and prints no credential or note content. Run it with the
runtime's own Python (the "python" value in device.json):

    <device python> -I -B check_setup.py --root <runtime> --clients claude-code,codex

Exit status is 0 only when no check fails. Checks that need a human (a real
tool call in a fresh chat, a reboot) are listed as MANUAL and never pass here.
"""
import argparse
import json
import os
import re
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

BEGIN = '<!-- BEGIN SECOND-BRAIN -->'
END = '<!-- END SECOND-BRAIN -->'
CLIENTS = ('claude-code', 'claude-desktop', 'codex', 'grok')
rows = []
_SUPPORT = None


def support():
    global _SUPPORT
    if _SUPPORT is None:
        _SUPPORT = runpy.run_path(str(Path(__file__).resolve().with_name('setup_runtime.py')))
    return _SUPPORT


def add(status, name, detail=''):
    rows.append((status, name, detail))


def load_json(path, home=None):
    helpers = support()
    if home is not None:
        path = helpers['resolve_client_path'](path, home)
    raw = helpers['read'](path)
    if raw is None:
        raise FileNotFoundError(path)
    return helpers['json_object'](raw)


def same(left, right):
    """Same written path. Links are not resolved: two environments can share one base Python."""
    clean = lambda value: os.path.normcase(os.path.abspath(os.path.expanduser(str(value).strip('"'))))
    return bool(str(left)) and clean(left) == clean(right)


def owned(entry, python, root):
    """Compare the owned command fields while allowing each assistant's other settings."""
    expected = support()['connection']().connection_spec(root, python)
    if not isinstance(entry, dict) or any(entry.get(key) != value for key, value in expected.items()):
        return False
    return os.name == 'nt' or stable_launcher(root, python)


def stable_launcher(root, python):
    try:
        support()['connection']().validate_launcher(root, python)
    except (ValueError, OSError):
        return False
    return True


def instructions(path, imports=True, depth=0):
    """Text an assistant loads from this file. Claude Code also follows @imports."""
    path = Path(path).expanduser()
    if depth > 5 or not path.is_file():
        return ''
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    found = text
    fenced = False
    for line in text.splitlines() if imports else ():
        if line.lstrip().startswith('```'):
            fenced = not fenced
        if fenced:
            continue  # Claude Code does not import from inside a code block.
        for name in re.findall(r'(?:^|\s)@([^\s`]+)', line):
            target = Path(os.path.expanduser(name))
            found += '\n' + instructions(target if target.is_absolute() else path.parent / target, True, depth + 1)
    return found


def protocol(name, path, release, imports=True):
    text = instructions(path, imports)
    starts, ends = text.count(BEGIN), text.count(END)
    if starts == 0 and ends == 0:
        return add('FAIL', name, f'no Second Brain block is loaded from {path}')
    if starts != 1 or ends != 1 or text.index(END) < text.index(BEGIN):
        return add('FAIL', name, f'{starts} begin and {ends} end markers loaded from {path}; exactly one pair is required')
    if release:
        wanted = (release / 'docs/AI-PROTOCOL.md').read_text(encoding='utf-8').strip()
        actual = text[text.index(BEGIN) + len(BEGIN):text.index(END)].strip()
        if actual != wanted:
            return add('FAIL', name, 'block differs from this release; refresh it')
    add('PASS', name, 'one block' + (' matching this release' if release else ''))


def probe_stdio(command, args):
    """Start the generated command with the service already up and require the six tools."""
    import asyncio
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    child = support()['connection']().probe_environment()

    async def once():
        transport = StdioTransport(command=command, args=args, env=child, keep_alive=False)
        async with Client(transport, timeout=70, init_timeout=60) as client:
            return {tool.name for tool in await client.list_tools()}

    names = asyncio.run(once())
    required = {'brain_context', 'brain_search', 'brain_read', 'brain_capture', 'brain_update_current', 'brain_health'}
    return required <= names, names


def skill(name, candidates, release):
    real = {}
    for candidate in candidates:
        if candidate.is_file():
            real[candidate.resolve()] = candidate
    if not real:
        return add('FAIL', name, 'no second-brain skill found in ' + ', '.join(str(c.parent) for c in candidates))
    if len(real) > 1:
        return add('FAIL', name, 'separate copies: ' + ', '.join(map(str, real.values())))
    path = next(iter(real))
    if release and path.read_bytes() != (release / 'skills/second-brain/SKILL.md').read_bytes():
        return add('WARN', name, f'{path} differs from this release (fine if edited on purpose)')
    add('PASS', name, str(path))


def main():
    rows.clear()
    # A path may hold characters the console's own encoding lacks; printing it must never end the check.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=str(Path.home() / '.local/share/second-brain'))
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--clients', default='', help='Comma-separated: ' + ', '.join(CLIENTS))
    parser.add_argument('--expect-version', help='For example 0.2.16')
    parser.add_argument('--release', help='Verified release checkout; compares the protocol block and skill with it')
    parser.add_argument('--no-hooks', action='store_true', help='The owner declined Claude Code hooks')
    parser.add_argument('--local-only', action='store_true', help='Notes must have no Git remote on this device')
    parser.add_argument('--no-service', action='store_true', help='The background service was deliberately not installed')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    root, home = Path(args.root).expanduser().resolve(), Path(args.home).expanduser()
    grok_registration = None
    release = Path(args.release).expanduser().resolve() if args.release else None
    if release and not (release / 'docs/AI-PROTOCOL.md').is_file():
        parser.error('--release is not a release folder.')
    clients = [value for value in args.clients.split(',') if value]
    if set(clients) - set(CLIENTS):
        parser.error('Unknown client selection.')

    device = {}
    try:
        device = load_json(root / 'device.json')
        if not isinstance(device, dict):
            raise ValueError
        add('PASS', 'device record', f"version {device.get('version')}, role {device.get('role')}")
    except (OSError, ValueError) as error:
        add('FAIL', 'device record', f'{root}/device.json unreadable: {type(error).__name__}')
    python, vault = Path(device.get('python', '')), Path(device.get('vault', ''))
    if args.expect_version:
        ok = device.get('version') == args.expect_version
        add('PASS' if ok else 'FAIL', 'installed version', f"{device.get('version')} (expected {args.expect_version})")
    add('PASS' if device and python.is_file() and os.access(python, os.X_OK) else 'FAIL', 'recorded Python', str(python))
    if device and not same(python, sys.executable):
        add('WARN', 'checker interpreter', 'run this check with the recorded Python for a like-for-like result')
    nested = device and (root in vault.resolve().parents or vault.resolve() in root.parents or same(root, vault))
    add('PASS' if device and vault.is_dir() and not nested else 'FAIL', 'vault folder', f'{vault} (must exist, outside the runtime)')

    health = {}
    if args.no_service:
        add('SKIP', 'service health', 'the service was not installed on purpose')
    elif device and python.is_file():
        try:
            done = subprocess.run([str(python), str(root / 'app/brain_cli.py'), 'health'], capture_output=True,
                                  text=True, timeout=120, stdin=subprocess.DEVNULL, check=False,
                                  env={k: v for k, v in os.environ.items() if not k.startswith(('BRAIN_', 'BASIC_MEMORY_'))})
            answer = json.loads(done.stdout)
            if isinstance(answer, dict) and (done.returncode == 0 or answer.get('status') == 'blocked'):
                health = answer
        except (OSError, ValueError, subprocess.TimeoutExpired):
            health = {}
    running = health.get('status') == 'running' and health.get('version') == device.get('version')
    if not args.no_service:
        if health.get('status') == 'blocked':
            add('FAIL', 'service health', f"{health.get('reason', 'blocked')}. {health.get('guidance', '')}")
        else:
            add('PASS' if running else 'FAIL', 'service health',
                f"status {health.get('status')}, version {health.get('version')}" if health else 'health command gave no result')
    if health and health.get('status') != 'blocked':
        add('PASS' if health.get('vault_exists') and health.get('database_exists') else 'FAIL', 'vault and index',
            f"vault {health.get('vault_exists')}, index {health.get('database_exists')}")
        sync = str((health.get('maintenance') or {}).get('git', 'not run yet'))
        add('FAIL' if sync.startswith('needs attention') else 'WARN' if sync.startswith('paused') else 'PASS', 'notes sync', sync[:200])
        # The background pass that links milestones and syncs reports its own state; health shows it, so does this.
        upkeep = health.get('maintenance') or {}
        state = str(upkeep.get('status', 'not run yet'))
        reason = str(upkeep.get('error') or '')
        add('WARN' if state == 'needs attention' else 'PASS', 'maintenance', (state + (': ' + reason if state == 'needs attention' and reason else ''))[:200])
        try:
            done = subprocess.run([str(python), str(root / 'app/brain_cli.py'), 'index', 'status'], capture_output=True, text=True,
                                  timeout=600, stdin=subprocess.DEVNULL, check=False,
                                  env={k: v for k, v in os.environ.items() if not k.startswith(('BRAIN_', 'BASIC_MEMORY_'))})
            state = json.loads(done.stdout).get('status') if done.returncode == 0 else None
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            state = None
        add('PASS' if state == 'verified' else 'WARN', 'search index',
            str(state) + ('' if state == 'verified' else '; a new index needs a few minutes, otherwise run: brain_cli.py index retry'))

    if device.get('role') == 'maintainer':
        # Only the maintainer builds wiki pages. A device with no assistant, or with a run that stopped, says so here.
        again = 'run setup again with --wiki claude or --wiki codex'
        if not os.path.lexists(root / 'state/wiki-model.json'):
            add('WARN', 'wiki assistant', 'none selected, so no wiki pages are built (optional); ' + again)
        else:
            # The runtime reads the record exactly as a wiki run would, without starting the program.
            # It also gives the saved state of the last run, so this line is the same with and without the service.
            chosen, worker, reason = {}, {}, 'the runtime gave no answer'
            try:
                done = subprocess.run([str(python), str(root / 'app/brain_cli.py'), 'wiki', 'model'], capture_output=True, text=True,
                                      encoding='utf-8', errors='replace', timeout=300, stdin=subprocess.DEVNULL, check=False,
                                      env={k: v for k, v in os.environ.items() if not k.startswith(('BRAIN_', 'BASIC_MEMORY_'))})
                if done.returncode == 0:
                    answer = json.loads(done.stdout)
                    chosen, worker = (answer.get(name) if isinstance(answer.get(name), dict) else {} for name in ('executable', 'worker'))
                else:
                    reason = ((done.stderr or '').strip().splitlines() or [reason])[-1]
            except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired) as error:
                reason = type(error).__name__
            if not chosen.get('path'):
                add('WARN', 'wiki assistant', ('the recorded choice cannot be used (' + reason[:200] + '); ' + again))
            elif worker.get('attention_required'):
                add('WARN', 'wiki assistant', f"{chosen.get('path')}; the last wiki run needs attention ({str(worker.get('reason'))[:100]}): see brain_cli.py wiki status, then wiki retry"[:500])
            else:
                add('PASS', 'wiki assistant', f"{chosen.get('path')}, model {chosen.get('model')}, effort {chosen.get('effort')}"[:400])

    if 'claude-code' in clients:
        claude_json = home / '.claude.json'
        if not claude_json.is_file():
            add('FAIL', 'Claude Code connection', 'no second-brain entry')
        else:
            try:
                entry = (load_json(claude_json, home).get('mcpServers') or {}).get('second-brain')
                if entry is None:
                    add('FAIL', 'Claude Code connection', 'no second-brain entry')
                else:
                    add('PASS' if owned(entry, python, root) else 'FAIL', 'Claude Code connection',
                        'points at this runtime' if owned(entry, python, root) else 'points somewhere else')
            except (OSError, ValueError, AttributeError):
                add('FAIL', 'Claude Code connection', '~/.claude.json unreadable')
        protocol('Claude Code instructions', home / '.claude/CLAUDE.md', release)
        skill('Claude Code skill', [home / '.claude/skills/second-brain/SKILL.md'], release)
        if args.no_hooks:
            add('SKIP', 'Claude Code hooks', 'declined by the owner')
        else:
            try:
                hooks = load_json(home / '.claude/settings.json', home).get('hooks') or {}
                missing = []
                for event in ('SessionStart', 'Stop'):
                    commands = [handler.get('command', '') for entry in hooks.get(event) or [] for handler in entry.get('hooks') or []]
                    mine = [c for c in commands if 'brain_hooks.py' in c]
                    parts = [shlex.split(c, posix=os.name != 'nt') for c in mine]
                    good = [p for p in parts if len(p) == 2 and same(p[0], python) and same(p[1], root / 'app/brain_hooks.py')]
                    if len(mine) != 1 or len(good) != 1:
                        missing.append(f'{event}: {len(mine)} found, {len(good)} for this runtime')
                add('FAIL' if missing else 'PASS', 'Claude Code hooks', '; '.join(missing) or 'SessionStart and Stop, once each')
            except (OSError, ValueError, AttributeError, TypeError):
                add('FAIL', 'Claude Code hooks', '~/.claude/settings.json unreadable')
    if 'codex' in clients:
        codex_toml = home / '.codex/config.toml'
        if not codex_toml.is_file():
            add('FAIL', 'Codex connection', 'no second-brain entry')
        else:
            try:
                import tomllib
                target = support()['resolve_client_path'](codex_toml, home)
                raw = support()['read_client'](target)
                if raw is None:
                    raise FileNotFoundError(target)
                entry = tomllib.loads(raw.decode('utf-8-sig')).get('mcp_servers', {}).get('second-brain')
                if entry is None:
                    add('FAIL', 'Codex connection', 'no second-brain entry')
                else:
                    add('PASS' if owned(entry, python, root) else 'FAIL', 'Codex connection',
                        'points at this runtime' if owned(entry, python, root) else 'points somewhere else')
            except (OSError, ValueError, ImportError, AttributeError):
                add('FAIL', 'Codex connection', '~/.codex/config.toml unreadable')
        protocol('Codex instructions', home / '.codex/AGENTS.md', release, imports=False)
        skill('Codex skill', [home / '.codex/skills/second-brain/SKILL.md', home / '.agents/skills/second-brain/SKILL.md'], release)
    if 'grok' in clients:
        # Grok Bot registers MCP servers in the account. This file is the spec it should be given;
        # Grok Bot does not load it by itself. The skill beside it is the instruction block.
        try:
            grok_registration = support()['validated_grok'](home, root, python)
        except (OSError, ValueError) as error:
            add('FAIL', 'Grok Bot connection', str(error))
            add('FAIL', 'Grok Bot server', 'registration validation failed before starting the command')
        else:
            add('PASS', 'Grok Bot connection', 'one generated second-brain server for this runtime')
        if grok_registration is not None and running:
            _, entry = grok_registration
            try:
                started, _names = probe_stdio(entry['command'], entry['args'])
                add('PASS' if started else 'FAIL', 'Grok Bot server', 'the command offered the six tools' if started else 'the command started without the six tools')
            except Exception as error:
                add('FAIL', 'Grok Bot server', type(error).__name__)
        elif grok_registration is not None:
            add('SKIP' if args.no_service else 'FAIL', 'Grok Bot server',
                'static registration checked on a service-free device' if args.no_service else 'the service is unavailable for the stdio check')
        skill_path = home / 'agent-data/workflows/second-brain/SKILL.md'
        try:
            skill_path = support()['resolve_client_path'](skill_path, home)
            skill_present = support()['read_client'](skill_path) is not None
        except (OSError, ValueError):
            skill_present = False
        if skill_present:
            protocol('Grok Bot instructions', skill_path, release, imports=False)
            skill('Grok Bot skill', [skill_path], release)
        else:
            add('FAIL', 'Grok Bot instructions', 'shared instruction block unavailable')
            add('FAIL', 'Grok Bot skill', 'shared skill unavailable')
        add('PASS' if skill_present else 'FAIL', 'Grok Bot hooks',
            'session start and capture are in the shared instruction block; Grok Bot has no separate hook file'
            if skill_present else 'the shared instruction block is missing')
    if 'claude-desktop' in clients:
        folder = (Path(os.environ.get('APPDATA', home / 'AppData/Roaming')) / 'Claude' if os.name == 'nt'
                  else home / 'Library/Application Support/Claude' if sys.platform == 'darwin' else home / '.config/Claude')
        packages = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'Packages'
        if os.name == 'nt' and not (folder / 'claude_desktop_config.json').exists() and packages.is_dir():
            # The Microsoft Store build keeps its settings inside its own package folder.
            stored = [item / 'LocalCache/Roaming/Claude' for item in packages.iterdir() if 'claude' in item.name.lower()]
            folder = next((item for item in stored if (item / 'claude_desktop_config.json').exists()), folder)
        try:
            entry = load_json(folder / 'claude_desktop_config.json', home).get('mcpServers', {}).get('second-brain')
            add('PASS' if owned(entry, python, root) else 'FAIL', 'Claude Desktop connection',
                'points at this runtime' if owned(entry, python, root) else 'missing or points somewhere else')
        except (OSError, ValueError, AttributeError):
            add('FAIL', 'Claude Desktop connection', 'claude_desktop_config.json unreadable')

    if device and vault.is_dir():
        names = []  # A folder that is not a repository of its own has no remote.
        if (vault / '.git').exists():
            try:
                remotes = subprocess.run(['git', '-C', str(vault), 'remote'], capture_output=True, text=True, timeout=30, check=False)
                names = remotes.stdout.split() if remotes.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                names = None
        if names is None:
            add('FAIL' if args.local_only else 'WARN', 'notes repository', 'Git could not say whether the notes have a remote')
        elif names:
            add('FAIL' if args.local_only else 'PASS', 'notes repository',
                ('the owner wants no Git remote, but one is configured: ' if args.local_only else 'remote: ') + ', '.join(names))
        else:
            add('PASS', 'notes repository', 'no Git remote; notes stay on this device')
            if device.get('role') != 'maintainer':
                add('WARN', 'device role', 'a capture device with no sync never links captures into daily notes; a single device should be the maintainer')

    for client in clients:
        add('MANUAL', f'{client}: fresh chat', 'call brain_context, search a known note, read it, save one test capture')
    add('MANUAL', 'restart', 'after a reboot or login the service is running without help')

    failed = sum(status == 'FAIL' for status, _, _ in rows)
    if args.json:
        print(json.dumps({'failed': failed, 'checks': [dict(zip(('status', 'check', 'detail'), row)) for row in rows]}, indent=2))
    else:
        for status, name, detail in rows:
            print(f'{status:<6} {name}: {detail}')
        print(f'\n{failed} failed. ' + ('Fix the FAIL lines and run again.' if failed else 'Automatic checks passed; finish the MANUAL lines.'))
        if grok_registration is not None:
            path, entry = grok_registration
            print('\nGrok Bot MCP registration (add this server in the account):')
            print('  spec:    ' + str(path))
            print('  command: ' + entry['command'])
            print('  args:    ' + json.dumps(entry['args']))
            print('  env:     {}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
