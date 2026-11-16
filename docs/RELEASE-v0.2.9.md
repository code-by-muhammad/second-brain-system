# v0.2.9 candidate - the maintenance log no longer fills up

Status: candidate preparation, 16 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

Found on an owner Mac after its restart check on v0.2.8: every acceptance line passed, but `health` reported `maintenance: needs attention` with "Operational log rotation is deferred: active logs are preserved and may continue growing." The Windows maintainer had shown the same line. The cause is in the product, not on the devices.

## The defect

The supervisor started the background maintenance pass every fifteen minutes with its standard output appended to `logs/service-maintenance.log`. The pass prints its whole result, including the review list, which it has already saved to `state/maintenance-status.json`. On the Mac that was about 69,000 bytes per pass (622 review rows), so the log passed 5,000,000 bytes in under a day. From then on every pass set the status to "needs attention", and nothing ever made the log smaller: maintenance deliberately never truncates or renames a log that another process may be appending to. The acceptance check did not show this, because it read only the Git line of the maintenance state.

## What changed

1. **The pass no longer writes its result into a log.** The supervisor starts it with standard output discarded and only error output kept. The result is still saved to `maintenance-status.json` and shown by `health`; the `brain_maintenance.py` command prints it as before when run by hand.
2. **A new log name, and the old file is retired.** Error output of a pass now goes to `logs/service-upkeep.log`. Nothing appends to `logs/service-maintenance.log` any more, and maintenance no longer counts that one file in its size check. The old file is not renamed, cut or removed by anything: it stays where it is and the owner may delete it. No live log is ever touched, so the earlier guarantee holds without any new locking.
3. **A pass that needs attention leaves one short line in the log.** Handled failures, such as a Git synchronization that needs review, were only in the printed result. The command now also writes one line to error output whenever its status is "needs attention": time, status, stage, error type, error and Git state, each cut to 400 characters on its own, as one complete JSON record. The saved status is overwritten by the next pass; this line is the lasting record.
4. **The acceptance check has a `maintenance` line.** PASS when the last pass completed or is running, WARN with the reason when it needs attention.

A device that already has an oversized `service-maintenance.log` reports a completed pass at the first maintenance run after the update, about fifteen minutes after the service starts, unless another log is also over the limit.

## Boundaries to review

- **The retired file stays on disk.** On the Mac that showed this defect it is 10 MB. It is never cleaned up automatically.
- **A pass from the previous version** that is still running during the update writes its last output to the old file, as before.
- **The other logs are unchanged.** `service-server.log`, `service-bootstrap.log`, `service-supervisor.log` and the new `service-upkeep.log` receive error output and are never renamed or cut. They grow slowly (the server log on that Mac was 573,102 bytes after a month), but any one of them over 5,000,000 bytes leaves the device in "needs attention" until someone decides what to do with it. A pass that failed at every run would add one line of at most about 2,500 bytes each time, which reaches the limit after roughly three weeks of continuous failure.
- **What is discarded is not read by anything.** It is the same data that `maintenance-status.json` holds.

## Known limits

- The limits of the [v0.2.8](RELEASE-v0.2.8.md) and [v0.2.7 notes](RELEASE-v0.2.7.md) still apply.
- `health` can show `initial_index_validation: needs review` from an earlier retry while `index status` and the acceptance check report the index as verified; the acceptance check uses the current status.

See [the setup guide](SETUP-GUIDE.md) section 8.
