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

Two pre-existing manual-repair gaps are also closed: unquoted origin dates, malformed explicit catalog titles and unsafe/non-Markdown claim sources now receive controlled integrity attention during context preparation or topic merging. Missing titles retain their path fallback. Notes and pending work remain preserved; evidence-based repair and an explicit retry resume from a fresh snapshot. Safe historical Markdown paths remain supported even after the original note is removed.

Unused pending context is refreshed when its stored topic snapshots change, including catalog additions or deletions. This closes a pre-existing resume gap that could bypass repair preflight and waste a model request. Saved artifacts remain preserved; actual validated partial publication still repairs from its original output without another call.

Lint now reports malformed topic titles and JSON/UTF-8 claim metadata with controlled field/page/claim diagnostics. Rendering stops for malformed catalog/source-record fields without retry strikes. If an older source becomes undecodable or oversized, publication uses bounded current reads and exact historical Git evidence; unavailable proof requires owner repair and explicit `wiki retry`. The compiler never guesses an encoding or rewrites the source.

Existing derived claim metadata and catalog titles now pass the established credential screen before fresh or saved-context model requests. A possible credential stops for owner repair with a controlled field diagnostic; ordinary API-key discussion remains allowed.

Decoded structured claim metadata receives the existing credential screen before JSON escaping can obscure it. Invalid topic frontmatter or wiki_kind now stops unused compilation with a controlled page/field diagnostic; validated partial publication can still repair without another model request. Impossible source dates keep their full original evidence in the queue with unknown metadata date, while strict topic parsing requires owner repair. Quoted date-like strings remain literal.

Deep YAML and alias-expanded context now fail within explicit parser/JSON bounds. Direct-editor changes remain protected by the exact before image consumed during planning; unreadable derived auxiliary notes require owner repair. Malformed-origin diagnostics contain only controlled fields. Fully verified actual legacy partial publication can finish saved writes without another model request, while unused plans remain screened and lint retains unresolved metadata issues. The existing conservative credential detector also screens source/catalog/output filenames before future work; filtering preserves originals and retrieval.

Malformed explicit YAML scalar tags now obey the same controlled parser contract as invalid dates/deep YAML: strict derived-topic parsing requires owner repair, while non-strict source metadata falls back without losing body evidence. Maintenance continues to Git synchronization on these malformed inputs; valid explicit tags and aliases remain supported.


Topic discovery now ignores normal hidden editor artifacts without reading them and preflights canonical category/slug paths, including case-only renames. Unsafe paths stop with a safe locator/path hash; no filename disables the existing credential screen. Strict maintenance review flags malformed derived metadata while capture linking and Git synchronization continue. Relinking validates all topic headers and stages serializations before changing navigation.

The compiler automatically rotates its active compilation Log near 1 MB into a SHA-named archive containing every previous raw byte. Normal plans stay v2; rotation uses a validated v3 descriptor and fixed 64 KiB streaming, including oversized legacy history. Archives form a linked history chain; original notes and daily notes are unaffected. Durable partial replay spends no additional model call. Archive-only creation cannot override owner exclusions, and missing/corrupt private recovery input requires review. Private completed spool cleanup preserves pending data and permanent archives. These operations use the existing locking/fsync/atomic-write discipline, without claiming OS-level CAS against arbitrary direct editors.

Shared scalar aliases are measured once per value and counted per expanded use. A full private packet has a distinct 2,000,000-character operation cap; an over-limit catalog reports Index/packet rather than a claim error. Lint validates paths before copying diagnostics, and unrepresentable owner Source-record metadata stops with serialization attention without dropping data or retry strikes.

Archive hash/UTF-8/descriptor integrity is checked when creating or recovering a rotation. Permanent archive Markdown syncs through the existing Git service. Ordinary lint does not periodically rehash the entire history; archives and the compilation Log are never source/topic/model context.

Catalog discovery also uses bounded derived reads and skips case-renamed Log/archive history without loading it. Normal raw topic hashes and strict parser checks remain unchanged; oversized topics receive controlled content attention.

Maintenance also skips compiler Log/archive history before reading it, bounds other derived-header inspection, and streams exact original source hashes. Malformed/oversized metadata and source growth/read failures remain review flags while capture linking and Git synchronization continue. The worker’s archive guarantees and original source bytes are preserved.

Maintenance public diagnostics now screen source/page names and fresh Git errors before formatting. Health re-screens legacy caches read-only, omits unchecked extra attributes and retains safe operational fields/counts. Withheld page names use a generic locator plus opaque path SHA; original notes and private evidence remain unchanged. Safe ordinary paths and API-key setup guidance are preserved.

Explicit malformed typed timestamp mappings also receive controlled parser handling, including constructor TypeError; valid explicit tags retain their established behavior.

Verified saved publication plans can recover from error state after archive-collision repair without a new model request. Unsafe unused legacy targets are superseded with all old artifacts preserved, then a clean generation can request a replacement. Actual partial owner-CAS conflicts retain the queue hold and now identify the safe affected page and repair action; no automatic owner overwrite or partial-plan discard is introduced.

The existing Git service now streams exact SHA-named compiler archives up to 64 MiB and independently checks the pinned staged object. The compiler enforces that same capacity before content reads, spooling or paid calls. Normal note limits are unchanged. Local history bytes stay exact; Git can normalize EOL on another checkout, and only exact raw or calculated CRLF-to-LF staged bytes are accepted. See the device guide for capacity and saved-plan repair.

Daily aggregation isolates missing/invalid templates, bounded-read failures and failed writes into per-capture review flags, preserving originals and attempting the remaining maintenance/Git phases. Git's own invalid-note safeguards still apply. Display fields are escaped to prevent marker/heading injection; exact marker-plus-capture-link identity preserves genuine and legacy idempotency. Daily owner edits are checked against the consumed raw before image and remain untouched on conflict, within the existing cooperative writer limits.

Maintenance review now recognizes the compiler's LF/CRLF/CR and optional-BOM source representations through bounded streaming after a raw-hash mismatch. Real content changes remain flagged, source files stay unchanged and publication CAS remains raw.

Public wiki diagnostics also re-screen legacy worker cache fields and optional receipt filenames. Unsafe locators use Index plus an opaque path SHA; pending attention rows expose only validated known identities. Invalid receipts are read within the existing 2 MB note limit and do not erase valid neighboring proof. Private caches, original receipts and pending plans remain unchanged.

Historical source reads ignore local Git replacement-object views, verifying canonical committed objects instead. The repository's replacement references are retained, ordinary Git synchronization is unchanged, and missing or altered historical proof remains visible.

Owner-edited ordinary archives now synchronize under the normal 2,000,000-character guard, retaining strict streaming/staged validation; oversized history remains hash-bound. Daily marker/link ambiguity requires owner reconciliation without duplication. Raw Git diagnostic paths are screened before escaping and withheld locators have a documented local SHA lookup. Unused plan conflicts request fresh generation while actual semantic partial publication retains saved-output repair. Corrupt durable input stops with controlled recovery attention before any model call. See the device guide for exact limits and owner steps.
