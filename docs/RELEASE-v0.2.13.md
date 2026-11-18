# v0.2.13 release candidate

v0.2.12 was prepared as a candidate, installed on two owner devices from its exact commit and never published. v0.2.13 is that candidate plus the corrections its independent review asked for. A device on the v0.2.12 candidate updates like any other.

- One-command setup resumes a matching interrupted initial installation with the recorded device identity, credentials and options. A first maintainer publishes the ownership note before setup finishes. If another device already owns the notes, setup stops and keeps the recovery record.
- The six-line self-test bounds its MCP handshake and reports recognized local adapter startup failures. The report keeps raw adapter diagnostics private.
- macOS setup finds Homebrew Python 3.12 in its formula directory even if another installation owns the global links. Process inspection tolerates an absent previous-release Python. The retained checkout is still needed for recovery.
- Windows service cleanup verifies exit on its retained handle when Task Scheduler and the controller stop the same process concurrently.
- Wiki lint spends its existing historical-evidence time budget on Git history work.
- The local service checks for missed Markdown changes and unfinished vector work after startup and every 15 minutes after an index worker completes. It repairs bounded sets of affected entries and verifies current coverage afterward. A clean index needs no embedding work. This applies to maintainer and capture devices.
- Initial and manual indexing count committed vector shards even when a pass finishes between samples. A manual full rebuild continues with incremental passes so a second full reset cannot discard that progress.

The setup matrix covers fresh and repeated setup, adding a client to a partial configuration, a capture-to-maintainer transition, and a v0.2.11 upgrade on Windows and macOS. Native service jobs exercise offline edits with preserved size and timestamps, file moves, and a note that needs more than one vector shard. They also run the six-step MCP self-test. The hosted Ubuntu setup job exercises client configuration with service registration disabled.

## Corrections after the review of the v0.2.12 candidate

- **The background repair and the maintenance pass take turns.** The repair holds the maintenance lock as well as the index lock, and the supervisor starts neither beside the other; a maintenance pass that is running or due goes first. Maintenance links milestones and synchronizes with Git, so the notes no longer change under a repair through those managed paths.
- **A repair acts only on a folder that still matches its scan.** The inventory is read again before any change and again before vectors are built; if it differs, the rest is left to the next pass. A milestone saved by an assistant during a repair is the remaining unmanaged change, and it is caught the same way.
- **Sources of a removed note are refreshed through the same screening as every other file,** and only if the current scan found them eligible. A source in a folder that is ignored now, linked, offline or over the size limit is never opened.
- **A repair cannot outlive its budget.** Cancelling it cannot stop model work already handed to a thread; 60 seconds after the 240-second budget the process writes a receipt with `RepairDeadline` and ends itself, which releases its locks. An open database write is rolled back by SQLite.
- **A resumed initial setup keeps the first run's choice about the service.** A setup begun with `--no-service` does not register or start a service when it is resumed with the plain command, and a resume that newly asks for `--no-service` is refused as a different install.
- **Wiki lint:** a current-source read that happens inside the legacy fallback no longer spends the history budget.

## Boundaries to review

- **An empty notes inventory is never repaired.** A notes folder that is unavailable looks the same as one whose notes were all deleted, and removing every index entry on that evidence would cost a whole rebuild. Entries of a folder that really is empty stay until a manual `index retry`.
- **The repair loads the upstream indexer's configuration as every index run does.** If that file carries a key from an older upstream version, the upstream loader rewrites it in the runtime's `state` folder and leaves a `.bak` beside it. Nothing outside the runtime's `state` folder is written.
- **After a deadline exit the receipt is the only record;** the pass that was cut off repeats its batch at the next turn.
- **At service start the repair waits for the first maintenance pass,** so a device whose index needs repair is repaired a few minutes later than in the v0.2.12 candidate.

Use the [setup guide](SETUP-GUIDE.md) for commands. Qualification applies to the tested commit and platform. Owner-device acceptance, fresh named assistant sessions and reboot checks have separate receipts.
