# Set up or update a Second Brain device

Workflow revision: **29 October 2026**. Windows v0.1.8 is a hardening candidate, not automatic deployment approval. Use the trusted handover's exact commit, manifest SHA256 and qualification report. Mac/Linux stay on accepted v0.1.7; do not clone or provision v0.1.8 on an owner device. Current dated deployment decisions take precedence over historical preparation notes.

## Choose a route and protect the inputs

| Situation | Route |
| --- | --- |
| No existing brain | Create a blank starter and the person's own private notes repo. |
| Adding the same owner's device | Reuse that owner's notes history; create a new capture-only runtime. |
| Importing an existing vault/library | Inventory and back up outside the destination; approve the mapping before copying. |
| Runtime already installed | Inspect it; use the forward updater. Client-only refresh below applies to an existing v0.1.8 runtime. |

Resolve account, actual vault/runtime/checkout paths, Git identity, timezone and selected clients in one short intake. A new independent first maintainer needs an explicit ownership choice; another device of an existing shared brain is capture-only. No automatic wiki model schedule or new paid provider is enabled by this guide.

Keep kit, software, runtime and vault separate. Runtime and vault cannot be nested. Prefer short Windows paths such as `C:/SB-Kit` and `C:/Second-Brain`; inspect actual final import paths and traversal errors. Do not silently rename originals, overlay an occupied folder, enable global long-path policy, copy a whole repository or move a Git vault into another sync system. Backups and scoped inventory precede changes; Git sync is not a complete backup.

Review nested `AGENTS.md`/`CLAUDE.md`, duplicate groups, attachments, runtime outputs, case collisions, credentials and ignored-but-already-tracked files before any bulk import. A pattern scan is limited evidence, not a clean bill of health. Exclusion from wiki compilation does not exclude a note from Git publication. Preserve accepted source-selection decisions; do not infer exclusions from words such as research or handoff.

## Windows host preflight

Use the intended user's ordinary host PowerShell, outside a packaged or restricted app surface. Record app/surface/version and confirm files and interpreter paths from that independent host terminal. A child shell and a successful package-identity check alone do not prove all host visibility.

Install official Git (2.38+), GitHub CLI and **uv 0.9.16+**. Prepare Python once, serially:

```powershell
$BrainUv = (Get-Command uv -ErrorAction Stop).Source
& $BrainUv python install 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python preparation failed' }
$BrainBasePython = (& $BrainUv python find --managed-python 3.12).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BrainBasePython)) { throw 'Host Python missing' }
& $BrainBasePython -I -B -c "import sys; assert sys.version_info[:2]==(3,12); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Host Python cannot run' }
```

Reopen stale terminals if tool paths changed. Keep absolute uv/Python paths; do not rewrite global PATH to repair one process. Change into the verified software checkout before running setup so the trusted tool is outside the checkout/current directory. Use PowerShell for native Windows switches; Git Bash can translate `/TN` and `/PID`.

GitHub authentication is attended: the user runs `gh auth login --web`, completes account/browser choices, then checks `gh auth status`. Never collect tokens or device codes in shared receipts. Public software access and the person's private notes access are separate.

## Verify the software before execution

Clone the exact approved tag into a NEW plain checkout, with LF Git bytes. For an upgrade the location must be `<runtime>/releases/v0.1.8`. Reuse an existing cache only after verification; never reset/delete it to conceal a failure. The handover's trusted helper checks expected HTTPS origin, tag/HEAD commit, manifest SHA256, staged/tracked file set, payload bytes and unexpected files before any downloaded Python runs. The document/helper itself must come from a trusted handover.

The following standalone consistency check is useful AFTER that independent pin check. It cannot authenticate itself:

```powershell
& $BrainBasePython -I -B -c "from pathlib import Path; import runpy; print(runpy.run_path(str(Path.cwd()/'update.py'))['verify'](Path.cwd())['version'])"
```

