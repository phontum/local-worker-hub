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

## Board Phase 0 probe — 2026-10-03

`benchmarks/probe_models.py` calls native Ollama directly (one model resident, 16K context) with
schema-constrained proposer, arbiter and triage prompts on 10 tasks (7 for proposals/arbiter).
Single run per setting, seed 7, so these are measurements for design decisions, not accuracy claims.
"Gold recall" is mechanical: the share of key task phrases that a valid verbatim `task_quote` overlaps.
Private raw results: `~/.local/state/opencode/local-worker/benchmarks/board-probe-20261003-*/results.json`.

| Setting | Model | JSON valid | Anchors valid | Gold recall | Median |
| --- | --- | ---: | ---: | ---: | ---: |
| Proposals, thinking on (4096 tokens) | qwen3.5:9b | 9/14 | 18/18 | 0.60 | 42.8s |
| Proposals, thinking on (4096 tokens) | gemma4:12b-it-qat | 12/14 | 40/40 | 0.87 | 26.6s |
| Proposals, thinking off (2048 tokens) | qwen3.5:9b | 14/14 | 42/43 | 0.92 | 11.4s |
| Proposals, thinking off (2048 tokens) | gemma4:12b-it-qat | 14/14 | 45/45 | 0.98 | 12.7s |
| Arbiter, thinking on | qwen3.5:9b | 0/7 | - | - | ~45s |
| Arbiter, thinking on | gemma4:12b-it-qat | 5/7 | 18/18 | 0.70 | ~50s |
| Arbiter, thinking off | qwen3.5:9b | 7/7 | 35/35 | 1.00 | 14.9s |
| Arbiter, thinking off | gemma4:12b-it-qat | 7/7 | 22/22 | 1.00 | 9.9s |

- Every invalid output had `done_reason=length`: thinking plus JSON in one call exhausted the token cap. This is a
  budget/pattern limit, not a structured-output incapability. The thinking-on Qwen arbiter result (0/7) reflects
  the cap and must not be read as arbiter quality.
- Residency: Qwen 5.76 GB and Gemma 7.67 GB, both 100% on GPU at 16K when loaded alone. Swap-in load times were
  8-17s (varied between runs; the first load also includes disk reads).
- Arbiter adoption was balanced (Qwen arbiter 47 own / 42 Gemma; Gemma arbiter 43 Qwen / 44 own); no
  self-preference showed at this sample size. Neither arbiter dropped an anchored requirement with thinking off.
- Triage: Qwen 10/10, Gemma 9/10 (Gemma skipped the stable-knowledge question that explicitly forbade web search).
- Qwen emitted 11 derived requirements versus Gemma's 2 across 14 proposals; whether those are useful or
  over-constraining is untested.
- Not measured: proposal usefulness, the full board end to end, thinking with a larger budget or a separate
  format turn, and Gemma's tool calling.

## Board live smoke — 2026-10-04

Three live runs of `local-worker --board "Find the cheapest RTX 5070 in Novi Sad today. It should be in stock."`
(Gemma default, lite mode). These are integration findings from single runs, not a quality evaluation.

1. **Ollama killed by the Linux OOM killer** mid-repair. WSL has about 7.6 GB RAM. llama.cpp keeps its prompt cache in
   host RAM (default limit 8 GB) and it grows with every distinct prompt prefix: llama-server RSS rose from 1.4 GB to
   3.5 GB after four long prompts on Gemma (about 560 MB per prompt; 4.9 GB after six) and from 1.25 GB to 2.2 GB on
   Qwen. A throwaway Ollama on another port confirmed `LLAMA_ARG_CACHE_RAM=512` is honoured: RSS stayed flat at about
   1.8 GB over six prompts. The Ollama service override still needs that variable (requires sudo). The hub now unloads
   models when available RAM falls below 2500 MB (`memory-guard` events) and reports a dropped Ollama connection as a
   retryable 502. `local-worker doctor` shows host memory and whether the cache limit is configured.
2. **`plan_web_task` failed for Gemma in every phase** because it omitted the required `requirement` key and copied
   `kind` from the board briefing's layout; the engine then withheld all other web tools, so the critic reported
   BLOCKED. The briefing layout is now prose, the tool description names the required keys, and the final JSON turn
   asks for short `web_claims` quotes (one repair executor produced 8,221 characters of pasted page text and hit
   the 4096-token cap).
3. **Completed, 470s, PARTIAL.** Triage, proposals, arbiter (4 anchored requirements, no drift flags), seeded plan,
   executor, critic, a provenance-triggered repair and a second critic all ran, with one model swap and four
   memory-guard unloads. Nothing was verified: one retailer origin read succeeded, another returned HTTP 403, and the
   models cited retailer pages they had only seen in search results (6 searches, 2 fetches). The guard correctly
   refused to present unverified price or stock as current. The repair did not improve the outcome.

Whether the board beats the normal reviewed flow is not established; run `benchmarks/run_board.py` with repeats and
frontier grading before drawing conclusions. The Qwen-vs-Gemma default comparison is also still pending.

## Board pilot — 2026-10-04 (time-boxed to ~22 minutes, stopped early)

`benchmarks/run_board.py --arms review board-lite`, one repeat, Gemma default. Only five jobs finished before the
cap; the planned docs, trick and no-web tasks never ran, so there is no unnecessary-web measurement and no
`review-matched` arm. One sample per cell: anecdotes, not evidence. No frontier acceptance was recorded.

