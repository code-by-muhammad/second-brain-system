"""Assign whole files and verify pytest reports for each CI shard."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from shutil import copyfile
from statistics import median
import sys
from xml.etree import ElementTree


def load_map():
    data = Path(__file__).with_name('ci_shards.json').read_bytes()
    return {**json.loads(data), 'sha256': sha256(data).hexdigest()}

def map_digest(mapping):
    return mapping.get('sha256') or sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()

def roster(mapping):
    count = mapping['general_shards']
    general = [f'general-{i}of{count}' for i in range(1, count + 1)]
    if not general or {'all', *general}.intersection(mapping['lanes']):
        raise ValueError('Invalid shard roster')
    return [*general, *mapping['lanes']]

def assign(mapping, files):
    files, assigned = set(files), {}
    for key, paths in mapping['lanes'].items():
        for path in paths:
            if path in assigned or path not in files:
                raise ValueError(f'Duplicated or missing lane file: {path}')
            assigned[path] = key
    bins, weights = roster(mapping)[:mapping['general_shards']], mapping['weights']
    default = median(weights.values()) if weights else 1
    load = [0] * len(bins)
    for path in sorted(files - assigned.keys(), key=lambda path: (-weights.get(path, default), path)):
        index = min(range(len(bins)), key=lambda i: (load[i], i))
        assigned[path] = bins[index]
        load[index] += weights.get(path, default)
    return assigned

def _watchdog(log):
    timeout = re.search(r'^TIMEOUT (.*)$|^Timeout \(', log, re.M)
    before = log[:timeout.start()] if timeout else log
    matches = re.findall(r'^CHECKPOINT \S+ (\S+) (\S+) (.*)$', before, re.M)
    scope, limit, node = matches[-1] if matches else (None, None, None)
    if timeout and timeout[1] and ' ' in timeout[1]:
        scope, node = timeout[1].split(' ', 1)
    try:
        limit = float(limit) if limit is not None else None
    except ValueError:
        limit = None
    phases = [phase for phase, item in re.findall(r'^PHASE (\S+) (.*)$', before, re.M) if item == node]
    return bool(timeout), dict(scope=scope, node=node, limit=limit, phase=phases[-1] if phases else None)

def _write(path, result):
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

def check_row(directory, key, mapping):
    directory = Path(directory)
    paths = (directory / name for name in ('exit-code.txt', 'watchdog.txt', 'ledger.jsonl'))
    code, watchdog, ledger = (path.read_text(encoding='utf-8', errors='replace') if path.is_file() else None for path in paths)
    print(f'{key} exit-code.txt = {code.strip() if code is not None else "absent"}')
    for log, keep in ((watchdog, 40), (ledger, 10)):
        for line in (log or '').rstrip('\n').split('\n')[-keep:]:
            print(line if len(line) <= 1200 else line[:1200] + ' … [see artifact]')
    timeout, checkpoint = _watchdog(watchdog or '')
    events, counts, skips, per_file = defaultdict(list), Counter(), Counter(), Counter()
    errors, collected, selected, not_run = [], [], [], []
    try:
        for line in (ledger or '').rstrip('\n').split('\n') if ledger else []:
            record = json.loads(line)
            events[record['event']].append(record)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f'Invalid ledger record: {exc}')
    recorded_map = next((record.get('map_sha256') for record in events['session']), None)
    if None in (code, watchdog, ledger) or code.strip() != '0' or timeout:
        errors.append('Expected complete reports, exit code 0, and no watchdog timeout')
    if [record.get('exitstatus') for record in events['finish']] != [0]:
        errors.append('Expected one finish with exitstatus 0')
    if [record.get('shard') for record in events['session']] != [key] or recorded_map != map_digest(mapping):
        errors.append('Session shard or map digest does not match')
    try:
        ([collection], [selection]) = (events[event] for event in ('collected', 'selected'))
        ids = collection['ids'], selection['ids']
        if any(not isinstance(items, list) or not all(isinstance(node, str) for node in items) for items in ids):
            raise ValueError('Invalid collected or selected IDs')
        collected, selected = ids
        if len(set(collected)) != len(collected) or events['collect_skip']:
            errors.append('Collection contains duplicate IDs or collection-time skips')
        files = [node.split('::', 1)[0] for node in collected]
        assigned = assign(mapping, files) if key != 'all' else {}
        expected = [node for node, file in zip(collected, files) if key == 'all' or assigned[file] == key]
        if selected != expected or key not in ['all', *roster(mapping)]:
            errors.append('Selected IDs differ from the shard assignment')
        expected_counts = Counter(selected)
        for event in ('start', 'end'):
            observed = Counter(record['id'] for record in events[event])
            if event == 'start':
                not_run = [node for node in selected if node not in observed]
            if observed != expected_counts:
                errors.append(f'{event} missing {dict(expected_counts - observed)}, extra {dict(observed - expected_counts)}')
        for record in events['end']:
            outcome = record['outcome']
            counts[outcome] += 1
            if outcome == 'skipped':
                skips[record.get('reason') or 'unspecified'] += 1
            per_file[record['id'].split('::', 1)[0]] += record['seconds']
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f'Invalid collection or execution evidence: {exc}')
    if counts.keys() - {'passed', 'skipped', 'xfailed'}:
        errors.append(f'Unsuccessful outcomes: {dict(counts)}')
    seconds = sum(per_file.values())
    if not errors:
        try:
            suites = list(ElementTree.parse(directory / 'junit.xml').iter('testsuite'))
            if not suites or any(suite.get(field) != '0' for suite in suites for field in ('errors', 'failures')):
                raise ValueError('Expected zero errors and failures')
            seconds = sum(float(suite.get('time', seconds)) for suite in suites)
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            errors.append(f'junit.xml: {exc}')
    classification = 'failed' if errors else 'ok'
    if timeout:
        classification = 'timeout'
    elif code is None:
        classification = 'killed-without-dump'
    row = {'key': key, 'class': classification, 'counts': counts, 'skips': skips, 'errors': errors, 'seconds': seconds,
           'seconds_per_file': per_file, 'map_sha256': recorded_map, 'not-run': not_run,
           'collected_sha256': sha256(json.dumps(collected).encode()).hexdigest()}
    if timeout or code is None:
        row['timeout' if timeout else 'checkpoint'] = checkpoint
        print(f'{classification} {checkpoint}')
    print('\n'.join(errors + ([f'not-run: {not_run}'] if not_run else [])))
    directory.mkdir(parents=True, exist_ok=True)
    _write(directory / 'row.json', row)
    return {**row, 'collected': collected, 'selected': selected}

def gate(artifacts, mapping, *, sha=None, junit_directory=None):
    names = {f'regression-win32-{key}-{sha}' if sha else key: key for key in roster(mapping)}
    directories = {path.name: path for path in Path(artifacts).glob('*') if path.is_dir()}
    errors = [f'Extra row directory: {name}' for name in sorted(directories.keys() - names.keys())]
    rows = {}
    collected, selected, digests = [], Counter(), set()
    for name, key in names.items():
        row = {'key': key, 'class': 'missing'}
        if name in directories:
            directory = directories[name]
            row = check_row(directory, key, mapping)
            collected = row.pop('collected')
            selected.update(row.pop('selected'))
            digests.add(row['collected_sha256'])
            junit = directory / 'junit.xml'
            if junit_directory is not None and junit.is_file():
                copyfile(junit, junit_directory / f'junit-{key}.xml')
        rows[key] = row
        if row['class'] != 'ok':
            errors.append(f'{key}: {row["class"]}')
    if len(digests) > 1:
        errors.append('Collected digests differ')
    if selected != Counter(collected) or any(count > 1 for count in selected.values()):
        errors.append('Selected IDs do not partition the collection')
    print('Shard  Class  Outcomes  Pytest seconds  Skips')
    for key, row in rows.items():
        print(f'{key}  {row["class"]}  {row.get("counts", {})}  {row.get("seconds", 0):.1f}  {row.get("skips", {})}')
    print('\n'.join(errors))
    return {'ok': not errors, 'rows': rows, 'errors': errors}

if __name__ == '__main__':
    sys.stdout.reconfigure(errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command, flags in [('check-row', ('--dir', '--key')), ('gate', ('--artifacts', '--out'))]:
        subparser = commands.add_parser(command)
        for flag in flags:
            subparser.add_argument(flag, type=str if flag == '--key' else Path, required=True)
    args, mapping = parser.parse_args(), load_map()
    if args.command == 'check-row':
        sys.exit(check_row(args.dir, args.key, mapping)['class'] != 'ok')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = gate(args.artifacts, mapping, sha=os.environ.get('GITHUB_SHA'), junit_directory=args.out.parent)
    _write(args.out, result)
    sys.exit(not result['ok'])
