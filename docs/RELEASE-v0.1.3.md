# v0.1.3 — safely append to catalog topics outside the model context

Initial backfill exposed two additional worker defects. A model could return an existing catalog topic whose old claims did not fit the small context packet; publication then refused the update. Every catalog entry now carries its prepare-time SHA256. The compiler can append exact-source claims to that topic after verifying its unchanged revision, retaining all prior claims. Concurrent edits still stop publication.

The CLI echoes source text in private logs. A source mentioning rate limits could wrongly turn a later local validation error into an authentication/quota cooldown. Global cooldown classification now requires a failed model process and excludes the exact echoed model input. Genuine authentication/quota diagnostics still trigger cooldown; successful model output followed by a local validation failure does not.

The model output schema now restricts each new claim's source to the current batch's original source paths. Prior claims remain context and are retained by the compiler; their older source paths cannot be selected as evidence for a new claim. Deterministic exact-quote and full-source hash checks still run before publication.

The v0.1.2 frontmatter fix remains included. No queue, original note, identity, role or credential is reset by this upgrade. The device setup guide now pins v0.1.3. Tests cover catalog-only append with preserved old evidence, concurrent edit rejection, echoed rate-limit mentions and genuine auth/quota failures, in addition to the complete earlier suite.
