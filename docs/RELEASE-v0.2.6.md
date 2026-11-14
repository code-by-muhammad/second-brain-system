# v0.2.6 candidate - one-command setup, complete assistant wiring, index retry and FastMCP 4.1.8

Status: candidate preparation, 14 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

v0.2.5 could update a Mac, but a device was only complete after manual steps that the installer left out: the Claude Code hooks, the Claude Code instruction block and the skill. A setup that skipped them looked finished and was not. This candidate moves those steps into the installer and puts one command in front of it.

## What changed

1. **One command.** `bootstrap/setup.sh` (Mac, Linux) and `bootstrap/setup.ps1` (Windows) clone one exact tag into `<runtime>/releases/<tag>`, compare its commit with the value fixed in the published script, refuse a release folder whose files differ from that commit, and start `setup_device.py`. The published scripts are the tracked files with the tag's commit written into their `COMMIT` line; the tracked files themselves refuse to run. That script verifies the manifest, inspects the device, then runs the existing `install.py` for a new device or the existing `update.py` for an installed one, completes the assistants, and ends with `check_setup.py`. It contains no install logic of its own. Running it again on a finished device only adds what is missing, starts a service that is not running, and repeats the check. With `--local-only` it refuses before installing when the notes folder has a Git remote.
2. **Complete assistant wiring.** The sealed client plan now also carries the instruction block for Claude Code (`~/.claude/CLAUDE.md`) and the bundled skill for Claude Code and Codex. `setup_device.py` selects the Claude Code hooks unless `--no-hooks` is given. `install.py` itself keeps `--include-hooks` as an explicit option.
3. **Shared rules and skill files.** An instruction or skill file may be a link to one shared copy, and Claude Code may load the block through an `@path` import. The plan follows such a link or import only inside the resolved home folder, refreshes the block in the one file that holds it, and writes a shared file once. With no block yet, the first one goes into the file the selected assistants share. A link that leaves the home folder is refused, an import that leaves it is ignored, and a block loaded from two files is refused. Connection and hook settings files are unchanged: a linked one is still refused.
4. **Adding to an installed device.** `install.py --configure-clients --clients-only` adds what a selected assistant lacks on the same installed release, with the recorded Python, under the update lock, without redeploying or touching the service. `--refresh-only` is the same switch. An update still refreshes only what existed; `setup_device.py` runs this step after it.
5. **Timezone.** A new device takes `--timezone` or the system timezone (through `tzlocal`, already in the lock). An unknown zone, or a system zone that cannot be read, is refused. The silent default of `Asia/Karachi` is gone. An existing device keeps its value.
6. **Notes without a Git remote.** `brain_git.sync()` returns an explicit `off` status when the vault is not a Git repository or its repository has no `origin`. That is decided from the repository's own files before any Git command, so nothing is staged or committed. A notes folder that only sits inside another repository is never synchronized through it. Maintenance no longer reports attention every pass. A single device must be the maintainer, because only the maintainer links captures into daily notes.
7. **Index retry.** One upstream `bm reindex` run is not always a finished index, for three reasons found in Basic Memory 0.23.2: it embeds a very large note 256 chunks per run (`semantic_vector_sync.py`), it drops a note's search rows when a write-lock wait fails and still exits 0 (`batch_indexer.py`), and it ends with an orphan cleanup that is quadratic in chunk count and changes no row (`sqlite_vec_index.py`), which the stall watchdog then reported as a failure even on a verified index. `brain_index_bootstrap` now repeats the incremental run while defects fall or embedded chunks grow, rebuilds lost search rows once with `--full --search`, stops after two passes without progress or 64 passes, and lets the coverage check decide the result. A run that stalls with nothing left to verify is `verified`. A run that stalls with no progress and unverified coverage is still `IndexStalled`.
8. **Refusal reason.** `update.py` adds the controller's last error line to "Verified controller refused ...".
9. **FastMCP 4.1.8, no advisory exception.** Basic Memory 0.23.2 pins `fastmcp==4.0.0b1`. `[tool.uv] override-dependencies` now selects `fastmcp` and `fastmcp-slim` 4.1.8, which contain the fix for GHSA-vqqp-x5qm-5qp4 (fixed upstream in 4.1.2). The exception is removed from all four profiles and the gate no longer excuses that advisory. The raw audit of this lock reported no finding on 14 November 2026. `authlib` moves to 1.9.5 and the keyring packages leave the graph as a result.
10. **Shipped with the release.** The blank starter vault (`starter/`), the read-only acceptance checker (`check_setup.py`) and one setup guide (`docs/SETUP-GUIDE.md`).
11. **Wording.** The protocol block, the skill and the hook text no longer name one language or one operating system for the maintainer.

## Boundaries to review

- **Link following** applies to instruction and skill Markdown files only, never to JSON or TOML settings, and never outside the resolved home folder.
- **Clients-only configuration** writes assistant settings outside the update transaction. It refuses another release, another Python, an active update and an unfinished first install, and it never creates a second `second-brain` entry that points at another runtime.
- **The dependency override** runs Basic Memory 0.23.2 against a FastMCP it does not declare. The evidence is the regression suite, the import smoke test on every CI platform, and real MCP calls on the devices recorded on the release page. Basic Memory itself is unchanged.
- **Index retry** treats a stall as success only when the independent coverage check verifies the index. It does not read upstream logs.

## Known limits

- Fresh installs on Windows and Linux are exercised by CI on disposable machines, not on an owner device.
- The wiki compiler still requires the Windows maintainer and an explicitly selected `codex.exe`.
- The folder of the version a device was updated from must stay until the next update.
- A full index rebuild of a very large vault still takes long, because each upstream run ends with the quadratic cleanup until the watchdog stops it.
- Changing the timezone or the role of an installed device is not supported.
- A packaged desktop installer is not part of this release.

See [the setup guide](SETUP-GUIDE.md), [device setup](DEVICE-SETUP.md), [verification boundaries](DEVICE-TESTING.md), [security limits](../SECURITY.md) and the [v0.2.5 notes](RELEASE-v0.2.5.md).
