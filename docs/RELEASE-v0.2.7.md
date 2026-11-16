# v0.2.7 candidate - wiki with Claude or Codex on every platform, role change, Windows update corrections

Status: candidate preparation, 16 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

v0.2.6 made the setup assistant-neutral but left the wiki compiler tied to a native `codex.exe` on Windows. A device whose owner has only Claude, or a Mac, could not build wiki pages. The first Windows update of v0.2.6 on an owner device also showed three faults. This candidate corrects both.

## What changed

1. **Wiki with Claude or Codex.** `wiki configure-model --executable <path>` accepts the `claude` or the `codex` program. The assistant is the selected program's own name; nothing else is stored. Claude is called as `claude -p --output-format json --json-schema <schema> --tools "" --strict-mcp-config --setting-sources "" --disable-slash-commands --no-session-persistence --system-prompt <fixed text>`, with the same prompt and packet on standard input that Codex gets. Its one result object goes to a private `model-reply.json` in the batch folder; the `structured_output` inside it becomes `output.json` and then passes the unchanged shape, evidence and publication checks. A result that reports an error, or carries no structured output, publishes nothing. Sign-in and usage-limit messages from Claude are recognized as the existing "model authentication/quota requires attention" state. The Codex command is unchanged.
2. **Wiki on macOS and Linux.** The two Windows-only refusals are gone. On Windows the model still runs in the existing private Job. Elsewhere it runs in its own process group with the same 480-second deadline and 5,000,000-byte output bound, and the whole group is ended when the run finishes, times out or exceeds the bound. On macOS and Linux the selected path may be the usual install link (for example `~/.local/bin/claude`): the link is followed on every run, and the file it names is hashed and recorded in the batch, so an assistant that updates itself keeps working. Windows keeps refusing linked paths and launchers.
3. **One setup switch.** `setup_device.py --wiki claude|codex` finds that program on the device and records it. It calls no model and builds nothing; pages are built only by `brain_cli.py wiki run`.
4. **An installed capture device can become the maintainer.** `setup_device.py --role maintainer` on an installed capture device stops the service, writes `Control.md` naming this device (never over an existing file), sets the role in `device.json` and starts the service. It refuses when the notes already name another device and when an update is in progress. The other direction and a handoff between devices remain unsupported.
5. **No second Codex skill copy.** Codex also reads `~/.agents/skills`. The client plan wrote the skill only under `~/.codex/skills`, so a device that already had it under `~/.agents/skills` got a second copy and then failed its own acceptance check. The plan now keeps the existing copy current and creates no other.
6. **Service start window.** The supervisor reports readiness on a 20-second cycle and the controller waited 60 seconds, which left a new server about 40 seconds to listen. The first start of a new Python environment on Windows exceeded that once; the update rolled back cleanly and passed on the second attempt. The wait is now 180 seconds.
7. **Start failure reason.** "Startup readiness failed" now carries the type and text of the underlying error, so the updater's refusal line shows the cause.
8. **Guide.** One new section on the maintainer role and the wiki, the rule to run Windows commands from ordinary PowerShell and not from inside the packaged Claude desktop app, and three new troubleshooting rows.

## Boundaries to review

- **Note text leaves the device** only in `wiki run`, to the provider of the selected assistant, under the owner's own sign-in and plan. Setup never starts it.
- **Claude's isolation rests on its command-line flags** as observed in Claude Code 2.1.325: no tools, no user/project/local settings (so no hooks and no instruction files), no MCP servers, no saved session. A later CLI that changes those flags fails closed here only if it rejects them; the result is still validated against the notes before publication.
- **The JSON schema is passed to Claude on the command line.** It contains the packet's source paths, which are visible to other processes of the same user while the run lasts.
- **The non-Windows runner** ends the process group it created. A program that moves itself into another session escapes that; the Windows Job does not have this gap.
- **The role change** edits `device.json` outside the update transaction, with the service stopped. Like the first-install ownership step, it inspects `Control.md` and then creates it.

## Known limits

- The wiki with Claude was exercised on one Mac against a small disposable set of notes. No Windows or Linux device has built pages with Claude yet, and no macOS device has built pages with Codex.
- A Codex installed through npm on macOS or Linux is a script that needs `node` on the service's PATH. Use Claude, or a native Codex build, if a scheduled run cannot find it.
- The wiki is not scheduled by setup. The owner runs `wiki run` or creates a schedule.
- Changing the timezone of an installed device, turning a maintainer into a capture device, and moving the maintainer between devices are not supported.
- A packaged desktop installer is not part of this release.
- The limits listed in the [v0.2.6 notes](RELEASE-v0.2.6.md) that are not named above still apply.

See [the setup guide](SETUP-GUIDE.md), [device setup](DEVICE-SETUP.md), [verification boundaries](DEVICE-TESTING.md) and [security limits](../SECURITY.md).
