# Second Brain setup and updates on another device

Use this guide with a local Codex agent on Windows, macOS or Linux. The notes repository shares knowledge; the software repository shares tested releases. Each device installs its own runtime, credentials, index and local MCP connection. The Windows maintainer compiles the shared wiki. New devices use capture/search mode.

Release covered: **v0.1.7**. Check the shared `02-Areas/AI-Memory/Second-Brain-System.md` for a newer explicitly approved release before starting. Do not install an arbitrary branch or silently select the newest internet release.

Audit status on 2026-10-17: **HOLD — v0.1.7 is a candidate, not an approved installation.** R126–R129 retain their dated scoped Windows/native Linux evidence. R130 and R131 now have sealed scoped passes on the same reviewed combined source, plus an independent postvalidation review with no findings. These receipts do not approve this newly coalesced final candidate: its freeze, regenerated manifest, complete Windows/native Linux suites, exact original-contract replay, evidence binder, actual integration and fresh final-source Codex/Claude reviews remain pending. Native Mac, hosted CI, tag, deployment and physical-client acceptance remain pending and unwaived. The earlier b258 Linux full run remains nonpassing (1,942 passes, one legacy fixture assertion failure, five expected skips and zero errors). The approved node-cap fixture passed as an exact private test copy in R130's scoped checks on both OSes; this new candidate now coalesces those same approved bytes, without relabeling b258 or claiming a new full run. Production remains v0.1.6 and PRIMARY remains `0ed` unchanged. Use setup/update examples only after the shared system status explicitly approves v0.1.7. Older dated pending statements below describe their historical source phases and are superseded only by explicit later receipts.

## Give this prompt to the other device's Codex agent

Use this prompt only after the shared system status approves the exact release and its manifest; it is not permission to install the current held candidate.

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

Source inventory and fresh-file fallback recognize .md, .MD and .Md consistently on supported operating systems. Keep each original filename's exact casing: paths identify source parts, receipts and owner policy. A committed rename does not require rewriting original content or copying another device's index.

The optional shared `02-Areas/AI-Memory/Wiki-Source-Policy.md` contains one JSON fenced object with `version: 1`, `exclude_paths` and `exclude_prefixes` arrays. Exact paths identify Markdown originals; explicit directory prefixes end with `/`. Paths use forward slashes under existing source roots. Absolute paths, traversal, hidden/derived paths, wildcards, duplicates and unknown keys are rejected. An absent policy preserves normal ingestion; an invalid policy stops compilation for review. Do not treat words such as "research" or "handoff" as automatic exclusions.

Exact exclusions require a lowercase .md suffix. For an uppercase original, an explicit folder-prefix rule excludes the source notes in that folder; keep that wider scope intentional.

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

Shared scalar aliases are measured once per value and counted per expanded use. A full private packet has a distinct operation envelope of 2,000,000 characters, 100,000 traversal nodes and depth 128; an over-limit catalog reports Index/packet rather than a claim error. The check includes the final batch identity, expanded serialized JSON node/depth shape and all existing decoded selected-context guards before any durable batch is created; fitting YAML aliases remain supported. If preparation exceeds capacity, preserve the notes, repair the catalog size, then retry; eligible source work remains pending. Existing malformed saved inputs still require verified owner recovery and are never silently rewritten. Lint validates paths before copying diagnostics, and unrepresentable owner Source-record metadata stops with serialization attention without dropping data or retry strikes.

If `wiki status` reports `integrity_field: path`, compare lint’s safe page/path SHA with local filenames, repair the original derived filename intentionally, then run `wiki retry`. Keep canonical topic folders/case. For `serialization`, simplify or repair the affected owner metadata using the current SHA; no automatic field removal occurs. A `packet` field identifies a private whole-packet character, node or depth limit, separate from the bounded model catalog. Do not delete archives or pending `log-history-<sha>.bin` spools. If a newer owner Log conflicts with a partially published plan, review its saved before/after hashes and resolve explicitly before retrying; never discard that history merely to clear attention.


### Legacy history capacity and publication repair

The compiler and Git support canonical Log archives up to 64 MiB, using bounded 64 KiB reads; archives above the ordinary note character limit require a matching SHA filename. Normal rotation is near 1 MB. A larger legacy Log stops before reading history, spooling or calling a model with `integrity_field: archive_capacity`; preserve it and arrange an explicit owner-reviewed history migration. Do not delete history or increase just one component's capacity. Ordinary notes retain the existing 2,000,000-character Git guard. Local archive raw bytes stay exact; existing Git text settings may normalize CRLF to LF on other devices, so cross-device identity is textual/provenance identity rather than a raw EOL-byte guarantee. No Git attributes are changed.

