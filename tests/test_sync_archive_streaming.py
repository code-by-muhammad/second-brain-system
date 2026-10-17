"""Independent R59 contracts: the archive exception is capacity, never trust.

All repositories, attributes, filters and credential-shaped strings are synthetic.
No real vault, Git configuration, remote account or network service is used.
"""
import hashlib
import io
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import brain_git


ARCHIVES = "02-Areas/AI-Memory/Wiki/Log-Archives/"
BLOCK = 65_536
MAX_BYTES = 64 * 1024 * 1024


def _git(repo, *args, raw=False):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def _pair(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    a, b = tmp_path / "a", tmp_path / "b"
    subprocess.run(["git", "clone", str(remote), str(a)], check=True, capture_output=True)
    for name, value in (("user.email", "synthetic@example.invalid"),
                        ("user.name", "Synthetic Archive Test"),
                        ("core.autocrlf", "false"), ("core.safecrlf", "false")):
        _git(a, "config", name, value)
    (a / "01-Projects").mkdir()
    (a / "01-Projects/Initial.md").write_bytes(b"Initial synthetic source.\n")
    _git(a, "add", ".")
    _git(a, "commit", "-m", "initial")
    _git(a, "push", "-u", "origin", "HEAD")
    subprocess.run(["git", "clone", str(remote), str(b)], check=True, capture_output=True)
    for name, value in (("user.email", "synthetic@example.invalid"),
                        ("user.name", "Synthetic Archive Test"),
                        ("core.autocrlf", "false"), ("core.safecrlf", "false")):
        _git(b, "config", name, value)
    return a, b


def _archive(repo, raw, relative=None):
    relative = relative or ARCHIVES + hashlib.sha256(raw).hexdigest() + ".md"
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return relative, path


def _assert_refused_unchanged(repo, path, raw):
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(brain_git.GitSyncError):
        brain_git.sync(repo)
    assert _git(repo, "rev-parse", "HEAD") == before
    assert not _git(repo, "diff", "--cached", "--name-only")
    assert path.read_bytes() == raw


def _history():
    # One byte beyond the ordinary note capacity, with no compressibility or
    # string-size shortcut assumed by the assertion.
    return (b"Synthetic compiler history.\n" * 80_000)[:2_000_001]


def test_large_canonical_archive_pushes_to_real_origin_and_second_clone(brain, tmp_path, monkeypatch):
    a, b = _pair(tmp_path)
    original = (a / "01-Projects/Initial.md").read_bytes()
    raw = _history()
    assert len(raw) == 2_000_001
    relative, path = _archive(a, raw)
    log = a / "02-Areas/AI-Memory/Wiki/Log.md"
    log.write_text("History archived in [[" + relative.removesuffix(".md") + "]].\n", encoding="utf-8")
    original_read_text = Path.read_text

    def forbid_archive_read_text(self, *args, **kwargs):
        if self == path:
            pytest.fail("The oversized archive must be scanned as bounded raw bytes.")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_archive_read_text)
    assert brain_git.sync(a) == "synced"
    assert path.read_bytes() == raw
    assert _git(a, "show", "HEAD:" + relative, raw=True) == raw
    assert brain_git.sync(b) == "synced"
    assert (b / relative).read_bytes() == raw
    assert (b / "02-Areas/AI-Memory/Wiki/Log.md").read_bytes() == log.read_bytes()
    assert (a / "01-Projects/Initial.md").read_bytes() == original
    assert brain_git.sync(a) == "synced"
    assert brain_git.sync(b) == "synced"
    assert not _git(a, "status", "--porcelain")
    assert not _git(b, "status", "--porcelain")


