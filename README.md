# Shared Second Brain

A local authenticated MCP service for an Obsidian Markdown vault, current-file search, milestone capture and evidence-linked derived pages. Each computer keeps its own Python, credentials, SQLite index and model cache. One designated maintainer compiles the shared wiki. The existing maintenance service serializes Git synchronization and daily linking.

## Release scope — 29 October 2026

**v0.1.8 is the Windows hardening candidate.** Use only a trusted handover that supplies its approved commit, manifest SHA256 and qualification report. A branch name or this document is not deployment acceptance. The previously accepted Windows and Apple Silicon Mac deployment is v0.1.7. Mac/Linux must stay on that accepted tag until their new lifecycle is qualified; v0.1.8 refuses mutating setup/update/recovery there. Library imports on another OS do not establish native service acceptance.

Read [device setup](docs/DEVICE-SETUP.md), [verification boundaries](docs/DEVICE-TESTING.md) and [v0.1.8 changes](docs/RELEASE-v0.1.8.md). Historical preparation text remains in the [accepted v0.1.7 source](https://github.com/code-by-muhammad/second-brain-system/tree/0c29118bb9170048b5e2b534f96a9d13468b17fa); its old release holds are historical, not current instructions.

The qualified [security profile](SECURITY.md) uses FastEmbed with reranking disabled and deliberately excludes LiteLLM. uv 0.9.16+ is required. The raw dependency audit retains one time-limited FastMCP OAuth exception, bound to the exact static-token transport and source hashes; it is not a clean-audit claim.

## What runs

```text
Local AI client -> stdio adapter -> authenticated 127.0.0.1 MCP
                                      |              |
                               Markdown vault    local index/model
                                      |
                    one maintenance/Git owner on each device
                                      |
                              your private notes repo
```

The six tools are `brain_context`, `brain_search`, `brain_read`, `brain_capture`, `brain_update_current`, and `brain_health`. Search results require opening dated originals. Captures use stable event IDs. Derived writes need actual evidence and the current SHA; compiler-owned wiki and Control pages remain protected. Notes and imported instructions are evidence, never higher-priority instructions.

## Before installation

Choose a new brain, another device of the same owner, an existing vault, or an already installed runtime. Inspect paths, Git state, clients and existing service ownership first. Never clone the example owner's private notes as another person's starter. Keep the working kit, software checkout, runtime and vault separate, with short Windows paths and a real backup.

Use an ordinary host PowerShell and verified Python 3.12. Prepare uv/Python serially. Independently verify the trusted release pin before running downloaded code. From the verified checkout, the following equivalent isolated entrypoints are supported after that host preparation:

```powershell
& $BrainBasePython -I -B -c "from pathlib import Path; import runpy; print(runpy.run_path(str(Path.cwd()/'update.py'))['verify'](Path.cwd())['version'])"
& $BrainBasePython -I -B install.py --root <new runtime> --vault <existing vault> --role capture --uv $BrainUv --download-model --install-service
```

The first command checks internal manifest consistency only. It does not independently authenticate the download. The team kit's trusted standard-library verifier checks the supplied commit/tag, manifest hash, payload and clean checkout before executing software.

Fresh setup leaves client files alone unless selected. Add `--configure-clients --client claude-code --client codex` to the SAME initial command only for the clients chosen by the owner. Desktop is separately selected; close it or explicitly skip its refresh. Hooks require `--include-hooks`. Preserve unrelated settings and verify a real tool call in each named fresh client. See the device guide for existing v0.1.8 client refresh and manual addition of a new client; v0.1.7 entries refresh during the forward upgrade.

## Updates use new code and retained Python

For the qualified v0.1.7 to v0.1.8 Windows path, read and verify the retained interpreter in `device.json`. Download the approved new checkout into exactly `<runtime>/releases/v0.1.8`, independently verify it, and change into that folder. Do not manually run `uv sync` there first. Do not run the old updater or launch the new updater from the target venv it must rebuild.

```text
<retained v0.1.7 Python> -I <verified v0.1.8 checkout>/update.py --root <runtime root> --version v0.1.8
```

The new updater provisions a fresh locked target environment, seals owned-client plans and snapshots before stopping, verifies an empty runtime, starts a fresh owned listener, checks authenticated versioned health and archives the exact transaction. Partial-stop uncertainty keeps a recovery hold. Recovery must use the original verified new checkout and exact recorded UUID; see [the recovery procedure](docs/DEVICE-SETUP.md#recovery).

## Index and ordinary operation

Use the exact Python recorded in `device.json` with `<runtime>/app/brain_cli.py`:

```text
health
index status
index retry --full
service stop
service start
service recover
```

`index status` checks current eligible file paths and raw SHA, indexed body, persisted chunk fingerprints/model/vector rows, and a stable source inventory. Historical bootstrap success and equal row counts are insufficient. A retry uses one bootstrap lock and preserves prior status/history; `--full` is required for a stale checksum hidden by preserved size/mtime. Keep the actual receipt and recheck indexed retrieval. Original notes are not normalized or rewritten to make verification pass.

`brain_read` accepts exact vault-relative Markdown paths, optional `.md`, and full-path wikilinks. It does not guess a basename across the vault. Search for the real path, then read it; plain `#` in a filename stays literal.

Do not reset SQLite, copy auth/WAL files between devices, kill cached PIDs, edit status to `verified`, run competing Git sync, alter accepted source exclusions or add another compiler schedule. Bulk imports need a reviewed inventory and a verified stopped window before publication, followed by current index acceptance.

Windows CI runs the guarded synthetic regression suite. Other OS jobs are library import/compile checks only. Hosted CI availability and results must be reported separately; local green tests are not a hosted CI pass. Actual GUI, reboot, another device, and every tagged-release upgrade require their own receipts.