An archive collision repaired by the owner can resume a fully validated saved publication with `wiki retry` and the usual bounded worker run; it does not request another response. An unsafe unused legacy plan is preserved as superseded and the retry uses a new generation. Never edit private input/output/plan hashes or remove pending spools to force progress.

For `integrity_field: publication_cas`, status names a screened `integrity_page` and `recovery_action: resolve_saved_publication_cas_then_retry`. Inspect that derived page and the batch's private `publication.json`. Preserve the owner's current edit separately, then make an explicit owner-approved repair to the exact saved before-image or planned after-image. An absent before-image is a distinct state; the compiler never deletes a new owner page automatically. Request `wiki retry` after reconciliation. The same validated plan finishes without another model call. Keep the partial plan and receipts; unrelated compilation remains held until this shared transaction is resolved.


Daily capture-link failures are shown in maintenance review with the affected capture and a safe repair hint. Check `Templates/daily-note.md`, the daily page's UTF-8/size/write access and any concurrent owner edit. Preserve the current files and repair the specific cause; the next existing maintenance cycle retries the link. Do not create another daily logger or sync job. Remaining phases are attempted, but Git can still refuse invalid note content under its normal guard.

Source review accepts representation-only LF/CRLF/CR/BOM checkout differences on other devices. A genuine changed or missing source still requires review; no source normalization or automatic owner edit is performed. Capture display text is escaped in daily notes, while its immutable saved milestone remains authoritative.


If worker/receipt status withholds a locator, use the local path-SHA lookup below to identify the affected file; the public Index locator is not an instruction to overwrite Index. Preserve the original file and pending plan. Unsafe optional receipts are flagged before reading, while valid neighboring receipts still verify. Historical verification ignores local `git replace` views without deleting your references, so reported origin bytes come from canonical Git objects.

### Owner edits and saved input recovery

An ordinary Log archive of at most 2,000,000 decoded characters can receive an owner annotation, Obsidian link update or credential redaction and synchronize under its existing filename. This uses the ordinary UTF-8 universal-newline character limit, including a BOM as one character; it is not a 2 MB byte limit. Creation/recovery still verifies the compiler's saved raw archive identity. Git still checks strict UTF-8, NUL, credentials, the 64 MiB streaming bound and the exact staged raw or calculated CRLF-to-LF representation. Editing history does not change old publication receipts or make an archive a model source.

An archive above that ordinary character limit receives only the narrowly bounded capacity exception: its current raw SHA256 must match its filename. If an owner edits such an archive, stop and review the edit. Preserve the previous version and pending publication artifacts. With no saved publication still depending on that archive, an owner can calculate the edited file's raw SHA256 locally, rename the reviewed archive to that digest, and deliberately reconcile its history-chain links. Review the resulting Git rename/deletion explicitly; automatic sync preserves deletions and cannot finalize that migration for you. If a saved partial publication still references the old path or bytes, resolve that transaction first from its verified before/after evidence. Do not rewrite private plan/input/output hashes, discard spools, or relax the capacity/credential guard to make a migration pass.

Daily entries with one exact standalone capture marker and the immediately following canonical capture link stay idempotent. If the owner changes that identity line, inserts a line between them, removes the link, or duplicates the marker, maintenance preserves the daily note and capture and reports owner reconciliation. Restore the intended canonical marker/link pair, or explicitly remove a stray marker if a new genuine entry should be added. The service never guesses which owner entry to discard. Inline historical display markers cannot suppress a genuine capture.

`unused_publication_cas` with `retry_fresh_generation_preserving_owner_edits` means the saved plan has no independently verified semantic publication. Creating an archive alone does not qualify. Keep owner edits and the old artifacts; normal backoff or an explicit retry prepares a fresh generation and may request another model response. Do not restore an old Index or source-record before-image just to clear this diagnostic. `publication_cas` and `resolve_saved_publication_cas_then_retry` retain the actual partial-publication repair procedure above.

`recovery` attention means saved input is missing, malformed, has the wrong batch identity, or fails essential stored-packet checks. It stops before model requests and does not charge source/systemic strikes. Preserve all artifacts and restore only independently verified matching saved input before retry; never fabricate a packet. Ordinary input read I/O failures retain 60-second backoff. Oversized saved input retains the separate `packet` diagnostic.

Recovery attention includes a validated `batch_id` when available. Inspect only that device's `state/wiki/batches/<batch_id>` and preserve its input, output, publication plan and spools. Malformed saved output or plan JSON and unsafe saved page paths also require matching verified recovery evidence; repeated retry cannot repair corrupted state. Do not copy an arbitrary packet from another batch or alter hashes to make it pass.

