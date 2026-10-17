"""Staged ordinary blob memory, Unicode, filters, and index ownership contracts.

All Git commands below use disposable local repositories and a local bare remote.
The reduced 32-character probe cap keeps transformed fixtures at most 129 bytes;
no memory/disk exhaustion or external network request is attempted.
"""
import hashlib
import io
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

import brain_git as syncer
import brain_store as store
from test_sync_archive_streaming import _git, _pair

CAP = 32
RELATIVE = '01-Projects/Filtered.md'


def _intercept_first_size(monkeypatch, action):
    """Inject once at the first staged read on either historical or bounded transport.

    Retain the existing test function bodies; an ordinary Git show read is the
    BEFORE control entrypoint, while cat-file -s is the AFTER entrypoint.
    """
    native = syncer._run_git
    fired = False
    def intercept(command, *args, **kwargs):
        nonlocal fired
        operation = command[4:] if isinstance(command, (list, tuple)) and command[:2] == ['git','--literal-pathspecs'] else []
        staged_read = operation[:2] == ['cat-file','-s'] or (len(operation) == 2 and operation[0] == 'show' and operation[1] == ':'+RELATIVE)
        if not fired and staged_read:
            fired = True
            override = action(subprocess.run,command,args,kwargs)
            if override is not None: return override
        return native(command,*args,**kwargs)
    monkeypatch.setattr(syncer, '_run_git', intercept)
    return lambda: fired

def install_filter(repo, tmp_path, transformed):
    script = tmp_path/'synthetic-ordinary-clean.py'
    script.write_text('import sys\n'
                      'sys.stdin.buffer.read()\n'
                      'sys.stdout.buffer.write('+repr(transformed)+')\n', encoding='utf-8')
    command = shlex.quote(sys.executable.replace('\\','/'))+' '+shlex.quote(str(script).replace('\\','/'))
    _git(repo, 'config', 'filter.ordinary.clean', command)
    _git(repo, 'config', 'filter.ordinary.smudge', 'cat')
    _git(repo, 'config', 'filter.ordinary.required', 'true')
    (repo/'.gitattributes').write_text(RELATIVE+' -text filter=ordinary\n', encoding='utf-8')
    _git(repo, 'add', '.gitattributes'); _git(repo, 'commit', '-m', 'Synthetic ordinary filter'); _git(repo, 'push')


def observe_blob_transport(monkeypatch):
    native = syncer._run_git
    calls = []
    def traced(command, *args, **kwargs):
        if command[:2] == ['git','--literal-pathspecs']:
            action = command[4:]
            if action and action[0] == 'show' and action[1].startswith(':'):
                pytest.fail('Staged note must not be captured completely in subprocess memory.')
            if action[:2] == ['cat-file','blob']:
                assert kwargs.get('stdout') not in (None, subprocess.PIPE)
                assert not kwargs.get('capture_output', False)
                assert kwargs.get('stderr') == subprocess.DEVNULL
                assert kwargs['env']['GIT_ALLOW_PROTOCOL'] == ''
                assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
                assert kwargs['env']['GIT_NO_REPLACE_OBJECTS'] == '1'
                calls.append(action)
        return native(command, *args, **kwargs)
    monkeypatch.setattr(syncer, '_run_git', traced)
    return calls


def observe_decoded_read(monkeypatch):
    original = io.TextIOWrapper
    requests = []
    class Checked:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __enter__(self): self.wrapped.__enter__(); return self
        def __exit__(self, *args): return self.wrapped.__exit__(*args)
        def read(self, size=-1):
            requests.append(size)
            assert type(size) is int and 0 < size <= CAP+1
            return self.wrapped.read(size)
        def __getattr__(self, name): return getattr(self.wrapped, name)
    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        return Checked(result) if 'newline' in kwargs and kwargs['newline'] is None and kwargs.get('encoding') == 'utf-8' else result
    monkeypatch.setattr(syncer, 'io', SimpleNamespace(TextIOWrapper=wrapper))
    return requests


def test_tiny_staged_note_uses_disk_stdout_then_positive_bounded_decoded_read(brain, tmp_path, monkeypatch):
    a,b = _pair(tmp_path)
    monkeypatch.setattr(syncer, 'NOTE_CHARACTER_LIMIT', CAP)
    path = a/RELATIVE; raw = 'Small é note.\r\n'.encode('utf-8'); path.write_bytes(raw)
    calls = observe_blob_transport(monkeypatch)
    reads = observe_decoded_read(monkeypatch)
    assert syncer.sync(a) == 'synced'
    assert calls and reads == [CAP+1]
    assert path.read_bytes() == raw
    assert syncer.sync(b) == 'synced' and (b/RELATIVE).read_bytes() == raw


@pytest.mark.parametrize('transformed', [b'x'*CAP, '漢'.encode()*CAP, '😀'.encode()*CAP,
                                       b'\r\n'*CAP, b'\xef\xbb\xbf'+b'x'*(CAP-1)],
                         ids=['ascii-at-char-cap', 'multibyte-over-byte-cap', 'four-byte-at-byte-ceiling',
                              'crlf-universal-newline-cap', 'bom-counted-at-cap'])
