# v0.1.7 source-revision and cross-device evidence follow-up

The v0.1.6 audit fixes passed independent Codex and Claude reviews, four-platform CI and Windows deployment. Subsequent real owner edits and a new upstream import exercised two additional cases. This follow-up preserves that release and its evidence; it does not rewrite the earlier review results.

| ID | Verified trigger | Required behavior |
| --- | --- | --- |
| R19 | A topic retains an earlier claim after its source changes; the exact earlier quote is absent from the current note and the topic's shared source-hash map has advanced | Preserve the historical claim, recover its actual prior source revision, verify exact historical bytes and quotation, and distinguish verified historical evidence from an unresolved integrity issue. Retain per-claim origin metadata for future publications. |
| R20 | Real Git checkouts produce CRLF on Windows and LF on Linux for identical committed text; the receipt's canonical part hash is unchanged but raw whole-file SHA differs | Reuse only the exactly matching canonical source-part proof and unchanged claim fingerprint. Bind the local completion ledger to the current raw file SHA. Keep raw SHA checks for concurrent-write protection; never normalize or rewrite original notes to force a match. |
| R21 | A real local partial clone fetched a missing promised blob when the newer lazy-fetch environment switch was removed to model the documented Git 2.38 interface | Explicitly deny all Git transports inside historical evidence reads. A missing local blob stays unverified; normal maintenance fetch/push is unaffected. |
| R22 | A direct owner/editor exclusion arrives during fresh input/output/plan persistence; the just-created unused plan wrongly gains a durable-recovery exemption, including after interruption | Supersede unused excluded plans even if saved. Recheck policy after persistence and before note writes. Permit excluded recovery only when a fully validated plan has an actual changed derived note at its planned hash. Preserve partial publication and saved history without another model call. |
| R23 | Several legacy claims reparse the same actual historical topic headers after the verification deadline, even when Git blobs are cached | Parse metadata once per fetched revision, cache per-blob origin indexes, and check the cooperative deadline before parsing/indexing/traversing history. Reuse verified cached proofs; keep unverified budget-exhausted claims visible. Never expose cached raw metadata in public proofs. |

R19 was observed on the Windows vault after the existing sync service accepted an owner-authored upstream revision. It did not overwrite local edits: the local source blob still matched the common ancestor. The prior quotation remains subject to exact historical-source verification, rather than being silently accepted because metadata says it existed.

R20 was reproduced using real disposable Git clones under Windows and native Linux. Six positive cases failed before the fix on each OS; six changed-content/tamper/write-protection cases already passed. The test evidence is retained privately on the maintainer. Hosted CI and fresh-clone verification must confirm the final candidate separately.

R21 used an actual `file://` partial-clone fixture without network traffic. Before the transport guard, removing the newer lazy-fetch switch let the helper fetch a promised object. The fixed helper keeps the object missing and preserves repository configuration/pack state; a separate explicitly file-only control proves the object was otherwise retrievable. This simulates the older documented interface rather than claiming a Git 2.38 binary was executed.

R22 was independently reproduced in four synthetic cases on candidate `902d4bd`, and the owner-edit ordering was separately confirmed by the implementer. Normal MCP policy edits share the writer lock; direct editor saves can bypass it. A private plan file alone is no longer publication progress. Recovery requires actual changed derived bytes matching a fully validated plan, including a replacement that occurred before journal progress was saved. Unchanged planned files do not qualify.

R23 was identified by Claude and independently reproduced against actual Git history by Codex and the implementer. A modest seven-revision/eight-claim fixture performed 63 topic-header parses before caching and seven afterward; an expired warm-cache pass performed 56 before and zero afterward. An independent thirteen-revision/six-claim reproduction performed 78 historical parses after expiry. These measurements establish repeated work and the missed deadline checks; they do not claim an actual production capture timeout occurred. The 15-second budget is cooperative between bounded operations: an already running Python operation is not preempted. Verified positive proofs may be reused after expiry without new parsing.