`source_revision` with `resolve_saved_source_revision_then_retry` means an actual partial or complete-but-unfinalized publication still owns a verified transaction, but its original source changed or disappeared. Preserve the current source revision and owner edits, as well as all saved artifacts. Reconcile the recorded original revision and the exact derived before/after images deliberately with the owner, then use `wiki retry` and the usual bounded run. The service does not restore or rewrite original notes automatically, and a new batch must not bypass the unfinished transaction.

`reconcile_saved_publication_then_retry` is a conservative unfinished-transaction hold. A previously published semantic note may have been edited/deleted by the owner, or a durable write intent may remain after a crash, so no current planned after-image independently permits replay. Preserve the named batch folder, current source and owner edits. If the saved plan is missing, restore only independently verified matching recovery evidence; never invent a plan. Explicitly reconcile a verified planned semantic after-image, source revision and other derived before/after conflicts before retrying the same saved output. Historical progress or intent can require this hold, but never grants replay authority or a source-policy exemption. The service does not automatically restore or delete original notes.

Older applying journals with empty or archive-only progress cannot distinguish a crash after semantic replacement from an ordinary pre-write failure. Changed source/policy/catalog/derived context therefore requires conservative owner review. Current journals record a semantic write intent before replacement; an abrupt process termination retains that intent. A normal caught pre-write failure clears it only when no successful replacement or matching returned receipt is known and the target still matches its recorded before-image. Archive creation alone remains unused work, and repeated retry cannot release a genuine unfinished transaction.

When replay upgrades an older applying journal, that uncertainty remains recorded through all applying checkpoints and retries. The marker only requires reconciliation; it cannot authorize an overwrite or exempt a source from policy. Preserve the original journal and current owner bytes even if a later checkpoint carries the current intent version.

The existing database-complete, missing-plan legacy quarantine remains separate. It does not assert a verified final journal and still requires explicit migration/review; the new unfinished-plan hold does not replace that older recovery contract.

`content` attention before a model request identifies invalid saved model context, such as a possible credential, NUL or invalid UTF-8 string. Keep the affected batch and its evidence for local review. Redact genuine credentials in their authoritative source deliberately; restore saved state only from matching verified evidence. Do not disable credential screening or edit private hashes to clear the diagnostic. Ordinary long words spanning a source-part boundary remain valid because full-source screening and canonical part verification preserve their original context.

Packet-wide screening uses the existing 100,000-node/depth-128 structural envelope and 2,000,000-character packet cap. Individual claims retain their smaller 20,000-node/depth-64 bounds. The model still receives only the bounded selected catalog, so growing the local wiki does not send the full catalog on every request. Whole-packet character, node or depth capacity reports packet attention; review catalog/packet sizing before retrying rather than editing an innocent claim. Invalid individual metadata remains claim-integrity attention.

An unused saved packet whose canonical source path disappeared is refreshed from current inventory, including a case-only rename on Windows. A wrong part identity for a still-present source remains recovery attention. A genuine unfinished publication retains its stricter source/owner reconciliation hold.

Case-only source renames need an explicit reviewed Git rename before sharing them. On a case-insensitive filesystem, a filename changed only in Explorer/Finder may look correct locally while Git still records the old spelling; a case-sensitive device then cannot resolve the compiled evidence link. Synchronization pauses when tracked and actual path spellings disagree. Preserve all files, review the intended old/new names, and use two explicit `git mv` steps through a unique temporary name followed by an owner-reviewed rename commit. Recheck tracked paths and lint before the existing maintenance service resumes. Do not change `core.ignorecase`, delete a source, or stage unrelated work to clear the pause. Directory casing needs the same deliberate reconciliation.

Keep shared system folders at their canonical spelling, especially `02-Areas/AI-Memory` and its `Wiki` folder. If one was accidentally renamed only by case, restore the canonical spelling through a unique temporary name and review Git paths/lint before sharing it. Do not commit a renamed system namespace as if it were an ordinary source-folder rename. The central derived memory namespace and reserved per-project `01-Projects/**/AI-Memory.md` summaries stay excluded from future wiki source ingestion even on a case-insensitive filesystem. These summaries remain available for search/read and source-backed updates. Existing claims and receipts stay historical; follow their dated originals before relying on facts. Ordinary neighboring research notes are not excluded by their tags or a similar name.

