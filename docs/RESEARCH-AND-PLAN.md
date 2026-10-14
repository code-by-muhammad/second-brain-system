# Implementation decisions — 14 October 2026

The owner authorized implementation of the proposed wiki and multi-device plan, adding Linux support. Preserve existing Obsidian sources and local Basic Memory retrieval. Complete the missing wiki workflow before replacing retrieval.

## Evidence and design

- [Karpathy's original LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): raw sources plus a maintained wiki; ingest, query, lint; an index and compilation log. This project implements that pattern, not software released by Karpathy.
- [Basic Memory configuration](https://docs.basicmemory.com/reference/configuration): retain local SQLite and FastEmbed. Authoritative content stays in Markdown. A fresh-file fallback handles index delay.
- [uv project layout and cross-platform lock](https://docs.astral.sh/uv/concepts/projects/layout/): distribute pinned source and dependency metadata, create native environments per machine. Never synchronize a virtual environment or SQLite database.
- [Official Codex non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode): structured output and saved CLI authentication support the semantic compiler. The worker disables configured MCP/plugins and shell tools, uses a temporary working directory and read-only sandbox, and publishes through deterministic validation. Saved login and account quota are required; this is not a free unlimited LLM.
- [Apple LaunchAgents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html), [systemd user services](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html): only startup adapters vary by OS. Windows uses a limited interactive-user scheduled task.
- [Syncthing synchronization](https://docs.syncthing.net/users/syncing.html): remains a separately piloted option. Existing GitHub synchronization stays active. Never enable two live sync transports over the same vault or synchronize .git with Syncthing.
- [GitHub hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners): Windows, Linux, Apple Silicon and Intel Mac checks use synthetic data only. Private-repository runners consume account allowance; they do not establish that the owner's physical Mac/Linux device is installed.

## Boundaries

One configured device owns wiki compilation and daily aggregation. Other devices can capture and retrieve, and each owns its local index and serialized note synchronization. A shared Markdown control page records the maintainer ID and prevents accidental role duplication after sync; it is not a distributed consensus lease. Explicitly disable the former writer before handing ownership to another host.

The compiler quotes source evidence exactly and labels all automatically generated claims historical, uncertain or superseded. This does not mathematically prove that a paraphrase follows from its quote. Original notes, dates, prior claims and write backups remain available. The confirmed Profile page is excluded from automatic compilation.

Model processing sends selected note text to the chosen model provider, as existing cloud-agent reads already do. Indexing remains local. No new public endpoint, hosted database or API key is introduced.

Sources up to 2 MB are split into overlapping 24 KB parts; each part has durable processing state and publication verifies the hash of the complete original. Larger sources and sources containing credential patterns are excluded from automatic model input and need redaction or manual import. Source changes cause reprocessing; three failures require attention instead of an endless quota-consuming loop.

Keep Basic Memory 0.23.2 and its required FastMCP 4.0.0b1/split package pinned. FastMCP is an upstream prerelease: reproducible installation and tests do not imply a security guarantee. Review the lock and vulnerability report before a future dependency upgrade.

## Acceptance

1. Preserve source bytes and existing client configuration; preserve tokens and device identity on reinstall.
2. Reject traversal, secret-like output, fabricated quotations, unsupported link targets, stale source/page hashes and unauthorized writer roles.
3. Verify compile, replay, partial-publish recovery, changed-source queue and structural lint in disposable vaults.
4. Run native library/MCP imports and the common test suite on all CI platforms.
5. Verify real Windows retrieval, authenticated MCP, wiki ingest, restart and synchronization. Physical Mac/Linux client login and reboot tests remain separate deployment checks.
6. Publish an explicit tagged software release with hashes; updates preserve local data and retain rollback instructions.