| Task | Arm | Wall time | Status | Searches / fetches | Verified origin observations |
| --- | --- | ---: | --- | ---: | ---: |
| RTX 5070, Novi Sad | review | 121s | PARTIAL | 3 / 2 | 0 |
| RTX 5070, Novi Sad | board-lite | 327s | PARTIAL (repaired) | 4 / 5 | 0 |
| 2 TB NVMe SSD | review | 113s | PARTIAL | 3 / 1 | 0 |
| 2 TB NVMe SSD | board-lite | 491s | PARTIAL (repaired) | 6 / 1 | 7 |
| RTX 5070 Ti variant | review | 171s | PARTIAL | 2 / 3 | 1 |

Every run ended PARTIAL, and the board took 3-4x longer. The one board run with verified observations (SSD) named
a product with origin-verified price and stock quotes, yet its prose still called it "cheapest" while the critic's own
checklist marked lowest-price and delivery as unknown; the unverified items were listed in Risks. The board drift
check never flagged anything (0 flags in both board runs). Treat the board as unproven until a larger, frontier-graded
comparison exists. Long benchmarks are out of scope for this tool; keep any future run under ~30 minutes.

## Gemma 4 12B vs Qwen3.5 9B on the delegation fixtures — 2026-10-04

`benchmarks/run_delegation.py --presets work` (16K, one run per cell, about 5 minutes per model; Qwen selected through
temporary per-phase `model` entries in roles.json, restored afterwards; effective-config events confirmed each model).
Single samples on four tiny fixtures: indicative only, not a reliability measurement.

| Case | Gemma | Qwen |
| --- | --- | --- |
| Discovery (investigator) | 7s, PARTIAL, 283 tokens | 23s, PARTIAL, 582 tokens |
| Settings/view edit slice (editor, 1 repair allowed) | 72s, PARTIAL, 3,758 tokens | 195s, PARTIAL, 15,607 tokens |
| Backend None-handling fix (editor) | 32s, COMPLETE, mechanically correct | 18s, COMPLETE, mechanically correct |
| Log diagnosis (investigator) | 11s, PARTIAL | 5s, COMPLETE, mechanically correct |

- Mechanically correct: Gemma 1/4, Qwen 2/4. Neither passed the two-file edit slice.
- Both discovery answers contained the right value (10). The PARTIAL on Gemma's two investigator runs (and Qwen's
  discovery) came from the host's evidence check: the report cited no `E<n>` evidence IDs, so it was downgraded
  ("Unverified or missing requested source evidence"). Qwen cited E1 in log diagnosis; Gemma cited none in either.
- Gemma used far fewer tokens and less time on the editing slice but its own risk note admitted the `String()`
  rendering does not escape HTML; Qwen's attempt also failed the behaviour check.
- No frontier review of the diffs was recorded. Web research, the board, 32K context and larger fixtures were not compared.

Reading: no clear winner. Gemma is faster and cheaper on the edit slice; Qwen follows the evidence-citation contract
more reliably in the investigator role.

## Simplification pass — 2026-10-04

Changes under test: plain best-effort answers for Personal/Researcher (no automatic review or origin-proof; `--verify`
opts in), metric/24-hour preferences, checks-decide coding flow with advisory review, investigators judged on files
actually read instead of `E<n>` citations, forgiving globs with a corrective hint on empty searches, cheaper board.
Single live runs, Gemma default, indicative only.

Personal (end to end, CLI): weather for a time window 15s, 25s, 11s; price lookup (RTX 5070, Serbia) 12s with a
sourced list; docs question 2s; rain tomorrow 15s. Answers were plain text in °C, km/h, 24-hour time with a source and
a local "as of" time. Defects found and fixed while testing: a first price answer claimed from memory that the
RTX 5070 was unreleased (prompt now requires searching anything that changes over time), UTC "as of" times, a Serbian
reply to an English question, and markdown asterisks. Correctness of the figures was not independently checked
beyond the cited pages.

Coding (`run_delegation.py --presets work`, about 2 minutes total):

| Case | Before (Gemma) | After (Gemma) |
| --- | --- | --- |
| Discovery | 7s, PARTIAL | 7s, COMPLETE, correct |
| Settings/view edit slice | 72s, PARTIAL | 75s, PARTIAL; the check found user text still reached the HTML and one repair did not fix it |
| Backend None-handling fix | 32s, COMPLETE | 42s, COMPLETE, correct |
| Log diagnosis | 11s, PARTIAL | 7s, COMPLETE, correct |

Mechanically correct 3/4 versus 1/4 before; the two cases that changed were downgraded for missing `E<n>` citations,
not for wrong answers. The edit slice remains a real model failure, correctly reported. Note that
`~/.config/local-worker/roles.json` still sets `editor.thinking: true`, which overrides the new thinking-off default.
Background delegation: `local-worker delegate --read-only ...` returned one compact JSON brief in 17s (the model
searched with swapped `text`/`pattern` arguments and missed the target; tool hints were added afterwards, not re-measured).

## Plain-mode tool use, Gemma vs Qwen — 2026-10-04

Three runs each of two time-sensitive personal questions on the plain flow (no review). Qwen was selected with a
temporary `personal.model` entry in roles.json, restored afterwards (six earlier runs were cancelled because the
profile is read at job start, not at submission).