def test_real_compiler_rotation_archive_push_and_second_clone_use_one_model_call(brain, tmp_path, monkeypatch):
    import brain_settings as cfg
    import brain_store as store
    import brain_wiki as wiki
    from test_wiki_remediation import explicit, fake_model

    a, b = _pair(tmp_path)
    monkeypatch.setattr(cfg, "VAULT", a)
    monkeypatch.setattr(store, "VAULT", a)
    source = a / "01-Projects/Initial.md"
    original = source.read_bytes()
    log = a / "02-Areas/AI-Memory/Wiki/Log.md"
    log.parent.mkdir(parents=True)
    raw = _history()
    log.write_bytes(raw)
    packet = wiki.prepare(4)
    result = explicit(packet, {"pages": [{"category": "Projects", "slug": "initial", "title": "Initial",
        "related": [], "claims": [{"text": "The source records an initial synthetic source.",
        "source": "01-Projects/Initial.md", "quote": "Initial synthetic source.", "status": "historical"}]}]})
    # The fake model patches subprocess.Popen; restore it before exercising Git.
    with monkeypatch.context() as model_patch:
        calls = fake_model(model_patch, result)
        report = wiki.run()
    assert report["status"] == "complete" and len(calls) == 1
    relative = ARCHIVES + hashlib.sha256(raw).hexdigest() + ".md"
    assert (a / relative).read_bytes() == raw and source.read_bytes() == original
    assert relative.removesuffix(".md") in log.read_text(encoding="utf-8")
    assert not wiki.lint()["issues"]
    assert brain_git.sync(a) == "synced"
    assert brain_git.sync(b) == "synced"
    assert (b / relative).read_bytes() == raw
    assert (b / "02-Areas/AI-Memory/Wiki/Log.md").read_bytes() == log.read_bytes()
    assert source.read_bytes() == original
    assert brain_git.sync(a) == "synced" and brain_git.sync(b) == "synced"


def test_real_git_crlf_normalization_preserves_bom_lone_cr_unicode_and_local_raw(brain, tmp_path):
    a, b = _pair(tmp_path)
    (a / ".gitattributes").write_text("*.md text eol=lf\n", encoding="utf-8")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-m", "synthetic explicit LF attributes")
    _git(a, "push")
    # Euro crosses the first 64 KiB boundary; CRLF crosses the next boundary.
    raw = b"\xef\xbb\xbf" + b"a" * (BLOCK - 4) + "€".encode("utf-8")
    raw += b"\r\nUnicode: " + "漢字".encode("utf-8") + b"\rlone-carriage-return\n"
    raw += b"z" * ((2 * BLOCK - 1) - len(raw)) + b"\r\n"
    raw += (b"ordinary history\r\n" * 120_000)
    assert len(raw) > 2_000_000
    relative, path = _archive(a, raw)
    expected = raw.replace(b"\r\n", b"\n")
    assert expected != raw and b"\rlone-carriage-return" in expected
    assert brain_git.sync(a) == "synced"
    assert path.read_bytes() == raw
    assert _git(a, "show", "HEAD:" + relative, raw=True) == expected
    assert brain_git.sync(b) == "synced"
    assert (b / relative).read_bytes() == expected
    assert brain_git.sync(a) == "synced"
    assert brain_git.sync(b) == "synced"


@pytest.mark.parametrize("shape", ["ordinary", "wrong-hash", "upper-hash", "folder-case",
                                   "nested", "upper-extension", "suffix-lookalike", "other-area"])
def test_capacity_exception_is_exact_and_hash_bound(brain, tmp_path, shape):
    a, _ = _pair(tmp_path)
    raw = _history()
    digest = hashlib.sha256(raw).hexdigest()
    choices = {
        "ordinary": "01-Projects/Large.md",
        "wrong-hash": ARCHIVES + "0" * 64 + ".md",
        "upper-hash": ARCHIVES + digest.upper() + ".md",
        "folder-case": ARCHIVES.replace("Log-Archives", "Log-archives") + digest + ".md",
        "nested": ARCHIVES + "nested/" + digest + ".md",
        "upper-extension": ARCHIVES + digest + ".MD",
        "suffix-lookalike": ARCHIVES + digest + "-history.md",
        "other-area": "04-Archive/Log-Archives/" + digest + ".md",
    }
    _, path = _archive(a, raw, choices[shape])
    _assert_refused_unchanged(a, path, raw)


@pytest.mark.parametrize("raw", [b"valid prefix\x00invalid\n", b"valid prefix\xffinvalid\n",
                                b"trailing incomplete UTF8 \xe2\x82"])
def test_invalid_archive_content_is_controlled_and_preserved(brain, tmp_path, raw):
    a, _ = _pair(tmp_path)
    _, path = _archive(a, raw)
    _assert_refused_unchanged(a, path, raw)


