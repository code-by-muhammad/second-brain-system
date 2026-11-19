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
import shlex
import subprocess
import sys
from pathlib import Path

BEGIN = '<!-- BEGIN SECOND-BRAIN -->'
END = '<!-- END SECOND-BRAIN -->'
CLIENTS = ('claude-code', 'claude-desktop', 'codex', 'grok')
rows = []


def add(status, name, detail=''):
    rows.append((status, name, detail))


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def same(left, right):
    """Same written path. Links are not resolved: two environments can share one base Python."""
    clean = lambda value: os.path.normcase(os.path.abspath(os.path.expanduser(str(value).strip('"'))))
    return bool(str(left)) and clean(left) == clean(right)


def owned(entry, python, root):
    args = entry.get('args') if isinstance(entry, dict) else None
    return (isinstance(args, list) and len(args) == 1 and same(args[0], root / 'app/brain_client.py')
            and same(entry.get('command', ''), python))


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


def probe_stdio(command, args, env):
    """Start the registered command and require the six tools. The caller only does this when the service is already up."""
    import asyncio
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    child = os.environ.copy()
    child = {key: value for key, value in child.items() if not key.startswith(('BRAIN_', 'BASIC_MEMORY_'))}
    child.update(env)

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
    # A path may hold characters the console's own encoding lacks; printing it must never end the check.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', default=str(Path.home() / '.local/share/second-brain'))
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--clients', default='', help='Comma-separated: ' + ', '.join(CLIENTS))
    parser.add_argument('--expect-version', help='For example 0.2.13')
    parser.add_argument('--release', help='Verified release checkout; compares the protocol block and skill with it')
    parser.add_argument('--no-hooks', action='store_true', help='The owner declined Claude Code hooks')
    parser.add_argument('--local-only', action='store_true', help='Notes must have no Git remote on this device')
    parser.add_argument('--no-service', action='store_true', help='The background service was deliberately not installed')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    root, home = Path(args.root).expanduser().resolve(), Path(args.home).expanduser()
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
            health = json.loads(done.stdout) if done.returncode == 0 else {}
            health = health if isinstance(health, dict) else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            health = {}
    running = health.get('status') == 'running' and health.get('version') == device.get('version')
    if not args.no_service:
        add('PASS' if running else 'FAIL', 'service health',
            f"status {health.get('status')}, version {health.get('version')}" if health else 'health command gave no result')
    if health:
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
        try:
            entry = (load_json(home / '.claude.json').get('mcpServers') or {}).get('second-brain')
            add('PASS' if owned(entry, python, root) else 'FAIL', 'Claude Code connection',
                'points at this runtime' if owned(entry, python, root) else 'missing or points somewhere else')
        except (OSError, ValueError, AttributeError):
            add('FAIL', 'Claude Code connection', '~/.claude.json unreadable')
        protocol('Claude Code instructions', home / '.claude/CLAUDE.md', release)
        skill('Claude Code skill', [home / '.claude/skills/second-brain/SKILL.md'], release)
        if args.no_hooks:
            add('SKIP', 'Claude Code hooks', 'declined by the owner')
        else:
            try:
                hooks = load_json(home / '.claude/settings.json').get('hooks') or {}
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
        try:
            import tomllib
            entry = tomllib.loads((home / '.codex/config.toml').read_text(encoding='utf-8-sig')).get('mcp_servers', {}).get('second-brain')
            add('PASS' if owned(entry, python, root) else 'FAIL', 'Codex connection',
                'points at this runtime' if owned(entry, python, root) else 'missing or points somewhere else')
        except (OSError, ValueError, ImportError):
            add('FAIL', 'Codex connection', '~/.codex/config.toml unreadable (needs Python 3.11 or newer)')
        protocol('Codex instructions', home / '.codex/AGENTS.md', release, imports=False)
        skill('Codex skill', [home / '.codex/skills/second-brain/SKILL.md', home / '.agents/skills/second-brain/SKILL.md'], release)
    if 'grok' in clients:
        # Grok Bot registers MCP servers in the account. This file is the spec it should be given;
        # Grok Bot does not load it by itself. The skill beside it is the instruction block.
        spec_path = home / 'agent-data/workflows/second-brain/mcp-registration.json'
        entry = None
        try:
            document = load_json(spec_path)
            servers = document.get('mcpServers') if isinstance(document, dict) else None
            if not isinstance(servers, dict) or set(servers) != {'second-brain'}:
                add('FAIL', 'Grok Bot connection', 'the spec must contain exactly one server, named second-brain')
            else:
                entry = servers['second-brain']
                env = entry.get('env', {}) if isinstance(entry, dict) else None
                clean = isinstance(env, dict) and all(isinstance(key, str) and isinstance(value, str) and not key.startswith(('BRAIN_', 'BASIC_MEMORY_')) for key, value in env.items())
                good = owned(entry, python, root) and clean
                add('PASS' if good else 'FAIL', 'Grok Bot connection',
                    'one spec for this runtime' if good else 'missing, duplicated, or points somewhere else')
                if not good:
                    add('FAIL', 'Grok Bot server', 'not started because the registration spec is not this runtime')
                elif running:
                    try:
                        started, _names = probe_stdio(entry.get('command', ''), list(entry.get('args') or []), env)
                        add('PASS' if started else 'FAIL', 'Grok Bot server', 'the registered command started and offered the six tools' if started else 'the command started without the six tools')
                    except Exception as error:
                        add('FAIL', 'Grok Bot server', type(error).__name__)
                else:
                    add('FAIL', 'Grok Bot server', 'the spec is valid and the service is not running, so the command was not started')
        except (OSError, ValueError, AttributeError):
            add('FAIL', 'Grok Bot connection', 'agent-data/workflows/second-brain/mcp-registration.json unreadable')
            add('FAIL', 'Grok Bot server', 'not started because the registration spec is unreadable')
        skill_path = home / 'agent-data/workflows/second-brain/SKILL.md'
        protocol('Grok Bot instructions', skill_path, release, imports=False)
        skill('Grok Bot skill', [skill_path], release)
        add('PASS' if skill_path.is_file() else 'FAIL', 'Grok Bot hooks',
            'session start and capture are in the shared instruction block; Grok Bot has no separate hook file'
            if skill_path.is_file() else 'the shared instruction block is missing')
    if 'claude-desktop' in clients:
        folder = (Path(os.environ.get('APPDATA', home / 'AppData/Roaming')) / 'Claude' if os.name == 'nt'
                  else home / 'Library/Application Support/Claude' if sys.platform == 'darwin' else home / '.config/Claude')
        packages = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'Packages'
        if os.name == 'nt' and not (folder / 'claude_desktop_config.json').exists() and packages.is_dir():
            # The Microsoft Store build keeps its settings inside its own package folder.
            stored = [item / 'LocalCache/Roaming/Claude' for item in packages.iterdir() if 'claude' in item.name.lower()]
            folder = next((item for item in stored if (item / 'claude_desktop_config.json').exists()), folder)
        try:
            entry = load_json(folder / 'claude_desktop_config.json').get('mcpServers', {}).get('second-brain')
            add('PASS' if owned(entry, python, root) else 'FAIL', 'Claude Desktop connection',
                'points at this runtime' if owned(entry, python, root) else 'missing or points somewhere else')
        except (OSError, ValueError):
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
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