| Question | Gemma 4 12B | Qwen3.5 9B |
| --- | --- | --- |
| Cheapest in-stock RTX 5070 in Serbia | 0/3 searched; all three said the card is unreleased | 2/3 searched (107,018 RSD from a snippet, unverified); 1/3 said unreleased |
| Current weather in Moscow (Russian) | 3/3 searched, 0 fetched; 2/3 repeated a stale +4°C cached snippet and relabelled 0.9 m/s as km/h | 0/3 searched; all three invented a temperature (+16, +10, +12°C) and a Yandex source line with a time |

Earlier user runs showed the same patterns: an hourly °F table turned into a wrong °C evening value (63°F, 17°C,
reported as 11°C), and `--extended` still routed through the strict reviewer, ending PARTIAL with a likely-correct answer.
Reading: both models skip the search tool unpredictably and will fabricate a cited answer when they do; neither should
be left to decide alone whether to look things up, convert units or write source lines.

## v3 host-driven pipelines — 2026-10-04

`benchmarks/eval_small.py` (12 personal questions, 6 repository lookups, 4 delegation fixtures), one run each, about
5-10 minutes per model. Current-fact answers were graded by hand from the saved sources. Indicative only.

| | Baseline (tool loop, Gemma) | v3 Gemma | v3 Qwen |
| --- | --- | --- | --- |
| Personal: used the web when needed | 11/12 | 12/12 | 12/12 |
| Personal: links not actually fetched | 2 | 0 | 0 |
| Personal: median time | 2.1s | 5.1s | 2.6s |
| Current facts correct (Moscow now, latest Python, Tokyo time) | 0/3 (stale +4°C, 3.13.0 from 2024, 19:47) | 3/3 (+11°C, 3.14.8, 21:16) | 1/3 (Python said 3.13, Tokyo not searched and wrong) |
| Repository lookups (path and value) | 6/6 | 6/6 | 6/6 (timing invalid: answer step stayed on Gemma) |
| Delegation fixtures | 3/4 in 154s | 4/4 in 18s (after the thinking fix) | 3/4 in 59s |

- The first v3 fixture run failed 2/4 at about 279s each: the role profile's `editor.thinking: true` let Gemma spend the
  whole 4,096-token output on reasoning and write no edit blocks. Edits now think only with `--model-thinking on`.
  The settings/view slice passed for the first time; both diffs were checked by hand.
- Remaining weaknesses: weather answers can still misread a page (an evening value read as 14°C), and "cheapest"
  claims cover only the shops whose pages could be read (several Serbian shops need JavaScript). The routing step
  initially used dates in queries (pulling monthly pages) and replied in Serbian to an English question; both
  prompt fixes were confirmed on a recheck.
- Decision: Gemma 4 12B stays the default for every step (better answers and edits; a single model avoids swaps).
  Qwen remains selectable per phase in roles.json.
- SearXNG (self-hosted, Docker) answered every query, but only via Google CSE; Brave and DuckDuckGo refused the instance.

## Realistic personal eval set and replayable web — 2026-10-05 (baseline before structured providers)

`benchmarks/evals/personal.jsonl` replaces the 12 inline questions with 58 cases: stable facts, math and code (direct), typos,
Serbian/German/Russian questions, unit and clock requests, weather (8), currency (3), current software/people facts, and
shopping (7, including variants and a JavaScript-heavy store). Each case records the route it should take
(`direct`, `web`, `provider:weather|fx|clock`), regexes that must or must not match and whether the data is current.
`--web record` stores search results and page text under the private state directory; `--web replay` serves them back with no
network, so only the model varies. Gemma 4 12B at 16K, host pipelines, `work` preset. No code other than the harness changed.

| | Live record pass (1 run per case) | Replay, 3 runs per case |
| --- | --- | --- |
| Runs | 58 | 174 (58 cases) |
| Route as expected, exact | 42/58 | 126/174 |
| Route as expected, web accepted for provider cases | 51/58 | 168/174 |
| Required text present (cases with a regex) | 37/37 | 106/111 |
| Metric units and 24 h clock in the answer | 56/58 | 168/174 |
| Fetched links only | 58/58 | 174/174 |
| COMPLETE | 53/58 | 171/174 |
| Median time | web 9.1s (live), direct 2.6s | web 5.1s, direct 2.6s |

- Providers do not exist yet, so every weather (24 runs), currency (9) and clock (9) run went to generic web search: 0 exact
  route matches for those categories is the expected starting point. They are the target of the provider work.
- `clarify-weather` ("What's the weather like?", no place) searched the web in 3/3 runs although the prompt says to ask for the
  place. `web-ollama-flag` (a specific Ollama setting) was answered from memory in all 4 runs (live and replay) and was right in only 2 of them, so the router
  treats an uncertain specific as stable knowledge.
- Both unit failures are the same bug: the answer body is converted to metric but the cited page titles are not, so a source
  line such as "Weather Today | 62°F, Partly cloudy" leaves Fahrenheit in the reply (weather-ns, weather-sr-week).
- `fx-rsd` failed its required text 3/3: the model answered from a generic page without the amount. The ECB reference rates behind
  the planned currency provider do not include the Serbian dinar, so that case will fall back to web search.