Isolated Python prevents caller/checkout import injection; it is not an OS sandbox or a guarantee about an untrusted executable/environment. Preserve rejected caches for inspection. Never re-author or encode a refused operation to bypass automatic approval.

## Fresh Windows runtime

The vault must already exist and contain only the approved material. Launch Obsidian normally at least once and choose **Open folder as vault**. Prefer that supported GUI flow over editing `obsidian.json`; failed URI launch is not proof the app is absent. If GUI access is unavailable, leave GUI acceptance pending for the user.

Set `$brainRoot`, `$brainVault` and `$brainRelease` to the inspected absolute paths. `$brainRelease` is the independently verified checkout. From that directory:

```powershell
Set-Location -LiteralPath $brainRelease
& $BrainBasePython -I -B (Join-Path $brainRelease 'install.py') --root $brainRoot --vault $brainVault --role capture --uv $BrainUv --download-model --install-service
if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete; preserve its private receipts' }
```

The model is local; its first download needs Internet and local CPU/storage. No cloud language-model key is needed for retrieval. Add selected client options to this SAME initial command before running it. For example, `--configure-clients --client claude-code --client codex` selects those two. Desktop requires an explicitly selected/discovered config path; hooks additionally require `--include-hooks`. The default leaves client files alone and does not install hooks.

Use the verified locked profile: FastEmbed only, with reranking disabled. LiteLLM SDK/proxy is deliberately absent even though upstream Basic Memory still declares it in metadata; do not reinstall it to silence a generic dependency warning. The server refuses unsupported provider/reranker settings or an environment containing that SDK. Read the [security profile and scoped advisory exception](../SECURITY.md) before rollout; the audit is not represented as clean.

The same isolated entrypoint, using the already recorded absolute host executables:

```powershell
& $BrainBasePython -I -B install.py --root <new runtime> --vault <existing vault> --role capture --uv $BrainUv --download-model --install-service
```

Before service startup, read `device.json`; from ordinary host PowerShell verify that exact lexical `.venv/Scripts/python.exe`, its sibling `pythonw.exe`, absolute `pyvenv.cfg` base home and base executables exist and run. Do not replace the venv path with the resolved base path. Store/AppData-only visibility failures leave startup pending; do not repeatedly rebuild in the same invisible location.

Then use that recorded Python with `<runtime>/app/brain_cli.py service start`, authenticated `health`, and `index status`. The owned task is `SecondBrainLocalService`, limited to the intended interactive user. An existing task for another root/owner is preserved and refused. Never install over the existing maintainer merely because an example path looks convenient.

## Client configuration and proof

Back up host-visible settings and preserve unrelated entries, environment values and options. Client plans compare hashes before each write. Fresh selection adds only chosen integrations; updater refresh changes only exact existing owned entries. Malformed or ambiguous ownership requires review. Close/checkpoint Desktop through its own UI before selecting its config. Do not kill all Claude processes or the host running this setup. If it must remain open, explicitly skip Desktop refresh; a reopened/concurrently edited config is deferred and must be checked later.

If an existing `second-brain` entry points at a different or unknown runtime, preserve the refusal. Back up that exact host-visible settings file, inspect its command/args and the previous target, and establish whether it is still used. With the client closed or through its supported settings UI, the owner may remove or rename only a confirmed obsolete entry, then re-plan the selected integration. If its ownership or purpose remains uncertain, leave it intact and omit that client until resolved. Do not remove the whole config, alter unrelated entries, guess a new path, or repeatedly reinstall over it. A scalar/malformed managed entry also needs this attended inspection.

For an existing **v0.1.8** runtime, its own verified v0.1.8 checkout's `install.py --reuse-python <recorded Python> --refresh-clients --refresh-only --client <chosen client> --root <root> --vault <vault>` refreshes owned entries without deploying software or creating missing integrations. Inspect a read-only `--validate-only` plan first. The v0.1.7 installer does not support these refresh flags: existing entries refresh during the first forward upgrade. To add a missing integration on either version, use that client's supported MCP settings with backup/merge and a real invocation; do not rerun fresh configuration over an installed runtime.

