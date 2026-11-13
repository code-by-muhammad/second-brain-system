# v0.2.4 candidate - macOS launchd inventory and dependency maintenance

Status: candidate preparation, 13 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are pending. This note is not publication, deployment or physical-device acceptance.

On macOS 27.2, `launchctl print gui/<uid>` lists some unrelated Apple agents with a short parenthesized last-exit tag such as `(jt)` in the status column. The Mac registration reader accepted only `-` or an integer there and refused the whole services inventory with "Native GUI services inventory grammar is unknown". Every service command inspects that inventory, so a forward update from v0.1.7 stopped at preflight with "update deferred; no hold created" and left the old runtime untouched. The correction accepts a short lowercase tag in parentheses in that column and keeps refusing every other unknown form. The same reader runs after the stop and during rollback, which is why the correction is required before any Mac update rather than worked around.

The regression controls cover tagged rows with and without the owned label and five malformed tags that must still refuse. They are pure grammar fixtures, not native receipts. The corrected reader was also run against one real macOS 27.2 arm64 inventory and found the installed service. The native Mac upgrade fixture uses the production label and remains for disposable hosts only; it was not run on an owner device.

Dependency maintenance: `opentelemetry-api` is constrained to 1.45.2 for GHSA-mf5v-w3w3-f6cg, published on 13 November 2026. The resolver moved `opentelemetry-sdk` to 1.45.2 and `opentelemetry-instrumentation` and `opentelemetry-semantic-conventions` to 0.66b2 with it. No other dependency version changed. The scoped FastMCP exception for GHSA-vqqp-x5qm-5qp4 was re-reviewed after its 12 November deadline and renewed to **12 December 2026** as a bridge; FastMCP 4.1.2 fixes that advisory and a separately tested upgrade should remove the exception before the new deadline. The four host profiles were rebound to the final source bytes and the changed dependency versions. This is not a clean-audit claim.

This candidate also carries the v0.2.3 Windows compiler temporary-working-directory correction unchanged.

Known limits carried forward: the updater reports only "Verified controller refused ..." without the refusal reason; hooks, the Claude Code instruction block and the bundled skill are not installed by default; an update refreshes only client entries that already exist; a client file that is a symbolic link is refused; there is no local-only route. These are planned for a following release and are not changed here.

Use the verified new checkout at `<runtime>/releases/v0.2.4` with the installed device's recorded retained Python. Do not manually provision the target venv or patch an installed app. Require no outstanding hold and preserve fresh private backups, identity, selected clients, source policy, maintenance ownership and the existing schedule. A real device update follows its exact qualified pins and the owner's dated approval. Guide/PDF/helper assets are separate versioned deliverables, not source evidence.

See [device setup](DEVICE-SETUP.md), [verification boundaries](DEVICE-TESTING.md), [security limits](../SECURITY.md) and the retained [v0.2.3 source history](RELEASE-v0.2.3.md).
