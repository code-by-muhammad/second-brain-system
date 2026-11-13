# v0.2.5 candidate - macOS 27 service readers and listener inventory

Status: candidate preparation, 14 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are pending. This note is not publication, deployment or physical-device acceptance.

v0.2.4 corrected one macOS 27 refusal and was published before the complete service inspection had been run on a real runtime. Three further refusals followed on macOS 27.2 arm64, each at the updater's preflight, before any hold was created. v0.2.4 must not be used to update a Mac on macOS 27. This candidate corrects all three:

1. **Nested service blocks.** `launchctl print gui/<uid>/<label>` now contains `LWCR = { "reqs" => { ... } }`. The reader counted only blocks that open with `= {`, so the inner closing brace ended the service block early and the rest was refused as trailing data. Blocks that open with `=> {` are now counted. Their content is still ignored, and unbalanced output still refuses.
2. **Homebrew Python homes.** An environment built on Homebrew Python records the prefix `bin` directory as its `pyvenv.cfg` home, not the framework `bin`. The framework identity check now also accepts a home whose interpreter of the same name resolves to the selected framework interpreter. A home that resolves anywhere else still refuses.
3. **Hidden netstat table.** On macOS 27.2 `/usr/sbin/netstat -an -p tcp` exits cleanly with no output when the caller is not a system binary, so listener inspection refused every lifecycle step. Only in that case the reader now takes the calling user's own listeners from `/usr/sbin/lsof -nP -iTCP:<port> -sTCP:LISTEN -Fptn`, with a strict grammar and the same protected-tool check. Where netstat prints its table, behaviour is unchanged.

Security boundary of the third change: on the lsof path, listeners that belong to other users of the Mac are not visible, and the port is never probed by bind or connect to find them. Such a listener is therefore reported as absent rather than refused as foreign, and one that coexists on another address of the same port (for example a wildcard bind) no longer causes a refusal. Credentials are still only sent to a listener that is positively verified as the runtime's own process, and a start against an occupied port fails at bind. The loss is diagnostic precision on a multi-user Mac, not a new credential path.

Evidence: grammar fixtures for each change, including malformed variants that must still refuse; and the complete `service inspect`, `service status` and `health` commands run from this source against one real macOS 27.2 arm64 runtime (v0.1.7, Homebrew Python 3.12), which now return results. The native Mac lifecycle harness has not run on macOS 27 and CI runs macOS 15, so stop, install, start, rollback and recovery on macOS 27 remain unqualified until one real update is recorded.

Carried forward unchanged from v0.2.4: `opentelemetry-api` constrained to 1.45.2 for GHSA-mf5v-w3w3-f6cg; the FastMCP exception for GHSA-vqqp-x5qm-5qp4 reviewed to 2026-12-12 (FastMCP 4.1.2 fixes it and must replace the pin before that date); the v0.2.3 Windows compiler correction.

Known limits carried forward: the updater reports only "Verified controller refused ..." without the refusal reason; hooks, the Claude Code instruction block and the bundled skill are not installed by default; an update refreshes only client entries that already exist; a client file that is a symbolic link stops the update at preflight; there is no local-only profile.

Use the verified new checkout at `<runtime>/releases/v0.2.5` with the installed device's recorded retained Python. Do not manually provision the target venv or patch an installed app. Require no outstanding hold and keep fresh private backups.

See [device setup](DEVICE-SETUP.md), [verification boundaries](DEVICE-TESTING.md), [security limits](../SECURITY.md) and the [v0.2.4 notes](RELEASE-v0.2.4.md).
