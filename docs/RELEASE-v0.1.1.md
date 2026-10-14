# v0.1.1

The initial real Windows upgrade exposed a startup race: a fixed ten-second health delay could trigger rollback before the new Python environment's service was ready. Its rollback also needed to stop the new supervisor before restoring the prior configuration. These issues were found during acceptance testing; source notes and credentials were preserved.

This patch polls authenticated health for the expected version with a bounded two-minute deadline and verifies health after rollback. Two regression tests cover delayed readiness, a wrong version and the deadline. The complete acceptance suite now contains 44 tests, with native MCP, library imports and dependency audits across Windows, Linux, Apple Silicon and Intel Mac.

Use this version for fresh installation and upgrades. v0.1.0 is retained as superseded history and a known prior installation; its original tag is not rewritten. All wiki, capture, sync, role, source-preservation and physical-device availability boundaries from the initial release remain in force.
