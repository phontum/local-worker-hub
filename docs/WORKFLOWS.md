# Advanced workflows

Fresh-install instructions and the project overview are in [README](../README.md).

## Start using it

```sh
local-worker dashboard
```

Open http://127.0.0.1:8765 and enter the one-time pairing code. The dashboard shows
task history, brief, report, accessed files, checks, review decisions, measured
context, token usage, GPU/CPU/WSL memory and GPU history. Browser sessions expire
after eight hours or a service restart. Generate another code to reconnect.

After manual MCP registration, new Codex/Claude sessions load the registered `local-worker` MCP server. Its tools
are `submit_job`, `get_job`, `get_result`, `read_artifact`, `get_project_profile`,
`summarize_result`, `cancel_job`, and `record_review`. The CLI also provides a personal web-only mode from any directory:

```sh
local-worker "Hey! What is the weather today in Novi Sad?"
local-worker --extended "Compare these public sources and explain the differences."
```

Bare prompts select Personal with a 16K work preset (300s cumulative model time);
`--extended` selects 32K, thinking and a fresh requirements review. Personal has public search/fetch, no repository,
private account, shell or messaging access. Questions with missing essential
details, such as the weather location, ask for that detail rather than guessing.
Personal searches are public briefs sent to the configured provider. Engineering
requests use explicit roles or `--read-only`, `--write`, or `--repo`.

Engineering examples from an intended repository root:

```sh
local-worker --read-only 'Read src/config.ts and identify the timeout default.'
local-worker --write --allow-path src/config.ts --timeout 600 \
  'Change only the timeout default from 30 to 45; preserve other behavior.'
local-worker --role researcher \
  'Find the official React documentation for useEffect cleanup; cite its URL.'
local-worker --role validator --checks checks.json \
  'Summarize the recorded typecheck outcome; report failures accurately.'
```

Supply substantial tasks through quoted stdin. A check file is an array of approved
argv definitions, never a shell string:

```json
[{"name":"typecheck","argv":["npm","run","typecheck"],"cwd":"frontend","timeout":120}]
```

`--async` returns a job ID. Use `local-worker status ID`, `result ID`, `cancel ID`,
or `review ID accepted --notes 'Verified evidence and tests'`. Cancellation does
not undo edits already made. Failed edits are never automatically rerun.

For a small, fully scoped implementation, supply exact edit paths and reviewed
check argv in one handoff:

```sh
local-worker --role editor --workflow implement --repair-attempts 1 \
  --allow-path src/component.tsx --checks checks.json --timeout 600 --async \
  'Make the specified change; preserve other behavior.'
```

The coordinator edits, runs supplied checks without model tokens, starts a
fresh read-only review, and permits one targeted repair when evidence supports it.
`--investigate-first` adds scoped read-only discovery. Two repairs remain an
experiment until measured. The frontier inspects the diff and tests for final
acceptance. Freshness checks stay inside the tool; editors need not copy hashes.

## Cheap validation and reviewed project checks

Validator jobs now return deterministic command evidence by default, even when
Ollama is unavailable. They use zero model tokens. Set `summary_mode=local` only
when a model interpretation is useful, or analyze an already finished job without
rerunning commands:

```sh
local-worker summarize JOB_ID --idempotency-key unique-analysis-request
local-worker result ANALYSIS_JOB_ID
local-worker artifact JOB_ID check-0.log --offset 0 --limit 4000
```

Results default to a brief view capped at 2 KiB; `detail="summary"` remains an
8 KiB overview and status is capped at 2 KiB. MCP `wait_job` and CLI
`local-worker wait ID` wait up to 30 seconds for progress or completion. Use
`local-worker result ID --full` or MCP `get_result(detail="full")` for legacy raw
evidence. Artifact offsets are bytes; follow `next_offset` while `has_more`.
Read-only commands neither initialize config nor start services. If unavailable,
run `local-worker start`. External approval waits cannot be observed by the hub;
CLI status/result can distinguish actual worker activity from a stalled MCP call.
Transport failures get at most one identical safe retry, not a new job.