```json
{"mcpServers":{"second-brain":{"command":"<recorded absolute venv Python>","args":["<runtime>/app/brain_client.py"]}}}
```

Claude Code and Desktop use their actual discovered JSON settings; Codex uses the `mcp_servers.second-brain` TOML table with startup/tool timeouts 60/120 seconds. Preserve other tables and managed environment/options. Skills and the managed protocol do not grant MCP access. Restart/checkpoint the named client and open a fresh chat, then call `brain_context`, search and read. A process ancestor, config/menu screenshot, tools/list or standalone adapter harness is insufficient proof of Desktop Chat, Code, Cowork, a web chat or another client.

`brain_read` accepts exact vault-relative Markdown paths with optional `.md`, or full-path wikilinks such as `[[Maps/AI-Context|label]]`. It handles selectors inside wikilinks and preserves literal `#` in plain filenames. A bare basename such as `Device-Setup.md` is not resolved across the vault; search for the real path first. Preserve original notes rather than globally rewriting their links.

## Approved import and current index acceptance

If service Git publication is already active, obtain a verified stopped window before an approved import. `service stop` disables the owned Windows task, admits only after active writers finish, drains verified processes and publishes an empty receipt. Automatic clients cannot override an explicit stop. A busy refusal preserves work and restores prior enabled state; it does not prove an empty runtime. Never copy a library during an assumed 15-minute review window.

Run `index status` after copying approved material. It checks current eligible paths/raw SHA, normalized indexed body and persisted vector metadata, not just counts. A starter-only or historical bootstrap `verified` result does not cover a late import. Use one `index retry` for missing files; use `index retry --full` for a stale checksum or preserved-size/mtime edit. Do not edit note mtimes, rewrite line endings, reset SQLite, delete the DB, copy WAL files or set a status file to verified.

The defaults allow eight hours overall with a 30-minute no-observable-row-count-progress budget; explicit CLI budgets are bounded. The signal is whole-DB counts, not proof a particular process is alive or that a same-count re-embed stalled. Retain elapsed time, exit/status/history, current file coverage and indexed retrieval receipts. A nonzero exit, timeout, source change, missing vector or uncertain metadata remains needs attention/review. Do not start overlapping reindexes or retry indefinitely. Standard YAML/BOM/CRLF bodies are covered; unusual frontmatter parser differences can require review and must not be silently declared complete.

Verify a late-imported known source in the indexed arm of search and through an exact read. Fresh-file fallback/read alone is not index or semantic coverage. Record model, counts and hashes without copying note bodies into a team report.

## Forward Windows update: v0.1.7 to v0.1.8

Keep the existing service healthy during preflight. Read `device.json` and require the retained Python to match the accepted v0.1.7 release's `.venv/Scripts/python.exe`. Check its host-visible absolute non-Store base and Python 3.12, plus `import filelock, psutil` in isolated mode. If this cannot be established, preserve the installation and obtain a reviewed recovery path; do not substitute a bare host Python, a new unpinned environment or the target venv.

The updater inspects task ownership before creating a hold. A missing or explicitly disabled task is refused; it does not silently override an owner's stop. Inspect and deliberately restore/start the intended existing service before requesting the forward update. A foreign or unreadable transaction remains preserved for inspection, with no guessed recovery UUID.

Place the independently verified new source at EXACTLY `<runtime>/releases/v0.1.8`. Change into it and run its NEW updater with the RETAINED installed Python:

```text
<retained v0.1.7 Python> -I <verified v0.1.8 checkout>/update.py --root <runtime root> --version v0.1.8
```

