# v0.2.9 candidate - the maintenance log no longer fills up

Status: candidate preparation, 16 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

Found on an owner Mac after its restart check on v0.2.8: every acceptance line passed, but `health` reported `maintenance: needs attention` with "Operational log rotation is deferred: active logs are preserved and may continue growing." The Windows maintainer had shown the same line. The cause is in the product, not on the devices.

## The defect

The supervisor started the background maintenance pass every fifteen minutes with its standard output appended to `logs/service-maintenance.log`. The pass prints its whole result, including the review list, which it has already saved to `state/maintenance-status.json`. On the Mac that was about 69,000 bytes per pass (622 review rows), so the log passed 5,000,000 bytes in under a day. From then on every pass set the status to "needs attention", and nothing ever made the log smaller: maintenance deliberately never truncates or renames a log that another process may be appending to. The acceptance check did not show this, because it read only the Git line of the maintenance state.

## What changed

1. **The pass no longer writes its result into the log.** The supervisor starts it with standard output discarded and only error output appended to the log. The result is still saved to `maintenance-status.json` and shown by `health`; the `brain_maintenance.py` command prints it as before when run by hand.
2. **A pass that needs attention leaves one short line in the log.** Handled failures, such as a Git synchronization that needs review, were only in the printed result. The command now also writes one line of at most 2,000 characters to error output (time, status, stage, Git state, error) whenever its status is "needs attention". The saved status is overwritten by the next pass; this line is the lasting record.
3. **An oversized maintenance log is set aside under the maintenance lock.** Before starting a pass, the supervisor renames a `service-maintenance.log` over 5,000,000 bytes to `service-maintenance.log.previous`, replacing an earlier one, and only while it holds `state/maintenance.lock`, the lock every running pass holds. If the lock is taken, or the rename fails, the log is left as it is. Two files at most. The rule in maintenance is unchanged: it still reports an oversized `service-*.log` and still never touches one.
4. **The acceptance check has a `maintenance` line.** PASS when the last pass completed or is running, WARN with the reason when it needs attention.

A device that already has an oversized `service-maintenance.log` clears itself at the first maintenance start after the update, about fifteen minutes after the service starts.

## Boundaries to review

- **Only the maintenance log is handled.** `service-server.log`, `service-bootstrap.log` and `service-supervisor.log` are appended to as before and are never renamed, because no lock proves their writer is gone. They receive error output only and grow slowly (the server log on the Mac that showed this defect was 573,102 bytes after a month), but one of them over 5,000,000 bytes would again leave the device in "needs attention" until someone decides what to do with it.
- **A pass between start and lock.** A pass has its log open from the moment it is started and takes the maintenance lock a moment later. A pass left over from an earlier supervisor in exactly that moment could have its log renamed under it; on macOS and Linux it then writes its error output to the renamed file, on Windows the rename fails and nothing changes. No output is lost in either case.
- **Error output is still unbounded within one run.** A pass that printed megabytes of errors would grow its log until the next start sets it aside.
- **What is discarded is not read by anything.** It is the same data that `maintenance-status.json` holds.

## Known limits

- The limits of the [v0.2.8](RELEASE-v0.2.8.md) and [v0.2.7 notes](RELEASE-v0.2.7.md) still apply.
- `health` can show `initial_index_validation: needs review` from an earlier retry while `index status` and the acceptance check report the index as verified; the acceptance check uses the current status.

See [the setup guide](SETUP-GUIDE.md) section 8.