def test_filtered_staged_unicode_character_capacity_is_preserved(brain, tmp_path, monkeypatch, transformed):
    with io.TextIOWrapper(io.BytesIO(transformed), encoding='utf-8', newline=None) as oracle:
        assert len(oracle.read()) == CAP
    a,b = _pair(tmp_path); install_filter(a,tmp_path,transformed)
    monkeypatch.setattr(syncer, 'NOTE_CHARACTER_LIMIT', CAP)
    path = a/RELATIVE; raw = b'Harmless owner draft.\n'; path.write_bytes(raw)
    calls = observe_blob_transport(monkeypatch)
    reads = observe_decoded_read(monkeypatch)
    assert syncer.sync(a) == 'synced'
    assert calls and reads == [CAP+1]
    assert _git(a, 'show', 'HEAD:'+RELATIVE, raw=True) == transformed
    assert path.read_bytes() == raw
    assert syncer.sync(b) == 'synced' and (b/RELATIVE).read_bytes() == transformed


@pytest.mark.parametrize('transformed', [b'x'*(CAP+1), b'x'*(4*CAP+1), b'\xffinvalid',
                                       b'ordinary\x00invalid', b'secret: '+b'a'*16],
                         ids=['one-over-character-cap', 'one-over-utf8-byte-ceiling', 'invalid-utf8', 'nul', 'credential'])
def test_filtered_staged_invalid_content_refuses_before_commit_and_preserves_original(brain, tmp_path, monkeypatch, transformed):
    a,_ = _pair(tmp_path); install_filter(a,tmp_path,transformed)
    monkeypatch.setattr(syncer, 'NOTE_CHARACTER_LIMIT', CAP)
    path = a/RELATIVE; raw = b'Harmless owner draft.\n'; path.write_bytes(raw)
    before = _git(a,'rev-parse','HEAD')
    calls = observe_blob_transport(monkeypatch)
    with pytest.raises(syncer.GitSyncError) as error:
        syncer.sync(a)
    assert 'preserved' in str(error.value).lower()
    assert transformed.decode('utf-8',errors='replace') not in str(error.value)
    assert 'a'*16 not in str(error.value)
    assert _git(a,'rev-parse','HEAD') == before and not _git(a,'diff','--cached','--name-only')
    assert path.read_bytes() == raw
    assert bool(calls) == (len(transformed) <= 4*CAP)


@pytest.mark.parametrize('entry', ['100644 '+'a'*40+' 1', '120000 '+'a'*40+' 0',
                                 '100644 bad-object 0', 'malformed'],
                         ids=['nonzero-stage', 'symlink-mode', 'invalid-oid', 'malformed'])
def test_invalid_staged_metadata_refuses_without_launching_any_git_blob_reader(brain, monkeypatch, entry):
    def forbidden(*args,**kwargs): pytest.fail('Invalid staged metadata must stop before any Git blob call.')
    monkeypatch.setattr(syncer, '_run_git', forbidden)
    with pytest.raises(syncer.GitSyncError,match='verified locally'):
        syncer._ordinary_blob(brain[0],RELATIVE,entry)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'size-mismatch'], ids=['git-exit', 'git-timeout', 'blob-size-mismatch'])
def test_staged_reader_operational_failure_is_controlled_and_redacts_raw_git_output(brain, monkeypatch, failure):
    marker = 'sk-SyntheticAlphaBetaGammaDelta'
    relative = '01-Projects/Keys—'+marker+'.md'; oid = 'a'*40
    calls = []
    def controlled(command, **kwargs):
        calls.append(command[4:])
        if command[4:6] == ['cat-file','-s']:
            return subprocess.CompletedProcess(command,0,b'3\n',None)
        if failure == 'timeout': raise subprocess.TimeoutExpired(command,90,output=marker.encode())
        if failure == 'size-mismatch': kwargs['stdout'].write(b'x'); kwargs['stdout'].flush()
        return subprocess.CompletedProcess(command,1 if failure == 'exit' else 0,marker.encode(),marker.encode())
    monkeypatch.setattr(syncer, '_run_git', controlled)
    with pytest.raises(syncer.GitSyncError) as error:
        syncer._ordinary_blob(brain[0],relative,'100644 '+oid+' 0')
    message = str(error.value)
    assert marker not in message and relative not in message
    assert hashlib.sha256(relative.encode()).hexdigest() in message
    assert not store.SECRET.search(message) and len(calls) == 2


@pytest.mark.parametrize('owner_action', ['unrelated-entry', 'restaged-owned-note'])
def test_staged_failure_rolls_back_only_exact_owned_index_entries(brain, tmp_path, monkeypatch, owner_action):
    a,_ = _pair(tmp_path)
    transformed = b'secret: '+b'a'*16
    install_filter(a,tmp_path,transformed)
    monkeypatch.setattr(syncer, 'NOTE_CHARACTER_LIMIT', CAP)
    path = a/RELATIVE; raw = b'Harmless owner draft.\n'; path.write_bytes(raw)
    unrelated = '01-Projects/User-staged.md'; revised = b'Independent user revision.\n'
    def user_stage(native, command, args, kwargs):
        if owner_action == 'unrelated-entry':
            (a/unrelated).write_bytes(revised)
            native(['git','-C',str(a),'add','--',unrelated],check=True,capture_output=True)
        else:
            path.write_bytes(revised)
            native(['git','-C',str(a),'-c','filter.ordinary.clean=cat','add','--',RELATIVE],check=True,capture_output=True)
    fired = _intercept_first_size(monkeypatch,user_stage)
    before = _git(a,'rev-parse','HEAD')
    with pytest.raises(syncer.GitSyncError): syncer.sync(a)
    assert fired() and _git(a,'rev-parse','HEAD') == before
    expected = unrelated if owner_action == 'unrelated-entry' else RELATIVE
    assert _git(a,'diff','--cached','--name-only').splitlines() == [expected]
    assert _git(a,'show',':'+expected,raw=True) == revised
    assert path.read_bytes() == (raw if owner_action == 'unrelated-entry' else revised)