For a saved-state path refusal, use the validated `batch_id` to inspect that device's preserved batch folder. The path-SHA lookup below applies only when status or lint actually supplies `path_sha256`; Index may be a generic safe locator rather than the filename that needs repair. Do not guess a target from that locator.

Legacy receipt migration treats malformed or deeply nested journals as unverified instead of crashing or deleting them. Missing/corrupt input or output can use only independently verified existing completion/claim evidence; it does not establish a new final receipt. Keep the artifacts, inspect the migration report and reconcile verified evidence before deliberately requeuing anything.

Malformed legacy entry job keys are ignored as unverified evidence before lookup. A missing, unknown or non-string publication journal state instead stops unfinished recovery with the validated batch locator. Preserve that journal, its matching plan/input/output and current owner edits. Restore only independently verified recovery evidence; do not guess a state or change it to complete merely to clear attention.

A cleanup, audit-log or final verification error can occur after the note replacement succeeded. The compiler retains that write intent even if a direct owner edit restores the old bytes or deletes a new topic. The first failure may retain its original I/O error while `wiki status` records the recovery hold; explicit unresolved retries remain held without new model requests or source strikes. Review and reconcile the actual planned after-image and owner edits before retrying. A log failure is not evidence that no note was written.

Malformed recovery control fields or progress records also require review of the named batch. Preserve its matching plan, input, output, journal and current owner edits; restore only independently verified evidence. Do not delete a progress record, coerce a version/flag or guess a write hash to clear attention. Older journals may legitimately omit newer fields, and archive progress alone never authorizes semantic replay.

### Local path-SHA lookup

When status withholds a file or folder name, use its `path_sha256` in a private terminal on the affected device. The digest identifies the exact vault-relative POSIX path string, encoded as UTF-8 with `surrogatepass`; `.` identifies the vault itself. It is not a hash of note contents and grants no write authority. The following Python 3.12 snippet hashes every entry name in each visited directory, including hidden names and links, without reading notes or traversing hidden or linked directories. A folder or link can match from its parent's directory listing even when its target cannot be listed. Use the runtime's configured Python. It prints only matching paths, escaped for a terminal, and does not save anything. Keep that output local if the name contains private information or possible credentials; do not paste it into chat, screenshots or notifications.

```python
from pathlib import Path
import hashlib, os, re

vault = Path(input('Absolute local vault path: ')).expanduser().resolve(strict=True)
wanted = input('Reported path_sha256: ').strip().lower()
if not vault.is_dir() or not re.fullmatch(r'[a-f0-9]{64}', wanted):
    raise SystemExit('Use a real vault directory and a 64-character SHA256.')
matches = 0
def inspect_name(path):
    global matches
    relative = path.relative_to(vault).as_posix()
    digest = hashlib.sha256(relative.encode('utf-8', errors='surrogatepass')).hexdigest()
    if digest == wanted:
        print(ascii(relative))
        matches += 1
inspect_name(vault)
for current, dirs, files in os.walk(vault, followlinks=False):
    for name in dirs + files:
        inspect_name(Path(current) / name)
    dirs[:] = [name for name in dirs
               if not name.startswith('.')
               and not (Path(current) / name).is_symlink()
               and not os.path.isjunction(Path(current) / name)]
print('Matching local paths:', matches)
```

No match can mean the file or folder was renamed, moved, removed, has not synchronized to this device, or its parent cannot be listed. Hidden and linked directory entry names can match, but their descendants are deliberately not traversed. Inspect the affected device's current paths and preserved local journal deliberately; do not guess a replacement or overwrite Index because it is the generic public locator. Git failure diagnostics screen raw filenames before JSON escaping and use the same opaque locator when needed. This changes displayed diagnostics, not which otherwise valid note filenames may synchronize.


Maintenance source-review warnings cover central AI-Memory pages, reserved project AI-Memory summaries and Maps/AI-Context, including case-renamed reserved filenames. Central Markdown suffixes match case-insensitively on every OS, with hidden and linked directories pruned before traversal and enumeration failures reported through a safe locator. Source changes and review age use the existing recorded SHA256/30-day checks. Ordinary neighboring originals and immutable compiler receipt/Log/archive history are excluded from summary-age review; receipt integrity remains checked by wiki status, and claim/source integrity by wiki lint. Control retains its existing age review and ownership guard. A warning requests evidence-based review; it does not rewrite a current label, infer profile facts or modify original notes. Keep canonical shared folder/file casing and use the current SHA before an authorized derived update.

Fresh publication and resumed recovery now use the same bounded JSON packet values, including numeric metadata keys. The first saved input stays stable through publication; caller types, fitting aliases and existing owner claims remain preserved. Already-stranded older noncanonical plans remain held for conservative recovery review. Never rewrite their private hashes or fabricate evidence; this correction does not automatically repair those journals.

