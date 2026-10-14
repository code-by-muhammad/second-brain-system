# v0.1.5 — restore Mac service registration on restart

Stopping the macOS service unloads its LaunchAgent. Starting now checks whether the job is registered, restores it from the existing per-user plist when needed, and then kickstarts it. Already registered agents are not bootstrapped again. This repairs the documented stop/start acceptance step and adapter recovery after a manual stop.

Two protocol regression cases cover registered and unloaded jobs. A macOS-only native test uses a unique temporary LaunchAgent and synthetic sleeper to exercise actual install, stop and start with a new PID. It cleans up its own job and skips explicitly if the runner lacks a logged-in GUI domain. No private notes, model requests or wiki schedule are involved.

The setup guide also fixes manifest-check quoting for built-in Windows PowerShell 5.1; the former command reproduced a NameError and the corrected command passed. Both Mac/Linux and Windows instructions pin v0.1.5. Earlier parser, catalog revision, source-schema, receipt-retention and topic-budget fixes remain unchanged.

Per-user agents depend on a logged-in user session. Reference: [Apple's launchd programming guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html). Owner-device login/reboot and client acceptance remain part of onboarding.