Private reviewed profiles live in `~/.config/local-worker/projects`. Create a
profile from [the generic template](../examples/project-profile.json), review its commands against your
project guide, then inspect its current hash. `local-worker profile seed` only
initializes the private directory and preserves existing profiles:

```sh
local-worker profile show --repo ~/dev/my-project
local-worker --role validator --repo ~/dev/my-project --async \
  --profile-hash REVIEWED_HASH --check-group typecheck --check-group unit \
  --failure-policy continue_independent 'Run the reviewed frontend checks.'
```

MCP equivalents are `get_project_profile(repo)` and `submit_job(profile_hash=...,
check_groups=[...], failure_policy="continue_independent")`. Changes to the profile,
package scripts, or source guide invalidate the hash, including while queued.
Profiles execute nothing when inspected and grant no edit scope. Submit only the
named groups you reviewed. Checks with `requires_test_database=true` require an
explicit `TEST_DATABASE_URL` for an existing loopback PostgreSQL database ending
in `_test`; dependencies, database creation and migration repair are not automatic.
Never supply production credentials. Missing prerequisites remain recorded blockers.

Checks accept `depends_on` names. With `continue_independent`, unrelated checks
continue after failures, and failed prerequisites cause explicit skips. Existing
ad hoc jobs remain fail-fast. Use prerequisites for browser tests that require a
build. A check with `guard_next_dev=true` blocks a Next build while a development
server uses the same repository.

Dashboard history is compact; selected-job details, heartbeat, deadlines and check
progress are fetched separately. Logs load only when expanded. A quiet check is
not declared stuck. Cancellation/restart preserve completed check evidence.

Run `.venv/bin/python benchmarks/run_pairs.py` for six paired controlled tool
exercises. Evidence is private under hub state. Tool timings omit frontier
deliberation; unavailable frontier totals stay blank. `record_review` accepts
`measurement_source=manual_estimate` to keep estimates separate from measured
matched baselines. No session logs are imported and no net savings are invented.

For workflow comparison, run `.venv/bin/python benchmarks/run_workflow.py`
with a bounded subset such as `--cases simple --variants baseline 32k
--repeats 2 --max-seconds 900`. The runner creates disposable repositories,
checks exact outputs, and records local usage and elapsed time. Review each
diff and check log before accepting a run; frontier token totals and net savings
remain unknown unless separately measured. `--model-context 32768` and
`--model-thinking off` are per-job experiments; the installed defaults remain
16K and one repair.

## Roles and boundaries

| Role | Available model tools |
| --- | --- |
| Investigator | Scoped reads, globs and literal search |
| Editor | Investigator tools plus writes/unique text edits to exact authorized files |
| Validator | No tools; trusted check argv, deterministic reports, optional local analysis |
| Researcher | Configured public search and hosted page extraction; no repository or context attachment |
| Personal | General public questions and configured public search/fetch; no repository or private account access |

There is one FIFO executor, one Ollama inference slot, one resident model at a time
(Gemma 4 12B by default; Qwen3.5 9B for board triage and some proposals), and a
16K default model context. Eight tool rounds and at most 18 model steps bound exploration;
older large tool outputs are shortened and a final tools-disabled turn is reserved.
Edits require a fresh observed read; symlinks, hardlinks, secret files and excluded
directories are blocked. The model has no shell, native browser or recursive agent
tool. Validation commands are trusted programs, **not an OS sandbox**: select them
carefully. Use `guard_next_dev=true` for Next builds that share a development directory.

Research queries go to the configured hosted provider. Give only public library/version names
and sanitized generic examples. It retrieves text, rather than running a logged-in
browser. No OpenRouter account or cloud inference is required. Public URLs are
validated before requesting fetches; provider failures stay visible in the report.

