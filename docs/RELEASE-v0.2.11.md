# v0.2.11 candidate - one end-to-end self-test for every device, and an index run that is not ended while it works

Status: candidate preparation, 17 November 2026. Exact source/manifest, review, CI/audit and scoped device qualification are recorded on the release page, not here. This note is not publication, deployment or physical-device acceptance.

Found on the owner's Windows maintainer on v0.2.10: `index retry --full --stall-seconds 300`, the command the setup guide gave for a search index that stays on WARN, ended twice with `IndexStalled` (after 314 and after 1,862 seconds) while its log had not passed the first line of the text-index phase. Reproduced and measured on a Mac with a disposable install on a copy of a 4,367-note vault, sampled every 15 seconds.

## What the measurements showed

- **A plain `index retry` works.** First index from nothing: 125 minutes, 9 passes, `verified`, 0 invalid vectors.
- **The text-index phase of `--full` changes none of the counted tables.** For 6 minutes the indexer worked at full CPU while `entity`, `search_vector_chunks` and `search_vector_embeddings_rowids`, the only signals the stall rule reads, stayed the same. On a slower device that phase is longer than the stall budget, so a working indexer was ended as stalled.
- **After embedding, the upstream program does not end by itself.** It stayed at full CPU inside SQLite for four hours with no row change until the stall rule ended it; the follow-up passes then embedded the rest and the run ended `verified`. Ending it there is therefore right, and judging progress by CPU time would be wrong.
- **`--full` was never needed for the Windows state** (all notes indexed, some vectors invalid, nothing stale). The guide's advice was wrong.

## What changed

1. **The text-index phase has its own budget.** Until a pass has written the upstream line that starts embedding, the count rule does not run; the phase may take up to two hours, cut to a quarter of the time the run had left when the pass began so that coverage can still be read afterwards, and never less than the stall budget, which is what applied to it before. The count rule and its clock start when embedding starts. Each pass is judged by what it wrote itself.
2. **The guide gives a plain `index retry`** for a search index that stays on WARN, with measured durations, and `--full` only for a stale checksum that remains. `index status` says the same.
3. **`--selftest`: one end-to-end test, the same on every device.** `setup ... --check --selftest` (or `brain_cli.py selftest`) starts the same stdio adapter with the same Python that an assistant's connection entry starts, and through it lists the tools, reads health and context, saves one milestone, reads it back and finds it by search. Always six PASS/FAIL lines, also when the connection itself fails; any FAIL fails the command. Read-back passes only for the note at the saved path with this device's identity, title and text. It calls no model.

## Boundaries to review

- **The self-test writes one note per device** into `00-Inbox/AI-Captures`, with a fixed identity and text, so a repeat is a duplicate and adds nothing. With sync on, that note reaches the other devices like any milestone. It is opt-in: nothing runs it without `--selftest`.
- **It proves the service path an assistant uses, not the assistant.** That an assistant has loaded the connection still needs the MANUAL fresh-chat lines.
- **It needs the running service** and is skipped with a line on a device set up with `--no-service`.
- **The text phase is recognised by an upstream log line** (`Building vector embeddings`) of the pinned Basic Memory version. If a later version words it differently, a pass is judged by the two-hour budget alone, which is slower but ends nothing early.
- **A text phase that truly hangs** is now ended after two hours instead of after the stall budget.
- **A run can end differently when its overall budget is nearly used up by reading coverage.** The text phase may now use up to a quarter of the overall budget where it used the stall budget before. If that quarter plus the time the coverage reading takes no longer fits into the overall budget, the run ends with `overall_time_limit` where v0.2.10 could still verify. Example: `--overall-seconds 100 --stall-seconds 5` with a coverage reading of 80 seconds verified after 85 seconds before and now runs out of time. With the default eight hours the text phase gets two hours, so this needs a coverage reading of more than about five and a half hours; on the measured 4,400-note vault it takes under a minute.
- **Why that phase took over 30 minutes on the Windows device** against 6 to 11 on the Mac is not established. The service was running there during the retry and was not in the Mac measurement.
- **The follow-up passes still wait out their stall budget** after each 256-chunk step; a large first index spends about 40 minutes there. Unchanged.

## Known limits

- The limits of the [v0.2.10](RELEASE-v0.2.10.md), [v0.2.9](RELEASE-v0.2.9.md), [v0.2.8](RELEASE-v0.2.8.md) and [v0.2.7 notes](RELEASE-v0.2.7.md) still apply.

See [the setup guide](SETUP-GUIDE.md) sections 3, 4 and 8.
