# v0.1.4 — keep verified publication receipts after cleanup

Completed batch input, output, runner logs and schemas still expire after 30 days. The compact `journal.json` publication receipt now remains alongside the durable job database, so a milestone's local source reference continues to resolve. Journals contain publication paths, hashes and timestamps, not raw model inputs.

The cleanup regression test simulates a 40-day-old completed batch and verifies that its journal, source notes, pending work and database receipt survive. v0.1.3's catalog revision, evidence-source schema and genuine model-failure checks remain unchanged. The setup guide pins v0.1.4; upgrading preserves local identity, credentials, roles and queues.
