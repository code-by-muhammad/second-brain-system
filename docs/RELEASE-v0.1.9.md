# v0.1.9 corrective Windows candidate

Revision: 3 November 2026. This candidate corrects a Windows stop availability defect found during native v0.1.8 explicit-recovery testing. Production acceptance, publication and receiving-device deployment are separate steps. Full qualification of this exact release identity is pending. Do not infer acceptance from the version number or the earlier v0.1.8 CI run.

## Correction

An existing Windows process can retain the PID of an exited parent. If that PID is later reused by this runtime, the older process appears to be a possible descendant in a PID/PPID snapshot. The previous stop code requested a termination-capable handle before it could apply its creation-time exclusion. A protected older process could therefore make stop refuse indefinitely. The observed refusal occurred before scheduler End or signalling. Subsequent native evidence and source analysis support this as the likely incident cause; the specific originally denying process was not recorded.

The new controller reads creation time using a query-only handle first. It excludes an older, unrelated lifetime without requesting termination authority. A possible descendant must still grant a broad retained handle while the query handle remains open; their creation times must agree. Equal or unreadable births never authorize exclusion. Unexpected wait results refuse. Ancestry checks, handle cleanup, service locks and task restoration remain in force. No cached PID becomes signal authority.

The v0.1.8 tag and assets remain unchanged. Its failed explicit-recovery cleanup receipt remains failed/unverified. Corrective native cleanup of the isolated recovered v0.1.7 fixture does not retroactively qualify v0.1.8 or establish a complete v0.1.9 upgrade.

## Operator response

On a pre-End process-identity refusal, preserve the runtime, pending queue, transaction and receipt. Do not kill cached PIDs, clear a transaction, or repeat a completed recovery UUID. Inspect current lifetime evidence before choosing the recovery path. Access-denied metadata or unavailable birth-time reads can still cause a safe refusal; never turn a retry into an unverified kill.

For an approved Windows upgrade from v0.1.7 or a controlled-acceptance v0.1.8 installation, use the verified **new v0.1.9 updater**, launched with the retained recorded installed Python. Its exact checkout must be `<runtime>/releases/v0.1.9`. Do not use the old controller/updater, launch from the target venv being rebuilt, or pre-run `uv sync`. See [device setup](DEVICE-SETUP.md). Controller selection and automatic/explicit recovery must be verified on both upgrade paths before promotion.

Both forward paths require no active `state/service-transaction.json`. A hold created by v0.1.8 is sealed to its original v0.1.8 controller; v0.1.9 cannot recover or migrate it. If the old controller cannot stop safely, preserve the hold and original checkout for a separately reviewed incident recovery path. Do not patch the old checkout or edit snapshot identity to bypass this binding.

Automatic rollback or explicit recovery restores the old installed app and its routine service controller. A restored v0.1.8 runtime therefore still has the original stop-refusal defect for routine service commands; the fix applies to stops made by the v0.1.9 controller. Use the verified v0.1.9 updater for a later upgrade attempt; an unfinished recovery continues through its original recorded checkout and UUID.

## Qualification boundaries

The scoped corrective evidence includes the 64-case `windows-descendant-query-controls-r2.xml` report (the descendant-query and existing service-identity/control suites together), the separate `windows-descendant-native-pair.xml` launcher/birth-mismatch control, a read-only native inventory with all 12 handles closed, and one reviewed real product stop/conditional exact fixture-task deletion. These reports belong to the retained corrective handover, not files bundled in this source archive. The original failed receipt, correction, actual Claude reviews and native receipts are retained separately. No formal GitHub reviewer approval is implied.

The unchanged ownership limits remain material:

- Parent attribution uses live, birth-ordered PPID chains. Same-user processes deliberately assigned an owned parent, including through `PROC_THREAD_ATTRIBUTE_PARENT_PROCESS`, remain within that existing trust boundary.
- Descendant inspection skips an exited parent. Current supervisors use a kill-on-close Job for their managed descendants; legacy v0.1.7 has no such containment. Orphaned non-script children outside the observed live chain are therefore not covered by that descendant scan during a v0.1.7 migration.
- A possible descendant that is not older but refuses the termination-capable handle still prevents stop and an empty-runtime receipt. This preserves the refusal boundary for elevated or protected descendants.

Before promotion, bind the final commit/manifest to a fresh complete regression and dependency audit, native v0.1.7→v0.1.9 and v0.1.8→v0.1.9 upgrade/rollback/recovery evidence, selected-client invocations, and the team helper catalogue. Verify the tag, source ZIP and checksums after publication. Capture deduplication, real transport failure/crash, full-index equivalence and reboot are not established by the isolated cleanup. Receiving-device checks remain required.

Dependencies and the existing static-token/local-FastEmbed security profile are unchanged. The raw audit is not clean; its exact source-bound FastMCP exception expires **12 November 2026 UTC inclusive**. Complete fresh qualification within that window or obtain a separately reviewed dependency qualification; this candidate does not extend it. The installed runtime does not enforce that deadline automatically. See [SECURITY.md](../SECURITY.md).

This candidate retains the Windows-only mutating setup/update/recovery guards. Mac/Linux remain on accepted v0.1.7 until their new native lifecycle is qualified. The owner will execute receiving-device updates; publishing this software is not a claim that those machines have been updated. No compiler ownership, source exclusions, original-note policy, recurring schedules or note synchronization ownership changes are included.
