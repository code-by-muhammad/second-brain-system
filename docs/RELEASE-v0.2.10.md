# v0.2.10 candidate - a maintainer is no longer left without a wiki assistant

Status: candidate preparation, 17 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

Found on the owner's Windows maintainer after its update to v0.2.9: the update passed with "0 failed", while `health` showed the wiki worker in `attention` with reason `RuntimeError`, because no assistant program had been recorded. Nothing in the run said so.

## The defect

The assistant that builds wiki pages was recorded only when the command carried `--wiki claude` or `--wiki codex`. The plain command, which is what the guide gives for setup and for every update, never recorded one, never asked and never mentioned it. The acceptance check had no line for it, so a maintainer whose wiki could not run looked fully set up.

## What changed

1. **Setup settles the assistant on a maintainer that has none recorded.** When the command runs without `--wiki` and without `--check` on a maintainer, and `state/wiki-model.json` does not exist:
   - exactly one of `claude` and `codex` is found as a program: it is recorded, and the run says which and why;
   - both are found: a person at the terminal is asked (claude, codex or none, default none); when nobody can answer (`--yes`, or no terminal) nothing is recorded;
   - neither is found: nothing is recorded.
   In every case where nothing was recorded the run says so before the check and again at its very end (`Still open: ...`), followed by this run's own command with `--wiki claude` or `--wiki codex` added, so the other options of the run are kept.
2. **The acceptance check has a `wiki assistant` line on a maintainer.** PASS with the program's path, model and effort. WARN when none is recorded, when the runtime refuses the record (with its reason), or when the last wiki run needs attention (with the reason `health` already shows and the commands to run). A capture device has no such line.
3. **`brain_cli.py wiki model`** reads the record exactly as `wiki run` does, including the identity check of the program file, adds the saved state of the last run, and starts nothing. The identity check now also refuses a program file this user cannot execute, at selection and before a run. The check uses it, so a record that a run would refuse is never shown as PASS.

Recording an assistant calls no model and sends nothing. Pages are still built only by `wiki run`, which the owner starts. The programs are found exactly as with `--wiki`: real programs in absolute `PATH` folders, `.exe` on Windows.

## Boundaries to review

- **A recorded choice is never replaced** by the plain command, including one whose program has gone; the check warns and `--wiki` changes it. This keeps an owner's model and effort choice. The automatic recording goes through `wiki configure-model --keep-existing`, which looks again under the wiki lock, so a choice recorded while setup was waiting for an answer also stays.
- **Only setup repeats the remedy at its end,** and only when it could not settle the assistant. A `--check` run, a record the runtime refuses and a stopped earlier run show the remedy in the `wiki assistant` line alone.
- **The exit status is unchanged.** A maintainer without an assistant ends with status 0 and a WARN line, because wiki pages are optional. Only an assistant that was asked for with `--wiki` and could not be recorded fails the run, as before.
- **A maintainer that does not want wiki pages** and has both programs, or neither, keeps the WARN line. There is no switch to silence it.
- **The check does not start the assistant.** It cannot tell whether the program is signed in or the model is available to that account; that shows at `wiki run`.
- **Automatic recording follows `PATH` order** when one assistant is installed in several places, as `--wiki` does.
- **An earlier stopped run stays stopped.** A device that ran `wiki run` before an assistant was recorded shows the attention WARN until `wiki retry`.

## Known limits

- The limits of the [v0.2.9](RELEASE-v0.2.9.md), [v0.2.8](RELEASE-v0.2.8.md) and [v0.2.7 notes](RELEASE-v0.2.7.md) still apply.

See [the setup guide](SETUP-GUIDE.md) sections 3, 4, 7 and 8.
