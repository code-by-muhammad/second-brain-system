# Verification and device acceptance

Revision 21 November 2026. v0.2.16 declares Windows x64, Mac ARM64/Intel and Linux x64 source support. Check each exact candidate commit, tree and release-manifest digest against its current qualification receipts. Owner installation and reboot require separate acceptance.

The v0.2.2 qualification records its legacy v0.1.7 and controlled v0.1.8 forward, automatic rollback and explicit recovery paths. Its 15 scoped outcomes and their corrective receipts remain historical evidence for v0.2.2. Keep later candidate results with their own source identity. [Earlier maintenance release scope](RELEASE-v0.2.5.md)

Any forward path requires no active service transaction. A v0.1.8 hold cannot be migrated to v0.2.16: recovery is bound to its original checkout, and a failed old-controller stop needs a separately reviewed recovery path. Preserve the hold; do not change snapshot identity to make a test pass. After rollback, routine service commands use the restored old controller; a later upgrade attempt must use the verified new updater.

## Automated regression boundary

Windows lifetime controls test trusted-birth/foreign-PID refusal, distinct gone-to-member reuse, duplicate notices, denied/missing membership evidence, unsignaled handles and contradictory accounting. The actual nested-Job probe separately qualifies membership on the tested Windows build. Neither establishes reliable notification delivery or a finite kernel cleanup bound. Missing lifetime proof keeps caller locks and needs scoped operator diagnosis; it must never be called a successful timeout or empty-count acceptance.

The default pytest suite uses disposable synthetic roots outside the checkout and real HOME, an explicit `SB_TEST_SANDBOX` plus child `--basetemp`, private client/config/temp paths, local-only Git fixtures, offline model flags and a process guard. The guard always refuses Scheduler/launchd/systemd mutations, uv provisioning, production identities/paths and service-capable inline/module routes. There is no `--run-native` escape.

The Linux CI suite loads a checksum-pinned model only for `tests/test_index_real_cli.py`. That file runs `index retry` through the real CLI, the index worker, Basic Memory and FastEmbed on one synthetic note, to keep the same-key and growth shard cases verified end to end. It needs the model files and never downloads them. Set `SB_TEST_MODEL_CACHE` to a FastEmbed cache folder that contains `models--Qdrant--bge-small-en-v1.5-onnx-Q`; the tests copy it into the sandbox and only read the original. An installed runtime's `<runtime>/models` is such a folder. To make one on a developer machine or in a CI step that runs before pytest and has network access, use the locked project environment: `uv run --locked python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='<folder>', threads=1)"`, then pass `SB_TEST_MODEL_CACHE=<folder>` in the pytest environment next to `HF_HUB_OFFLINE=1`. When the variable is unset or the model is absent, the seven real-CLI cases are skipped. They include five shard regressions and two runner-deadline tests. A skip is not a pass: a run that claims these regressions must show them as passed. They are skipped on Windows, where the runner is qualified by its native job, and take about a minute each on one core.

Windows and Linux CI run the full guarded suite with a private sandbox, disabled plugin autoload, explicit `pytest_asyncio.plugin`, and a JUnit result. Checkout credentials are disabled. Run the four pinned native dependency profile checks independently of service acceptance.

Hosted Windows and macOS run fresh setup and separate retained v0.2.11 and v0.2.15 upgrades. Both upgrade lanes require an idle predecessor, authenticated candidate health, verified explicit index retry and positive Codex checks. Hosted Ubuntu checks the fresh service-free setup. Linux without systemd uses a non-root container account, a private passwd home, isolated PID/network namespaces and a pinned, checksum-verified embedding model. Separate scenarios exercise the mixed-build v0.2.15 forward path, post-health rollback, live legacy-worker deferral and first registration. The systemd user-unit qualification remains a separate native receipt.

Inspect failures, exact source hashes, skips and async results. No raw environment/credentials, private notes or installed SQLite should enter the test fixtures or shared report. A test guard is a tripwire, not an OS security sandbox.