## Evidence boundaries

Historical verification must use actual source bytes, not a receipt assertion alone. Git object reads must not invoke text conversion or filters. Bounded lookup failure, missing history, altered source bytes, a missing quote or changed claim text/source/quotation must remain visible. Existing fingerprint semantics permit historical status transitions while preserving the evidence. A verified historical quotation does not establish a present-day fact or factual entailment.

Per-part completion is distinct from whole-file byte identity. Equivalent line endings or an edit outside an unchanged canonical part must not cause that unchanged part to be compiled again. A changed part must remain pending even when it still contains a previously quoted sentence. Original raw source hashes and historical publication receipts remain preserved.

## Acceptance gates

1. Reproduce both behaviors before the fix. Retain positive and negative regressions with real Git checkouts and source revisions.
2. Pass the complete Windows suite, native Linux probes and four-platform hosted CI without dependency upgrades.
3. Independently review a frozen final candidate with Codex and Claude Code. Fix every qualifying finding, rerun affected checks, then repeat the final review.
4. Verify a fresh tagged checkout, all manifest hashes and installed application hashes. Deploy through the verified new updater while preserving device identity, auth, ownership and original notes.
5. Verify the observed historical quotation against its real prior revision, zero unresolved lint issues, fresh native/client MCP calls, rejection boundaries, cold restart, current source-part proofs and existing-service sync.
6. Apply the owner's explicit selective-source decision. Report excluded temporary source files/parts separately from completed eligible work; verify their original bytes remain unchanged and searchable. Current eligible parts must have exact verified proofs with zero pending/failed parts, receipt issues and worker attention. Invalid policy must stop compilation, and a policy change must not let an uncommitted stale packet publish newly excluded sources.

The owner requested download-based Mac/Linux onboarding. Physical login, reboot, two-device capture synchronization and GUI/Cowork sessions remain explicit device acceptance checks. No second compiler schedule, sync owner or broad conflict-merge policy is introduced.

## Owner-directed source selection

On 2026-10-15 the owner clarified that the new handoff/research import is temporary session material and does not need bulk permanent wiki compilation. A strict shared policy enumerates the intended exclusions; the original files, retrieval index and historical publications remain available. Selection does not use broad keywords or mutate source frontmatter. Concise meaningful milestones go through the existing capture-to-daily workflow. This is an intentional scope decision, not a claim that excluded work was compiled.

## Primary sources

- [Git attributes and end-of-line conversion](https://git-scm.com/docs/gitattributes): Git can store LF while checking out CRLF, and merge drivers have explicit temporary-file contracts.
- [Git cat-file](https://git-scm.com/docs/git-cat-file): read exact object contents; filters and text conversion are separate operations.
- [Git log](https://git-scm.com/docs/git-log): retrieve path-specific revision history with explicit bounds.
- [Git 2.38 environment controls](https://git-scm.com/docs/git/2.38.0): an explicit empty `GIT_ALLOW_PROTOCOL` list denies transport protocols even when the newer lazy-fetch switch is unavailable.
- [Python bytes and decoding](https://docs.python.org/3.12/library/stdtypes.html#bytes.decode): byte identity and decoded text identity are different checks.
- [Python JSON interoperability](https://docs.python.org/3.12/library/json.html#standard-compliance-and-interoperability): input size needs a bound; duplicate object keys and non-finite numbers need explicit rejection when a strict configuration contract is required.
- [Python monotonic time](https://docs.python.org/3.12/library/time.html#time.monotonic): use elapsed monotonic differences for the cooperative verification budget.
- [PyYAML safe loading](https://pyyaml.org/wiki/PyYAMLDocumentation): metadata remains parsed with the existing safe loader; this change caches results without changing dependencies or the YAML loading contract.

Observed final review, CI and deployment receipts are recorded only after those gates pass.
