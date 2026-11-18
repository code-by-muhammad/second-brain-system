# v0.2.12 release candidate

- One-command setup resumes a matching interrupted initial installation with the recorded device identity, credentials and options. A first maintainer publishes the ownership note before setup finishes. If another device already owns the notes, setup stops and keeps the recovery record.
- The six-line self-test bounds its MCP handshake and reports recognized local adapter startup failures. The report keeps raw adapter diagnostics private.
- macOS setup finds Homebrew Python 3.12 in its formula directory even if another installation owns the global links. Process inspection tolerates an absent previous-release Python. The retained checkout is still needed for recovery.
- Windows service cleanup verifies exit on its retained handle when Task Scheduler and the controller stop the same process concurrently.
- Wiki lint spends its existing historical-evidence time budget on Git history work.

The setup matrix covers fresh and repeated setup, adding a client to a partial configuration, a capture-to-maintainer transition, and a v0.2.11 upgrade on Windows and macOS. Native service jobs run the six-step MCP self-test. The hosted Ubuntu setup job exercises client configuration with service registration disabled.

Use the [setup guide](SETUP-GUIDE.md) for commands. Qualification applies to the tested commit and platform. Owner-device acceptance, fresh named assistant sessions and reboot checks have separate receipts.