class _BoundedReader(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.requested = []

    def read(self, size=-1):
        assert 0 < size <= BLOCK, "Archive scanner requested an unbounded or oversized read."
        self.requested.append(size)
        return super().read(size)


def _scan(raw, expected_size=None, expected_sha=None):
    reader = _BoundedReader(raw)
    result = getattr(brain_git, "_scan_archive")(
        reader, len(raw) if expected_size is None else expected_size, expected_sha
    )
    return result, reader


def test_stream_scanner_hashes_raw_and_only_crlf_normalization_in_bounded_reads():
    raw = b"\xef\xbb\xbf" + b"a" * (BLOCK - 4) + "€".encode("utf-8")
    raw += b"\r\n" + b"b" * (BLOCK - 5) + b"\r\nC\rlone\n"
    result, reader = _scan(raw, expected_sha=hashlib.sha256(raw).hexdigest())
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["lf_sha256"] == hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    assert result["bytes"] == len(raw)
    assert len(reader.requested) >= 3


_SECRET_WITNESSES = [
    "-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----", "-----BEGIN OPENSSH PRIVATE KEY-----",
    "sk-" + "a" * 20,
    *["gh" + kind + "_" + "a" * 20 for kind in "pousr"],
    *["xox" + kind + "-" + "a" * 15 for kind in "baprs"],
    "api_key: " + "a" * 16, "password=\"" + "a" * 16,
    "access-token: '" + "a" * 16, "secret=" + "a" * 16,
]


@pytest.mark.parametrize("witness", _SECRET_WITNESSES, ids=["pattern-%02d" % i for i in range(len(_SECRET_WITNESSES))])
def test_every_existing_secret_witness_crossing_stream_boundary_is_rejected(witness):
    # Newline provides the token word boundary without removing the split.
    prefix = b"a" * (BLOCK - len(witness) // 2 - 1) + b"\n"
    raw = prefix + witness.encode("utf-8") + b"\n"
    with pytest.raises(ValueError) as caught:
        _scan(raw)
    assert witness not in str(caught.value)


@pytest.mark.parametrize("whitespace", [" ", "\t", "\r\n", "\u2003", "\u00a0"])
def test_unbounded_unicode_whitespace_between_key_separator_and_value_cannot_evade_scan(whitespace):
    raw = ("password" + whitespace * (BLOCK + 19) + ":" +
           whitespace * (BLOCK + 23) + "a" * 16 + "\n").encode("utf-8")
    with pytest.raises(ValueError):
        _scan(raw)


@pytest.mark.parametrize("raw", [b"sk-" + b"a" * 19, b"ghp_" + b"a" * 19,
                                b"xoxb-" + b"a" * 14, b"password: short-value",
                                "ordinary Unicode \u2003 notes\n".encode("utf-8")])
def test_short_and_noncredential_controls_are_accepted(raw):
    result, _ = _scan(raw)
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("raw,expected", [(b"history", 6), (b"history", 8)])
def test_stream_growth_and_truncation_are_refused(raw, expected):
    with pytest.raises(ValueError):
        _scan(raw, expected_size=expected)


def test_stream_oversize_rejected_before_any_content_read():
    reader = _BoundedReader(b"small")
    with pytest.raises(ValueError):
        getattr(brain_git, "_scan_archive")(reader, MAX_BYTES + 1)
    assert not reader.requested


def test_stream_read_failure_is_not_silently_accepted():
    class Broken(io.BytesIO):
        def read(self, size=-1):
            raise OSError("synthetic reader unavailable")
    with pytest.raises(OSError):
        getattr(brain_git, "_scan_archive")(Broken(b"history"), 7)


def test_shared_archive_capacity_refuses_before_content_reads_and_preserves_local_history(brain, tmp_path, monkeypatch):
    a, _ = _pair(tmp_path)
    raw = b"Synthetic history beyond a deliberately tiny shared capacity.\n"
    _, path = _archive(a, raw)
    before = _git(a, "rev-parse", "HEAD")
    original_open = Path.open
    reads = []

    class UnreadableArchive:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def read(self, size=-1):
            reads.append(size)
            pytest.fail("Over-capacity archive content must not be read.")

    def wrapped_open(self, mode="r", *args, **kwargs):
        stream = original_open(self, mode, *args, **kwargs)
        return UnreadableArchive(stream) if self == path and mode == "rb" else stream

    with monkeypatch.context() as capacity_patch:
        capacity_patch.setattr(brain_git, "ARCHIVE_BYTES_LIMIT", 32)
        capacity_patch.setattr(Path, "open", wrapped_open)
        with pytest.raises(brain_git.GitSyncError, match="sync limit"):
            brain_git.sync(a)
    assert not reads and path.read_bytes() == raw
    assert _git(a, "rev-parse", "HEAD") == before
    assert not _git(a, "diff", "--cached", "--name-only")


def test_archive_read_oserror_has_controlled_diagnostic_and_preserves_original(brain, tmp_path, monkeypatch):
    a, _ = _pair(tmp_path)
    raw = b"Preserve this synthetic archive when its reader is unavailable.\n"
    _, path = _archive(a, raw)
    original_open = Path.open
    fired = False

    def unavailable_once(self, mode="r", *args, **kwargs):
        nonlocal fired
        if self == path and mode == "rb" and not fired:
            fired = True
            raise OSError("synthetic private diagnostic must not escape")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unavailable_once)
    before = _git(a, "rev-parse", "HEAD")
    with pytest.raises(brain_git.GitSyncError) as caught:
        brain_git.sync(a)
    assert fired and "synthetic private diagnostic" not in str(caught.value)
    assert path.read_bytes() == raw
    assert _git(a, "rev-parse", "HEAD") == before
    assert not _git(a, "diff", "--cached", "--name-only")


def test_archive_exception_still_refuses_linked_ancestor_before_staging(brain, tmp_path, monkeypatch):
    a, _ = _pair(tmp_path)
    raw = _history()
    _, path = _archive(a, raw)
    original_junction = brain_git.os.path.isjunction
    monkeypatch.setattr(brain_git.os.path, "isjunction", lambda candidate:
                        Path(candidate) == path.parent or original_junction(candidate))
    _assert_refused_unchanged(a, path, raw)


def _install_filter(repo, tmp_path, transformed_suffix):
    script = tmp_path / "synthetic-clean-filter.py"
    script.write_text(
        "import sys\n"
        "raw = sys.stdin.buffer.read()\n"
        "suffix = " + repr(transformed_suffix) + "\n"
        "sys.stdout.buffer.write(suffix + raw[len(suffix):])\n",
        encoding="utf-8",
    )
    command = shlex.quote(sys.executable.replace("\\", "/")) + " " + shlex.quote(str(script).replace("\\", "/"))
    _git(repo, "config", "filter.archive.clean", command)
    _git(repo, "config", "filter.archive.smudge", "cat")
    _git(repo, "config", "filter.archive.required", "true")
    (repo / ".gitattributes").write_text(ARCHIVES + "*.md filter=archive -text\n", encoding="utf-8")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-m", "synthetic local archive filter")
    _git(repo, "push")


@pytest.mark.parametrize("suffix", [b"\nsecret: " + b"a" * 16, b"\nbenign but unauthorized transformation\n"],
                         ids=["credential-introduced-by-filter", "benign-unauthorized-filter-change"])
def test_staged_filter_transformation_is_independently_refused_and_owned_stage_rolled_back(brain, tmp_path, suffix):
    a, _ = _pair(tmp_path)
    _install_filter(a, tmp_path, suffix)
    raw = _history()
    _, path = _archive(a, raw)
    before = _git(a, "rev-parse", "HEAD")
    with pytest.raises(brain_git.GitSyncError) as caught:
        brain_git.sync(a)
    if suffix.startswith(b"\nsecret:"):
        assert "possible credential" in str(caught.value)
    else:
        assert "differs beyond permitted CRLF-to-LF" in str(caught.value)
    assert suffix.decode("ascii") not in str(caught.value)
    assert _git(a, "rev-parse", "HEAD") == before
    assert not _git(a, "diff", "--cached", "--name-only")
    assert path.read_bytes() == raw


def _intercept_first_size(monkeypatch, action):
    original = subprocess.run
    fired = False

    def intercept(command, *args, **kwargs):
        nonlocal fired
        if not fired and isinstance(command, (list, tuple)) and "cat-file" in command and "-s" in command:
            fired = True
            override = action(original, command, args, kwargs)
            if override is not None:
                return override
        return original(command, *args, **kwargs)

    monkeypatch.setattr(brain_git, '_run_git', intercept)
    return lambda: fired


def test_poststage_unrelated_user_entry_is_preserved_when_archive_stage_is_rolled_back(brain, tmp_path, monkeypatch):
    a, _ = _pair(tmp_path)
    raw = _history()
    relative, path = _archive(a, raw)
    user_relative = "01-Projects/User-staged.md"

    def stage_user(original, command, args, kwargs):
        (a / user_relative).write_bytes(b"Independent user staging.\n")
        original(["git", "-C", str(a), "add", "--", user_relative], check=True, capture_output=True)

    fired = _intercept_first_size(monkeypatch, stage_user)
    before = _git(a, "rev-parse", "HEAD")
    with pytest.raises(brain_git.GitSyncError):
        brain_git.sync(a)
    assert fired()
    assert _git(a, "rev-parse", "HEAD") == before
    assert _git(a, "diff", "--cached", "--name-only").splitlines() == [user_relative]
    assert path.read_bytes() == raw
    assert relative not in _git(a, "diff", "--cached", "--name-only").splitlines()


def test_poststage_independent_restaging_of_owned_archive_is_preserved(brain, tmp_path, monkeypatch):
    a, _ = _pair(tmp_path)
    raw = _history()
    relative, path = _archive(a, raw)
    other = a / "01-Projects/Automatic.md"
    other.write_bytes(b"Automatically eligible sibling.\n")
    user_version = raw + b"Independent user edit after our index snapshot.\n"

    def restage_user(original, command, args, kwargs):
        path.write_bytes(user_version)
        original(["git", "-C", str(a), "add", "--", relative], check=True, capture_output=True)

    fired = _intercept_first_size(monkeypatch, restage_user)
    before = _git(a, "rev-parse", "HEAD")
    with pytest.raises(brain_git.GitSyncError):
        brain_git.sync(a)
    assert fired()
    assert _git(a, "rev-parse", "HEAD") == before
    assert _git(a, "diff", "--cached", "--name-only").splitlines() == [relative]
    assert _git(a, "show", ":" + relative, raw=True) == user_version
    assert path.read_bytes() == user_version
    assert other.read_bytes() == b"Automatically eligible sibling.\n"


@pytest.mark.parametrize("failure", ["oversized-object", "size-mismatch", "cat-file-failure", "cat-file-timeout"])
def test_staged_blob_size_and_service_failures_leave_no_owned_stage_or_commit(brain, tmp_path, monkeypatch, failure):
    a, _ = _pair(tmp_path)
    raw = _history()
    _, path = _archive(a, raw)

    def fail_size(original, command, args, kwargs):
        if failure == "cat-file-timeout":
            raise subprocess.TimeoutExpired(command, 90)
        if failure == "cat-file-failure":
            return subprocess.CompletedProcess(command, 3, stdout="", stderr="synthetic unavailable")
        value = MAX_BYTES + 1 if failure == "oversized-object" else len(raw) + 1
        output = str(value) + "\n"
        if not kwargs.get("text") and not kwargs.get("encoding"):
            output = output.encode("ascii")
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    fired = _intercept_first_size(monkeypatch, fail_size)
    _assert_refused_unchanged(a, path, raw)
    assert fired()


@pytest.mark.parametrize("failure", [None, "timeout", "failed-exit", "short-output"],
                         ids=["successful-raw-object", "blob-timeout", "blob-failed-exit", "blob-size-mismatch"])
def test_actual_owned_blob_read_is_spooled_local_only_and_failures_rollback(brain, tmp_path, monkeypatch, failure):
    a, _ = _pair(tmp_path)
    raw = _history()
    _, path = _archive(a, raw)
    native = subprocess.run
    observed = []

    def intercept(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and "cat-file" in command:
            env = kwargs["env"]
            assert env["GIT_ALLOW_PROTOCOL"] == ""
            assert env["GIT_NO_LAZY_FETCH"] == "1"
            assert env["GIT_TERMINAL_PROMPT"] == "0"
            assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
            assert kwargs["stderr"] == subprocess.DEVNULL
            assert kwargs["timeout"] == 90
            if "blob" in command:
                observed.append(command[-1])
                assert hasattr(kwargs["stdout"], "fileno")
                assert kwargs["stdout"] != subprocess.PIPE
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 90)
                if failure == "failed-exit":
                    return subprocess.CompletedProcess(command, 3)
                if failure == "short-output":
                    kwargs["stdout"].write(b"short object")
                    return subprocess.CompletedProcess(command, 0)
        return native(command, *args, **kwargs)

    monkeypatch.setattr(brain_git, '_run_git', intercept)
    before = _git(a, "rev-parse", "HEAD")
    if failure is None:
        assert brain_git.sync(a) == "synced"
        assert _git(a, "rev-parse", "HEAD") != before
    else:
        with pytest.raises(brain_git.GitSyncError):
            brain_git.sync(a)
        assert _git(a, "rev-parse", "HEAD") == before
    assert len(observed) == 1 and path.read_bytes() == raw
    assert not _git(a, "diff", "--cached", "--name-only")
