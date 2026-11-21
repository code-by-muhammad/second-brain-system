"""Observe native file reads from the test without replacing product reads."""

import os
from pathlib import Path


def observe_note_open(monkeypatch, target, intercept):
    """Intercept Path.open and the no-follow POSIX descriptor for one fixture."""
    path_open = Path.open
    fdopen = os.fdopen

    def opened(path, mode='r', *args, **kwargs):
        open_stream = lambda: path_open(path, mode, *args, **kwargs)
        if path == target and mode in ('r', 'rb'):
            return intercept(open_stream, mode)
        return open_stream()

    def descriptor_opened(descriptor, mode='r', *args, **kwargs):
        open_stream = lambda: fdopen(descriptor, mode, *args, **kwargs)
        if mode in ('r', 'rb') and target.exists():
            if os.path.samestat(os.fstat(descriptor), target.stat()):
                return intercept(open_stream, mode)
        return open_stream()

    monkeypatch.setattr(Path, 'open', opened)
    if os.name == 'posix':
        monkeypatch.setattr(os, 'fdopen', descriptor_opened)
