# Second Brain setup and updates on another device

Use this guide with a local Codex agent on Windows, macOS or Linux. The notes repository shares knowledge; the software repository shares tested releases. Each device installs its own runtime, credentials, index and local MCP connection. The Windows maintainer compiles the shared wiki. New devices use capture/search mode.

Release covered: **v0.1.7**. Check the shared `02-Areas/AI-Memory/Second-Brain-System.md` for a newer explicitly approved release before starting. Do not install an arbitrary branch or silently select the newest internet release.

## Give this prompt to the other device's Codex agent

> Set up my existing shared Second Brain on this device using the private code-by-muhammad/second-brain notes repository and the approved code-by-muhammad/second-brain-system release v0.1.7. Follow docs/DEVICE-SETUP.md and the shared AI-Context and wiki Control pages. Inspect existing installation and client settings first. Reuse an existing vault; preserve notes, local edits, credentials and unrelated settings. Verify the release manifest before running the installer. Use capture/search role; do not transfer wiki ownership or create a compiler schedule. Install native locked dependencies, configure supported local MCP clients, download the local embedding model and register the per-user service. Test authenticated health, tools, source search/read and a single clearly labeled onboarding capture; verify that capture reaches the Windows vault through the existing maintenance sync. Test service restart and verify again after reboot. Report actual receipts and remaining failures. If GitHub authentication needs me, show the official device login flow; never ask me to paste tokens or copy another device's auth files.

## Inspect before installing

Find the existing vault, runtime `device.json` and clients. If already installed, use the update procedure below. An existing runtime cannot be redirected to another vault: installation rejects that change before editing settings. For another vault, use a separate runtime and rebuild its native index explicitly. Do not clone over an existing directory or overwrite uncommitted work. Use the current device's login and Git identity.