CI uploads only its source identity, synthetic JUnit, exported runtime requirements, raw dependency audit and separate profile-check output, including after failures where a report exists. The SHA-pinned artifact action retains them for 30 days; missing reports remain visible, not inferred passes. Keep the final curated handoff beyond that hosted retention window. No runtime environment, owner inputs or private runner logs are uploaded.

Local fixtures scrub ambient proxy/certificate/auth overrides before imports and use private model/config homes. Python subprocess and asyncio routes refuse external inference and shell wrappers except an exact sandbox junction fixture; the wiki model boundary is refused unless explicitly mocked. Python socket connections/DNS are limited to loopback, and known real-owner credential/config paths are refused in child arguments/environment. This is not an OS network or filesystem sandbox: native extensions/children require their own isolation controls. Actual synthetic MCP/proxy controls disable environment and registry proxy routing. The synthetic Windows MCP server uses a private Job and verified cleanup; this transport test and mocked listener controls do not establish production native service ownership. POSIX two-peer rename cases remain skipped on Windows and are a retained qualification gap.

The audit gate reconciles the exact locked runtime graph for the selected host. It retains raw findings and would separately apply only an exact, source-bound and time-limited exception recorded in [the security profile](../SECURITY.md); v0.2.16 records none. Missing/unscanned packages, other advisories, protected source changes and expiry fail the gate. LiteLLM exclusion, provider/reranker refusal, static-token routing, native lock contention and the patched embedding/model behavior need their own tests; package resolution is insufficient.

## Native Windows fixture proof

Native service qualification is a separately reviewed artifact harness, not the default pytest suite or an automatic CI gate. It uses a unique non-synced synthetic boundary, one positively absent UUID task with ZERO triggers, a non-production random port, private environment/data, bounded own children, retained birth-verified handles and exact cleanup. It measures actual install/start, authenticated health/read, writer refusal, verified stop, explicit-stop persistence, external End/Job cleanup, real stdio proxy refusal of a foreign listener without a request, and exact UUID fresh-start/finish archival.

Record the actual source map/driver hashes, version, task absence, empty port/tree, source/live metadata invariance and any failed earlier runs. A success on pre-bump source does not validate a later release automatically. Tagged fresh-install/forward-upgrade/rollback/interruption/recovery and subsequent-upgrade receipts are separate requirements.

The old native sleeper fixtures were retired because a sleeper cannot satisfy current ownership/heartbeat/authentication and their cached-PID/substring cleanup was unsafe. Their historical source is preserved in the audit archive/Git history; they are not replaced by fake passing E2E tests.

## Index proof

Use only synthetic notes and a copied local model cache. Keep original model bytes and source bytes/mtimes unchanged; never copy owner auth/index/vault inputs. Check starter coverage, an approved late import, preserved-size/mtime stale detection, normal retry control, full repair, original-row chunk/vector coherence and actual indexed FTS/vector retrieval under the configured threshold. Query setup can write derived DB tables, so label that phase as a writer; the read-only status phase must not repair the main DB. Record SQLite sidecars separately. A Python network detector is not a native-code network sandbox.

Counts alone cannot establish all-file coverage. File fallback/read alone cannot establish indexed retrieval. Unsupported metadata remains reviewable rather than silently normalized or discarded.

## Native macOS and Linux qualification requirements

Keep each owner device on its accepted installation until the exact proposed release/platform is qualified and its deployment is approved. The independently reviewed native harness must use exact owned process identities, real launchd GUI/session or systemd user-bus evidence and safe retained cleanup. On Mac, prove bootout absence and re-bootstrap/start in the intended user's GUI domain on each architecture claimed. On Linux, prove the actual escaped ExecStart, daemon-reload/enable/start/stop with an existing user bus; a required CI bus missing is a failure, not a skipped pass. Do not mutate an owner's session or use cached PID plus command-line substring matching to manufacture acceptance.

## Target device remains a separate gate

Follow [device setup](DEVICE-SETUP.md). Verify each requested named client with actual tool results, the existing maintenance-owned Git round trip, and reboot/login only when actually performed. A library import or test on this Windows host cannot establish Raza's repaired PC, another team member's device, Desktop Chat/Code/Cowork/web connectivity or reboot acceptance. Preserve failures and publish actual concise receipts only.
