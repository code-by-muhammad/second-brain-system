# v0.2.16 release candidate

Basic Memory stays pinned to **0.23.2**. The isolated index worker replaces the quadratic vector orphan cleanup with two project-scoped deletes in one transaction. It verifies the reviewed dependency and private API shapes before indexing. The bootstrap compares a fresh source digest between passes and keeps bounded time, progress and retry limits. An ordinary file edit or additional shard can be indexed in a follow-up pass.

## Upgrade and recovery

Verify the approved commit, tree and manifest digest. Place the matching, locally tagged checkout at `<runtime>/releases/v0.2.16`. Keep the previous checkout and the working interpreter recorded in `device.json`. For a registered device, the interpreter must match the existing enabled native service. The sealed predecessor catalog contains three exact v0.2.14 app maps and two exact v0.2.15 maps. An independently matching catalog map can qualify an installation whose retained checkout has different app bytes, a missing tag or a dirty worktree. Other supported predecessors use an exact verified retained checkout. An extra app payload or a missing or changed installed file refuses upgrade before the service hold.

Run the documented capture-device wizard from the verified checkout. [Setup guide](SETUP-GUIDE.md#5-updating-a-device-that-already-runs-second-brain)

```bash
uv run --no-project --python 3.12 python -I -B \
  /home/box/.local/share/second-brain/releases/v0.2.16/setup_device.py \
  --root /home/box/.local/share/second-brain --yes --role capture
```

The held transaction seals the old app, device, launcher, deployment artifacts, selected client images and affected Linux registration files. Rollback verifies native quiescence before restoring these images. It preserves and reports concurrent assistant-file edits. A conflicting mandatory artifact or unverified stop/withdrawal retains the hold for exact-UUID recovery. Keep the snapshot, private receipt and recorded controller interpreter.

Run manual recovery from the original verified v0.2.16 release folder.

```bash
cd "$verified_v0216_checkout"
"$controller_python" -I -B "$verified_v0216_checkout/update.py" \
  --root "$runtime" --recover --hold-id "$recorded_hold_id"
```

A live pre-v0.2.16 index bootstrap or separate-session worker causes deferral before native End. The old service resumes ordinary admission after unchanged-state proof and hold cancellation. Let that work exit, then repeat the wizard. A manually launched or unverifiable worker can also prevent a verified stop. [Recovery procedure](DEVICE-SETUP.md#recovery)

First service registration can run under the same hold on proven-unregistered non-systemd Linux. A version-changing `--no-service` is available for that same verified-absence case and ends with file integrity and service absence checks. Partial registrations require recovery or inspection. Existing systemd, macOS and Windows native service paths retain their own registration checks.

## Service and assistant behavior

On Linux without systemd, the service suspends new launches, drains the exact enrolled bootstrap and index worker by boot-relative lifetime, and verifies process and port absence before completing a stop. Managed drain and transient launch deferrals remain eligible for a later authorized attempt. Unknown listener ownership, multiple sockets and wildcard bindings block authenticated probing. `service inspect`, local `health` and setup check surface the blocked cleanup reason. The next eligible headless `boot` or explicit start rechecks the reason and can replace a lost keeper.

Linux and macOS use the stable launcher for Claude Code, Codex and Grok. Windows uses the recorded Python and stdio adapter. The adapter owns one cold-start deadline. Setup updates selected owned entries and hooks and preserves unrelated options. The service login hook preserves raw profile bytes and permissions, and desktop autostart quotes its executable argument. Held upgrades preserve existing crontab entries. Creation of a new `@reboot` hook is deferred for held upgrades, including first registration. Ordinary service installation can add the hook when `crontab` is available.

The ten search-memory limits apply to Basic Memory's effective environment. A valid lower setting is retained. Read-only startup preserves a current-schema config file. Basic Memory can still migrate legacy schema or explicitly save effective settings during operations that write its config.

## Index guard and repair

`index retry` prints JSON. Exit **0** with `status: verified` and `process_exit: 0` proves the pass verified. Exit **3** reports `index_worker_guard_refused` with the named `guard_reason`. The internal isolated worker uses exit **78** for a guard refusal and **75** when its release gate does not open. Use the installed device's recorded Python to run `app/brain_cli.py`.

For a guard refusal involving a missing or different dependency, first verify the matching release checkout and recorded interpreter. If the recorded interpreter is that checkout's in-place `.venv/bin/python` (or `.venv\Scripts\python.exe`), use the verified release and its validated base interpreter to repair that exact environment in a quiet maintenance window.

```bash
uv sync --locked --no-dev --project "$verified_release" --python "$verified_base_python" --reinstall
"$recorded_python" -B "$runtime/app/brain_cli.py" index retry
```

If the installer recorded an external Python, the checkout's default `.venv` would leave the recorded interpreter unchanged. Repair the external environment through its original provisioning method against the verified lock, or use the verified installer recovery procedure. For a linked or changed worker file, restore the exact verified application payload before retry. Preserve the database, model cache and private diagnostic history.

## Qualification and known limits

Ubuntu CI runs the guarded regression suite offline with a checksum-pinned synthetic test model. The Windows control tests exercise mocked Task XML and FILETIME semantics on Linux, and the macOS listener test uses mocked launchd semantics. Tests for bounded note reads observe the real no-follow descriptor on POSIX. The historical heartbeat fixture measures native service freshness. Controller isolation uses a copied POSIX venv interpreter. The two `.pyd` cases run on Windows, where those suffixes are importable.

Native scenarios require separate receipts for Windows and both macOS architectures, hosted Ubuntu without a service, Linux without systemd and the existing Ubuntu systemd user-unit lane. Release status remains `candidate_pending_external_receipts`. Owner-device rollout requires the exact candidate's scoped receipts and approval. [Verification and device acceptance](DEVICE-TESTING.md)

The Linux keeper appends to `logs/user-service.log` with no automatic size limit or rotation. A live child can retain that descriptor across a rename. Archive only after verified service and writer quiescence. Independent idle watchdog repair is deferred.