Do not manually run `uv sync` first. The updater rebuilds the target `.venv` itself, preserving a cached one by quarantine. Running the updater from that same target Python is refused before stopping. Running a different checkout's updater is also refused before state/provisioning. Do not use the old v0.1.7 updater; its legacy configure-client preflight is intentionally refused before service stop. Mac/Linux must not attempt this upgrade, including its old clone/sync path.

The new flow seals client/device hashes and a verified prior-app snapshot before stop, uses the verified new controller for both forward and rollback actions, releases parent writer locks before any stop, and requires a fresh birth/heartbeat plus authenticated expected-version health before releasing the exact hold. It preserves notes, local index, credentials, identity, role and accepted source policy. Client edits/reopened Desktop are preserved and reported as deferred. Configuration checks do not prove every GUI client has reconnected.

## Recovery

Read private `state/upgrade-status.json` and `service recover` first. Keep the original verified new checkout, snapshot, UUID and retained/new environments. Do not clear a transaction file, delete a cache, reuse a completed UUID or kill whichever PID owns a port. An unverified partial stop leaves the hold for diagnosis. A positively refused pre-End stop may be cancelled only after unchanged-state proof; it is not a successful shutdown.

Use the EXACT original new checkout's `update.py --root <root> --recover --hold-id <recorded UUID>`. A verified retained Python or the recorded new controller Python can launch recovery; the actual children use the recorded new environment, which must still be intact. Recovery acquires the update lock, checks snapshot/source identity, stops safely before restoring exact app/device bytes, preserves concurrent client edits, reinstalls the task and verifies old-version health. Legacy heartbeat availability is reported honestly; a restored old listener is not new-release acceptance.

After verified recovery, inspect receipts before a new update. Do not loop upgrades to hide an unchanged failure. Share only concise event/count/hash receipts; private snapshots, sealed plans, credentials, raw logs and note bodies stay local.

## Acceptance before sharing with the team

1. Correct paths, role, identity, expected version and host-visible interpreter.
2. Authenticated context/search/read and one labeled capture with a stable device event ID; actual path/SHA receipt.
3. Current file/body/vector coverage and indexed late-note retrieval after the approved import.
4. Verified stop/empty state, fresh start identity/heartbeat and authenticated version/read.
5. Per-client persistence and actual tool calls in each requested fresh named surface.
6. Existing maintenance-owned Git publication and round trip, without another sync loop.
7. Actual reboot/login and other-device checks when performed; otherwise explicitly pending.

Do not infer team members' physical-device acceptance, GUI/cloud connectivity or personal facts from this host's tests. Keep original notes and current source exclusions unchanged. [Verification boundaries](DEVICE-TESTING.md) distinguish automated regression, native fixture proof and target-device acceptance.


## Troubleshooting

- Tools missing: inspect that client's generated second-brain entry, restart it and check a new session. Test the adapter and authenticated local health before changing settings.
- Service unavailable: check the per-user startup entry and local service logs. Keep log/source excerpts private; never dump auth files.
- Windows Git helpers: only an actually signaled synchronization root starts the one-second background-process grace. Remaining owned descendants then enter termination and full lifetime verification before the root result or lock release; the original 90-second execution deadline still wins. Background hook writes are not guaranteed to finish during that grace. Uncertain cleanup keeps the owning pass at attention; do not force-release its locks. If a signing or credential agent must persist, have the owner start/authenticate its official agent outside this command lifetime while preserving the existing signing policy.
- Git fsmonitor: core.fsmonitor=false is a per-command override for synchronization and staged-object commands routed through brain_git._run_git. It does not rewrite owner Git settings, apply to other clients or claim to cover separate read-only wiki-history operations.
- Oversized operational logs: a service-*.log above 5,000,000 bytes remains preserved and reports attention on every maintenance pass while it grows. There is no automatic bounded rotation. Stop the registered service, then positively verify that every writer to the exact log, including already-running maintenance/children, has exited. A stop command alone is insufficient proof. Only after verified quiescence, move the exact file with its bytes and permissions into an owner-private dated archive outside the active service-*.log set, then restart and verify new logging and health. If writer identity or cleanup is uncertain, preserve the original and attention; do not truncate/delete/rotate a live log or force-release locks.
- Search not ready: check local index progress and model download. Do not copy another OS's SQLite/WAL while it is live.
- Index bootstrap needs attention: diagnose the private receipt and fix the cause. Use the supported `index retry` or `index retry --full` workflow above; preserve status/history, the retrieval database and model cache. Do not reset the marker or start overlapping reindexes.
- Sync paused: preserve all files and staged changes; inspect the conflict. The service must remain the only sync owner.
- Wiki compilation: the designated Windows maintainer owns it. Do not run `wiki run`, `wiki retry` or create a schedule on a capture device.
- Wiki worker needs attention: the maintainer inspects `wiki status` and the controlled private failure receipt, repairs the cause and explicitly runs `wiki retry`. Pending sources and historical publication proofs remain preserved. Transport/timeouts and invalid model output must not cause unlimited scheduled model calls or blame unrelated sources.