Required tools: Git 2.38 or newer ([Git](https://git-scm.com/downloads)), [GitHub CLI](https://cli.github.com/) and [uv](https://docs.astral.sh/uv/getting-started/installation/) from their official distributions. The installer obtains Python 3.12 through uv and uses the checked-in dependency lock. Do not copy the Windows virtual environment onto another OS. Installing Codex or Claude is separate from installing this service; the local clients must already be installed and authenticated for their own use.

Run `git --version` and confirm Git 2.38 or newer, then run `gh auth status`. If authentication is missing, use `gh auth login` on that device. Private repository access is required. Do not print or paste tokens.

## Validate the downloaded release on this OS

After cloning the approved tag and verifying its manifest, run the target OS checks before executing the installer. Record `git rev-parse HEAD`, the manifest SHA256, Python version, `platform.platform()` and `platform.machine()` in a private validation receipt. Use the exact same commit for all checks; a newer branch or an older Mac result does not validate this download.

Before test imports, temporarily set `BRAIN_SYSTEM_ROOT`, `BRAIN_STATE`, `BRAIN_VAULT` and `BASIC_MEMORY_CONFIG_DIR` to new disposable validation directories outside the checkout. Restore any earlier environment values afterward. Never use an installed runtime, live vault or its auth files as these paths.

From the verified release directory, run the test and audit commands in `.github/workflows/verify.yml`: locked `uv sync --locked --python 3.12`, full `uv run --locked python -m pytest -q --junitxml reports/preinstall.xml`, compileall, `tests/smoke_import.py`, the locked non-dev requirements export and pinned pip-audit 2.9.0. The `reports/` directory is ignored by Git; keep its receipt and logs private. Tests use disposable synthetic vaults and unique test service names. Do not supply real notes, auth files or model credentials to them.

Inspect every skip in JUnit. On macOS, `test_native_mac_stop_start_registration` must actually pass in the intended user's logged-in GUI launchd domain. On Linux, `test_native_linux_unit_stop_start` must pass with an existing working user service bus. On Windows, `test_native_windows_task_stop_start` must pass. A green command with that host's native lifecycle skipped is incomplete validation. Record the actual host architecture; Rosetta execution is extra compatibility evidence, not a native Intel Mac host result. Never change the owner's startup/session settings merely to hide a skipped check.

As of 2026-10-15, the final v0.1.7 candidate's native Mac execution remains pending because hosted CI did not start. Follow the dated shared system status for subsequent evidence. Download/setup instructions and Windows/WSL results do not claim that this physical Mac is already tested. Keep its status pending until these checks and the device acceptance steps below actually pass.

## New Mac or Linux installation

These commands assume there is no vault or runtime at the chosen paths. Change the vault path to reuse existing notes.

```sh
brain_vault="$HOME/Documents/Second-Brain"
brain_root="$HOME/.local/share/second-brain"
brain_release="$brain_root/releases/v0.1.7"

gh repo clone code-by-muhammad/second-brain "$brain_vault"
gh repo clone code-by-muhammad/second-brain-system "$brain_release" -- --branch v0.1.7 --depth 1
cd "$brain_release"
uv run --no-project --python 3.12 python -c "from pathlib import Path; import update; print(update.verify(Path.cwd())['version'])"
uv run --no-project --python 3.12 install.py --root "$brain_root" --vault "$brain_vault" --configure-clients --download-model --install-service

brain_python="$brain_release/.venv/bin/python"
"$brain_python" "$brain_root/app/brain_cli.py" service start
"$brain_python" "$brain_root/app/brain_cli.py" health
```

The manifest check must report `0.1.7` without an exception. macOS uses a per-user LaunchAgent, consistent with [Apple's launchd documentation](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html). Linux uses a systemd user service; a working systemd user session is required. A machine without one needs an explicit startup decision, not a false success claim. Run under the intended normal user. Keep the user session available while testing; sleep/offline is not continuous service availability.

## New Windows installation

Use PowerShell as the intended normal user. These example paths are for another Windows installation; reuse the real vault if present.

```powershell
$brainVault = Join-Path $env:USERPROFILE 'Documents\Second-Brain'
$brainRoot = Join-Path $env:LOCALAPPDATA 'Second-Brain-System'
$brainRelease = Join-Path $brainRoot 'releases\v0.1.7'

gh repo clone code-by-muhammad/second-brain $brainVault
gh repo clone code-by-muhammad/second-brain-system $brainRelease -- --branch v0.1.7 --depth 1
Set-Location -LiteralPath $brainRelease
uv run --no-project --python 3.12 python -c "from pathlib import Path; import update; print(update.verify(Path.cwd())['version'])"
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
| Original notes, captures, wiki pages, index, history, Control and explicit wiki source policy | Python code, installers, lock file, client templates, tests and this guide | device.json, paths, auth, SQLite/WAL, embedding model, environments, logs, backups and pending queue |

Notes synchronize automatically through the existing per-device maintenance service. Same-note conflicts, staged edits and deletions require review; never force-push or discard the user's work. A laptop can capture/read locally while another laptop is off. Synchronization catches up when it returns online.

Software updates are explicit. Read the approved version from the shared system status. For the first upgrade from v0.1.5, **use the verified new v0.1.7 updater**, because the old updater does not contain the rollback and preflight fixes. Download the approved tag into `<runtime>/releases/v0.1.7` with `gh repo clone code-by-muhammad/second-brain-system <candidate directory> -- --branch v0.1.7 --depth 1`. If that directory already exists, verify it and reuse it; do not clone over it or change its files. Use the current Python from `device.json` to verify the candidate manifest using `update.verify(Path(<candidate directory>))` before executing its installer/updater.

```text
<candidate v0.1.7 Python> <verified v0.1.7 checkout>/update.py --root <runtime root> --version v0.1.7
```

Before the update command, prepare the verified candidate environment with `uv sync --locked --no-dev --project <verified v0.1.7 checkout> --python 3.12`. Use `<candidate>/.venv/bin/python` on Mac/Linux or `<candidate>/.venv/Scripts/python.exe` on Windows to run the updater and migration dry run. This matters when a v0.1.5 POSIX installation recorded the faulty base interpreter: that old interpreter cannot import the updater dependencies. Manifest verification itself uses only the standard library and can run before environment creation.

Capture/search devices use that command without migration flags. The updater verifies the tag, prepares native locked dependencies and preflights client settings while the old service remains healthy. It serializes the switch against compilation and maintenance, preserves identity/auth/role, and polls authenticated versioned health. Failure restores exact software/device/client snapshots without invoking the old faulty installer; original notes, tokens and SQLite are not rolled back.

A designated maintainer upgrading from v0.1.5 or an older release also needs a legacy-publication migration. Its agent must use the candidate Python to run the new checkout's `app/brain_cli.py wiki migrate` with `BRAIN_SYSTEM_ROOT` set to the existing runtime. This defaults to a dry run. Inspect verified and unverified source parts first; a Source note alone is not proof that all parts completed. Then invoke the new updater with `--migrate-wiki --requeue-unverified`. It migrates exact per-part proofs and repairs source navigation while holding the compiler lock, and leaves unproved parts queued for a normal bounded worker batch. The Windows maintainer performs this once; new capture devices do not compile or migrate shared wiki state.

New shared `Wiki/Receipts/` pages contain source/chunk and claim hashes plus explicit per-part dispositions. Another designated maintainer can verify them and hydrate its own local queue without copying a live SQLite database or repeating the entire backlog. Historical journals and claims remain preserved. The general `brain_update_current` tool cannot replace compiler-owned wiki or Control pages.

An existing v0.1.6 device uses the normal verified v0.1.7 updater without legacy-migration flags. v0.1.7 verifies unchanged canonical source parts across equivalent Git LF/CRLF/BOM representations while keeping raw file SHA checks for writes. Do not rewrite an existing vault's line endings to force receipt matches. Changed parts remain queued even if they still contain an old quoted sentence.

## Select temporary sources without losing retrieval

The optional shared `02-Areas/AI-Memory/Wiki-Source-Policy.md` contains one JSON fenced object with `version: 1`, `exclude_paths` and `exclude_prefixes` arrays. Exact paths identify Markdown originals; explicit directory prefixes end with `/`. Paths use forward slashes under existing source roots. Absolute paths, traversal, hidden/derived paths, wildcards, duplicates and unknown keys are rejected. An absent policy preserves normal ingestion; an invalid policy stops compilation for review. Do not treat words such as "research" or "handoff" as automatic exclusions.

The owner has selected temporary session documents to remain searchable without future permanent wiki compilation. Excluded files and parts are reported separately from eligible completion. Original notes, existing historical claims and receipts remain preserved. Policy changes sync through the notes repository and require v0.1.7 or later on the maintainer; notes synchronization does not upgrade older software. Change this derived policy only from explicit owner evidence with its current SHA256.

An unused saved publication plan is superseded when its source becomes excluded. If actual derived-note publication has already started, a fully validated plan may repair that partial publication without another model call. Merely saving a plan or finding an unchanged planned file does not qualify as progress. Invalid policy stops further writes until repaired.

Historical verification has bounded object/command work and a cooperative 15-second history budget checked between operations, including historical metadata parsing and cached-history traversal. An operation already running is not preempted. Each fetched revision's metadata is parsed once; missing or budget-exhausted historical evidence stays unresolved. Bounded current-file byte/quote verification stays available independently of this history budget, so valid current claims keep their anchors. Parsed headers and claim content are private cache data, never public proof fields.

Explicit malformed source origins require integrity repair; they are never treated as absent legacy metadata or silently rebound. The worker stops with a controlled page/claim diagnostic and preserves the notes, without charging source or processing retry strikes. Repair the derived metadata from actual evidence before explicitly requesting a retry.

During manual repair, keep origin dates quoted as strings, for example `date: "2026-10-01"`, and use safe vault-relative `.md` source paths. Read the current derived-note SHA before saving a repair, then explicitly run `wiki retry`; the next preparation takes a fresh snapshot. Relevant malformed claims stop before any model call. A malformed target reached only during merging stops immediately after that call, without repeated requests. A safely named historical source may be absent locally if its actual prior Git evidence can still be verified; do not replace its attribution with a guessed current source.

Explicit topic titles must also be strings; quote a date-like title rather than saving a YAML date, number or null. A missing legacy title keeps its existing path fallback. Context that exceeds the model budget is omitted before claim validation, while all catalog titles remain subject to the type check.

An unused pending packet cannot retain topic context from before an owner edit or repair. The worker checks stored topic hashes and full catalog identity before resuming it, preserves the old artifacts, and prepares a current snapshot. A validated plan that has actually changed a derived note may instead finish its partial publication from saved output without another model request.

Agents should save one concise evidence-backed `brain_capture` milestone after meaningful work. The existing maintenance service links it into daily notes. Do not save raw session transcripts, manually append parallel daily logs, delete handoffs for the owner or create another schedule. Other devices keep capture/search role; compilation remains with the designated maintainer.

Historical evidence checks require the actual earlier source revision. Keep the notes repository's history available; do not make a shallow notes clone or discard history to reduce storage without reviewing that consequence. Missing history remains an integrity issue. A verified old quotation does not establish a current fact. Normal new ingestion can remain on the existing hourly worker; report its actual pending count separately from installation and audit-fix readiness.

Never replace a healthy runtime by copying folders or running `git pull` inside its active release. A change on Windows is shared as a tested tagged software release; each device installs that verified tag explicitly. Ownership transfer still requires disabling the previous writer first; shared receipts are replay evidence, not a distributed lock.

## Troubleshooting

- Tools missing: inspect that client's generated second-brain entry, restart it and check a new session. Test the adapter and authenticated local health before changing settings.
- Service unavailable: check the per-user startup entry and local service logs. Keep log/source excerpts private; never dump auth files.
- Search not ready: check local index progress and model download. Do not copy another OS's SQLite/WAL while it is live.
- Index bootstrap needs attention: have the agent diagnose the private bootstrap log and fix the cause first. With the registered service stopped, it can reset the local `state/index-bootstrap-status.json` marker and start the service to retry. Preserve the actual retrieval database and model cache, then verify indexing and known-source retrieval again.
- Sync paused: preserve all files and staged changes; inspect the conflict. The service must remain the only sync owner.
- Wiki compilation: the designated Windows maintainer owns it. Do not run `wiki run`, `wiki retry` or create a schedule on a capture device.
- Wiki worker needs attention: the maintainer inspects `wiki status` and the controlled private failure receipt, repairs the cause and explicitly runs `wiki retry`. Pending sources and historical publication proofs remain preserved. Transport/timeouts and invalid model output must not cause unlimited scheduled model calls or blame unrelated sources.

Reference: [v0.1.7 source and installer](https://github.com/code-by-muhammad/second-brain-system/tree/v0.1.7), [operating README](../README.md), and the shared vault's `Maps/AI-Context.md` and `02-Areas/AI-Memory/Wiki/Control.md`.

Lint now reports malformed topic titles and JSON/UTF-8 claim metadata with controlled field/page/claim diagnostics. Rendering stops for malformed catalog/source-record fields without retry strikes. If an older source becomes undecodable or oversized, publication uses bounded current reads and exact historical Git evidence; unavailable proof requires owner repair and explicit `wiki retry`. The compiler never guesses an encoding or rewrites the source.

Existing derived claim metadata and catalog titles now pass the established credential screen before fresh or saved-context model requests. A possible credential stops for owner repair with a controlled field diagnostic; ordinary API-key discussion remains allowed.

Decoded structured claim metadata receives the existing credential screen before JSON escaping can obscure it. Invalid topic frontmatter or wiki_kind now stops unused compilation with a controlled page/field diagnostic; validated partial publication can still repair without another model request. Impossible source dates keep their full original evidence in the queue with unknown metadata date, while strict topic parsing requires owner repair. Quoted date-like strings remain literal.

Deep YAML and alias-expanded context now fail within explicit parser/JSON bounds. Direct-editor changes remain protected by the exact before image consumed during planning; unreadable derived auxiliary notes require owner repair. Malformed-origin diagnostics contain only controlled fields. Fully verified actual legacy partial publication can finish saved writes without another model request, while unused plans remain screened and lint retains unresolved metadata issues. The existing conservative credential detector also screens source/catalog/output filenames before future work; filtering preserves originals and retrieval.

Malformed explicit YAML scalar tags now obey the same controlled parser contract as invalid dates/deep YAML: strict derived-topic parsing requires owner repair, while non-strict source metadata falls back without losing body evidence. Maintenance continues to Git synchronization on these malformed inputs; valid explicit tags and aliases remain supported.


Topic discovery now ignores normal hidden editor artifacts without reading them and preflights canonical category/slug paths, including case-only renames. Unsafe paths stop with a safe locator/path hash; no filename disables the existing credential screen. Strict maintenance review flags malformed derived metadata while capture linking and Git synchronization continue. Relinking validates all topic headers and stages serializations before changing navigation.

The compiler automatically rotates its active compilation Log near 1 MB into a SHA-named archive containing every previous raw byte. Normal plans stay v2; rotation uses a validated v3 descriptor and fixed 64 KiB streaming, including oversized legacy history. Archives form a linked history chain and synchronize through the existing notes Git service; original notes and daily notes are unaffected. Archive integrity is checked at creation and recovery; ordinary wiki lint does not rescan all historical archive bytes. Durable partial replay spends no additional model call. Archive-only creation cannot override owner exclusions, and missing/corrupt private recovery input requires review. Private completed spool cleanup preserves pending data and permanent archives. These operations use the existing locking/fsync/atomic-write discipline, without claiming OS-level CAS against arbitrary direct editors.

Shared scalar aliases are measured once per value and counted per expanded use. A full private packet has a distinct 2,000,000-character operation cap; an over-limit catalog reports Index/packet rather than a claim error. Lint validates paths before copying diagnostics, and unrepresentable owner Source-record metadata stops with serialization attention without dropping data or retry strikes.

If `wiki status` reports `integrity_field: path`, compare lint’s safe page/path SHA with local filenames, repair the original derived filename intentionally, then run `wiki retry`. Keep canonical topic folders/case. For `serialization`, simplify or repair the affected owner metadata using the current SHA; no automatic field removal occurs. A `packet` field indicates the private 2,000,000-character operation cap, separate from the bounded model catalog. Do not delete archives or pending `log-history-<sha>.bin` spools. If a newer owner Log conflicts with a partially published plan, review its saved before/after hashes and resolve explicitly before retrying; never discard that history merely to clear attention.


### Legacy history capacity and publication repair

The compiler and Git support exact SHA-named Log archives up to 64 MiB, using bounded 64 KiB reads. Normal rotation is near 1 MB. A larger legacy Log stops before reading history, spooling or calling a model with `integrity_field: archive_capacity`; preserve it and arrange an explicit owner-reviewed history migration. Do not delete history or increase just one component's capacity. Ordinary notes retain the existing 2,000,000-character Git guard. Local archive raw bytes stay exact; existing Git text settings may normalize CRLF to LF on other devices, so cross-device identity is textual/provenance identity rather than a raw EOL-byte guarantee. No Git attributes are changed.

An archive collision repaired by the owner can resume a fully validated saved publication with `wiki retry` and the usual bounded worker run; it does not request another response. An unsafe unused legacy plan is preserved as superseded and the retry uses a new generation. Never edit private input/output/plan hashes or remove pending spools to force progress.

For `integrity_field: publication_cas`, status names a screened `integrity_page` and `recovery_action: resolve_saved_publication_cas_then_retry`. Inspect that derived page and the batch's private `publication.json`. Preserve the owner's current edit separately, then make an explicit owner-approved repair to the exact saved before-image or planned after-image. An absent before-image is a distinct state; the compiler never deletes a new owner page automatically. Request `wiki retry` after reconciliation. The same validated plan finishes without another model call. Keep the partial plan and receipts; unrelated compilation remains held until this shared transaction is resolved.


Daily capture-link failures are shown in maintenance review with the affected capture and a safe repair hint. Check `Templates/daily-note.md`, the daily page's UTF-8/size/write access and any concurrent owner edit. Preserve the current files and repair the specific cause; the next existing maintenance cycle retries the link. Do not create another daily logger or sync job. Remaining phases are attempted, but Git can still refuse invalid note content under its normal guard.

Source review accepts representation-only LF/CRLF/CR/BOM checkout differences on other devices. A genuine changed or missing source still requires review; no source normalization or automatic owner edit is performed. Capture display text is escaped in daily notes, while its immutable saved milestone remains authoritative.
