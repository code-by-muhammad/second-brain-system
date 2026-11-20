"""Pure renderers and validation for the device-local MCP connection."""

import json
import os
import shlex
import stat
from pathlib import Path

GROK_SPEC_LIMIT = 65536


def connection_spec(root, python, *, windows=None):
    """The command installed in each assistant's MCP registration."""
    root = Path(root)
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        return {'command': str(python), 'args': [str(root / 'app' / 'brain_client.py')]}
    return {'command': str(root / 'bin' / 'second-brain-mcp'), 'args': []}


def grok_spec(root, python, *, windows=None):
    return {'mcpServers': {'second-brain': {**connection_spec(root, python, windows=windows), 'env': {}}}}


def validate_grok_spec(raw, root, python, *, windows=None):
    """Return generated argv after exact structural validation of the account spec."""
    if not raw or len(raw) > GROK_SPEC_LIMIT:
        raise ValueError('The Grok registration spec is missing or exceeds its size bound.')

    def unique(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError('The Grok registration spec contains a duplicate key.')
            result[key] = value
        return result

    expected = grok_spec(root, python, windows=windows)
    try:
        document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except RecursionError as error:
        raise ValueError('The Grok registration spec exceeds its nesting bound.') from error
    if document != expected:
        raise ValueError('The Grok registration spec must match the generated second-brain server and empty environment.')
    return expected['mcpServers']['second-brain']


def probe_environment():
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(('BRAIN_', 'BASIC_MEMORY_', 'PYTHON', 'LD_', 'DYLD_'))
            and key.upper() not in {'BASH_ENV', 'ENV'}}


def launcher_bytes(root, python):
    client = Path(root) / 'app' / 'brain_client.py'
    return ('#!/bin/sh\nexec ' + shlex.join([str(python), '-B', str(client)]) + '\n').encode('utf-8')


def validate_launcher(root, python):
    """Check the precise executable before sending it an acceptance request."""
    root = Path(root)
    if root.resolve(strict=True) != root:
        raise ValueError('The MCP runtime path is not canonical.')
    expected = launcher_bytes(root, python)
    directory = os.open(root / 'bin', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        folder = os.fstat(directory)
        if folder.st_uid != os.geteuid() or folder.st_mode & 0o022:
            raise ValueError('The MCP launcher directory is writable by another account or has a different owner.')
        descriptor = os.open('second-brain-mcp', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o7022 or not info.st_mode & stat.S_IXUSR):
                raise ValueError('The MCP launcher must be an owned, owner-executable regular file with safe permissions.')
            if stream.read(len(expected) + 1) != expected:
                raise ValueError('MCP launcher bytes differ from this release and interpreter.')
        current = os.stat('second-brain-mcp', dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mode, current.st_uid) != (info.st_dev, info.st_ino, info.st_mode, info.st_uid):
            raise ValueError('The MCP launcher changed during validation.')
        current_folder = (root / 'bin').lstat()
        if (current_folder.st_dev, current_folder.st_ino) != (folder.st_dev, folder.st_ino):
            raise ValueError('The MCP launcher directory changed during validation.')
    finally:
        os.close(directory)
