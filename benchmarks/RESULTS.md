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