The hub uses direct Ollama inference with role-bound adapters. A live compatibility
test on installed OpenCode v2.0.22 connected the MCP server but sent an empty tool
list to the model. The original OpenCode configuration is preserved; the archived
launcher and experimental adapter are not the active execution path. Revalidate
before switching back. Do not substitute a permissive legacy agent.

## Measurement and acceptance

Local input/cache/output counts come from actual Ollama usage, including unsuccessful
completed responses and recovery. Interrupted requests may not return token counts
and are not extrapolated. Context is the timestamped last completed request, not a live
exact tokenizer view. Native inference now streams thinking/content and scoped tool results. Token counts are recorded only
from a completed response; throughput includes prompt prefill and cold load.
Hardware samples describe the whole device/WSL instance, not just this helper.

`~/.config/local-worker/pricing.json` holds dated editable comparison rates. New installations leave comparison rates unset. Supply independently verified,
dated rates for your chosen comparison model before using the estimate.
Local tokenizer/cache behavior differs from a frontier model, so the result is an
API-equivalent workload estimate. It excludes cloud tools, power and orchestration.
It is not subscription dollars saved.

Actual estimated frontier tokens/cost avoided require `record_review` to supply a
matched baseline and delegated frontier total including orchestration, review,
retry and takeover costs. Negative savings are retained. No measured baseline has
been invented. Historical imports remain unverified and omit unavailable usage.

Live acceptance checks covered file discovery, a one-file edit followed by a passing
Node test with unrelated files preserved, failing validator output, public official
documentation search/fetch, and running-process cancellation. A larger React edit
failed exact matching twice; the primary completed it and recorded takeover. Start
with discovery, summaries, checks and small exact edits; this is not evidence that
9B can reliably implement larger frontend changes.

## Maintain and restore

Source lives in the checkout; private config is `~/.config/local-worker`; state is
`~/.local/state/opencode/local-worker`. Existing run directories are preserved and
legacy summaries can be imported once during service preparation. Hardware samples retain seven days; job
summaries remain. Logs can contain source. Remove old job artifact directories only
when finished with them; keep the database if retaining history. Backups are private.

The user systemd service is `local-worker-hub.service`. It starts on user login and
the CLI starts it on demand. WSL must be running. The listener binds only to
127.0.0.1:8765, validates Host/Origin, authenticates API callers and uses HttpOnly
SameSite browser cookies. Source/report access requires pairing. No credentials or
Codex sandbox settings were changed.

```sh
systemctl --user status local-worker-hub.service
systemctl --user restart local-worker-hub.service
cd ~/dev/local-worker-hub
.venv/bin/pytest -q
cd frontend
npm run build
npx playwright install chromium
node verify-browser.mjs
```

Roles and prompts are editable in `~/.config/local-worker/roles.json`. Keep the
16K context and single inference slot until measurements justify changes. For an existing managed migration with an installation manifest, restore its
original launcher and Codex/Claude configuration using:

```sh
cd ~/dev/local-worker-hub
.venv/bin/python -m hub.install uninstall
```

Uninstall refuses to overwrite files modified since installation. The installation
manifest records backup paths and original modes; project/history remain available.

## Complete handoffs and live trace

`--preset small` allows 120s of cumulative model work; `--preset work` allows
300s; `--preset extended` allows 300s with opt-in 32K context, thinking and a
fresh requirements review. An explicit
`--timeout` overrides that model budget. Check timeouts remain separate. Requests
without a preset preserve legacy timing. One repair is the supported default
for implementation workflows; no scope expansion or automatic 32K escalation.

```sh
local-worker --role editor --preset work --workflow implement --repair-attempts 1 \
  --read-path src --read-path tests --allow-path src/settings.ts \
  --allow-path tests/settings.test.ts --checks checks.json --async \
  "Implement the specified settings behavior and its regression test."
local-worker --read-only --preset work --repo ~/dev/my-project \
  --read-path backend --evidence-job FINISHED_JOB_ID \
  "Investigate the recorded failure; cite verified source and explicit unknowns."
```