Lint checks decoded potential selected context with the existing page/field/index guard: the last twelve claims of a page within the compact budget. Older unselected and compact-omitted claims retain ordinary graph/provenance checks. Enumeration warnings retain a safe failed-subtree identity or fall back to its namespace; the names-only lookup above resolves files, folders and vault root (`.`). Healthy review and the existing Git stage continue.

Undecodable Control refuses maintainer-only work through the existing permission guard. Maintenance reports that shared ownership could not be verified, skips aggregation and still attempts its existing Git stage under normal content checks. Preserve Control and repair UTF-8 deliberately from owner evidence; never guess an encoding or infer a handoff. The last authorized worker-success receipt stays historical, not proof that a refused attempt ran. Missing-Control bootstrap and explicit owner matching remain unchanged.

Memory writes screen capture evidence paths, derived-page source paths and dynamic destination paths with the existing credential/NUL guard and 400-character limit before writing. Original filenames and notes remain preserved and available through existing read/search; this does not rewrite historical metadata.

A derived update needs evidence distinct from its destination's actual file. Alternate separators, filesystem case aliases and hard links to the same existing file cannot supply independent evidence. Genuinely distinct files remain valid, including differently cased files on a case-sensitive device; a missing destination can still be created from genuinely distinct existing sources. Stored path strings are not rewritten.

For the R112–R118 candidate, the lookup above also identifies hidden note names and link entry names without reading their contents or traversing hidden/linked directories. Central AI-Memory review uses a pruned walk and reports enumeration failures. If the in-vault `02-Areas` parent is linked, review stops at that entry and its opaque locator identifies `02-Areas`, rather than a child that the safe lookup cannot reach. Preserve the original layout and review the link deliberately; a diagnostic does not authorize deleting it or following its target.

Immutable compiler receipt records do not acquire current-summary age reminders. Their optional proof integrity remains visible in `wiki status`, and claim/source evidence remains subject to `wiki lint`. Ordinary derived summaries and Control keep the existing 30-day review policy; Control's maintainer guard is unchanged.

An aggregate structural limit in optional compact topic context now omits that context only after its raw individual claims pass the existing guards. Character-based omission, malformed graph refusal, decoded checks for included context, whole-packet limits and publication authority stay unchanged. An omitted page is preserved; it is not silently repaired or granted more capacity.

Source-specific path/content attention identifies the screened source when possible. Cached public worker status may instead show the safe Index locator and `integrity_page_sha256`; use that digest with the local lookup above. An invalid source date or other selected source metadata remains pending with its original bytes intact. Inspect the actual source and repair only from owner evidence before an explicit `wiki retry`; do not overwrite Index or guess a replacement date from the generic locator.

Before unused work reaches a provider, the compiler also screens known standard YAML frontmatter after parsing its scalar values, keys and associations. It rereads each selected source within the existing byte bound and verifies the selected raw SHA256 before this check, so metadata beyond the first source chunk cannot bypass it. This parses known local frontmatter only; it does not decode arbitrary body text. A possible credential produces controlled source/content attention with no provider request or raw value in diagnostics. A changed source snapshot remains a transient refusal; original notes and saved inputs are not rewritten.

Selected-context claim diagnostics retain the original zero-based claim index when a valid full claim count is available, including a legacy/manual topic already above capacity. Invalid count types keep the existing safe fallback. The 120-claim publication limit is unchanged; bounded public status may omit an index outside its existing range, so inspect the controlled local attention receipt rather than guessing a window-relative index. No claims are deleted automatically to clear capacity or attention.

R119 extends only the known parsed source-frontmatter screen. Standard YAML tags can produce sets, binary bytes and ordered key/value pairs, so the candidate source-only walker covers those values as well as ordinary dictionaries, sequences, tuples and nested aliases. It also handles frozenset containers without changing the generic selected-model-context guard. Strings still require strict UTF-8 and the established credential/NUL checks. Known binary values receive a bounded byte-level version of the same detector with ASCII token boundaries and NUL refusal; benign opaque non-UTF-8 bytes do not require guessed decoding.

This screen inspects already parsed local metadata and its key/value associations under the existing graph/text bounds. It does not try arbitrary base64, string or body decoders, decrypt data, or guarantee discovery of every secret. The complete-source byte bound and selected raw SHA check, safe source-specific attention, pending evidence, transient owner-race refusal and verified partial-publication rules remain unchanged. Preserve original metadata and use explicit owner review for any genuine detected credential; do not disable the guard or rewrite saved hashes to force progress.

