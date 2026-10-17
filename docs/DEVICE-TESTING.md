# Set up and test another device

This is the short handover for the owner's Mac/Linux device. The v0.1.7 branch is a **device-testing candidate**, not a production release or proof that this physical device is already installed. Windows production stays v0.1.6 until explicit release acceptance. The dated shared `02-Areas/AI-Memory/Second-Brain-System.md` supplies the exact candidate commit, manifest SHA256 and current acceptance status. Use those pins; do not silently select a newer branch tip or an old installation example.

## What is shared

`code-by-muhammad/second-brain` contains the notes, captures and wiki. `code-by-muhammad/second-brain-system` distributes software. Each device has its own native Python environment, local index, model cache, credentials, device ID and MCP connection. Never copy Windows environments, live SQLite/WAL files or another device's tokens. Notes synchronize through the private GitHub repository; another laptop does not remotely mount the Windows PC.

New devices use **capture** role. Windows remains the sole wiki compiler and daily aggregator. The local service owns Git synchronization; do not add a cron, compiler schedule or second sync loop. Local search/capture can work while Windows is off; wiki compilation waits for its designated host. Internet is needed for GitHub synchronization and cloud AI calls. Same-note conflicts pause for owner review.

## Give this to the device's local Codex or Claude Code agent

> Set up my shared Second Brain on this actual device for testing. First read this document and the current shared System/Device-Setup pages. Inspect existing repositories, runtime and client settings. Use my authenticated private repositories and the exact commit/manifest SHA recorded in the current handover. Never paste tokens, overwrite existing notes or select an unapproved release. Use a new checkout and separate synthetic verification paths. Verify the manifest before executing downloaded code. Run the locked native checks below; keep failure receipts. Ask before major architecture changes, new external services or new recurring AI costs. If native verification passes and no existing installation would be replaced, install this explicitly authorized testing candidate into its own new runtime, using capture role and the packaged per-user service. Configure my installed local clients, install the supplied skill and managed instruction blocks as described below, then verify real MCP retrieval, one labeled onboarding capture, synchronization and restart. Preserve production installations. Stop and report any failure; do not start broad repeated model reviews or automatic wiki compilation. Record the exact commit, OS/architecture, native checks and actual connection/save receipts. This testing installation does not approve a production tag or transfer wiki ownership.

## Verify, then install

Use official [Git](https://git-scm.com/downloads), [GitHub CLI](https://cli.github.com/) and [uv](https://docs.astral.sh/uv/getting-started/installation/) distributions. Use this device's `gh auth login` if needed. Git must be at least 2.38. Clone the two private repositories into NEW paths, or carefully reuse an existing vault without resetting local edits. Checkout the exact supplied software commit with LF bytes preserved.

Before executing the candidate, independently compare all entries in `release-manifest.json` with the downloaded files using trusted Python standard-library hashing; require the handover's manifest hash, version and exact tracked file set. Do not run the downloaded verifier to establish its own trust. Then follow the locked commands in `.github/workflows/verify.yml`: native Python 3.12 dependency sync, full pytest/JUnit, compile/import checks and pinned dependency audit. Use separate synthetic runtime/state/vault/config paths outside the checkout, offline model settings and local-only Git fixtures. Clear inherited BRAIN_/BASIC_MEMORY_/GIT_/PYTEST overrides before providing the controlled fixture configuration. Never use real notes or authentication in test fixtures.

Inspect the actual skips. The host's native lifecycle test must pass: macOS `test_native_mac_stop_start_registration`, Linux `test_native_linux_unit_stop_start`, or Windows `test_native_windows_task_stop_start`. Linux requires a working existing systemd user bus; Mac requires the intended user's actual GUI launchd session. Do not change session settings just to conceal a skipped check. Rehash source afterward. Actual Apple Silicon results do not prove native Intel Mac execution, and vice versa. No paid CI runner is required.

On a NEW Mac/Linux installation, after successful verification and path selection, invoke from the verified checkout:

```sh
uv run --no-project --python 3.12 python -I -B -c 'import runpy; runpy.run_path("install.py", run_name="__main__")' --root "$brain_root" --vault "$brain_vault" --role capture --configure-clients --download-model --install-service
```

`brain_root` and `brain_vault` must be the actual chosen absolute paths, not values copied from Windows. For an existing installation, stop before replacing it and follow the updater's exact accepted-release procedure. An unreleased candidate is not an accepted updater tag.

## Skill and instruction files

The portable skill is `skills/second-brain/SKILL.md`. Install a copy under the user's skill directory, preserving any existing custom skill of the same name until its differences are reviewed:

- Codex: `~/.agents/skills/second-brain/SKILL.md` (or the discoverable user skill root explicitly configured by that installed client).
- Claude Code: `~/.claude/skills/second-brain/SKILL.md`.

The canonical always-on protocol is `docs/AI-PROTOCOL.md`. The installer already merges it into `~/.codex/AGENTS.md` between `<!-- BEGIN SECOND-BRAIN -->` and `<!-- END SECOND-BRAIN -->`. The onboarding agent should merge that SAME block into `~/.claude/CLAUDE.md`, preserving all content outside it. Replace an existing complete managed block rather than appending duplicates; stop on malformed/duplicate markers. Back up the file locally before editing. Do not copy private project instructions between devices.

Restart the relevant client and check skill discovery (`$second-brain` in Codex, `/second-brain` in Claude Code). A skill/instruction file does not grant access by itself: verify the actual `second-brain` MCP tools in a NEW chat. Claude web/Cowork and ChatGPT cloud chats may have different connector/skill scope; local CLI success is not proof that those interfaces are connected.

## Acceptance on this device

1. Confirm health, the new unique device ID and capture role.
2. In each intended local AI client, actually call `brain_context`, search a known note and read its dated source through MCP.
3. Save one explicitly labeled onboarding capture with a stable per-device event ID; report its real path/hash receipt. Verify it reaches Windows through the existing maintenance sync, without running a competing sync.
4. Restart the service/client, repeat a read, and verify again after the owner next reboots. Mark reboot verification pending until it is actually done.
5. Report current limitations honestly. Do not compile temporary handoff/research imports, infer profile facts, or claim every GUI/cloud client works from configuration alone.

For later updates, pull notes through the existing sync owner. Software is updated separately from an explicitly accepted release after preserving local work. Reinstall the packaged skill/protocol when their reviewed versions change. Keep runtime databases and authentication local.

Official format references: [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Claude Code skills](https://code.claude.com/docs/en/skills), [Claude Code memory](https://code.claude.com/docs/en/memory).