`read_paths` restricts all file reads/discovery. `evidence_job_ids` attaches up to
four finished same-repository reports/check results, with read-only paged check
logs; source freshness still requires a new read. Reviewed profile groups also
work for Editor; profiles never authorize edits. Use `--handoff-id` for related
jobs; put a matched baseline on one review, including all frontier costs.

The paired dashboard displays Local model trace live, grouped by phase/step.
Thinking-disabled phases show no thinking. CLI `local-worker trace JOB_ID` and
MCP `read_trace` expose the same saved trace, paged by byte offset. Reports and
compact results exclude trace text. Traces are private, capped at 16 MiB per
job; browser rendering is bounded to 60,000 characters, with saved pagination.
Interrupted traces remain available; no token counts are inferred for unfinished
responses. Tool results, check outcomes, reviews and traces are separate evidence.

For ambiguous edits, read a relevant range and use `replace_lines`. Whole-file
replacement of an existing file requires complete observed contents. The
`scoped-diff.txt` artifact includes only authorized files, including new files.

Use `benchmarks/run_delegation.py` for representative discovery, saved-log diagnosis,
settings/view and backend-fix fixtures at 16K/32K. Freeze engine/prompt versions
for comparisons, inspect diffs/checks and record reviews; mechanical correctness
alone is not acceptance. The latest single live samples remain mixed, so 32K
stays opt-in and no frontier-token savings are claimed without a matched baseline.

Extended answers now run two passes: an initial answer, then an independent
review with the full original task and an index of recorded tool evidence.
The reviewer can read saved evidence without rerunning it, or obtain additional
scoped evidence, and evaluates every original requirement as met, unmet or
unknown. Met claims must include exact supporting quotes from observed tool
results or the final answer; common explicit bullet/list counts also receive
mechanical checks. These guards establish that quotes exist, not that the small
model interpreted all evidence correctly. It publishes a corrected final answer; any unmet/unknown item prevents
`COMPLETE`. This is local quality control; frontier acceptance is still required.
Initial work receives at most 55% of the shared model budget, leaving time for
review. A missing or interrupted review cannot publish the draft as successful.
The draft, requirements assessment and both phases' traces stay private and are
available in the paired dashboard. Tool evidence is reused, thinking is not.
Thinking remains enabled for extended work and review; a forced final JSON
formatting turn disables thinking to reserve output for the actual answer.
Its effective step configuration is recorded. Empty/malformed structured output
does not crash the parser or repeat formatting indefinitely. The checklist assesses
the original task only; runtime JSON/report instructions are transport protocol.

```sh
local-worker --extended "Answer my question and follow these requirements: ..."
local-worker --review "Review this answer too, using the normal 16K context: ..."
local-worker --extended --no-review --model-thinking off "Use a single fast pass."
```

MCP callers use `review_pass=true` to opt in at 16K, or
`execution_preset="extended"` for 32K + thinking + review defaults. Explicit
`review_pass=false` and `model_thinking=false` override those defaults.
Editor implementation workflows already review the original requirements after
edits/checks; they do not gain an extra pass. Direct Validator jobs still execute
recorded checks with no model tokens.

