# Known issues

## 2026-10-05: Editor silently truncates large edits, then applies or misreports the result

Found by reading two frontier reviews of Editor jobs. Status: **Releases 1-3 implemented (branch `feature/editor-safety`); the experiments of Release 3 (decomposition, line matching, chaining, JSON format, 8K cap) were measured and their outcomes are in `benchmarks/RESULTS.md`.**

| Item | Status |
| --- | --- |
| Truncation invisible (`done_reason` dropped) | Fixed: `Caller` keeps a `Generation` with `done_reason`/`output_tokens`; a cut-off reply applies zero edits and the report says so; the session records every generation |
| Unterminated block parsed as complete | Fixed: strict parser (exactly seven-character markers, every block terminated, `WHOLE` bodies may contain `=======`), whole reply voided on any problem |
| Destructive replacement applied | Fixed: blocks that replace 20+ lines with under a quarter, files losing 40%+ of their lines, and removed definitions are refused and sent back for correction |
| Half-applied job | Fixed: one transaction per job (plan in memory, commit all or nothing, rollback on a failed write); clean edits stay staged for the corrective turn and an aborted job leaves `staged.diff` |
| Report described the last turn only | Fixed: Files and Findings come from the actual diff plus one line per turn; in-place jobs also get an acceptance packet |
| Rejection reasons not recorded | Fixed: rejected/takeover reviews need notes and take a reason code; `local-worker incident JOB` exports metadata only |
| `in_place` on the MCP surface | Fixed: removed from `submit_job`, refused by the service for MCP callers, CLI flag kept |
| Large file shown in part, `read_paths` ignored, protocol words as identifiers | Fixed (Release 2): `hub/contextpack.py` shows a file whole when it fits the context budget (window minus output cap and prompt), otherwise the head plus every text the task names, matching definitions and neighbours; `read_paths` files that are not editable are added as read-only references; protocol words are not identifiers; named texts that do not exist in the authorized files are reported, and a job whose `old -> new` texts all miss them is refused without a model call |
| Oversized task reaches the model, and a cut-off reply loses all work | Fixed by continuation (Release 3): complete blocks of a cut-off reply stay staged and the model continues; the gate (`hub/editgate.py`) now only advises by default, with `refuse_oversized` and `proposed_split` available. Automatic decomposition (`auto_split`) was built and measured but is not better than continuation, so it is opt-in |
| Files deleted or skipped silently at apply | Fixed (Release 2): deletion needs `delete_paths` plus a `DELETE` block, and `apply_result` needs `accept_removals`; undeclared removals are never applied |
| Concurrent changes to files the job only read were invisible | Fixed (Release 2): `read_paths` hashes are recorded; apply and the result show `stale_dependencies`; `revalidate` re-runs the approved checks on your tree plus the patch before writing |
| No way to check or undo an applied job | Added (Release 2): `run_checks` after apply and `revert_result` (refuses if a file changed again) |
| A repair turn that could not finish lost the whole job to a timeout | Fixed (Release 2): it is skipped with a stated reason and the patch is kept |
| Frontier had to remember safe defaults | Added (Release 2): `investigate_code`, `implement_change`, `fix_failing_test`, `add_regression_test`, `run_checks` |
| Same-size edit within one second ran stale bytecode in checks | Found later and fixed: checks run with `PYTHONDONTWRITEBYTECODE=1` |
| COMPLETE with the task's own `old -> new` renames partly undone | Found by the incident benchmark and fixed: the host verifies stated mappings and downgrades to PARTIAL |
| A regression test could be an existing test edited to fail | Found by a live run and fixed: the failing test must be defined on an added line |

### What the frontier saw

Two `implement` jobs (Editor, `in_place: true`, Gemma 4 12B, 16K context) on a frontend slice, three authorized files, `repair_attempts: 1`.
The frontier recorded `rejected` and `takeover` with empty notes, so the reason had to be reconstructed from the job records.

| Job | Outcome | Frontier's decision |
| --- | --- | --- |
| `2c10d4f5…` | PARTIAL, no file changed, check passed on the untouched tree | rejected |
| `8466d6c0…` | PARTIAL, two files changed, check failed after the repair | takeover |

Both reports ended with `<component file>: SEARCH does not match the current file; copy the lines exactly as shown`.
The second report also said `Findings: No change was applied` and `Files: None`, while `changed_files` listed two files and the
frontier's working tree contained the half-applied edits.

### Evidence (from the saved job records)

- **Every model call stopped at exactly the output cap.** All five edit and repair generations across the two jobs report
  `output: 4096` tokens, which is the hard maximum (`hub/phases.py:42`, `min(4096, …)`). The raw output (trace `content`) is 30-31k characters per
  turn and ends mid-line (`…if (job.phase === 'manifest') {` and `…</td>`). The model was cut off in the middle of a block, every time.
