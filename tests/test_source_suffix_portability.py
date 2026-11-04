"""Supported source suffixes retain literal paths and existing discovery guards."""
import json
import os
import subprocess

import pytest

import brain_store as s
import brain_wiki as w


TOKEN = 'suffixregressiontoken'
RAW = ('# Original source\n\n' + TOKEN + ' remains original evidence.\n').encode()


def write(vault, relative, raw=RAW):
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def paths(rows):
    return {row['path'] for row in rows}


def policy(vault, *, exact=(), prefixes=()):
    rules = {'version': 1, 'exclude_paths': list(exact), 'exclude_prefixes': list(prefixes)}
    return write(vault, w.SOURCE_POLICY, ('# Owner source policy\n\n```json\n' + json.dumps(rules) + '\n```\n').encode())


@pytest.mark.parametrize('root', w.SOURCE_ROOTS)
@pytest.mark.parametrize('suffix', ['md', 'MD', 'Md'], ids=['lower', 'upper', 'mixed'])
def test_supported_suffix_is_pending_and_searchable_in_every_source_root(brain, root, suffix):
    vault, state = brain
    relative = root + '/Nested/Original source.' + suffix
    source = write(vault, relative)
    direct = s.read(relative)
    inventory = w.inventory()
    fresh = s.fresh_search(TOKEN)
    status = w.status()
    packet = w.prepare(1)
    assert direct['path'] == relative and direct['sha256'] == s.digest(RAW)
    assert source.read_bytes() == RAW
    assert paths(inventory) == paths(fresh) == {relative}
    assert inventory[0]['job_key'] == relative + '::part1'
    assert inventory[0]['sha256'] == s.digest(RAW)
    assert status['eligible_source_parts'] == status['pending_source_parts'] == 1
    assert packet is not None and packet['sources'][0]['path'] == relative
    assert packet['sources'][0]['sha256'] == s.digest(RAW)
    assert source.read_bytes() == RAW


@pytest.mark.parametrize('mode', ['hidden-directory', 'hidden-file', 'oversize', 'invalid-utf8', 'linked-directory'])
def test_suffix_discovery_preserves_read_guards_with_lowercase_positive_control(brain, mode):
    vault, state = brain
    control = write(vault, '01-Projects/Control.md')
    relative = '01-Projects/Rejected.MD'
    raw = RAW
    if mode == 'hidden-directory':
        relative = '01-Projects/.hidden/Rejected.MD'
    elif mode == 'hidden-file':
        relative = '01-Projects/.Rejected.MD'
    elif mode == 'oversize':
        raw = RAW + b'x' * 2_000_001
    elif mode == 'invalid-utf8':
        raw = RAW + b'\xff'
    elif mode == 'linked-directory':
        outside = vault.parent / 'outside-linked-fixture'
        outside.mkdir()
        target = outside / 'Rejected.MD'
        target.write_bytes(raw)
        link = vault / '01-Projects' / 'Linked'
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
            assert os.path.isjunction(link)
        else:
            link.symlink_to(outside, target_is_directory=True)
            assert link.is_symlink()
        relative = '01-Projects/Linked/Rejected.MD'
    source = vault / relative if mode == 'linked-directory' else write(vault, relative, raw)
    assert paths(w.inventory()) == {'01-Projects/Control.md'}
    assert paths(s.fresh_search(TOKEN)) == {'01-Projects/Control.md'}
    assert source.read_bytes() == raw and control.read_bytes() == RAW


@pytest.mark.parametrize('mode', ['credential-like', 'central-derived', 'project-derived', 'compiler-capture'])
def test_suffix_discovery_preserves_compiler_source_boundary_and_keyword_retrieval(brain, mode):
    vault, state = brain
    control = write(vault, '01-Projects/Control.md')
    relative = '03-Resources/Excluded source.MD'
    raw = RAW
    if mode == 'credential-like':
        raw += b'api_key: ' + b'A' * 24 + b'\n'
    elif mode == 'central-derived':
        relative = '02-Areas/AI-Memory/Derived.MD'
    elif mode == 'project-derived':
        relative = '01-Projects/Example/AI-Memory.MD'
    else:
        relative = '00-Inbox/AI-Captures/Milestone.MD'
        raw = b'---\ntags: [ai-capture]\nproject: Second Brain\n---\n\n' + RAW
    source = write(vault, relative, raw)
    assert paths(w.inventory()) == paths(w.inventory(include_excluded=True)) == {'01-Projects/Control.md'}
    # Retrieval does not promote a file into compiler eligibility and returns only a locator.
    assert paths(s.fresh_search(TOKEN)) == {'01-Projects/Control.md', relative}
    assert source.read_bytes() == raw and control.read_bytes() == RAW


def test_prefix_excluded_uppercase_source_remains_readable_but_unselected(brain):
    vault, state = brain
    control = write(vault, '01-Projects/Control.md')
    relative = '03-Resources/Deferred/Original.MD'
    source = write(vault, relative)
    owner_policy = policy(vault, prefixes=['03-Resources/Deferred/'])
    policy_raw = owner_policy.read_bytes()
    assert paths(w.inventory()) == {'01-Projects/Control.md'}
    assert paths(w.inventory(include_excluded=True)) == {'01-Projects/Control.md', relative}
    assert paths(s.fresh_search(TOKEN)) == {'01-Projects/Control.md', relative}
    status = w.status()
    packet = w.prepare(4)
    assert status['excluded_source_parts'] == 1 and status['eligible_source_parts'] == 1
    assert [row['path'] for row in packet['sources']] == ['01-Projects/Control.md']
    assert source.read_bytes() == control.read_bytes() == RAW
    assert owner_policy.read_bytes() == policy_raw


def test_exact_lowercase_owner_exclusion_is_unchanged(brain):
    vault, state = brain
    control = write(vault, '01-Projects/Control.md')
    relative = '03-Resources/Deferred.md'
    source = write(vault, relative)
    owner_policy = policy(vault, exact=[relative])
    policy_raw = owner_policy.read_bytes()
    assert paths(w.inventory()) == {'01-Projects/Control.md'}
    assert paths(w.inventory(include_excluded=True)) == {'01-Projects/Control.md', relative}
    assert paths(s.fresh_search(TOKEN)) == {'01-Projects/Control.md', relative}
    assert w.source_policy()['exclude_paths'] == [relative]
    assert source.read_bytes() == control.read_bytes() == RAW
    assert owner_policy.read_bytes() == policy_raw


def test_exact_uppercase_policy_path_still_requires_explicit_lowercase_contract(brain):
    vault, state = brain
    relative = '03-Resources/Deferred.MD'
    source = write(vault, relative)
    owner_policy = policy(vault, exact=[relative])
    policy_raw = owner_policy.read_bytes()
    with pytest.raises(w.WikiPolicyError, match='exact paths must end with lowercase .md'):
        w.source_policy()
    assert source.read_bytes() == RAW and owner_policy.read_bytes() == policy_raw