Public search adapts the provider's required `objective` field. `fetch_web`
defaults to `mode="current"`: it reads the exact public URL directly, with
DNS-pinned public connections, checked redirects, no credentials or environment
proxies, a 30-second total deadline and a 2 MB response bound. It keeps up to
24,000 extracted characters and returns 6,000-character pages;
use `start` and `characters` to inspect relevant later content, or `find` to
locate a literal term with surrounding text. Cached pages within
a phase do not spend another fetch request. Five retrieval attempts and three
searches per phase remain the limits. This uses [Exa's public search and webpage
extraction](https://github.com/exa-labs/exa-mcp-server), without authenticated
browser actions. Blocked origin reads fall back to hosted extraction, explicitly
marked **UNVERIFIED CURRENT CONTENT**. `mode="cached"` opts into provider text.
`WEB_EVIDENCE` records the method, exact URL, observation time, HTTP status,
cache headers/age, content hash and whether current evidence is eligible. A cache
age over 15 minutes is ineligible. Provider retrieval time does not establish when the origin was
updated, and extraction can omit dynamic content. The worker must report any
required fact it cannot establish, rather than turn a snippet into confirmation.

For current questions, final JSON includes `web_claims: [{url, source, quote}]`.
Only exact quotes from eligible origin evidence observed in the last 15 minutes
qualify as current observations, preserving full-line negations; search snippets,
self-citations and cached quotes cannot qualify. Unreviewed answers display those
observations. A fresh reviewed answer keeps the model's synthesis and requested
format when its current source claims pass the provenance checks. A rejected
source does not erase unrelated confirmed observations. There is one verification
reminder within the existing model/tool budget. Origin content can still be served from a CDN or omit JavaScript;
there is no authenticated or rendered browser in this path. The dashboard links
the source-verification artifact, including exact quotes and retrieval metadata.

Before web research, the initial model calls `plan_web_task` to interpret the
original prompt and record requirements, exact prompt anchors, acceptance checks,
whether evidence must be current, search breadth, strategy, output format and
stopping conditions. The LLM chooses those details; scripts do not prescribe
price, stock, source-count or domain-specific acceptance rules. The independent
reviewer checks the initial plan against the original prompt, records its own
corrected plan, reuses valid evidence and investigates gaps. Planning happens
inside each existing pass and its existing time/context budget, not a third model
pass. Each pass may revise its plan once with an evidence-grounded reason; both
versions remain visible in dashboard activity. Stable/historical facts and
present-changing facts can require different freshness decisions. Page bounds
are exposed in the tool schema before reads. Greetings and self-contained answers need no research plan. A plan grants
no new tool permissions. Both plans appear in the paired dashboard and are saved
as `work.research-plan.json` and `answer-review.research-plan.json`.

Transient inference gateway failures receive one identical request retry per
job, inside the existing timer; no tools or edits are repeated. The retry has a
separate trace phase, and bounded upstream errors are saved in private job events
instead of being hidden behind an opaque HTTP 502. A second failure stays failed.

The default Exa endpoint is **free keyless MCP**, with no configured Exa account,
OAuth or API key. Only public search/fetch tools are enabled, not paid `agent_run`.
[Exa documents free rate-limited keyless access](https://exa.ai/docs/get-started/exa-mcp).
LangSearch is an optional free API provider requiring a private account key and
subject to a daily allowance; its repository supplies integration documentation,
not a self-hostable search-engine implementation. See [LangSearch](https://github.com/langsearch-ai/langsearch).

```sh
local-worker web status
local-worker web configure langsearch  # prompts for a hidden key if needed
local-worker web configure exa        # restores keyless search
```

Provider selection is snapshotted per job. The LangSearch key stays in
`~/.config/local-worker/langsearch-api-key` (owned by you, mode 600), outside model
prompts, traces, job snapshots and dashboard responses. LangSearch full webpage
text can satisfy `fetch_web(mode="cached")` without another provider call. Snippets cannot.
Other cached reads and blocked-origin fallback use free Exa extraction; changing the search
provider does not buy a paid extraction service. Exa rate limits and transient
failures are reported as failures, rather than source evidence. If a private
LangSearch key is already configured, search explicitly reports its free fallback
and the actual provider. A five-minute per-job Exa cooldown spans both passes to
avoid repeated failing calls. Default provider configuration remains unchanged.
Missing fallback credentials or poor results stay explicit gaps; no URLs or
facts may be invented to compensate. [SearXNG](https://docs.searxng.org/) is a self-hostable
open-source alternative; it would require an instance and a separate adapter.


## Host-driven pipelines (default)

Small local models skipped searches, trusted stale snippets, converted units in their heads and invented citations
when they drove the procedure through tool calls; Ollama's tool-call layer for Qwen3.5 and Gemma 4 also has open
bugs. The default paths therefore use no tool calling. The host does the procedure; the model makes a small
schema-constrained decision and then reads and writes text.

**Ask (Personal and Researcher)**, `hub/ask.py`:

1. Decide (one JSON call): does this need the web, up to two search queries, the reply language, and optionally a
   structured provider with its arguments (see below). A follow-up chat message also sees up to six earlier turns.
2. Search (`hub/retrieval.py`): the configured provider first, then the others, with every fallback reported and any provider failure
   isolated (a rate limit never ends the job). With SearXNG chosen the order is SearXNG, LangSearch, Exa; with Exa, Exa then LangSearch.
3. Read the best three pages live through the DNS-pinned origin reader. Main text is extracted with trafilatura.
   A page that cannot be read falls back to a labelled third-party copy, or to its search snippet.
4. Rank 900-character passages with BM25 and give the model numbered excerpts with their kind and read time.
5. Answer (one JSON call): the answer, the excerpt numbers used, whether the question was answered, and
   optionally one follow-up query, which triggers one more search round.
6. The host removes any source lines or unfetched links the model wrote. It appends `Sources:` from the excerpts
   actually used, with real URLs and local "as of" times, and converts imperial units and 12-hour times (unless
   the user asked for them).

Typical time is 10-20 s. COMPLETE when the model answered; PARTIAL when the sources did not contain the answer.
`--extended` gives the answer step 32K and thinking, without the strict reviewer. `--verify` keeps the older strict
research loop with origin-proof quotes; `--review` adds the independent requirements review. `--agent-loop` uses
the older model-driven tool loop, for comparison.

**Structured providers**, `hub/providers/`: for questions with an exact public data source the decide call can pick a
provider instead of a web search. `weather` (Open-Meteo: geocoding, current conditions and an hourly table for the day and
hours asked about, in the preferred units; an ambiguous place name is flagged in the data so the answer states which
place it used), `fx` (Frankfurter, European Central Bank reference rates; about 30 major currencies, no RSD; the host does
the arithmetic) and `clock` (local, no network: current time in up to three IANA zones). Provider data becomes a
numbered excerpt of kind `provider`, answered and cited like any page; the source shown is the provider's site and the
exact API request is kept in `ask.json` under `retrieval.provider`. Requests go through the same DNS-pinned public-only
transport and carry only a place, currency codes or time zones. If a provider fails, has no arguments or finds no result,
the pipeline falls back to web search and records why. `~/.config/local-worker/providers.json` with
`{"disabled": ["fx"]}` turns providers off. The strict `--verify` loop does not use providers.

**Pages that need JavaScript**, `hub/browser_page.py`: after the plain origin read, a page whose text is thin, whose HTML says to
enable JavaScript, or whose app root is empty is rendered in a locked-down Chromium (Playwright; `local-worker doctor` shows
whether it is installed). The browser never touches the network itself: every request it makes is re-issued by the same
DNS-pinned public-only client (redirects are validated hop by hop), only GET/HEAD for documents, scripts, stylesheets and
fetch/XHR are served, images, fonts, downloads, popups, service workers and cookies are off, and it is launched with a proxy that
is a local listener which only counts and closes connections, so a WebSocket or anything else that escapes interception fails
closed and is reported as `escaped_connections`. Limits: 60 requests, 2 MB per document, 1.5 MB per subresource, 6 MB and 20 s in
total, one browser at a time. Because the site still sees our client, rendering helps with JavaScript-only content and does
not help with sites that refuse the client (403), so those are not retried in the browser. Evidence has method
`origin-browser` and `javascript_rendered: true`. `~/.config/local-worker/browser.json` with `{"mode": "off"}` disables it and
`"always"` renders every page. The strict `--verify` loop does not use the browser: pages that need JavaScript stay unverified there.

**Product data**, `hub/product_extract.py`: JSON-LD, schema.org microdata and `product:price` meta tags are parsed on the host
(price as a decimal, ISO currency, availability, condition, seller, variant) and shown to the model as a host-written
`PRODUCT DATA` excerpt before the page text; the text stays visible, so a "sold out" banner next to an InStock offer is reported
as a conflict. Markup values are single-line and bounded before they reach the prompt.

**Dashboard chat**: the Chat view posts ordinary Personal jobs (`caller: dashboard`) from the paired browser, so every reply
is inspectable under Tasks. Its settings panel covers mode (small/work/extended preset), model (`model` on the request,
any registry alias, applied to every phase; `--model` in the CLI), context, thinking, review, strict verify, the older
tool loop and the board, rejects combinations the hub would refuse, and shows the equivalent `local-worker` command.
Earlier turns travel in the request's bounded `history` (six turns, 800 characters each; public web roles only), not in
`context`, which Personal still refuses. The conversation and settings are kept in the browser's local storage only.
Browser mutations require a same-origin request, so use the built dashboard, not `npm run dev` on another port.

**Replayable evaluation**: `benchmarks/eval_small.py --web record` stores search results, page text and provider
responses under the private state directory, and `--web replay` serves them back without network, so only the model
varies (`hub/web_fixtures.py`). The harness writes `~/.config/local-worker/web-fixtures.json` (with an expiry) for the
run, because job processes start with a minimal environment, and restores it afterwards. Job processes load `hub/` code
per job: do not edit it during a run.

`~/.config/local-worker/preferences.json` (optional): `{"units": "metric", "clock": "24h", "timezone": "Europe/Belgrade"}`.

**SearXNG**: `local-worker web setup-searxng` writes `~/.config/local-worker/searxng/settings.yml` with a random
secret and starts a container bound to 127.0.0.1:8888 (JSON on, limiter off). It then selects it as the search
provider. `local-worker web status` checks its health. Some engines (Brave, DuckDuckGo) often refuse self-hosted
instances; Exa remains the fallback. The strict `--verify` loop keeps using Exa.

**Investigator**, `hub/pipelines.py` and `hub/localize.py`, Agentless-style:

- The host builds a repository map (files and top-level symbols) and runs literal searches for identifiers named in the task.
- The model picks up to six line ranges; the host reads them through the scoped reader, which keeps evidence IDs and guards.
- The model answers and may ask for one more read round.

**Editor**, `hub/textedit.py`, aider-style:

- The host puts the authorized files in the prompt (regions around named identifiers for files over 400 lines).
- The model replies with `FILE:` plus SEARCH/REPLACE blocks, or WHOLE blocks for small or new files.
- Blocks apply by exact match, then a whitespace-tolerant match with indentation correction.
- Ambiguous or missing matches get one corrective turn with the exact error.
- Writes go through `ScopedFiles` (exact paths, symlink, hardlink, secret and freshness checks against the hash taken
  when the host read the file).

## Coding: checks decide

`--workflow implement` runs the edit, then the approved checks. Passing checks with a real diff mean COMPLETE; the
local review runs only with `--review` and its findings are advisory notes. A failed check triggers one targeted
repair (`--repair-attempts`) fed with the failing output, through the same text edit protocol.

## Hardware and runtime notes

- **Memory:** On this class of machine (12 GB VRAM, 16 GB RAM, WSL with about 8 GB), 9-12B dense models are the
  practical tier. 30-35B mixture-of-experts models with about 3B active parameters reportedly reach 30-40 tok/s on
  12 GB cards with experts kept in system RAM, but need about 18-20 GB of RAM in total. A 32 GB upgrade and a
  larger `memory=` in `.wslconfig` would allow testing that.
- **Ollama alternatives:** llama.cpp `llama-server` is reported about 10% faster than Ollama and allows MoE offload
  and speculative decoding. LM Studio matches llama.cpp. TabbyAPI/ExLlamaV3 is reported 30-60% faster on NVIDIA
  but needs EXL3 quants. The host pipelines only need chat plus JSON-schema output, so the backend can be swapped
  later after a measured comparison.

## Background delegation for Claude/Codex

```sh
local-worker delegate --read-only "Find where the retry timeout is set"   # run in the background; prints one JSON brief
local-worker watch JOB_ID                                                   # optional progress lines for Monitor
```

`delegate` submits, waits quietly and prints only the compact brief (status, findings, changed files, checks, next
action), exiting 0 complete, 2 partial, 124 timeout, 130 cancelled. In Claude Code run it as a background command and
keep working; Codex can use a background terminal. Over MCP use `submit_job`, then `get_job` (it reports
`eta_seconds` and an upper bound on queue wait) and `get_result`; MCP calls never block for long. For git state use a
Validator job with approved argv checks (`git status --short`), which costs no model tokens. One job runs at a time, so
a long job delays the ones behind it.

## Board deliberation (opt-in, rarely useful)

```sh
local-worker --board "..."                      # skeptic + challenger proposals
local-worker --board --board-mode full "..."    # four proposals
```

The board is available for Personal and Researcher only, with a 300 s budget and fixed 16K contexts. In the pilot it was
3-4x slower than the normal flow without a measurable benefit (`benchmarks/RESULTS.md`), so use it only for open-ended
tasks with several implicit requirements. Flow, one model call at a time:

1. **Triage** (Qwen, thinking off) skips the board for greetings, definitions and single-fact lookups; a skipped job
   runs the normal flow. If external facts are needed it proposes one short neutral search query.
2. **Scout:** the host runs that single search through the job's web ledger (no fetches, no tool loop). Only the
   skeptic proposer sees the snippets, marked untrusted.
3. **Proposals** in fresh contexts, tools off, thinking off, structured JSON: `lite` runs a skeptic (Qwen) and a
   challenger (Gemma); `full` adds a direct (Qwen) and first-principles (Gemma) proposer. Proposers never see each
   other and have no field for facts. Every requirement must quote the task verbatim.
4. **Arbiter** (Gemma, thinks then formats in a separate turn) sees shuffled anonymous candidates; nothing is voted.
   It decides whether web access is needed at all.
5. **Host drift report** (`board-drift.json`, plain code): explicit requirements the arbiter dropped, requirements
   without a verbatim task quote, and task clauses no requirement covers.
6. **One executor pass** answers in plain language with the board's briefing and the shared web ledger (6 searches,
   10 fetches, pages shared for 10 minutes). If the board decides no web is needed, web tools are withheld.
   There is no critic, repair or second review.

A board with fewer than two usable proposals, or an unusable arbiter, falls back to the normal single flow and is
recorded as degraded. Phase order (Qwen phases, then Gemma phases) keeps model swaps to one or two per job.

Host memory: llama.cpp keeps its prompt cache in host RAM and Ollama's default limit (8 GB) exceeds a small
WSL host, so the Linux OOM killer can end Ollama mid-job. Set `LLAMA_ARG_CACHE_RAM=512` (MiB) in the Ollama
service environment (verified to bound memory growth). As a fallback the hub unloads resident models when available
RAM drops below `LOCAL_WORKER_MIN_AVAILABLE_MB` (default 2500), at the cost of a reload (about 10s), and records
`memory-guard` events. `local-worker doctor` reports host memory and the configured cache limit. KV cache is already
q8_0 with flash attention for both models; `LLAMA_ARG_NO_MMPROJ_OFFLOAD=1` would move the vision projector to host RAM
(Qwen: -1.26 GiB VRAM, +0.87 GB RAM) and is not needed while a single model uses about 9 of 12 GB.