- Native child cleanup still waiting: a command deadline does not guarantee a finite Windows kernel cleanup time. The runner retains ownership and caller locks until known process lifetimes and the private Job are verified complete. PID reuse has explicit evidence rules; absent notifications, denied queries or unresolved birth coverage can still keep an unverified hold. Preserve transaction UUID, status and private diagnostics; do not mark success, delete locks, reset state, kill cached PIDs or repeatedly restart the updater. An operator must inspect exact retained process/Job/task identities and arrange scoped cleanup before the recorded forward-controller UUID recovery. An empty process count alone is diagnostic, not permission to release this hold. This is a known availability limit, not evidence that original notes changed.

Reference: [retained v0.1.7 source and installer (historical baseline)](https://github.com/code-by-muhammad/second-brain-system/tree/v0.1.7), [operating README](../README.md), and the shared vault's `Maps/AI-Context.md` and `02-Areas/AI-Memory/Wiki/Control.md`.

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

## Current refusal recovery

- A named Windows checkout-length pause is specific to that receiving device's vault location and Git setting. Use a shorter vault path, or explicitly choose `git -C <vault> config --local core.longpaths true` after checking other tools can use the long paths. The service does not change Git configuration or Windows registry settings. The same incoming note may work on another device.

- Windows-incompatible tracked names pause publication with an exact safe locator or a path hash. Preserve original and staged bytes. One owner can explicitly rename the original on a capable POSIX device, commit and sync that repair; other devices already tracking the old name can then fetch the portable upstream repair while keeping their captures. Do not choose a different replacement independently on every peer or disable Git Windows protection. New incompatible candidates, unsafe incoming trees, case-only mismatches and existing owner staging retain their safeguards.
- If a POSIX peer has an uncommitted edit to an already tracked incompatible name, preserve that edit and pending captures. If another device has committed the shared rename, review local staging, explicitly commit only the tracked edit under its current name (`git add -- <old-path>`, then an owner commit), and sync to receive that rename. Otherwise make one explicit owner rename on a device that can open the original. Do not choose different replacement names on each peer or disable safeguards; conflicting edits still need owner review.
- A source, existing capture or derived destination over2MB is refused without truncation. Use smaller genuine evidence for a derived update; an oversized destination needs an explicit owner-controlled repair. Capture evidence links alone do not read or restrict the referenced source size.
- A literal inventory filename that resolves as a different separator alias requires wiki path attention before a saved batch/model call. Preserve the source; repeated automatic retries cannot repair its identity. Genuine transport failures and snapshot changes retain their ordinary retry behavior.
- Unusable credential pairs stop installation/preflight. Inspect existing credentials privately and retain their bytes; do not delete them or rotate tokens automatically. A missing server verifier can be rebuilt from the valid existing client token; other invalid pairs require owner recovery.
