# v0.2.15 release candidate

v0.2.15 applies a newer build of the same product line when the installed runtime does not match the release, and it points Claude Code and Codex at the same stable launcher Grok Bot already uses. Mac, Windows and Ubuntu 24.04 with systemd keep the service they already run.

## Upgrade from v0.2.14

Put this release at `<runtime>/releases/v0.2.15` and tag that checkout `v0.2.15` (the setup command checks the tag). Leave the v0.2.14 folder in place. `<runtime>` is the folder that already contains `device.json`. On the Grok Bot computer that is `/home/box/.local/share/second-brain`. From any directory:

```text
uv run --no-project --python 3.12 python -I -B /home/box/.local/share/second-brain/releases/v0.2.15/setup_device.py --root /home/box/.local/share/second-brain --yes --role capture
```

On another device, use that device's runtime in both places:

```text
uv run --no-project --python 3.12 python -I -B "<runtime>/releases/v0.2.15/setup_device.py" --root "<runtime>" --yes
```

Then:

```text
uv run --no-project --python 3.12 python -I -B "<runtime>/releases/v0.2.15/setup_device.py" --root "<runtime>" --check --selftest
```

A same-version rerun does not replace `app/` or `state/config.json`. If this device already reports v0.2.15 but the installed scripts do not match this checkout's manifest, setup stops and says so. It does not report success after refreshing only the assistant files. The forward update from v0.2.14 still runs when the installed scripts are an earlier build of v0.2.14 than the copy in `releases/v0.2.14`. An extra file under `app/` is still refused.

## Assistants

On Linux and macOS, Claude Code, Codex and Grok Bot each have one `second-brain` entry. `command` is `<runtime>/bin/second-brain-mcp`, `args` is `[]`. Codex keeps its own timeout and environment keys. An owned entry that still names the release Python and `app/brain_client.py` is rewritten to that launcher. Windows keeps the recorded Python and `app/brain_client.py`, because the launcher is a shell script.

The launcher runs the boot check when PID 1 is not systemd, then the client. The check is quiet: it does not print FastMCP's startup line.

Indexing limits in an existing `state/config.json` are lowered by this update: one embedding thread, batch size 1, and an index batch of 8. Other keys in that file stay.

Use the [setup guide](SETUP-GUIDE.md) for the rest of the command. v0.2.14's service and Grok Bot notes stay in [that release](RELEASE-v0.2.14.md).
