# Shared Second Brain

Private, local Obsidian retrieval plus an evidence-linked Karpathy-style wiki. Same Python core and commands on Windows, macOS and Linux. This repository contains software only; the private `second-brain` repository contains notes and derived wiki pages.

## What runs

```text
Claude / Codex / compatible local MCP client
                  |
           local stdio adapter
                  |
    authenticated 127.0.0.1 MCP service
          /                      \
 Obsidian Markdown         local SQLite + embeddings
          |
 one maintainer: durable changed-source queue -> Codex compiler
          |
 evidence validation -> derived wiki, index, compilation log
          |
 per-device serialized Git sync -> other devices
```

The service exposes six constrained tools: `brain_context`, `brain_search`, `brain_read`, `brain_capture`, `brain_update_current`, `brain_health`. Original notes are not rewritten by the compiler. Automatically compiled claims stay historical/uncertain/superseded; the confirmed Profile page is protected from the compiler.

## New Mac or Linux device

Prerequisites: Git, [GitHub CLI](https://cli.github.com/), and [uv from Astral](https://docs.astral.sh/uv/getting-started/installation/). Authenticate on that device; do not copy tokens from another computer.

```sh
gh auth login
gh repo clone code-by-muhammad/second-brain ~/Documents/Second-Brain
gh repo clone code-by-muhammad/second-brain-system ~/Code/second-brain-system
cd ~/Code/second-brain-system
git checkout v0.1.2
uv run --no-project --python 3.12 install.py --vault "$HOME/Documents/Second-Brain" --configure-clients --download-model --install-service
```

If the vault already exists, use its actual path instead of cloning again. Default runtime location is `~/.local/share/second-brain`; override with `--root`. Default role is **capture/search**. Linux startup requires a working systemd user session. macOS startup uses a per-user LaunchAgent. Both depend on the user session; sleep/offline devices catch up after they return.

The installer creates a native environment from `uv.lock`, a local index/configuration, a fresh token, client configuration backups and startup registration. Claude Code and Codex configuration are supported. Claude Desktop configuration is added on Windows/macOS. Restart clients and verify their exposed tools. Web ChatGPT/Claude and Cowork access require their own verified connector route; local installation does not establish those connections.

It also installs a managed Codex instruction block and Claude Code SessionStart/Stop hooks for the shared memory protocol, preserving unrelated owner instructions and hooks. Those hooks request concise milestones after meaningful work; they do not save raw conversations. Other MCP clients receive the server's protocol instructions, subject to that client's capabilities.

The first model download comes from the FastEmbed-supported BAAI embedding model. Later embedding runs force local/offline use. Initial indexing can take time; fresh-file search is available while it completes.

## Windows installation

The current installation uses `E:\Second-Brain-System` and the existing vault `E:\Second-Brain`. New Windows installations use the same installer, with their own actual paths:

```powershell
uv run --no-project --python 3.12 install.py --vault 'E:\Second-Brain' --root 'E:\Second-Brain-System' --configure-clients --download-model --install-service
```

Do not add `--role maintainer` on another device. Writer ownership must be explicitly handed off first.

## Common commands

Use the Python executable recorded in the local `device.json`, then the installed `app/brain_cli.py`:

```text
python <runtime>/app/brain_cli.py health
python <runtime>/app/brain_cli.py search "project evidence"
python <runtime>/app/brain_cli.py read "02-Areas/AI-Memory/Wiki/Index.md"
python <runtime>/app/brain_cli.py wiki status
python <runtime>/app/brain_cli.py wiki lint
```

Maintainer only: `wiki run --limit 4` compiles a bounded batch. Three failures leave a source part for attention; `wiki retry` clears the failure counters after the cause is fixed. Queue and verified receipts survive restarts. Existing source changes requeue all parts of that source. Large Markdown sources are split into overlapping bounded input parts; source hashes still cover the entire original.

The scheduled responsibility is configured separately through the desktop scheduler. Idle runs avoid a model request. The compiler requires the official Codex CLI, a local authenticated account, and remaining quota. Private source excerpts are sent to that model provider. SQLite, embedding model and search remain local. The current schedule requires its host and desktop app to be available; no 24/7 availability is implied.

## Shared and local data

Shared knowledge: notes, immutable captures, wiki topic/source pages, index, compilation history and writer control page. Shared software: this repository and tagged releases. Local only: `device.json`, SQLite/WAL, model cache, auth files, Python environments, pending job database, logs and client/startup settings. Never copy the whole runtime folder through Git/Syncthing.

Git sync runs every 15 minutes on each installed device, with one local maintenance owner. It merges independent offline edits only after a conflict-free preflight. Same-note conflicts, staged user changes, unknown files and source deletions pause sync for review. It never force-pushes. Automatic same-note conflict resolution would risk losing meaning.

Keep one live sync transport. Syncthing is not enabled by this release. GitHub currently provides store-and-forward when devices are not online together.

## Software releases and recovery

Notes sync does not upgrade running software. Install an explicit release using the current runtime Python:

```text
python <software-checkout>/update.py --root <runtime> --version v0.1.2
```

The updater clones only the owner's private repository, verifies the release file hashes, creates a native environment before stopping the service, preserves tokens/paths/role, registers startup/client settings and polls authenticated MCP health for the expected version for up to two minutes. It records the prior release, stops a failed new service before restoring the former one, and verifies restored health. Hashes detect accidental corruption; they do not substitute for repository access controls or independent code review.

To return to the previous release manually, stop the registered service; use the previous `release_root` and Python from `state/previous-release.json` to run its `install.py` with the same `--root`, `--vault`, `--reuse-python`, `--configure-clients`, `--install-service`; then start the service. Source notes and SQLite are not deleted. Original pre-migration app/task backups are retained on the initial Windows host.

Ownership transfer: finish/sync pending work, disable the old worker schedule, stop shared maintenance on the old host, change its role to capture, record the new device ID in the shared Control page, sync that page, then explicitly enable maintainer role/schedule on the new host. The Control page prevents accidental duplicate writers; it is not a global lease during a network partition.

## Verification and sources

`uv sync --locked --python 3.12`, then `uv run --locked python -m pytest -q`. CI also imports native retrieval/MCP libraries on Linux, Windows, Apple Silicon and Intel Mac. No private vault is uploaded to CI. Actual device login, client tool discovery, indexing, wake/reboot and two-device acceptance checks are separate from synthetic CI.

See [research and acceptance plan](docs/RESEARCH-AND-PLAN.md). Pinned FastMCP is an upstream prerelease; dependency auditing and tests report observed results, not a promise of zero security risk.
