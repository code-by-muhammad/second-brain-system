# Second Brain setup and updates on another device

Use this guide with a local Codex agent on Windows, macOS or Linux. The notes repository shares knowledge; the software repository shares tested releases. Each device installs its own runtime, credentials, index and local MCP connection. The Windows maintainer compiles the shared wiki. New devices use capture/search mode.

Release covered: **v0.1.3**. Check the shared `02-Areas/AI-Memory/Second-Brain-System.md` for a newer explicitly approved release before starting. Do not install an arbitrary branch or silently select the newest internet release.

## Give this prompt to the other device's Codex agent

> Set up my existing shared Second Brain on this device using the private code-by-muhammad/second-brain notes repository and the approved code-by-muhammad/second-brain-system release v0.1.3. Follow docs/DEVICE-SETUP.md and the shared AI-Context and wiki Control pages. Inspect existing installation and client settings first. Reuse an existing vault; preserve notes, local edits, credentials and unrelated settings. Verify the release manifest before running the installer. Use capture/search role; do not transfer wiki ownership or create a compiler schedule. Install native locked dependencies, configure supported local MCP clients, download the local embedding model and register the per-user service. Test authenticated health, tools, source search/read and a single clearly labeled onboarding capture; verify that capture reaches the Windows vault through the existing maintenance sync. Test service restart and verify again after reboot. Report actual receipts and remaining failures. If GitHub authentication needs me, show the official device login flow; never ask me to paste tokens or copy another device's auth files.

## Inspect before installing

Find the existing vault, runtime `device.json` and clients. If already installed, use the update procedure below. Do not clone over an existing directory or overwrite uncommitted work. Use the current device's login and Git identity.

