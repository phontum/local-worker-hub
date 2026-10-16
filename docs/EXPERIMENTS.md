# Experimental learning and execution

All experiments default to off. Concurrency remains one. A reviewed private project
profile can include `experiments` alongside its existing constraints and check groups:

```json
{
  "enabled": true,
  "shadow": true,
  "sample_rate": 0.2,
  "daily_shadows": 10,
  "minimize": true,
  "mutation": true,
  "slicing": true,
  "graph": true,
  "recipes": true,
  "seconds": 60,
  "trials": 12,
  "mutants": 10,
  "cheap_check_names": ["unit"]
}
```

Changing a reviewed profile changes its reference; inspect it again before submitting
profile-bound jobs. Flags grant no file access or command authority. Checks still need
explicit approval, and writes still require exact paths. The `experiments` object is also
accepted on a structured delegation spec or job. The project policy controls shadow
sampling. All private candidates retain guarded apply and freshness checks.

## Observing tasks kept by the frontier

Install lifecycle adapters with `local-worker hook setup`. Claude uses command hooks;
Codex hooks need client trust in `/hooks`. Start a fresh session. Adapters activate only
in opted-in reviewed repositories. `UserPromptSubmit` captures a sanitized baseline before
edits and supplies an observation ID; the frontier calls `record_task_decision` with a
structured spec and its keep/delegate intent. It calls `finish_frontier_task` when the
original task is finished; the Stop hook takes the final snapshot after completion.
Interruptions are retained as incomplete observations, never converted into successes.

Without hooks, call `record_task_decision` before editing and `finish_frontier_task` after
completion. An empty observation scope captures the sanitized repository; a supplied
`observation_paths` list limits capture independently of editor scope. Snapshots exclude
secrets, links, binaries and dependency directories and fail when the file set changes
while being captured. Raw snapshots expire after seven days; metadata and history remain.

A kept task may be sampled for a private worker job. Normal jobs receive queue priority
and interrupt active shadows. Shadow jobs are omitted from normal history and statistics;
ordinary result/artifact endpoints deny access. Shadow workspaces cannot be applied or
continued. Only `get_task_comparison` exposes a finished comparison, after frontier
completion. Cancellation is marked censored. No live shadow answer is injected into hooks.

Shadow checks and experimental checks use Bubblewrap namespaces with no network and a
private candidate filesystem. Approved dependency directories mount read-only. If the
host cannot create the namespace or satisfy a check runtime, the experiment reports
unsupported or inconclusive; it does not retry on the user's real tree. Text inference
uses the same scoped host pipelines and local inference slot as ordinary jobs.

```sh
local-worker experiments decision --json-file decision.json
local-worker experiments tasks
local-worker experiments finish --task-id TASK_ID
local-worker experiments compare --task-id TASK_ID
local-worker experiments review --task-id TASK_ID --json-file assessment.json
```

## Structural supervision

Both normal final outcomes and shadow comparisons record files, changed symbols,
AST fingerprints, literals, edit overlap, missing/extra edits and added tests. Python
uses its AST; JavaScript/TypeScript use Tree-sitter. Other languages retain structural
or lexical evidence. AST equivalence is syntactic evidence, never proof of behavioral
correctness. Structural overlap percentages do not measure the fraction of the task solved.

`record_comparison_review` supplies reviewed labels: `completely_wrong`, `nearly_correct`,
`right_idea_wrong_implementation`, `correct_with_extras`, `partial_solution`,
`equivalent_solution` or `unknown`. Requirement assessments are `met`, `unmet` or `unknown`;
a met claim needs supporting evidence. Normal `record_review` accepts `patch_labels`,
`requirements` and `requirement_evidence` as well as its existing accept/reject/takeover
fields. After takeover, call `record_outcome` when your patch is finished. Dataset schema 2
includes richer supervision. Text/literals remain private and require source opt-in.
The paired dashboard's Learning view presents finalized observations and versioned recipes.

## Recipe promotion

Accepted real outcomes and explicitly reviewed equivalent task outcomes can be mined with
`propose_recipes`. With recipe experiments enabled, recording an accepted job also checks
for proposals. Initial mining recognizes repeated exact literal transformations over a
stable set of files and occurrence counts. It requires five distinct accepted examples,
three parameter bindings and exact replay across all examples. This deliberately narrow
learner leaves semantic/generalized recipes to later evidence; AST-leaf and import operations
are available as reviewed DSL primitives, not automatically inferred capabilities.

A recipe is a versioned declarative list of literal, AST-leaf or explicit import operations,
with parameter bindings, evidence and required checks. It cannot execute arbitrary code.
`review_recipe` approves/rejects/revokes/suspends the exact version. `run_recipe` requires
explicit file scope and checks and queues a zero-model private Editor job. The resulting
patch still needs review and `apply_result`; approval grants no real-tree write authority.