### Candidate source-screen work bounds and review priority

R120 supports valid parsed YAML nonfinite floats, such as infinity and NaN, in original source metadata. Their local association-detector representation does not make them valid generic model JSON: selected-claim and generic JSON guards retain strict nonfinite refusal. The [YAML float definition](https://yaml.org/type/float.html) includes these values, while [Python's JSON documentation](https://docs.python.org/3.12/library/json.html#json.dump) distinguishes strict `allow_nan=False` from local permissive rendering. This compatibility change does not rewrite original source values.

R124 defers each source-metadata key/value association check until its normal child checks consume the existing semantic budgets. R125 additionally limits one source-metadata screening call to 8,000,000 detector/render work units and caches numeric renderings by `(type, id)`, retaining each actual scalar object. Work is reserved before a bounded numeric render or association scan; strict UTF-8 string sizes are cached for mixed string/binary associations. This source-only counter is not a global packet byte cap or an elapsed-time guarantee. The separate 2,000,000 semantic text limit, 100,000-node/depth-128 envelope, known credential patterns, NUL checks, active-cycle refusal and supported aliases remain. No arbitrary body/string decoding, new credential heuristic or complete secret-discovery guarantee is introduced.

A controlled source/content attention result can mean metadata exceeded a work or structural bound; it does not by itself prove that a credential was found. Preserve the source, pending work, selected raw-SHA evidence and private saved inputs. Inspect the affected source locally, repair only with owner evidence and use explicit `wiki retry` after repair. Complete-source rereading/raw-SHA checks, transient owner-race refusal and verified partial-publication authority remain unchanged; never edit saved hashes to force progress.

R121 places actionable, uncertain or mixed source-review reasons before pure age reminders in the public bounded review list, preserving stable order within each group. The full private receipt order, total/truncation counts and diagnostic screening remain unchanged. Ordinary generated summaries and Control still receive the existing 30-day review policy; only the previously excluded immutable receipt/Log/archive history remains exempt. R122 also reports a direct linked `02-Areas/AI-Memory` root by its own opaque path hash, or the linked `02-Areas` parent when that is the first boundary. Review does not traverse either target; the unchanged names-only lookup resolves the entry from its safe parent listing. A linked child remains pruned without target traversal.

These are candidate behaviors with phase-specific evidence, not installation approval. R123's directory fixtures use actual private Windows junctions/native Linux symlinks with nonrecursive cleanup; only an explicit Windows file-symlink error1314 can produce the conditional capability skip, while unrelated errors propagate. Genuine unprivileged Windows hardware has not been tested. The deliberately corrected central-root warning oracle is recorded in the [remediation history](REMEDIATION-v0.1.7.md#r120-to-r125-source-screen-and-maintenance-follow-ups). R125 and all combined full-suite/review/platform/deployment gates remain pending at this documentation revision.

### R126 mixed source metadata follow-up — 2026-10-16

The existing source detector distinguishes known Unicode strings from opaque binary bytes. R126 repairs mixed key/value associations: a known Unicode string keeps its established case/whitespace semantics when paired with already parsed YAML binary. The binary fragment receives only a bounded ASCII inspection view, retaining high octets as replacement markers; it is not interpreted as UTF-8 or used to decode arbitrary source text. All-binary associations keep their existing ASCII detector, and all-string behavior is retained. [Python's regular-expression documentation](https://docs.python.org/3.12/library/re.html#re.ASCII) explains the Unicode/bytes distinction that made the old mixed conversion incomplete.

The proposed source-only repair caches that binary inspection view while retaining the actual object, charges its construction and association screening to the existing work ledger, and preserves strict string UTF-8, mixed-association size, 2,000,000 semantic text, 8,000,000 work, 100,000-node and depth-128 bounds. Complete-source raw-SHA binding, pending evidence, generic JSON/context guards, safe attention and verified recovery authority remain unchanged. This is a conservative check of known parsed metadata, not arbitrary decoding or a guarantee that every secret can be discovered. A bound-triggered attention result is not proof of a credential; preserve originals and use owner-evidenced repair before explicit retry.

The evidence is synthetic: two independently failed root contracts reached a fake provider, with four passing controls and no actual external transmission. The authored Windows before run has 38 cases, comprising 16 privacy/context failures, four additional cache-work failures and 18 controls; its 20 failures must not all be described as privacy transmissions. That before-phase description is historical: the sealed R12638 before/after receipt records passes on both Windows and the new E-hosted native Linux host; it does not approve a later combined source. The Mac/Linux installation, capture/maintainer roles and exact local lookup above need no new setup for this source-guard correction; native Mac acceptance remains separate. See the dated [remediation record](REMEDIATION-v0.1.7.md#r126-mixed-unicode-and-binary-source-associations--2026-10-16) for historical gates and remaining acceptance.

### R127 original YAML evidence — scoped verification, 2026-10-16

The source-header event screen now preserves evidence that ordinary YAML construction can discard: overwritten/merged values, set/null contents, scalar aliases, direct key/value associations and bounded standard binary values. Lexical evidence is checked before loss; known primitive association views are normalized with bounded standard constructors and cached per scalar definition. No arbitrary string/body decoder or new model authority is introduced. The exact R126 constructed-metadata helper and generic JSON/context checks remain preserved. [PyYAML documents events separately from constructed objects](https://pyyaml.org/wiki/PyYAMLDocumentation#events).

One complete-header event pass has a cumulative 8,000,000 detector/render work limit. The separate constructed-metadata pass retains its own 8,000,000 limit, so their combined limit is 16,000,000; neither pass can borrow the other's unused budget. These are work estimates, not elapsed-time promises or a larger packet allowance. Existing source reads remain at 2,000,000 raw bytes, semantic text at 2,000,000 characters, and source/context graphs at their existing 100,000-node/depth-128 bounds. Strict strings, the established conservative credential detector, original SHA binding, transient source-race handling, pending evidence and verified partial-publication authority remain unchanged.

Only exactly recognized closed frontmatter is event-parsed. Missing or unterminated opening fences remain ordinary Markdown. Closed nonmapping headers are screened before the usual empty metadata result. Closed syntax errors, unknown/ambiguous alias bindings and depth overflow produce controlled source/content attention. Parser-valid harmless invalid standard constructors retain safe fallback after lexical screening. A 500- or 1,000-deep source remains fully inventoried with its original body/hash and unknown metadata date, but compilation can refuse at depth128, preserving the pending source and writing no input packet. Original notes are not rewritten to satisfy a bound.

R127's sealed scoped receipt is `r127-remediation/frozen-after-receipt.json`, SHA256 `2914592fe048c13432418443ef45109b454273776ca90436e75598e1543fc53d`. The same64 author cases have28 failures/36 controls before, then64 passes on both Windows and native Linux. The28 include22 privacy/context/typed-normalization contracts and6 explicit new policy/resource contracts; they are not28 demonstrated transmissions. Root17, retained root6/root12, corrected prior groups and focused266 also pass on those recorded source phases. Three existing shipping-module exceptions are explicit: a valid parsed Unicode-key fixture replaces an old overlong-key fallback fixture; work counters attribute event/constructed phases without changing their literal aggregate; and the old deep500 acceptance oracle now checks documented depth attention while preserving source evidence. See the [remediation record](REMEDIATION-v0.1.7.md#r127-original-yaml-event-evidence--scoped-verification-2026-10-16) for exact pins and preserved failed phases.

### R128/R129 common frontmatter construction — candidate follow-up

Source screening happens after the common frontmatter loader may already have constructed metadata. R128 therefore bounds aggregate mapping visits, merge copies and dictionary population at 100,000 reserved work units in a private SafeLoader subclass, before those operations. This separate construction budget is not the graph-node cap. Valid fitting merge order, duplicate-key behavior, standard tags and aliases remain supported; strict derived parsing reports controlled invalid-frontmatter errors while non-strict source metadata can fall back without deleting original body evidence. R128's same30 authored cases pass after seven before failures/23 controls on each OS; its scoped receipt is `r128-remediation/frozen-after-receipt.json`, SHA256 `9295b1fe11b8030af875b7494ee95c5b5e3b644d95c1fae00baae0795fb53dca`.

R129 adds a separate cumulative 8,000,000-unit integer-construction estimate to that common loader. It reserves before the standard integer constructor, including conservative sexagesimal growth, and reuses the loader's normal alias cache; it does not change another PyYAML consumer's constructors. This is additional to the mapping budget and the two source-screen budgets. R129's same35 authored cases record10 failures/25 controls before, then35 passes on each OS; root8, prior mapping30, parser39 and focus266 also pass. Its scoped receipt is `r129-remediation/frozen-after-receipt.json`, SHA256 `3a5a314dad3764313b17ba6db07553e38942cbae43d773647425c2f14fe86f48`. These are recorded-source results, not full combined validation. The adapted pinned PyYAML merge algorithm will ship with its exact MIT notice in `THIRD-PARTY-NOTICES.md`. Do not describe these limits as a complete CPU/memory sandbox or a guarantee of all-secret detection.

The new E-hosted `Second-Brain-Tests` distro is the isolated native test host, with Python3.12.12; Windows uses Python3.12.8. Old Debian remains stopped, unrepaired and registered on C; no verified backup/move or Docker repair is claimed. The setup/roles/connectors and exact names-only lookup above require no new configuration for these candidate guards. Native Mac and the final frozen-source release gates remain separate. **HOLD.**

### Final combined fixture qualification — node cap

The common mapping-work bound is intentionally earlier than source graph screening. The existing known-YAML-types35 module's100,000-set fixture now verifies empty metadata fallback, followed by the unchanged full-source inventory, content attention, zero-provider-call, original-byte and pending-work checks. Its depth and binary cases still require their actual constructed types. Only one test function/preassertion changes; fixture size,35 parameter identities and downstream privacy/queue oracles remain unchanged. This is the fifth current existing-module exception, additional to the parsed Unicode fixture, phase counter, deep500 policy and fatal-error injection hook. Current type35 module SHA256 is `0bb94522a75b381d9abf53df1751b8dee103bacf98631e3aa14a90137c78b098`.

Exactly88 of the93 test/support files in base3ee remain byte-identical after those five exceptions; the historical central-history38 correction was already present in3ee and remains separately preserved. R127's earlier91/94 scoped identity statement remains a truthful historical scope, not the final combined identity claim. The exact corrected35 copy now passes on Windows/native Linux in R130 scoped phases; those runs used a private external test copy, and this new final candidate coalesces its same approved bytes. The whole coalesced candidate has no new full-source pass yet. Final counts and all source-bound acceptance gates remain pending; **HOLD**.

### R130/R131 scoped follow-up — final installation still held, 2026-10-17

The source-header check now handles standard scalar value-key mappings and nonspecific `!` scalar tags with their bounded known constructor views, including aliases, first-selector ordering and safe invalid-constructor fallback. It keeps raw evidence until outer associations have been checked; it does not decode arbitrary body text or invoke unknown constructors. The common loader and SOURCE primitive fallback also contain the pinned standard float constructor's OverflowError locally, while unrelated/fatal and rendering errors retain their previous behavior. Setup, capture/maintainer ownership, sync and connector configuration do not change for these guards.

R130's scoped receipt is `b8ff1b8e134962c9a4c739781bf2791df082d4bcd844039036a9f432555ed476`: same79 cases pass after on both OSes, with original21 and required prior suites also passing. R131's receipt is `d70c30f53900752df3c91ba9d187dbb151b49405379b47599bef64882a29ed1d`: same38 author and accepted16 original cases pass on each OS; the two original before-only observations are excluded. Independent narrow postvalidation reports no findings (`60d644927b5243b399f7481328f076260feb931f51261213f753de5698dc25b0`). See the [dated remediation record](REMEDIATION-v0.1.7.md#r130-value-key-mappings-and-nonspecific-tags--scoped-verification-2026-10-17) for exact phases and limits. These are synthetic scoped passes, not authorization to install this staged candidate.

| Final requirement | Staged derivation; final checks pending |
| --- | --- |
| Planned tracked source files / regenerated manifest entries | 136 / 135 |
| Test/support files / derived full case identities | 99 / 2,065 |
| Exact root platform executions / distinct contracts required | 495 / 483 |
| Binder families / complete module cases / mandatory exact IDs required | 60 / 1,643 / 1,825 |

These are staged requirements, not passing final gates. The source inventory is b258's 134 files plus the two new test modules; the manifest will exclude its own file. The full identity target is the old actual 1,948-case collection plus the disjoint actual 79- and 38-case authored JUnit sets. Root requirements add the original R130 21 and accepted R131 16 contracts to 458/446; the two stale R131 before-only observations are excluded. Binder requirements add two families and their 117 exact cases to 58/1,526/1,708. Final collection, generated manifest, bound root receipts and binder must independently confirm these targets after the new freeze.

The exact names-only lookup fence above and upstream MIT notice stay unchanged. Event8M and constructed8M screening remain independent (aggregate16M), alongside source-byte2M/semantic2M/node100k/depth128 and separate common mapping100k/integer8M construction bounds. Preserve source originals and pending evidence; bound-triggered attention is not proof that a credential exists. The new E-hosted test distro uses Python3.12.12, while Windows uses3.12.8. Old Debian is still stopped/unrepaired on C, no verified move/backup or Docker repair is claimed. Native Mac, hosted CI, tagged release, deployment and real client acceptance remain unwaived; productionv0.1.6 and PRIMARY0ed are unchanged. **HOLD.**
