# v0.1.7 — portable evidence and selective wiki sources

Owner edits and equivalent Windows/Linux Git checkouts exposed two cases after the v0.1.6 audit release. This follow-up preserves original notes, raw write-protection hashes and historical receipts while making source-part completion portable and historical quotation checks explicit.

An unchanged canonical part can reuse its verified publication proof across equivalent line endings or unrelated edits elsewhere in the note. Changed parts, altered claim text/source/quotation and tampered proof data remain rejected. Historical status transitions retain their existing fingerprint semantics. Local completion binds to the current raw file hash; source writes still require exact concurrent-edit checks.

Historical claims retain their evidence origin. If a quotation is missing from the current source, verification must locate the actual earlier source revision and check its bytes and exact quotation. Verified historical evidence is reported separately; missing or unverified history remains an integrity issue. Historical verification does not promote a claim to a current fact.

Historical Git reads explicitly block transport protocols, including on the documented minimum Git interface. Missing promised objects remain unverified without being fetched. Normal maintenance synchronization keeps its existing fetch/push behavior.

The release remains gated on regression tests, independent Codex/Claude reviews, four-platform tagged CI, a fresh manifest check and live Windows acceptance. See [the follow-up ledger](REMEDIATION-v0.1.7.md) and [device setup](DEVICE-SETUP.md).

The shared `02-Areas/AI-Memory/Wiki-Source-Policy.md` allows explicit source paths or directory prefixes to be excluded from future compilation. The owner's temporary session handoff/research import remains intact and searchable; exclusion is reported separately from completed work. Existing historical claims and receipts remain preserved. Invalid policy blocks new compilation with an actionable error. Meaningful progress uses concise deduplicated captures linked into daily notes by the existing service.

The existing hourly compiler handles eligible new ingestion. Physical-device and client-specific onboarding still require their actual documented checks.

A review-discovered direct-editor policy race is repaired: unused plans cannot gain an exclusion exemption by being saved during the edit. Actual partial publication may recover from validated planned hashes without another model call; unused work is superseded with its artifacts preserved.

Historical metadata is parsed once per fetched revision and origin lookups are cached per blob. The cooperative time budget is checked before further parsing, indexing and history traversal, including cached history; verified cached proofs remain reusable. Public proof fields exclude private parsed headers and claim content.

Current-file proofs remain available independently of the optional history budget; new claim origins and unchanged-note lint stay correct after history expiry. Explicit malformed origins produce controlled integrity attention without silent rebinding or retry strikes, while legacy missing origins remain supported.
