# v0.2.14 release candidate

v0.2.14 adds a Linux service for hosts that have no systemd, and adds Grok Bot as an assistant next to Claude and Codex. Mac, Windows and Ubuntu 24.04 with systemd are unchanged: they keep the same service definition and the same service program they had in v0.2.13.

## Upgrade from v0.2.13

Put this release at `<runtime>/releases/v0.2.14` and tag that checkout `v0.2.14` (the setup command checks the tag). `<runtime>` is the folder that already contains `device.json`. On the Grok Bot computer that is `/home/box/.local/share/second-brain`. From any directory:

```text
uv run --no-project --python 3.12 python -I -B /home/box/.local/share/second-brain/releases/v0.2.14/setup_device.py --root /home/box/.local/share/second-brain --yes --role capture
```

On another device, use that device's runtime in both places:

```text
uv run --no-project --python 3.12 python -I -B "<runtime>/releases/v0.2.14/setup_device.py" --root "<runtime>" --yes
```

Then:

```text
uv run --no-project --python 3.12 python -I -B "<runtime>/releases/v0.2.14/setup_device.py" --root "<runtime>" --check --selftest
```

A host whose PID 1 is systemd, and every Mac or Windows device, still refuses the update when its service is absent or explicitly stopped. Start that service with the old release first. A Linux host whose PID 1 is not systemd, and whose service file was never written, does not have a service to start: this update registers the user-space service instead of refusing. An explicitly disabled user-space service is still refused.

- **Mac** keeps the same launchd job and the same job definition. Nothing about login or restart changes.
- **Windows** keeps the same scheduled task and the same task definition. Nothing about login or restart changes.
- **Ubuntu 24.04 with systemd as PID 1** keeps the same user unit, `second-brain.service`, and the same requirement on the user bus. A host that fails that check still fails it, with the same refusal. Do not pass `--no-service` to get past it.
- **Linux whose PID 1 is not systemd** (a container, tini, or a machine with no systemd) could not register a service on v0.2.13. This update installs the user-space service instead. The notes folder, the device record and the credentials are left where they are. The vault is not deleted, moved, reset or overwritten.

A device that was installed with `--no-service` stays that way until you run setup again without that flag. Capture devices still sync only through the service. One maintainer per vault is unchanged.

## Linux without systemd

When PID 1 is not systemd, setup writes `~/.config/second-brain/second-brain.service` (the same unit text the systemd backend uses) and starts a session leader. That leader runs `brain_supervisor.py`, and if the supervisor exits it stops the rest of that session and starts it again after 10 seconds. A stopped service stays stopped.

After the host comes back, the service is started again by whichever of these exists:

- `~/.config/second-brain/boot-start`, also sourced from a marked block in `~/.profile`
- `~/.config/autostart/second-brain.desktop` (a desktop login)
- an `@reboot` user crontab line, when `crontab` works

The Grok Bot computer has no crontab, does not run `~/.profile` at boot, and has no autostart runner. `<runtime>/bin/second-brain-mcp` therefore runs the same boot check (`brain_linux_user_service.py boot`) before it connects. That check does nothing when the service is already running or was stopped on purpose, and starts it when it is enabled and down. A host whose PID 1 is systemd is left to systemd. `setup` then `--check --selftest` is the acceptance check. Health must say `running` and the six self-test lines must pass.

Indexing uses one ONNX thread and one note at a time, and an update lowers those knobs in an existing `state/config.json` without changing the other keys. A few thousand notes should stay well under a few gigabytes. A server that was killed is reaped before the supervisor looks at the port, so a leftover listener from that kill is not treated as a second owner.

Removing the boot hooks does not delete the definition. `brain_linux_user_service.py` records them so a later setup can replace the same files. The notes and the runtime are not part of that removal.

## Grok Bot

Grok Bot does not read a local MCP config file. `~/.grok/config.toml` belongs to a different product and is not written. `~/agent-data` may be a symlink. Setup follows it when every hop stays inside the home folder, the link is owned by this user or by root, and the directory it names is owned by this user and not world-writable. A link that leaves the home folder is refused for Grok Bot only; the other assistants are still written. Setup, when it finds `~/agent-data/workflows` or is given `--clients grok`, writes two files:

- `~/agent-data/workflows/second-brain/SKILL.md` — the same instruction text Claude and Codex receive, with the skill front matter those assistants already use
- `~/agent-data/workflows/second-brain/mcp-registration.json` — one server, `second-brain`. `command` is `<runtime>/bin/second-brain-mcp`, `args` is `[]`, and `env` is `{}`. That path stays the same when the release changes; the script it runs is rewritten to the current Python.

The bot adds that one server to the account. The check refuses a second server name and a spec that points at another runtime. It also starts the registered command when the service is already running and requires the six tools. Grok Bot has no hook file; the shared instruction block is the session-start and capture behaviour. Claude Code, Codex and Grok Bot are generated from `docs/AI-PROTOCOL.md`, so the three copies cannot drift.

To remove only this assistant, delete `~/agent-data/workflows/second-brain` when its spec points at this runtime. Setup writes it again the next time Grok Bot is selected.

Use the [setup guide](SETUP-GUIDE.md) for commands. Qualification applies to the tested commit and platform.
