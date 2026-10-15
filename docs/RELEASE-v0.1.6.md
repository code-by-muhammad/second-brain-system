# v0.1.6 — verified wiki publication, safe updates and resilient maintenance

This release addresses eighteen distinct items from the 15 October Codex and Claude Code audits. The evidence and acceptance gates are mapped in [the remediation ledger](REMEDIATION-v0.1.6.md).

## Wiki completion and recovery

Every selected source part now has an explicit published, skipped or deferred outcome. Published parts require exact evidence in that part; substantive skipped/deferred parts require a reason and exact quote. Empty or fewer-than-twelve-character parts receive a deterministic, hash-bound skip without a model call. Durable publication plans resume an interrupted batch from its saved validated output and verify each written file before finalizing completion. Revision/lock races preserve pending work without charging unrelated source parts. Invalid unattributed model output receives bounded batch backoff.

Shared derived `Wiki/Receipts/` pages bind current source/chunk hashes to preserved claim fingerprints. A new designated maintainer can verify these proofs and rebuild its local completion ledger. First upgrading the existing maintainer requires an explicit legacy dry run followed by reviewed migration; historical Source navigation alone does not establish completed parts. Source links are repaired from actual citations, preserving original notes and existing extra sections. General derived-page updates cannot replace compiler-owned wiki or Control pages.

## Installation, updates and maintenance

The installer preserves a POSIX virtual-environment executable instead of dereferencing its symlink, protects secret-bearing temporary files before writing, preserves private file permissions and rejects an existing runtime's vault relocation before mutation. The new updater preflights dependencies and client settings while the former service is healthy. It serializes the switch, snapshots software/device/client files, tests expected versioned health and restores the former registration and exact settings on failure. Notes, tokens and SQLite are never rolled back. Use the verified new updater and its candidate environment for the first v0.1.5 upgrade; see [device setup](DEVICE-SETUP.md).

Git fetch/push no longer hold the capture writer lock. Local worktree/index changes remain serialized; NUL pathspec input supports long Windows path lists. Failed commits recover only sync-owned staging, preserve user changes and expose controlled diagnostics. Operational merge errors are distinguished from actual conflicts. Malformed optional metadata is isolated so other maintenance and safe synchronization continue. Maintenance/bootstrap persist fresh terminal failure states. Health reports bound diagnostic lists and readonly SQLite paths handle URI-reserved characters.

## Acceptance boundaries

Regression suites use disposable notes, repositories, client homes and unique native service jobs. Hosted CI covers Windows, Ubuntu, Apple Silicon and Intel Mac with locked native imports and dependency auditing. Independent final reviews, CI run receipts and live Windows deployment receipts determine release readiness; this document does not replace those results.

Physical Mac/Linux login, reboot, two-device synchronization and each requested client session still require the documented onboarding checks. Claude Desktop/Cowork and web chat connectivity cannot be inferred from a working local CLI connection. The system retains one designated wiki maintainer and the existing sync/scheduler owners.
