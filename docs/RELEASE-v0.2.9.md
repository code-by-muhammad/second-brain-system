# v0.2.9 candidate - the maintenance log no longer fills up

Status: candidate preparation, 16 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

Found on an owner Mac after its restart check on v0.2.8: every acceptance line passed, but `health` reported `maintenance: needs attention` with "Operational log rotation is deferred: active logs are preserved and may continue growing." The Windows maintainer had shown the same line. The cause is in the product, not on the devices.

## The defect

The supervisor started the background maintenance pass every fifteen minutes with its standard output appended to `logs/service-maintenance.log`. The pass prints its whole result, including the review list, which it has already saved to `state/maintenance-status.json`. On the Mac that was about 69,000 bytes per pass (622 review rows), so the log passed 5,000,000 bytes in under a day. From then on every pass set the status to "needs attention", and nothing ever made the log smaller: maintenance deliberately never truncates or renames a log that another process may be appending to. The acceptance check did not show this, because it read only the Git line of the maintenance state.

## What changed

1. **The pass no longer writes its result into the log.** The supervisor starts it with standard output discarded and only error output appended to the log. The result is still saved to `maintenance-status.json` and shown by `health`; the `brain_maintenance.py` command itself prints as before when run by hand.
2. **An oversized service log is set aside by the one process that can do it safely.** Only the supervisor starts the server, the index bootstrap and the maintenance pass, and it starts each one only when the previous one is not running. Just before such a start, a log over 5,000,000 bytes is renamed to `<name>.previous`, replacing an earlier one, and a new log begins. Two files per log at most. The rule in maintenance is unchanged: it still reports an oversized `service-*.log` and still never touches one.
3. **The acceptance check has a `maintenance` line.** PASS when the last pass completed or is running, WARN with the reason when it needs attention.

A device that already has an oversized `service-maintenance.log` clears itself at the first maintenance start after the update, about fifteen minutes after the service starts. An oversized `service-server.log` is set aside the next time the server is started, for example by the update itself or a restart.

## Boundaries to review

- **The rename happens without a lock on the log.** It relies on the supervisor being the only starter of these workers and on the check that the previous one has ended. A server left over from an earlier supervisor that is alive but not yet listening would keep writing to the renamed file on macOS and Linux, and would make the rename fail on Windows; in both cases nothing is lost and the next start tries again.
- **Error output is still unbounded within one run.** A pass that printed megabytes of errors would grow its log until the next start sets it aside.
- **What was in the log is not read by anything.** The discarded output is the same data that `maintenance-status.json` holds.

## Known limits

- The limits of the [v0.2.8](RELEASE-v0.2.8.md) and [v0.2.7 notes](RELEASE-v0.2.7.md) still apply.
- `health` can show `initial_index_validation: needs review` from an earlier retry while `index status` and the acceptance check report the index as verified; the acceptance check uses the current status.

See [the setup guide](SETUP-GUIDE.md) section 8.
