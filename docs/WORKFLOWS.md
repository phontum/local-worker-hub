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

There is one FIFO executor, one Ollama inference slot, one Qwen3.5 9B model, and a
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