```sh
local-worker experiments propose --repo /path/to/repo
local-worker experiments recipes --repo /path/to/repo
local-worker experiments recipe-review --json-file approval.json
local-worker experiments run --json-file invocation.json
```

## Hybrid deterministic edits

Set `execution_mode` to `hybrid`, supply `deterministic_operations` and exact edit paths,
and optionally declare literal mappings. Each operation has `kind`, `path`, `old`, `new`,
`expected_count`, an optional AST `selector`, and optional `criterion_ids`. The host verifies
counts and syntax and commits the deterministic part transactionally in the private copy.
The model sees those receipts and performs remaining work. Final mappings and spec criteria
are checked again against the original baseline. `deterministic_only=true` skips inference
and executes approved checks. Undeclared mechanical changes are never guessed from prose.

## Patch minimization and causal validation

Minimization runs only after approved checks pass. It protects tests and declared mapping/
criterion paths, tries file groups then individual edits within the trial/time bounds,
requires repeatable cheap checks, and reruns the full approved checks before accepting a
reduction. It selects the smaller patch automatically only when all declared criteria have
mechanical verification. Otherwise it retains the original and saves an alternative.
`select_minimized_candidate` requires acceptance-criteria review notes and queues a private
continuation with all original checks; `apply_result` remains separate. Changed candidate
hashes or a stale workspace are refused.

Explicit `metamorphic_cases` contain a name, literal `edits` (path/old/new/count),
`check_names`, `expected` (`passed` or `assertion_failed`), optional exact `test_id`, and
`required`. They perturb only authorized candidate inputs and run only approved checks.
Expected assertion failures require the named test and parsed assertion evidence; syntax/
import failures never qualify. Required cases gate COMPLETE. Metamorphic and mutation
validation share the time budget and always leave the original candidate intact.

Mutation checks perturb parsed changed source: comparisons, booleans and numeric boundaries.
Syntax failures, missing runtimes and unparsed failures remain inconclusive. Set
`fix_paths` and `required_causal_test` to undo the fix while retaining the new tests and
require the named regression test to fail. A required causal gate prevents COMPLETE when
that relationship is unproved. Other mutation findings are advisory; surviving mutants
signal test gaps and do not themselves prove that the patch is incorrect.

## Coherent repository slices

Slice experiments preserve complete editable files, transitive Python and relative JS/TS
imports, test files and configuration. Reviewed `stub_files` can supply lightweight missing
dependencies but cannot overlap real editable files. Admission requires comparable recognized
baseline test collection in full and sliced candidates. Dynamic imports, unresolved aliases,
unknown external dependencies, more than 100 files, more than 10 MiB, or differing collection
fall back to the full private workspace. Only authorized file changes transfer back, and final
checks still run against the complete project. External runtimes may make a slice unsupported.

## Co-change graph and review effort

`explain_change_links` mines up to 1,000 reachable non-merge commits, excluding large commits,
locks/generated files and secrets, plus accepted normal jobs and reviewed equivalent task outcomes. File, symbol and
config nodes retain support, conditional frequency, lift and evidence. Suggestions require
five supporting observations, frequency at least 0.8 and lift above one. They never authorize
extra edits or checks. Git history initially supplies file relationships; richer nodes come
from captured task outcomes.

Every Editor result includes a review packet: per-edit path/symbol evidence, heuristic
criterion links, a one-line rationale, and a review score for unlinked edits, formatting,
files, hunks and changed lines. Links and rationales are explicitly unverified until frontier
review. Preserve formatting and prefer smaller changes. Actual frontier review time remains
the authoritative measurement; the current heuristic makes no learned time claim.

## Master switch and backend

`local-worker off` or `scripts/local-worker-switch off` gates new submissions and applies,
detaches owned MCP entries/hooks/managed instructions from global and known project configs,
cancels queued work and disables the user hub service. A current private job may drain before
the service exits. Shared control locks close the submission/apply race. Unknown custom
instructions are reported as residuals. `on` checks restoration merges before changing client
configs, restores only removed components and preserves unrelated edits. It never replays
cancelled jobs or restores obsolete uninstall backups. Start fresh frontier sessions afterward.

Inference now uses standalone llama.cpp at `127.0.0.1:8080` with no Ollama fallback. Internal
job messages remain normalized for historical compatibility; the gateway translates JSON
schemas, thinking configuration, streaming reasoning/content, assembled tool arguments and
provider usage. Truncated streams cannot authorize a tool call or a COMPLETE model response.
The managed server uses 16K context and one slot. To run explicit 32K experiments, reconfigure
its service with `scripts/setup-llama --context 32768` and the same binary/model paths first.
Historical Ollama measurements remain historical and do not establish backend parity.