- **Nothing downstream noticed.** `Caller.text()` returns only the message content and drops Ollama's `done_reason` (`hub/calls.py:44-48`);
  `write_session` records `finish: 'stop'` unconditionally (`hub/calls.py:67`). Neither the pipeline, the report nor the frontier can tell a cut-off reply from a finished one.
- **The parser accepts an unterminated block.** After `=======`, the REPLACE body is read until a `>>>>>>> REPLACE` line or the end of the text
  (`hub/textedit.py:63-66`). The first-turn output had 12 `SEARCH` markers and only 2 terminators; parsing it gives a final block whose
  replacement is 302 lines of runaway text. A truncated REPLACE is treated as complete.
- **A destructive edit was applied.** In `8466d6c0…`, the test file lost 68 lines and gained 17 (`scoped-diff.txt`): a 72-line region of existing
  tests was replaced by the single fragment `toHaveBeenCalled();`. That produced the failing check (`ReferenceError: toHaveBeenCalled is not defined`) and the repair did not fix it.
  Nothing compares a block's SEARCH size with its REPLACE size.
- **The large file was shown only in part.** The component file is 463 lines and `SHOW_WHOLE` is 400 (`hub/pipelines.py:19`), so the model saw 150 lines: the top 30
  plus ±40 lines around the "identifiers" found in the task. Those identifiers were `SEARCH`, `REPLACE` and four names taken from the task
  (`hub/localize.py:57-67`, six at most): two are the frontier's own format words, and the quoted targets the task actually named (three progress labels at lines 36-38 and a role message at line 110) were never shown. A SEARCH block cannot match text the model never saw.
  The prompt used only about 6k of the 16K context, so showing the whole file would have fit.
- **The final report describes the last turn, not the job.** `implement()` rebuilds the PARTIAL report from the last editor phase
  (`hub/runner.py:424-426`). The repair turn changed nothing, so it overwrote the first turn's list of edited files
  with `No change was applied` and `Files: None`.

### What it was not

- Not the wrong model: `effective-config` shows Gemma 4 12B at 16K, thinking off, as intended.
- Not a scope or freshness failure: only authorized files were touched, and the hash checks held.
- Not the check command: it ran correctly and its result (8 of 9 tests pass, one failing line) was reported accurately.
- Not an index or workspace problem: both jobs used `in_place: true`, which bypasses the private workspace and the acceptance packet.
  That is why the half-applied edits landed in the user's tree and needed a takeover.

### Contributing factors on the calling side

- The task asked for about ten distinct changes across three files in one job, well beyond the "mechanical change in 2 files" shape the worker is built for.
  Each SEARCH/REPLACE block for such edits is long (122-185 lines), which is what overran the cap.
- Both reviews had empty `notes`, so the reason for rejection was not recorded.

### Not verified

The exact block that produced `toHaveBeenCalled();` was not reproduced. Replaying the saved first-turn output against the reconstructed original files
applied only the CSS block and rejected both TypeScript blocks, which does not match the recorded edit events. The truncation and the lenient parser explain how a
partial REPLACE can be applied, but the specific sequence for that line is an inference.

### Suggested fixes, in order of value

1. **Detect truncation.** Keep `done_reason` from Ollama, fail the turn when it is `length`, and say so in Risks ("edit output was cut off at 4096 tokens; split the task").
2. **Require terminators.** Apply only blocks that have `>>>>>>> REPLACE`; discard an unterminated final block and report it.
3. **Guard destructive blocks.** Reject or flag a block whose REPLACE is far smaller than its SEARCH (for example under a quarter of a SEARCH over 20 lines).
4. **Show more of a large file.** Raise `SHOW_WHOLE` when it fits the context (this file was about 7k tokens), drop format words from the identifier list, and locate quoted
   strings from the task with the code index (`hub/codeindex.py`) instead of guessing identifiers.
5. **Report the whole job.** Build the PARTIAL report from the cumulative diff (changed files, line counts), never `No change was applied` when `changed_files` is not empty.
6. **Give `in_place` jobs the acceptance packet** (diff stats, scope, review focus), so a 68-removed / 17-added edit is visible without opening the diff.
7. **Let the editor output cap rise** (up to about 8k within 16K context) for Editor jobs, per job, with the existing "measure before promoting" rule.
8. **Calling guidance:** one file or one concern per job; for files over 400 lines name the exact strings to change; prefer the private workspace; put the reason in `record_review` notes.

A regression test for items 1-3 can feed `textedit.parse`/`apply_all` a reply cut off mid-REPLACE and assert that nothing is applied and the truncation is reported.