Required tools: [Git](https://git-scm.com/downloads), [GitHub CLI](https://cli.github.com/) and [uv](https://docs.astral.sh/uv/getting-started/installation/) from their official distributions. The installer obtains Python 3.12 through uv and uses the checked-in dependency lock. Do not copy the Windows virtual environment onto another OS. Installing Codex or Claude is separate from installing this service; the local clients must already be installed and authenticated for their own use.

Run `gh auth status`. If authentication is missing, use `gh auth login` on that device. Private repository access is required. Do not print or paste tokens.

## New Mac or Linux installation

These commands assume there is no vault or runtime at the chosen paths. Change the vault path to reuse existing notes.

```sh
brain_vault="$HOME/Documents/Second-Brain"
brain_root="$HOME/.local/share/second-brain"
brain_release="$brain_root/releases/v0.1.3"

gh repo clone code-by-muhammad/second-brain "$brain_vault"
gh repo clone code-by-muhammad/second-brain-system "$brain_release" -- --branch v0.1.3 --depth 1
cd "$brain_release"
uv run --no-project --python 3.12 python -c 'from pathlib import Path; import update; print(update.verify(Path.cwd())["version"])'
uv run --no-project --python 3.12 install.py --root "$brain_root" --vault "$brain_vault" --configure-clients --download-model --install-service

brain_python="$brain_release/.venv/bin/python"
"$brain_python" "$brain_root/app/brain_cli.py" service start
"$brain_python" "$brain_root/app/brain_cli.py" health
```

The manifest check must report `0.1.3` without an exception. macOS uses a per-user LaunchAgent. Linux uses a systemd user service; a working systemd user session is required. A machine without one needs an explicit startup decision, not a false success claim. Run under the intended normal user. Keep the user session available while testing; sleep/offline is not continuous service availability.

## New Windows installation

Use PowerShell as the intended normal user. These example paths are for another Windows installation; reuse the real vault if present.

```powershell
$brainVault = Join-Path $env:USERPROFILE 'Documents\Second-Brain'
$brainRoot = Join-Path $env:LOCALAPPDATA 'Second-Brain-System'
$brainRelease = Join-Path $brainRoot 'releases\v0.1.3'

gh repo clone code-by-muhammad/second-brain $brainVault
gh repo clone code-by-muhammad/second-brain-system $brainRelease -- --branch v0.1.3 --depth 1
Set-Location -LiteralPath $brainRelease
uv run --no-project --python 3.12 python -c 'from pathlib import Path; import update; print(update.verify(Path.cwd())["version"])'
uv run --no-project --python 3.12 install.py --root $brainRoot --vault $brainVault --configure-clients --download-model --install-service

$brainDevice = Get-Content -LiteralPath (Join-Path $brainRoot 'device.json') -Raw | ConvertFrom-Json
$brainCli = Join-Path $brainRoot 'app\brain_cli.py'
& $brainDevice.python $brainCli service start
& $brainDevice.python $brainCli health
```

The service is the limited-user `SecondBrainLocalService` task. Do not install this example over the existing Windows maintainer at `E:/Second-Brain-System`.

## Verify before declaring the device ready

1. Health reports `running`, the approved version, the correct vault and role `capture`. Each device has a different device ID and fresh local auth.
2. Restart the AI client and create a new local session. Verify actual exposure of `brain_context`, `brain_search`, `brain_read`, `brain_capture`, `brain_update_current` and `brain_health`. A capture device must reject derived-page writes even though the tool is listed.
3. Ask the agent to call `brain_context`, search for a known source, and open that original note with `brain_read`. A list of configured servers alone does not establish a working connection. The first local embedding index can take time; use health and actual retrieval results.
4. Save one clearly labeled onboarding milestone through `brain_capture`, with a stable event ID containing this device's ID. Do not invent work/profile facts. Retrying must return a duplicate receipt rather than create another event.
5. Let the existing maintenance service synchronize. It checks every 15 minutes while running and online. On Windows, search/read that capture through MCP and compare the actual path and SHA. Then verify a Windows-originated note on the new device. Do not start a second Git synchronization process.
6. Stop/start the registered service using `brain_cli.py service stop` and `service start`, then recheck authenticated health and retrieval. Repeat after the owner reboots/signs in. Physical-device acceptance is complete only when these steps actually pass.

The installer configures Claude Code, Claude Desktop and Codex settings where applicable, preserving backups and unrelated settings. Test each requested client in its actual session. Configuration does not establish ordinary web ChatGPT/Claude or Cowork connectivity. Do not expose a public endpoint or install a tunnel to make an unsupported client work without a separate design decision. This guide covers the local MCP installation.

## Keep notes and software current

| Shared through notes Git | Shared through software releases | Kept separately on every device |
| --- | --- | --- |
| Original notes, captures, wiki pages, index, history and Control | Python code, installers, lock file, client templates, tests and this guide | device.json, paths, auth, SQLite/WAL, embedding model, environments, logs, backups and pending queue |

Notes synchronize automatically through the existing per-device maintenance service. Same-note conflicts, staged edits and deletions require review; never force-push or discard the user's work. A laptop can capture/read locally while another laptop is off. Synchronization catches up when it returns online.

Software updates are explicit. Read the approved version from the shared system status. Use the Python recorded in that device's `device.json` to run the currently installed release's updater. For v0.1.3:

```text
<device Python> <current release_root>/update.py --root <runtime root> --version v0.1.3
```

The updater verifies the new tag's file hashes, installs native locked dependencies, preserves identity/auth/role, restarts the service and polls authenticated versioned health. A failed upgrade attempts to restore the previous release. Never replace a healthy runtime by copying folders or running `git pull` inside its active release. A change on the Windows PC is shared as a tested tagged release; each device installs that tag when asked.

## Troubleshooting

- Tools missing: inspect that client's generated second-brain entry, restart it and check a new session. Test the adapter and authenticated local health before changing settings.
- Service unavailable: check the per-user startup entry and local service logs. Keep log/source excerpts private; never dump auth files.
- Search not ready: check local index progress and model download. Do not copy another OS's SQLite/WAL while it is live.
- Sync paused: preserve all files and staged changes; inspect the conflict. The service must remain the only sync owner.
- Wiki compilation: the designated Windows maintainer owns it. Do not run `wiki run`, `wiki retry` or create a schedule on a capture device.

Reference: [v0.1.3 source and installer](https://github.com/code-by-muhammad/second-brain-system/tree/v0.1.3), [operating README](../README.md), and the shared vault's `Maps/AI-Context.md` and `02-Areas/AI-Memory/Wiki/Control.md`.
