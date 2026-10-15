# v0.1.6 remediation and acceptance plan

This release addresses the Codex audit and the independent Claude Code Opus 5.5 review of v0.1.5 on 15 October 2026. The former had four findings, the latter fifteen; the null-tags defect overlaps, giving eighteen distinct items. The original reports and reproduction receipts are retained privately on the Windows maintainer under `tests/`. Original notes and historical claims remain authoritative.

## Finding ledger

Each row needs a regression or an explicitly qualified disposition before release. Passing tests alone do not establish that every client or physical device is configured.

| ID | Demonstrated trigger or qualified observation | Required behavior |
| --- | --- | --- |
| R01 | POSIX reuse dereferenced the venv interpreter symlink | Preserve the venv executable path through installation, upgrade and rollback |
| R02 | Invalid optional source metadata aborted maintenance before sync | Isolate the bad page, record a controlled diagnostic and continue safe maintenance/sync |
| R03 | Null/scalar tags aborted source inventory | Normalize optional tags without losing valid source parts |
| R04 | merge-tree exit 128 was called a conflict | Separate genuine conflicts from operational/minimum-version errors |
| R05 | Failed commit left sync-owned staging and prevented retry | Preserve owner staging and edits; safely recover only sync-owned staging |
| R06 | Empty local ledger recompiled already published sources | Hydrate only from verified shared per-part publication proof; explicitly migrate legacy local receipts |
| R07 | Maintenance failure left an old success status | Persist a fresh failure/partial status and timestamp |
| R08 | Bootstrap exceptions left indexing forever | Record terminal attention status and avoid endless automatic relaunch |
| R09 | Slow fetch/push held writer.lock beyond capture timeout | Release the writer lock for network I/O; retain serialization for index/worktree mutations |
| R10 | Owner edit or lock race charged every source with a failure | Preserve pending work and separate transient failures from invalid source/output failures |
| R11 | Source records linked every page in their batch | Derive links from actual claim citations; correct legacy cohort links deterministically |
| R12 | Sync refused invalid content without naming the note | Give a controlled path/reason; preserve the credential and invalid-content guard |
| R13 | 320 moderately long paths exceeded Windows argv | Supply literal paths without a single oversized argument list |
| R14 | Health embeds an unbounded stale-review list | Bound output and include totals/truncation; prior client overflow was a conditional risk, not a reproduced failure |
| R15 | Atomic client rewrite changed POSIX mode 0600 to 0644 | Preserve restrictive permissions and protect secrets before writing their first byte |
| R16 | Worker retry made a new model call instead of replaying partial publication | Resume saved validated packet/output with durable per-file proof and DB-finalization recovery |
| R17 | Uncited input parts were silently marked processed | Require explicit per-part published/skipped/deferred disposition and evidence; omitted parts remain pending |
| R18 | Reinstall changed file-tool vault but retained old retrieval path | Reject an existing installation's vault relocation safely; use an explicitly separate native runtime/index for a new vault |

Additional hardening protects compiler-owned wiki/control pages from the general derived-page update tool. Directly imported dependencies are declared explicitly without upgrading their existing locked versions.

Review-driven regressions also cover Windows mixed-case wiki paths, URI-reserved characters in local SQLite paths, explicit deferred-part retry generations, malformed model JSON receiving bounded backoff, LF/CRLF/CR/BOM source-page repair, deterministic short-part skips and publication-plan integrity during replay. Existing Git tests use isolated runtime locks so concurrent review/test processes cannot interfere with one another.

The first full Claude follow-up found four further concrete defects: Windows non-UTF-8 subprocess JSON, unlimited repeated worker failures, ambiguous overlapping-quote fallback migration and malformed legacy claims crashing source-link derivation. Each is reproduced in disposable fixtures and must pass its fix, independent review and final release gates. UTF-8 regressions use real piped child processes; retry tests exercise actual `run()` calls, including a fourth attempted invocation. Legacy completion must remain unverified when preserved evidence cannot distinguish adjacent overlapping parts. A real Codex invocation in a synthetic-only vault also verifies strict disposition-schema acceptance, publication and portable proof; it is separate from live-vault acceptance.

Follow-up queue probes also reproduce a reachable v0.1.5 database/journal crash window and obsolete no-plan packets bypassing current completion/defer/attempt limits. Recovery preserves unproved legacy journals for explicit migration review, supersedes obsolete work without erasing inputs, and retains genuine durable-plan replay. A specifically attributed bad source must reach its own retry limit without blocking eligible peers through the global worker budget.

## Verification and review gates

1. Reproduce the old behavior in disposable fixtures, then keep a regression for the accepted fix. Never run synthetic Git experiments in the real notes repository.
2. Run the complete common suite and native imports on Windows, Ubuntu, Apple Silicon and Intel Mac. Exercise unique synthetic native service jobs, never the owner's registered service. POSIX installer tests verify venv-only imports and permissions. Failure-injection tests cover worker publication, queue, bootstrap, commit and rollback paths.
3. Review the stable final candidate independently with Codex and Claude Code, using the defect-first review-agent rules. Fix qualifying findings, rerun affected tests, and review the new final snapshot until no actionable findings remain. A changing snapshot or incomplete model run is not a passing review.
4. Verify the complete release manifest, dependency audit and explicit tagged CI. Original Markdown and device identity/auth must remain preserved during the Windows upgrade.
5. Migrate only legacy publication state justified by source/chunk hashes and current cited claims. Report unverifiable/uncited parts explicitly; never infer all-part completion from a Source record. Rebuild derived source navigation from actual citations. Keep a private migration receipt and backup.
6. Verify live authenticated tools, search/read, capture deduplication, rejection boundaries, service restart, clean wiki lint, publication receipts and existing-service Git sync. Do not start a competing sync or a second schedule.

Owner Mac/Linux client login/reboot and Claude Desktop/Cowork sessions remain device-specific onboarding checks; hosted CI and Windows checks do not silently close them.

## Primary sources

- [Python Path.absolute and Path.resolve](https://docs.python.org/3.12/library/pathlib.html): the venv executable must not be dereferenced to the base interpreter.
- [Python secure temporary files](https://docs.python.org/3.12/library/tempfile.html#tempfile.mkstemp): use restrictive creation and atomic replacement with deliberate permissions.
- [Git merge-tree exit status](https://git-scm.com/docs/git-merge-tree#_exit_status): distinguish successful merge, conflicts and execution errors.
- [Git pathspec input](https://git-scm.com/docs/git-add#Documentation/git-add.txt---pathspec-from-fileltfilegt): literal NUL-separated paths avoid shell parsing and Windows argv limits.
- [Microsoft schtasks](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks): native Windows test jobs use unique task identities and explicit cleanup.

Detailed observed acceptance results and review receipts are added only after the candidate actually passes them. No zero-risk or exhaustive semantic-accuracy guarantee is implied.
