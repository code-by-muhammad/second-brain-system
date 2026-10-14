# v0.1.4 — durable receipts and bounded topic growth

Completed batch input, output, runner logs and schemas still expire after 30 days. The compact `journal.json` publication receipt now remains alongside the durable job database, so a milestone's local source reference continues to resolve. Journals contain publication paths, hashes and timestamps, not raw model inputs.

The cleanup regression test simulates a 40-day-old completed batch and verifies that its journal, source notes, pending work and database receipt survive. v0.1.3's catalog revision, evidence-source schema and genuine model-failure checks remain unchanged. The setup guide pins v0.1.4; upgrading preserves local identity, credentials, roles and queues.

The model now receives the full existing claim count for each catalog topic even when only the last twelve claims fit its context. Compilation instructions direct it to create linked narrower topics near the existing 120-claim cap; the deterministic cap and historical evidence remain protected. A regression test verifies that a 30-claim topic reports its full budget while supplying only twelve context claims.
