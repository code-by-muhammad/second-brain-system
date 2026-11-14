# Second Brain setup guide (v0.2.6)

One guide for a new device and for a device that already runs Second Brain. One command does the setup. This guide tells you what to prepare, what the command asks, and how to read its result.

You can run the command yourself, or give this file to the assistant on your computer. Any assistant that can run terminal commands will do. Say:

> Read this whole guide, then set up or update my Second Brain on this device with the one command in section 3. Ask me only for choices you cannot inspect. Do not skip a checklist line. At the end show me the acceptance check with every line as PASS, FAIL, WARN or MANUAL, and what you actually saw.

A chat without terminal access cannot do this.

## 0. What is proven for this version

| Path | Status |
|---|---|
| Fresh install, Mac, Windows x64, Ubuntu 24.04 | {{FRESH_STATUS}} |
| Update an existing Mac on macOS 27 | {{MAC_UPDATE_STATUS}} |
| Update an existing Windows device | The updater code path is unchanged since v0.2.3. No v0.2.6 device record yet. |
| Notes kept on one device, no GitHub | Supported. Sync shows "off" and the check passes. |

Release identity. The command in section 3 checks the commit for you. These values let you check by hand:

- Tag `v0.2.6`, commit `{{COMMIT}}`
- `release-manifest.json` SHA256 `{{MANIFEST_SHA256}}`
- Software repository: `https://github.com/code-by-muhammad/second-brain-system` (public, no account needed)

If this copy still shows `{{...}}` placeholders, it is the source file. Use the copy attached to the release.

## 1. Rules

- [ ] Inspect before changing anything. Never guess a path, an account or a role.
- [ ] One operation at a time. Do not run the command twice in parallel.
- [ ] A refusal is a result. Read the reason and fix that cause. Do not rewrite a command to get around it.
- [ ] Never print, copy or commit tokens, credential files, databases or note contents.
- [ ] Never delete, reset or force anything to make a step pass.
- [ ] Never copy another person's notes or settings to start a new brain.
- [ ] If the owner says the notes must not go to GitHub, that rule wins over everything here. Use `--local-only`.

## 2. Before you start

- [ ] Git is installed. Mac: `xcode-select --install`. Windows: `winget install --id Git.Git -e`.
- [ ] Mac only: Homebrew Python 3.12 is installed: `brew install python@3.12`. Other Python builds on a Mac cannot run both the service and the search index, and setup refuses them.
- [ ] uv 0.9.16 or newer is installed. Mac and Linux: see https://docs.astral.sh/uv/getting-started/installation/. Windows: `winget install --id astral-sh.uv -e`.
- [ ] Open a new terminal after installing either tool. Windows: ordinary PowerShell, not Git Bash.
- [ ] Decide where the notes live. It must be a plain local folder, not inside OneDrive, iCloud or Dropbox. A folder that does not exist yet is created for you with a blank starter layout.
- [ ] Obsidian is optional. Install it from obsidian.md if you want to read and edit the notes there.
- [ ] Updating an existing device: close other assistant sessions first (see section 5).

You do not need a GitHub account. Sync is an optional extra (section 6).

## 3. The one command

Mac and Linux:

```text
curl -fsSLO https://github.com/code-by-muhammad/second-brain-system/releases/download/v0.2.6/setup.sh && echo "{{SETUP_SH_SHA256}}  setup.sh" | shasum -a 256 -c - && sh setup.sh
```

Windows PowerShell:

```text
iwr https://github.com/code-by-muhammad/second-brain-system/releases/download/v0.2.6/setup.ps1 -OutFile setup.ps1; if ((Get-FileHash setup.ps1 -Algorithm SHA256).Hash -ne '{{SETUP_PS1_SHA256}}') { throw 'setup.ps1 does not match this guide' }; powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The first part downloads a small script and checks it against the hash in this guide. The script then downloads the exact release, checks its commit, and starts the setup. A release folder kept from an earlier run must match that fresh download exactly. If any check fails, nothing runs.

What it asks on a new device:

- [ ] The notes folder. Default: `~/Documents/Second-Brain` (Windows `C:\Second-Brain`).
- [ ] The role, only when the notes folder already exists. The first device that uses a set of notes is the `maintainer`. Every additional device is `capture`.
- [ ] A final "Continue?" after it shows what it will do.

What it does without asking:

- [ ] Builds its own Python environment. Nothing is installed into your system Python.
- [ ] Reads the system timezone.
- [ ] Downloads the local search model (about 130 MB). Search runs on your computer.
- [ ] Registers a background service that starts at login.
- [ ] Connects every assistant it finds: the connection, the session hooks, the instruction block and the skill. Claude Code and Codex are found automatically.
- [ ] Runs the acceptance check and prints the result.

Options, added after `setup.sh` or `setup.ps1`:

| Option | Use |
|---|---|
| `--yes` | Accept every default without asking. For an assistant running it. |
| `--vault "<folder>"` | The notes folder. |
| `--role maintainer` or `--role capture` | Set the role yourself. |
| `--clients "claude-code,codex"` | Choose the assistants. Keep the quotes on Windows. Add `claude-desktop` for the Claude Desktop chat app, which must be closed while you run this. |
| `--no-hooks` | Leave out the Claude Code session hooks. |
| `--timezone Europe/Berlin` | Use this timezone instead of the system one. |
| `--local-only` | The owner wants no Git remote for these notes. Setup refuses to start if the notes folder has one, and the check fails if one appears later. |
| `--check` | Only run the acceptance check. Use it after a restart. |

A runtime folder other than the default: set `SECOND_BRAIN_ROOT` before the command. Default: `~/.local/share/second-brain` (Windows `C:\Second-Brain-System`).

Running the same command again is safe. On a device that is already set up it adds whatever is missing, tries to start the service if it is not running, and runs the check.

## 4. The device is done when this passes

The command ends with a list like this:

```text
PASS   service health: status running, version 0.2.6
PASS   Claude Code connection: points at this runtime
PASS   Claude Code instructions: one block matching this release
PASS   Claude Code skill: ...
PASS   Claude Code hooks: SessionStart and Stop, once each
MANUAL claude-code: fresh chat: ...
0 failed. Automatic checks passed; finish the MANUAL lines.
```

- [ ] No line says FAIL.
- [ ] Every WARN line is understood. "search index: needs review" right after a new install only means the first index is still running. Run the check again after a few minutes.
- [ ] MANUAL, each assistant: open a new chat and ask it to call `brain_context`, search for a note you know, read it, and save one test capture. All four must work.
- [ ] MANUAL, restart: restart the computer, log in, wait a minute, then run the command again with `--check`. The service must be running without help.

A device with a FAIL line is not set up. Do not use it as if it were.

What each piece is, if you need to look:

| Piece | Where it lives |
|---|---|
| Connection (`second-brain` MCP entry) | `~/.claude.json`, `~/.codex/config.toml`, Claude Desktop config |
| Claude Code hooks | `~/.claude/settings.json` |
| Instruction block (between the two `SECOND-BRAIN` marker lines) | `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, or the shared rules file those load |
| Skill | `~/.claude/skills/second-brain/`, `~/.codex/skills/second-brain/`, or the shared copy those link to |

If your assistant files are links to one shared rules or skills folder inside your home folder, the setup follows the links and keeps one copy. It does not add a second block. A shared file outside your home folder is never written. Setup tells you it was left alone, and the check shows whether it is current.

## 5. Updating a device that already runs Second Brain

Use the same command. It finds the installed version, updates it, then adds anything the old setup was missing.

Before:

- [ ] The service is running on the old version. A stopped service is refused.
- [ ] The notes folder has no half-finished Git work (no staged files, no merge in progress).
- [ ] Close Claude Desktop and other assistant sessions. A settings file that changes during the update is left alone and reported. Running the command again finishes it.
- [ ] Keep the folder of the version you are updating from. The service still needs it until the next update. Older release folders can go.

During:

- [ ] Wait for the result. Do not interrupt it. The service stops only after every check has passed.
- [ ] "Update deferred before a hold was created": nothing changed and the old version is still running. The reason is printed with it. Fix that cause and run the command again.
- [ ] "Upgrade failed; previous runtime health verified": it rolled back and the old version is running. Keep the files under the runtime's `state` folder and get the reason before a second attempt.
- [ ] "Transaction outcome is uncertain": stop. Do not start, stop or delete anything. Ask the maintainer of this software.

After:

- [ ] The acceptance check passes (section 4), including a new chat in each assistant.
- [ ] The search index says verified. If the check shows a WARN for it after ten minutes, see section 7.

