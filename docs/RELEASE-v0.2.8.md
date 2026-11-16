# v0.2.8 candidate - a fixed model and reasoning effort for the wiki

Status: candidate preparation, 16 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

In v0.2.7 the wiki compiler named no model. Claude ran on the default of the signed-in account (observed: `claude-fable-5-2`) and Codex on its own default (observed: `gpt-6.1-sol` at reasoning effort `none`). Two devices could therefore build different pages from the same notes, and the owner had no say in the cost. The owner chose one model and effort per assistant.

## What changed

1. **Fixed defaults.** Claude is called with `--model claude-opus-5-5 --effort high`, Codex with `-m gpt-6.1-sol -c model_reasoning_effort="high"`. The values are in `app/brain_wiki.py` (`MODEL_DEFAULTS`).
2. **An explicit choice.** `wiki configure-model --executable <path> [--model <name>] [--effort <level>]` stores another model or effort in `state/wiki-model.json`. Only what is given is stored; a device without a stored choice follows the defaults of the release it runs. A model name must start with a letter or digit and be 1 to 80 letters, digits, dots, colons, brackets, underscores or hyphens. The effort must be one of the levels that assistant accepts (Claude: low, medium, high, xhigh, max; Codex: none, minimal, low, medium, high, xhigh). Anything else is refused before it is stored and again before a run.
3. **Recorded per run.** The model and effort used are written to the batch's `model-executable.json` with the program's path and hash.

Nothing else changed. `setup_device.py --wiki claude|codex` still only records the program.

## Boundaries to review

- **The names are not checked against the provider.** A model the account cannot use, or a name a later CLI no longer knows, makes that run fail with the program's own error; nothing is published. For Claude such a message is classified from the program's error output as in v0.2.7.
- **Cost.** Both defaults are large models at high effort. On one Mac a single small note took about 15 seconds with Claude (reported as about 0.06 USD of usage) and about 33 seconds with Codex; a full batch of long notes takes longer and costs more. The 480-second deadline per run is unchanged.
- **An existing `wiki-model.json`** from v0.2.6 or v0.2.7 has no model or effort and now gets these defaults. A Windows maintainer that built with Codex at its default effort will build with effort `high` after this update.

## Known limits

- The limits listed in the [v0.2.7 notes](RELEASE-v0.2.7.md) still apply, among them: Codex cannot be run with an empty tool list, administrator-managed Claude policy still applies, and no Windows or Linux device has built pages with Claude.
- The acceptance check shows a WARN for the search index while a note that was just saved is not indexed yet.

See [the setup guide](SETUP-GUIDE.md) section 7 and [security limits](../SECURITY.md).
