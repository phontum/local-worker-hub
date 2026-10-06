# Measuring useful frontier work

The dashboard defaults to real jobs submitted since the statistics reset. Today
uses midnight in `preferences.json`'s timezone, with UTC if that zone is invalid.
All history includes earlier real jobs. Evaluation, benchmark and legacy-import
jobs are excluded from these dashboard views; history is preserved.

Review coverage is reviewed jobs / submitted jobs. Acceptance is accepted jobs /
reviewed jobs. A Validator can be accepted for accurate evidence of failing or
blocked checks, so accepted validation is distinct from passing checks.

API-equivalent workload prices recorded local input/cache/output counts at the
dated rates in private `pricing.json`. Today shows the same calculation for jobs
submitted today. Validators use zero model tokens by default, so extensive test
execution can leave this figure unchanged. It is not subscription savings.

Frontier tokens avoided remains unmeasured until a review records a matched
direct-frontier baseline and the delegated-frontier total. Include orchestration,
review, retries and takeover in the delegated total. Put one baseline on a handoff;
use the same `handoff_id` for its follow-ups. Preserve negative savings. Manual
estimates remain separate from measured baselines. Record actual review effort
where available; never replace unknown usage with an invented zero.

The summary API accepts `since` (inclusive), `until` (exclusive), `include_eval`
and `include_history`. `include_history=false` applies the reset when `since` is
not supplied. `period=today|since_reset|all` is an alternative to explicit bounds.
The dashboard uses `include_eval=false`. A request with no parameters retains
the original all-history, all-caller behavior. `selection`, `today`, `reviewed`
and `measurement_coverage` explain which population and evidence were counted.
Context events, usage, reviews and handoffs share the selected job population.

## Exact replacement jobs

For known literal replacements, pass typed mappings through `implement_change`:

```json
{
  "repo": "/path/to/project",
  "task": "Use shared motion tokens in both transitions",
  "files": ["ui.css"],
  "execution_mode": "literal",
  "handoff_id": "motion-update",
  "mappings": [{
    "path": "ui.css",
    "old": ".15s ease",
    "new": "var(--duration) var(--ease)",
    "expected_count": 2
  }]
}
```

Literal mode uses zero model tokens and makes all replacements in a private
workspace. Count mismatches and overlapping mappings write nothing. It retains
the existing scope, secret-file, link, destructive-edit and freshness guards;
review its diff and call `apply_result` as usual. Approved checks run after edits;
failing checks require frontier action. There is no model repair in literal mode.

Default model execution also accepts typed mappings. Every specified replacement
is checked before committing generated edits. This catches an omitted transition
even when the model says COMPLETE. Mappings are single-line literal strings;
semicolons are allowed. Supply path/count for precise verification.

CLI equivalent: `local-worker --write --repo /path/to/project --allow-path ui.css
--mappings mappings.json --literal --async "Use shared motion tokens"`.
Structured specs accept the same mapping fields and `execution_mode`.

Approved checks can include `expected_test_files` (paths relative to the
repository root) and `minimum_tests`. Missing files block execution; missing or
insufficient recognized test counts fail coverage even with exit code zero. Raw
exit codes are retained. These optional expectations must come from the caller
or a reviewed profile. No commands or permissions are inferred.

## Evidence and the twelve-task pilot

Investigator reports quote observed source beside location references. Explain,
compare and requirement tasks include a checklist anchored to literal task text.
Unknown requirements, missing reads and unresolved additional ranges produce
PARTIAL. Exhaustive/caller questions enumerate bounded name-based reference hits
and show any unread sites. A checked source location does not establish semantic
correctness; frontier review remains required. The authenticated
`work.investigation.json` artifact contains scope, read ranges, reference coverage,
the checklist and unknowns.

Run the pilot only after starting the service with the new code:

```sh
.venv/bin/python benchmarks/eval_frontier_work.py --out /tmp/frontier-work-pilot
```

The output directory must be new and outside the checkout. The harness creates
identical disposable starting states for four validation, four literal edit and
four investigation exercises. It preserves negative cases and local failures,
compares direct tool results with delegated results, and checks literal candidates
before applying them to the disposable repository. Benchmark jobs do not count
in normal dashboard statistics. Frontier acceptance is recorded separately.

`results.json` holds raw results. `frontier-measurements.json` is a measurement
sheet: use the same frontier model/configuration for both arms, observe the full
task effort/usage, and review correctness independently. Direct tool seconds are
machine timings and must not be entered as frontier effort. Missing frontier
measurements stay null. This tool pilot alone cannot establish net frontier
savings. Evaluate per-category paired differences before expanding delegation.