## 6. Optional: sync the notes with a private GitHub repository

Skip this if the notes stay on one device. Add it when you want a backup or a second device.

First device:

- [ ] Install the GitHub CLI and sign in as the owner of the notes: `gh auth login --web`, then `gh auth setup-git`.
- [ ] In the notes folder: `git init -b main`, set `git config user.name` and `git config user.email`, then review and commit the files.
- [ ] `gh repo create "<ACCOUNT>/<NOTES REPO>" --private --source "<NOTES FOLDER>" --remote origin --push`. Confirm on GitHub that it is private.
- [ ] Nothing else. The service notices the remote named `origin` and syncs on its own. The check line "notes sync" changes from "off" to "synced".
- [ ] The notes need a repository of their own. A notes folder that only sits inside another repository is never synced through it.

An additional device for the same notes:

- [ ] `gh repo clone "<ACCOUNT>/<NOTES REPO>" "<NOTES FOLDER>"` first.
- [ ] Then run the one command with `--vault "<NOTES FOLDER>" --role capture`.

Rules:

- [ ] One brain has exactly one maintainer. Every additional device is capture.
- [ ] Never sign in with someone else's GitHub account, and never push one person's notes to another person's repository.
- [ ] Do not run your own sync loop or scheduled push. The service is the only one that syncs.

## 7. Troubleshooting

| What you see | What to do |
|---|---|
| "Missing: git" or "Missing: uv" | Install it (section 2), open a new terminal, run the command again. |
| "This Mac has no Python 3.12 that can run both the service and the search index" | Run `brew install python@3.12`, then the command again. |
| "Release check failed" | Stop. The download is not the reviewed release. Keep the folder and tell the maintainer of this software. |
| "The installer stopped: ..." | The text after the colon is the reason. Keep everything as it is. Do not run the installer a second time over a half-finished install without reading the reason. |
| "Client file is linked" | An assistant settings file (connection or hooks) is a link. Replace it with a regular file. |
| "Left alone: ... leaves the home folder" | An instruction or skill file is a link that points outside your home folder. Setup never writes there. Put the shared copy inside your home folder, or make the file a regular one, and run the command again. |
| "Left alone: ... loaded from more than one file" | The instruction block exists twice. Remove one copy so exactly one BEGIN and one END marker remain, then run the command again. |
| "Existing second-brain entry belongs to another or unknown runtime" | An older Second Brain connection points somewhere else. Remove that one entry from the assistant's settings, then run the command again. |
| "Claude Desktop is open" | Close it, or leave `claude-desktop` out of `--clients`. |
| FAIL on a connection, hooks, instructions or skill line | Close that assistant, run the command again, then open a new chat. |
| The assistant cannot see the `brain_` tools | It was open during setup. Restart it and open a new chat. |
| Port 8765 is in use | If Second Brain already holds it, this device is already installed. If another program holds it, stop and ask. |
| "search index" stays on WARN | Run `"<DEVICE PYTHON>" "<RUNTIME>/app/brain_cli.py" index retry --full --stall-seconds 300` and wait. It repeats by itself while it makes progress and then says `verified` or what is still wrong. On a very large vault this can take over an hour. Judge the result with `index status`. |
| "notes sync: needs attention" | Read the text after it. Usual causes: no network, the GitHub sign-in expired, or other uncommitted files in the notes folder. |
| "notes sync: paused" | The text after it says what blocks the sync: a conflict between two devices, files you staged yourself, or other uncommitted files. Nothing is lost. Clear that in the notes folder with Git and the sync resumes. |
| After a restart the service is not running | Mac: `launchctl list | grep second-brain`. Windows: the task `SecondBrainLocalService`. Linux: `systemctl --user status second-brain.service`. Then run the command with `--check`. |

`<DEVICE PYTHON>` is the `python` value in `<RUNTIME>/device.json`.

## 8. Never

- [ ] Editing `device.json` or files under `state` by hand.
- [ ] Copying a database, token, index or Python environment from another device.
- [ ] Two maintainers for one set of notes.
- [ ] Deleting the folder of the version you just updated from.
- [ ] Running the upgrade test scripts in the repository on a device you use. They are for disposable test machines.
- [ ] Sharing files from the runtime folder. They hold your local credential.
