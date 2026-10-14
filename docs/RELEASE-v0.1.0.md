# v0.1.0

This release adds the missing evidence-linked wiki workflow to the existing local Obsidian MCP foundation and separates portable software from shared notes.

- Common configuration, installer and commands on Windows, macOS and Linux; OS-specific startup adapters only.
- Incremental source/section queue, bounded Codex compilation, exact quote checks, source/page hashes, retained uncertainty, source records, topic index and compilation history.
- One explicit maintainer; other devices capture/retrieve and synchronize their own local vault copy.
- Serialized Git reconciliation merges independent offline edits after a conflict-free preflight; conflicting same-note edits pause without discarding either version.
- Tagged upgrades, complete source/dependency hashes, native environments, local config/token preservation and rollback path.
- Completed private model artifacts expire after 30 days; durable receipts, notes and revision backups are retained.

The acceptance suite has 37 tests including a real authenticated MCP server against synthetic data. The CI matrix covers Linux x64, Windows x64, Apple Silicon and Intel Mac and audits native dependencies. Windows live retrieval, initial model compilation, MCP reconnection and service stop/start were also exercised. Actual installation/login/reboot on the owner's Mac/Linux machines remains a deployment task; no remote access to them was supplied.

Initial source backfill runs in bounded batches; installing this release does not mean every historical source has already been compiled. The scheduled Windows responsibility requires its host and desktop app to be available. Codex login/quota and model-provider data handling apply. No Dot integration, public endpoint, new hosted database or Syncthing migration is included.