- Replay integrity: 90 web jobs, 115 searches served from fixtures, 0 live, but 33 searches missed because the model asked for a
  follow-up query that was never recorded (reported as a normal failed search, never invented). The harness now counts these
  (`fixture_misses`); fixtures from this pass are the `baseline` set. Treat web answers from replay as comparable between
  models and code, not as current facts.
- Gotchas found while building this: the hub's job process starts with a minimal environment, so the switch is a private config
  file with an expiry rather than an environment variable; and job processes load `hub/` code per job, so editing the source during a
  run changes the run. One earlier attempt was discarded for that reason.

## Structured providers and provider routing — 2026-10-05

Same 58 cases, Gemma 4 12B, replay mode (`baseline` fixture set plus provider fixtures recorded once from the live APIs), 3 runs per case.
Providers: weather (Open-Meteo), currency (Frankfurter/ECB) and clock; the decide call chooses one with its arguments.

| | Baseline (web only) | With providers |
| --- | --- | --- |
| Route as expected, exact (`fx-rsd` relabelled `web`: ECB has no RSD) | 129/174 | 168/174 |
| Weather routed to the provider | 0/24 | 21/24 |
| Currency routed to the provider (RSD case falls back to web by design) | 0/6 | 6/6 |
| Clock routed to the provider | 0/9 | 9/9 |
| Required text present | 106/111 | 110/111 |
| Metric units and 24 h clock in the answer (after the source-title fix) | 168/174 | 174/174 |
| Searches that missed a replay fixture | 33 | 4 (after nearest-query matching) |
| Median time, weather cases | 4.35s | 4.1s |

- Routing was the real work. The first provider prompt sent 2/8 weather questions to the provider: the base prompt listed "weather" as a
  needs-web topic and the model committed to `needs_web: true` first. Putting the provider fields first in the schema made no
  measurable difference; removing weather from the base list, describing what the provider covers (rain, wind, forecast, any
  language) and two examples did. On the 24 questions used while tuning, 24/24; on 20 phrasings written afterwards (other cities and
  languages, plus traps such as "check the weather in Python", "weather at Waterloo", "euro symbol in Unicode"), 19/20, the miss being
  "How hot will it get in Madrid on Tuesday afternoon" (needs date arithmetic). The tuning score overstates generalization because two
  prompt examples resemble two tuning questions; the held-out figure is the one to rely on. Single run, temperature 0.
- Still web instead of the provider: `weather-sr-week` ("Kakvo je vreme u Beogradu ovog vikenda?", 3/3), and `web-ollama-flag` is still
  answered from memory (3/3) because the router treats it as stable knowledge.
- Answers from provider data are shorter and correct against the data (the window 15:00-20:00 now reaches the provider as
  `from_hour`/`to_hour`). The model still dropped the "other places share this name" warning for Springfield, so the host now writes that
  note itself.
- The replay compares models and code, not the world: provider fixtures freeze the weather of the recording day, and search replay
  serves the nearest recorded query when a prompt rewords it (Jaccard >= 0.6, reported in the audit).

## Product markup, browser reads, chat guard — 2026-10-05

Final replay of the 58 cases (3 runs each, `baseline` fixtures + provider fixtures) with every change in place, and live checks of the two
page-reading additions. Gemma 4 12B, 16K, work preset.

| Replay, 174 runs | Baseline | After providers | Final |
| --- | --- | --- | --- |
| Route as expected, exact (`fx-rsd` relabelled `web`) | 129 | 168 | 168 |
| Required text present | 106/111 | 110/111 | 122/123 |
| Metric units, 24 h clock | 168 | 174 | 174 |
| COMPLETE | 171 | 172 | 171 |
| Replay fixture misses | 33 | 4 | 3 |

The required-text denominator grew from 111 to 123 because the five chat/clarify cases now have assertions (see below).

