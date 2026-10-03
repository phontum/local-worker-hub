# Improvement acceptance — 2026-10-02

Six controlled fixtures compared frontier-directed native tool operations with
helper jobs. Identical starting files and exact acceptance checks were used.
Native-tool timings exclude frontier deliberation; complete frontier token and
cost totals were unavailable and remain blank. This is an integration and
overhead benchmark, not evidence of net subscription or frontier-token savings.

| Case | Helper wall time | Local output tokens | Frontier review |
| --- | ---: | ---: | --- |
| Locate health handler | 91.77s | 220 | Accepted; correct path and line |
| Read timeout/retries | 8.15s | 261 | Accepted; correct values |
| Passing validation | 2.10s | 0 | Accepted; exact exit recorded |
| Deliberately failing validation | 2.10s | 0 | Accepted report; command failed as expected |
| Exact answer edit | 14.24s | 859 | Accepted; unrelated content preserved |
| Exact timeout edit | 34.43s | 2,176 | Takeover; two edits rejected by hash guard |

The failing-log result was 1,751 bytes compact versus 12,272 bytes full (about
86% smaller). All command evidence remained accessible in the private artifact.
Small discovery results sometimes grew because metadata/artifact references
outweighed their tiny reports; compact mode primarily avoids large log/snapshot
payloads. The first discovery job was substantially slower than the second;
its root cause was not established, so it is not labeled a confirmed cold load.

For the failed edit, repeated reads showed the same actual digest and unchanged
content. The helper's suggestion of concurrent modification was unsupported.
The frontier inspected the evidence, recorded takeover, and completed only the
fixture change. SHA256 enforcement remains intact; its message now identifies
the mismatch without asserting a cause.

A private web-app profile also passed typecheck and all 295 unit
tests, using zero local model tokens. No full backend-suite claim is made.

Private paired evidence and review receipts are retained at:
`~/.local/state/opencode/local-worker/benchmarks/pairs-*/results.json`.
Original mechanical failure and unavailable frontier totals remain preserved.

Keep tiny literal reads/edits with the frontier when delegation costs more than
the task. Use deterministic validation for real command groups; request model
analysis only when it adds value. A later fully metered comparison must include
orchestration, review, retry and takeover before reporting net savings.

## Workflow implementation smoke comparison

The new bounded coordinator completed the same disposable `config.js` change and
Node check in three one-run variants. The frontier independently verified the
exact file content and recorded acceptance for each run.

| Setting | Elapsed | Local input / output tokens | Repairs |
| --- | ---: | ---: | ---: |
| 16K, thinking on (default) | 36.32s | 26,512 / 2,107 | 0 |
| 16K, thinking off | 22.82s | 4,451 / 300 | 0 |
| 32K, thinking on | 71.94s | 35,229 / 2,981 | 0 |

These are single samples, with cumulative local tokens across model calls; they
do not establish reliability or frontier-token savings. The existing 16K and
one-repair defaults stay in place. The two-repair path has deterministic test
coverage, but no demonstrated advantage in live tasks yet. Run
`benchmarks/run_workflow.py` with repetitions and harder fixtures before changing
either default. Evidence lives in the private `workflow-*` benchmark directories
under hub state; individual job receipts remain private.

## Complete handoff and personal-mode acceptance

Live disposable web-app fixtures verified evidence attachment, separated check
budgets, public personal mode and captured local thinking. Frontier inspected
source/diffs and original check outcomes before recording reviews.

| Case | Context | Wall time | Frontier review |
| --- | ---: | ---: | --- |
| Three-file route/default discovery | 16K | 41.54s | Accepted |
| Attached failure-log diagnosis | 16K | 6.66s | Accepted |
| Two-file settings/view slice | 16K | 243.77s | Takeover required; initial edit and one repair failed |
| Same settings/view slice | 32K | 224.02s | Takeover required; incorrect HTML handling |
| Missing/None backend totals | 16K | 259.95s | Takeover required; None remained unhandled |
| Same backend totals | 32K | 43.20s | Accepted; inspected diff and regression passed |

These are single live acceptance samples. Host and prompt corrections occurred
during the run; they are not a controlled comparison and do not show that 32K
is generally better. Initial scoped checks were blocked by an integration bug
(read scope was incorrectly applied to check cwd); those original failures are
retained. Check execution now uses its existing trusted repository boundary,
while model reads retain the explicit read scope.