- **A bug the old eval could not see.** "Thanks, that was helpful." was answered with the literal text `true` in all 6 runs of both earlier
  replays: the answer field copied the boolean next to it, and those cases had no regex so they were counted as fine. The same happened once
  in the dashboard chat on "Hello!". The host now rejects a bare `true/false/null/yes/no` answer, retries once with an explicit instruction, and
  otherwise reports PARTIAL; the chat cases carry assertions. Final replay: "You are very welcome." 3/3. `clarify-price` ("How much does it
  cost?") now fails its assertion in 1 of 3 runs ("I do not have any project context...") instead of asking what the user means.
- **Product markup (live, 7 shopping questions, 21 page reads).** Structured product data was found on pages for 5 of the 7 questions
  (prices in RSD, EUR, NZD, RUB, USD; availability from `schema.org`), and none for the book and the Steam Deck questions. A first version
  missed IKEA-style offers whose price sits in a list of `priceSpecification` objects; found by reading a real page, fixed, tested. In
  `shop-gpu-rs` the model cited the Asus product block but its claim (a Gigabyte at 76.999 RSD) came from another page, so the extraction
  reaches the model correctly while the model's grounding of "cheapest" claims is still weak. The markup can disagree with the visible
  page; the model is told to report such conflicts, which is not yet measured.
- **Browser reads.** On a public client-side-rendered practice site a plain read gave "thin text" and the browser returned 1,071
  characters in 3.3 s, against 0.6 s for the static version of the same page. In the live shopping run it was attempted once (a thin page
  that then answered with an HTTP 4xx) and rescued nothing, so on those shops the fallback was rarely needed; it is not shown to improve
  shopping answers. The tests check, against a real Chromium, that JavaScript content is read, private-address subrequests and redirects
  are refused before leaving the host, a `wss://` connection is trapped and counted, POSTs, oversized responses and request floods are cut off.
  It cannot help with sites that refuse our HTTP client, because the browser's traffic goes through that client.

## Junior-task baseline, Gemma 4 12B vs Qwen3.5 9B — 2026-10-05 (current pipelines, before the index/workspace rework)

`benchmarks/eval_junior.py`: 22 cases over the ten delegation shapes (find code, explain, config use, mechanical change, guard,
regression test, run tests, fix a failing test, compare, check a requirement) on two small fixture projects, 3 runs per case, 16K,
`work` preset, model chosen per request and verified from the job's effective-config events. Grading is mechanical: gold
`path:line` references (file named plus a nearby line or the quoted gold line), regexes, hidden checks the worker never sees,
and "fails on the bug, passes with the reference fix" for regression tests. Decision rule fixed beforehand: correct rate first,
then false-COMPLETE count, median time, tokens; switch only with a lead of >= 10 points or fewer false-COMPLETE without losing correctness.

| | Gemma | Qwen | Qwen, thinking on |
| --- | --- | --- | --- |
| Correct (66 runs each) | 47/66 (71.2%) | 38/66 (57.6%) | 33/66 (50.0%) |
| COMPLETE but wrong | 13 | 20 | 26 |
| Median seconds | 4.6 | 4.1 | 28.6 |
| Tokens | 57k | 53k | 176k |
| Regression test (fails on the bug) | 6/6 | 0/6 | 2/6 |
| Explain / find code | 6/6, 3/6 | 4/6, 3/6 | 1/6, 1/6 |
| Fix exact failing test, guard, mechanical | 9/9, 6/6, 6/6 | 7/9, 6/6, 6/6 | 9/9, 6/6, 5/6 |

- Verdict under the rule: keep Gemma. Qwen trails by 14 points and has more false-COMPLETE; thinking makes it slower and worse here.
- Qwen's regression tests were written but did not fail on the bug, and the report still said COMPLETE with "Checks: Not run".
- Both models: 0/6 on "run tests and summarize failures" (the brief holds counts but not the failing test names; names are only in the raw log)
  and 3/9 on requirement checks (the investigator read the requirements file, then answered "no code files found" or skipped code).
  These are pipeline gaps, not model gaps, and are the targets of the index/evidence-packet and test-parser work.
- Caveats: two tiny fixtures, one run configuration, 3 repeats; the first grader version rejected correct answers that cited
  "line 19" instead of `path:line` and was loosened before any baseline was analysed. Re-run after the pipeline changes before relying on the winner.

## Junior-task eval after the index, evidence-packet, workspace and test-parser work — 2026-10-05

Same 22 cases, 3 runs each, 16K, `work` preset, model per request (verified from effective-config events). Changes under test: deterministic
code index with host-added read ranges, host-verified `path:line` references (COMPLETE needs one), parsed test failures at zero tokens,
private editor workspace graded after a real `apply`. Editor runs also required the user's tree to be untouched before apply.

| | Gemma before | Gemma after | Qwen before | Qwen after |
| --- | --- | --- | --- | --- |
| Correct (66 runs) | 71.2% | 90.9% | 57.6% | 78.8% |
| COMPLETE but wrong | 13 | 3 | 20 | 11 |
| Median seconds | 4.6 | 5.8 | 4.1 | 4.7 |
| Run tests and summarize failures | 0/6 | 6/6 | 0/6 | 6/6 |
| Find all code involved | 3/6 | 6/6 | 3/6 | 6/6 |
| Explain how X works | 6/6 | 6/6 | 4/6 | 6/6 |
| Check a requirement | 3/9 | 6/9 | 3/9 | 6/9 |
| Compare A and B | 3/6 | 3/6 | 3/6 | 4/6 |
| Regression test (fails on the bug, passes with the fix) | 6/6 | 6/6 | 0/6 | 0/6 |
| Fix an exact failing test | 9/9 | 9/9 | 7/9 | 6/9 |

- Verdict under the pre-registered rule: Gemma 4 12B stays the default (12-point lead, 3 vs 11 COMPLETE-but-wrong). The pipeline work lifted both
  models by 19-21 points, so the gap is the model's, not the harness's. Qwen's regression tests never fail on the bug and its fix-the-test
  runs more often end COMPLETE with a wrong or partial fix.
- Most gain is host-side: test failures are parsed deterministically, the host reads the likely ranges and verifies citations. Where the model
  has to notice behaviour (compare reports.py with legacy_reports.py: the rounding and thousands-separator differences) neither model does.
- Honesty changes: 12 of 33 investigator runs were COMPLETE-but-wrong before; 3 are now. Unverified citations are dropped and named; unknown
  requirements keep the status PARTIAL through the model's `complete` flag.
- Caveats: two small fixtures; the index ranking was tuned on these same cases (20 of 22 gold references reachable without a model, 21 after
  the final quota tweak), so the held-out figure to trust is the live pass rate, and it needs repeating on a larger repository. Qwen with thinking
  was only measured before the changes. One grader change (accepting "line 19" style citations) was made before any baseline was analysed.

### Follow-up: job `kind`, host-verified regression tests — 2026-10-05

- `kind` (find_code, explain, ..., fix_test) now checks the role and sets defaults (`fix_test` uses the implement workflow with one repair;
  `regression_test` requires the check that runs the new test). A first version also appended a per-kind answer-format hint to the
  investigator prompt. Measured with Gemma, 3 runs per case: overall 80.3% against 90.9% without it (explain 2/6 against 6/6, compare 0/6
  against 3/6, 4 COMPLETE-but-wrong against 3). The hints were removed; with `kind` sent and no hints the affected shapes returned to
  explain 6/6, compare 3/6, check a requirement 6/9, regression test 6/6.
- Regression tests: the host runs the supplied check before the edit and after it; the new test must be a named failing test that was not failing
  before (a collection error or a passing test is PARTIAL). On the 6 regression runs the host's verdict agreed with the hidden reference
  grader 6/6 (host `reproduces_bug` true, hidden reference: fails on the bug and passes with the fix).
- Held-out check on this repository (6 path-and-value lookups, Gemma): 6/6 correct; 3 of 6 first ended PARTIAL because the model cited in prose
  ("at line 19"). The host now also accepts "file ... line N" phrasing when the line was actually read.

## Editor safety, Release 1 (frontier-incident fixes) — 2026-10-05

Changes under test: truncation detected and zero edits applied, strict transactional edit protocol, destructive-edit guard, cumulative host-written reports,
packet for in-place jobs, post-edit verification of `old -> new` mappings, a regression test must define the failing test on an added line, checks run
without bytecode caching. Gemma 4 12B, 16K, work preset, 3 repeats.

**Frontier-incident benchmark** (`benchmarks/eval_incidents.py`, synthetic fixtures, 12 jobs: the oversized ten-change task and the same work split into three bounded jobs):

| | Before the mapping check | After |
| --- | --- | --- |
| Correct | 6/12 | 6/12 |
| Honest (correct, or not claimed COMPLETE) | 9/12 | 12/12 |
| COMPLETE but wrong | 3 (all: renames partly undone) | 0 |
| Destructive-edit escapes, partial applications, silent truncations | 0, 0, 0 | 0, 0, 0 |
| Truncations detected | 1 | 0 |

- The oversized task did not overflow the output cap with Gemma on this fixture (it finished in about 17 s); the real failure came from a larger file and much longer
  blocks, so truncation, destructive replacements and half-applied jobs are covered by deterministic replays (`tests/test_edit_incidents.py`) rather than by this live case.
- The only live false-COMPLETE class found was the model reporting COMPLETE with some of the requested renames undone; the host now verifies stated mappings.

**`eval_junior.py` regression guard** (22 cases x3): 89.4% correct against 90.9% before (one run), COMPLETE-but-wrong 3 (all `cmp-reports`, a model limit, unchanged), median 6.1 s.
Editor shapes: 25/27 (mechanical 6/6, guard 6/6, fix a failing test 8/9, regression test 5/6) against 27/27 earlier, with zero editor false-COMPLETE:

- `ft-cache` once ended PARTIAL after three SEARCH mismatches and one generation that ran to the 4096-token cap; the report listed every turn, said the output was cut off and wrote nothing. The shape was 8/9 in an earlier run too.
- `rt-retry-sleep` once ended PARTIAL because the model edited an existing test to fail instead of adding one. Before the fix the same run was accepted (the host's red check counted any newly failing test). That
  run exposed that checks could execute stale bytecode when a same-size edit follows a baseline run within one second; checks now run with `PYTHONDONTWRITEBYTECODE=1`.
- Caveats: one model, two small fixtures, 3 repeats; the live runs cannot reproduce the original truncation, so the incident fixes rest on the replay tests. The 8K output cap and JSON edits were not evaluated, as planned.

## Editor reliability, Release 2 (context packing, gate, workspace hardening, wrappers) — 2026-10-05

Changes under test: context packing (whole file when it fits the budget, named texts always shown, read-only references), the complexity gate with proposed split, deletion
semantics, dependency hashes, revalidation, post-apply checks and revert, MCP wrappers, a repair skipped when time is short. Gemma 4 12B, 16K, 3 repeats.

| | Release 1 | Release 2 |
| --- | --- | --- |
| Incident benchmark, correct (12 jobs) | 6/12 | **12/12** (the three split jobs now all pass as one outcome, 3/3) |
| Incident benchmark, COMPLETE but wrong / destructive escapes / partial applications / silent truncations | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `eval_junior`, correct (66 runs) | 89.4% | **92.4%** |
| `eval_junior` editor shapes | 25/27 | 26/27 (mechanical 6/6, guard 6/6, fix a failing test 9/9, regression test 5/6) |
| `eval_junior`, COMPLETE but wrong | 3 (all `cmp-reports`) | 3 (all `cmp-reports`) |

- The rename job that was COMPLETE-but-incomplete in Release 1 is now correct in all runs: the model sees the whole file and the notes about texts it cannot find, so the mapping check had nothing left to
  catch (it remains as a safety net). The one regression-test miss is the same honest PARTIAL as before (the model edits an existing test instead of adding one).
- The gate did not trip on the incident benchmark's one-job task, which Gemma completes correctly in about 18 s. It is calibrated on the two real failures (11 behavioural clauses / 2.9k characters, and 9 renames + 8
  mapped values + 8 behaviours) against every job that succeeded; with two positive examples the thresholds are a starting point, to be recalibrated from `local-worker incident` records.
- **Manual round trip through the MCP tools on a scratch repository (live service):** an implement task with nine renames and nine behaviours was refused before any model call with a four-unit proposed split;
  `fix_failing_test` returned COMPLETE with the tree untouched and a packet saying `review_patch_then_apply_result`; `apply_result` after a concurrent edit refused with the conflict list; with the file restored,
  `apply_result(revalidate, run_checks)` applied it and the post-apply check passed; `revert_result` restored the original bytes. A first attempt used `python` as the test command and the check was reported
  `blocked` (`No such file or directory: 'python'`): checks run with the service's PATH, so frontier-supplied commands should name an interpreter that exists there.
- Not measured: automatic decomposition (not built), the 8K output cap, JSON edits, line-anchored SEARCH.


## Editor experiments, Release 3 — 2026-10-05

Five things were tried on a workload that really overflows the output cap: `benchmarks/eval_editor_experiments.py` builds a synthetic file of N handlers (about 25 lines each) and asks for the same structural change
(wrap in try/catch with that handler's number) in every one. Variants are set per request and checked from the job's own records. Gemma 4 12B, 16K, 3 runs per cell.

| Variant | 8 handlers (about 4.3k output tokens) | 16 handlers (about 9k) |
| --- | --- | --- |
| Release 2 code (no continuation), 4K cap | 0/3 (all cut off at 4096, nothing applied, 68 s each) | not run |
| **Continuation**, 4K cap | **3/3** (2 generations, 79 s) | **3/3** (3 generations, 155 s) |
| 8K cap, no continuation | 3/3 (one generation, 70 s) | 0/3 (cut off at 8192, 131 s, nothing applied) |
| 8K cap with continuation | 3/3 (70 s, never needed) | 3/3 (2 generations, 147 s) |
| Continuation + JSON format | 3/3 (76 s) | 3/3 (154 s) |
| Continuation + line-anchored matching | 3/3 (76 s) | not run |

- **Continuation is the fix.** It turns the incident's failure (everything lost) into a normal completion at the cost of one or two short extra turns. The 8K cap helps only tasks between 4K and 8K tokens, and with
  continuation it saves about 5% of the time on the largest case, so the default stays 4096. The new defaults reproduced these results (8 and 16 handlers: 3/3 each).
- **The complexity gate was blind to this workload** (it scored the task as 3 concerns) and **refused work the model does well**: on a gate-tripping 15-change task over the incident fixture, refusing took 1 s and
  produced nothing; one-shot was 3/3 correct in 20 s (about 1,100 output tokens). It is now advisory by default.
- **Automatic decomposition did not beat one-shot.** Three versions on that task, 3 runs each, all 0/3 correct (always PARTIAL, honest): units with only their own clauses lost what "these exact labels" refers to;
  units shown the whole task did each other's work and failed on blocks whose text was already changed; with tolerant units every part landed except the test-file label updates, which were only partly done. One-shot with
  continuation remains better (3/3, about 20 s against about 37 s and 5 generations). It stays as an opt-in experiment.
- **Line-anchored matching** is safe: replaying 581 recorded SEARCH blocks of earlier runs, 540 matched uniquely and all 540 were whole-line aligned (36 needed the whitespace-tolerant path, 5 were ambiguous, none cut
  through a word); the live runs since recorded 120 exact matches, all whole-line. Editor shapes with line mode: 25/27 against 26/27 (the difference is the known flaky `ft-cache` run). It is now the default.
- **JSON edit format** matched the text format on the heavy workload (same correctness, tokens and time) but was clearly worse on ordinary small edits: editor shapes 20/27 against 26/27 (fix a failing test 6/9, regression
  test 2/6). It stays off.
- **Workspace chaining**: the incident benchmark's split case run as three chained jobs and applied once: 9/9 jobs correct, 3/3 as one outcome, same time as three separate jobs, one apply instead of three.
- **Final defaults, full regression guards:** incident benchmark 12/12 correct (0 false-COMPLETE, 0 destructive edits, 0 partial applications, 0 silent truncations); `eval_junior` 89.4% correct (editor shapes 26/27, the same as
  before; the movement from 92.4% is in investigator shapes whose code did not change, so run-to-run variation).
- Caveats: one model, synthetic fixtures, 3 runs per cell. The heavy workload is a repeated structural change, one kind of overflow; tasks that overflow because they need many different long edits may behave differently.
  The gate thresholds still rest on two real failures.

## Architecture phase 1: regression guard and coder-model comparison — 2026-10-05

**Regression guard after the phase-1 changes** (transactional apply, Tier-0 tools, spec, execution context, Board refusal; service restarted): `eval_junior`, 22 cases x3, Gemma 4 12B, 16K.
59/66 correct (89.4%), 4 COMPLETE-but-wrong, median 5.7 s, 0 invalid model rows. Same as the previous guard (89.4%) with the same weak shapes: compare 3/6, check_requirement 6/9, regression_test 5/6 (`rt-retry-sleep`).
The `fix_test` frame context could not move this score: its fixtures (9/9 before and after) are small enough that the whole file already fits the context.

**Free code-specialised models vs Gemma, editor shapes** (mechanical, guard, regression_test, fix_test; 27 runs each, 16K, model per request verified from effective-config events; Gemma's rows are from the guard run above):

| | Gemma 4 12B | qwen2.5-coder 14B (Q4) | qwen2.5-coder 7B |
| --- | --- | --- | --- |
| Correct | **26/27 (96%)** | 16/27 (59%) | 16/27 (59%) |
| COMPLETE but wrong | **0** | 5 | 7 |
| mechanical / guard / regression_test / fix_test | 6/6, 6/6, 5/6, 9/9 | 3/6, 6/6, 1/6, 6/9 | 4/6, 6/6, 0/6, 6/9 |
| Median seconds | 4.6 | 4.6 | 3.2 |
| Local tokens | 34.5k | 22.3k | 25.4k |

- Verdict under the pre-registered rule (a lead of >= 10 points, or fewer false-COMPLETE without losing correctness): **Gemma stays the Editor model**; the coder models trail by 37 points and produce COMPLETE-but-wrong reports (a rename left half done in `mc-rename-pct`, regression tests that do not fail on the bug).
- Not run: the incident benchmark on the coder models (the decision did not need it), thinking variants, and any other quantization. Two tiny fixtures and 3 repeats: a clear gap, not a general claim about code models.
- The aliases `coder` and `coder7` stay registered for experiments; no phase uses them. Qwen2.5-coder 14B fits in 12 GB at 16K with the service's existing flash attention and q8 KV cache (no sudo needed).

### Guards after removing auto_split / JSON edits / word matching and indexing nested definitions — 2026-10-05

`eval_junior` (22 cases x3, Gemma 4 12B, 16K): 59/66 correct (89.4%, unchanged), COMPLETE-but-wrong 3 (was 4; all `cmp-reports`), honest 95.5%, median 5.6 s, 0 invalid model rows; the same weak shapes
(compare 3/6, check_requirement 6/9, regression_test 5/6). `eval_incidents` (3 repeats): 12/12 correct, 0 false-COMPLETE, 0 destructive escapes, 0 partial applications, 0 silent truncations.
Index changes under test: nested Python functions/classes and imports inside function bodies are indexed (VERSION 4), refreshes come from an in-memory index. One configuration, one run each: no movement, not an improvement.
`eval_intel` (lexical provider, 7 hand-checked cases on this repository): precision/recall 0.86 -> 1.00 after nested definitions; part of its gold was corrected after the first run, so this is a harness check, not a measurement.

## Language server vs the CodeIndex, code intelligence — 2026-10-05

`benchmarks/eval_intel.py`: 8 cases about this repository (definitions, callers, implementations), gold from hand reading plus, for the discriminating case, an AST scan independent of both providers. basedpyright 1.x (pip, run in a scratch
virtualenv with `--stdio`), Python only, one repository, 3 repeats. Providers: `LexicalProvider` (CodeIndex, shipped) and `LspProvider` (thin client + server, lexical fallback).

| | Lexical | LSP (basedpyright) |
| --- | --- | --- |
| Mean precision / recall (8 cases) | 0.98 / 0.97 | 1.00 / 1.00 |
| callers of `workspace.apply` (two functions are named `apply`; 29 true callers) | P 0.81, R 0.76 | **P 1.00, R 1.00** |
| Other cases (definitions, callers of `_commit`/`final_diff`, subclasses of `Strict`) | 1.00 / 1.00 | 1.00 / 1.00 |
| Latency per call (median) | under 1 ms | 20-70 ms warm; first server start plus project analysis about 1.5 s |

- The server is exact where names collide; the CodeIndex cannot tell same-named functions apart and so mixes in callers of `textedit.apply`. On the unambiguous cases they tie, so the gain is confined to ambiguity.
- Found by running a real server, not visible against the fake one: a client must answer `workspace/configuration` with one entry per requested item (answering null made pyright find no references at all); pyright advertises
  `implementationProvider` and does return subclasses, but includes the queried class itself; requests are now gated on advertised capabilities and an empty `implementation` answer falls back to the index's inheritance edges.
- The gold for the first 7 cases was partly corrected after seeing the lexical run; only the `apply` case is independent of the providers.
- **Promotion rule outcome:** LSP stays a Tier-0 tool provider (approved per project through the profile `lsp` field). It is NOT used inside the pipelines: `eval_junior` retrieval uses the CodeIndex, the warm server lives in the service
  (not in the per-job engine process), and there is no downstream measurement showing it would help. TypeScript, Go and Rust servers were not tried.

### Guards after the skills/ layout move — 2026-10-05

Every skill module moved under `hub/skills/` and all imports were rewritten; the engine subprocess and service ran from the new paths (service restarted). `eval_junior` (22 cases x3, Gemma 4 12B, 16K): 61/66 correct (92.4%; 89.4% before),
3 COMPLETE-but-wrong, median 6.1 s, 0 invalid model rows; check_requirement 8/9 (was 6/9), the other shapes unchanged. `eval_incidents` (3 repeats): 12/12, 0 false-COMPLETE, 0 destructive escapes, 0 partial applications, 0 silent truncations.
The move changes no behaviour, so the +3 points on check_requirement is run-to-run variation, not an improvement. Caveat: the incident benchmark ran while a second, abandoned `eval_junior` was also queued on the hub (my double launch), which slowed both; correctness is not affected by queueing.