The paired browser displayed the same private thinking returned by the trace
API, safely rendered model text, and passed mobile layout checks. Personal mode
was exercised from the home directory with a conversational greeting, a public
weather search at 16K and 32K, and a missing-location limitation without web calls.
Early answers inherited engineering framing or overstated source agreement;
those reports were rejected and their evidence retained. The Personal prompt
now separates conversation from engineering, and a mechanical source footer
preserves returned public URLs when the model omits them.

Repeat evaluations with a fixed engine/prompt version using:

```sh
.venv/bin/python benchmarks/run_delegation.py --presets work extended --max-seconds 3600
```

Mechanical correctness is not acceptance. Review receipts belong in the hub.
No matched frontier token baseline was available; net token/cost savings remain
unknown and are not inferred from local token counts. Private evidence is under
`~/.local/state/opencode/local-worker/benchmarks/delegation-*`.


### Personal requirements review (2026-10-03)

Extended now selects 32K, thinking, and independent answer review; `--review`
opts in at 16K. Initial work and review share one model budget. These are
implementation acceptance samples, not a controlled model comparison.

- Initial official-documentation samples falsely approved a fetched-page
  requirement based on search text, and later removed a requested bullet format.
  The frontier rejected/took over those reports.
- A later official-documentation sample (63s) retrieved both required version notes. Literal supporting quotes were
  verified, and the mechanical output-count guard correctly reported PARTIAL
  when the model substituted a numbered list for requested bullets. The frontier
  accepted this conservative report as a partial task outcome.
- General-question samples (36–87s) also exposed missing or compressed supporting
  references and changed formatting. They were not accepted as completed tasks.
  Prompt contracts were simplified; literal quote guards now retain exact observed
  substrings, handle line wrapping, and split ellipses only when every literal
  segment exists. Unambiguous list formatting can be normalized without semantic
  edits. Unsupported claims remain unknown; semantic interpretation is still
  reviewed by the frontier.
- Public Exa schema discovery confirmed `objective` is required on search; the
  adapter now supplies it. Hosted extraction gained paging and literal find.
- LangSearch's optional free API adapter was tested with synthetic HTTP responses;
  no real LangSearch key is configured, so live provider quality is unmeasured.
- Python suite: 95 passing tests (final changed review paths also retested separately). Dashboard build and paired Chromium checks
  verified the requirements assessment, initial-answer artifact, trace/API equality,
  escaped untrusted text and mobile layout.

No matched frontier-token baseline or financial savings are claimed. Small-model
self-review can still misinterpret evidence; the stricter completion guards may
produce more PARTIAL reports rather than falsely confident answers.

Final runtime refinements separated the original task from transport protocol,
reserved a thinking-disabled JSON formatting turn, and stopped malformed packets
from causing a parser KeyError. A literal-answer smoke
 completed in 9.4s with `Hello!` and a
verified exact supporting quote. Its historical report remains PARTIAL; replay
through the current rubric derives COMPLETE because its original requirement is
met. This is a trivial plumbing check, not evidence of complex-task accuracy.

## General web research flow — 2026-10-03

The implementation now has model-authored task plans, an independent reviewer
plan, one evidence-grounded plan revision per pass, explicit configured free
search fallback, bounded inference retry, schema-visible page bounds and private
dashboard plan/evidence traces. Domain-specific price/stock acceptance and the
blanket cheapest-market failure were removed. Deterministic verification remains
limited to literal source provenance, transport/contracts and tool boundaries;
the LLM decides task meaning, research breadth and semantic sufficiency.

125 Python tests, the frontend build and paired browser checks passed. Live
quality is **not accepted**: the shopping repeat exposed a missing-review-plan
bypass (fixed); its second run stopped on an Ollama HTTP 500 surfaced as gateway
502. The documentation test demonstrated that the reviewer added omitted output
and citation requirements, but suffered oversized read arguments and an overly
strict freshness choice. The second documentation run safely returned PARTIAL
when its reviewer omitted the mandatory plan after one reminder. No local
retries of those tasks were continued after two unsuccessful attempts. Missing
reviews and unsupported source claims did not become acceptance.

The installed model has presence_penalty 1.5. Two bounded native JSON-copy checks
passed identically with installed and neutral penalties, so this does not
establish a decoding cause and no decoding defaults were changed. Full semantic
compliance by Qwen3.5 9B remains an unresolved quality limitation; these changes
are not evidence of frontier-token or dollar savings.

Private job receipts, local usage and final decisions are recorded under
`~/.local/state/opencode/local-worker/benchmarks/web-research-plans-20261003/evaluation.json`.
The usual 16K model preset, concurrency one and optional extended mode remain.
