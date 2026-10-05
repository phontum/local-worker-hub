# Project instructions

This is the canonical development guide for Claude and Codex. `AGENTS.md` points
here. Read [README.md](README.md) for installation and
[docs/WORKFLOWS.md](docs/WORKFLOWS.md) for detailed runtime workflows.

## Architecture and ownership

Local Worker Hub is a Python FastAPI service with a React/TypeScript dashboard,
CLI and stdio MCP adapter. Native Ollama chat powers bounded roles; one FIFO
executor and one inference slot are intentional. `legacy/` is archived and must
not replace the scoped runtime.

Keep architecture, ambiguous diagnosis, security decisions and final acceptance
with the frontier agent. Do not delegate this helper's own launcher, permission,
security or configuration diagnosis to the helper. Tiny literal edits and
plan-only work stay with the primary. Delegate other work only when the objective,
read/edit scope, expected behavior, checks and acceptance are clear.

Inspect user changes before editing. Preserve unrelated changes; never reset,
clean or roll back a user's tree. Do not overlap edits with a worker's authorized
files. After two unsuccessful local attempts, take over.

## Layout

Core (`hub/*.py`) runs jobs: queue, runner, service, CLI, MCP adapter, models, scoped tools, report, store, engine and the host pipelines. Capabilities live in `hub/skills/`:
`coding/` (areas, low to high: `execution`, `intelligence`, `validation`, `editing`, `delegation`), `research/` and `personal/`. Coding and research never import each other, skills never import
the job machinery, and coding areas import only downward; `tests/test_import_boundaries.py` enforces this from the directory layout, so put new modules in a skill area. The old top-level names
(`hub.workspace`, `hub.codeindex`, ...) still import as aliases of the same module objects (`hub/__init__.py`); new code uses the canonical path.

## Files to know

- `hub/cli.py`, `hub/mcp_adapter.py`: user and frontier interfaces.
- `hub/service.py`, `hub/store.py`: authenticated localhost API, queue and history.
- `hub/runner.py`, `hub/engine.py`: role workflows and bounded model execution.
- `hub/skills/coding/intelligence/codeindex.py`, `hub/skills/coding/editing/workspace.py`, `hub/skills/coding/validation/acceptance.py`, `hub/skills/coding/execution/testparse.py`: deterministic code index and candidate ranking,
  private editor workspace with guarded apply, the acceptance packet, and parsing of test/lint output into failures.
- `hub/skills/coding/editing/textedit.py`, `hub/skills/coding/editing/contextpack.py`, `hub/skills/coding/editing/editgate.py`, `hub/skills/coding/editing/mappings.py`, `hub/skills/coding/delegation/incident.py`: strict transactional edit protocol (continuation after a cut-off reply, line-anchored
  matching), context packing for the edit prompt, the advisory complexity gate with proposed split, post-edit `old -> new` verification, and metadata-only incident export.
- `hub/skills/coding/intelligence/intel/`, `hub/skills/coding/delegation/spec.py`, `hub/skills/coding/execution/execctx.py`, `hub/skills/coding/delegation/outcomes.py`: Tier-0 code tools behind a provider interface (CodeIndex today), the DelegationSpec and its host-verified criteria,
  failure-frame context for `fix_test`, and delegation outcome rows, final frontier diffs and acceptance statistics. `hub/skills/coding/intelligence/testmap.py` selects relevant tests and `hub/skills/coding/validation/templates.py` fills approved `{tests}` templates;
  `hub/skills/coding/delegation/router.py` gives advisory Tier 0/1/2 routing. `tests/test_import_boundaries.py` classifies every module into a layer: add new modules there.
- `hub/scoped.py`, `hub/evidence.py`: scoped tools and freshness/provenance checks.
- `hub/skills/research/public_page.py`, `hub/skills/research/web_provider.py`, `hub/skills/research/web_verification.py`: public
  retrieval, search-provider adapters and current-source verification.
- `hub/skills/research/providers/`: structured data providers (weather, fx, clock) the ask decision can choose;
  `hub/skills/research/product_extract.py`: JSON-LD/microdata/meta product parsing; `hub/skills/research/web_fixtures.py`: eval record/replay.
- `hub/skills/research/answer_review.py`, `hub/report.py`: requirements review and report contract.
- `hub/skills/coding/validation/validation.py`, `hub/skills/coding/validation/profiles.py`: approved argv checks and private profiles.
- `hub/install.py`, `hub/settings.py`: installation and private locations.
- `frontend/src/`: dashboard, traces and evidence presentation.

## Boundaries to preserve

- Default model is Gemma 4 12B (`gemma4:12b-it-qat`); the junior-task eval (`benchmarks/eval_junior.py`, results in
  `benchmarks/RESULTS.md`) put it ahead of Qwen3.5 9B (90.9% vs 78.8% correct, 3 vs 11 COMPLETE-but-wrong) on two small fixtures; re-run it
  before changing the default. 16K context and concurrency one; 32K is opt-in. Only one model is resident at a time (`OLLAMA_MAX_LOADED_MODELS=1`);
  model names live in `hub/model_registry.py`. Do not promote experimental settings without
  measured evaluation.
- Default paths are host-driven pipelines without tool calling (docs/WORKFLOWS.md): ask
  (decide -> search/read -> answer from numbered excerpts, host-written sources), investigate
  (repo map + search hits -> chosen ranges -> answer) and edit (files in prompt -> SEARCH/REPLACE
  blocks applied through ScopedFiles). Keep host-written citations, the unit/time normalizer and
  the scoped write guards. `--verify`, `--review` and `--agent-loop` keep the older tool loop.
  Coding: passing approved checks decide; the local review is opt-in and advisory.
- `--board` is retired and refused (see docs/WORKFLOWS.md); its code stays until moved to `legacy/`. Proposers were tools-off and
  never see each other; no phase combines repository and web access. Preserve the literal
  task-quote anchors, host-computed drift report and shared web ledger. No critic or repair.
- Investigator reads are scoped, and its `path:line` references are verified by the host against lines it read. Editor writes require exact
  authorized paths and fresh observed source, and land in a private workspace; only `apply_result` writes the user's tree, and only if the
  authorized files are unchanged there. Preserve symlink, hardlink and secret-file guards.
- Editor replies are applied transactionally: strict parse, zero edits from a truncated or malformed reply, all-or-nothing commit, a guard against destructive
  replacements, and reports built from the actual diff. `in_place` is a human CLI option, never available over MCP. Keep these properties when changing `hub/skills/coding/editing/textedit.py`,
  `hub/pipelines.py` (`run_edit`) or the runner's report assembly; `tests/test_edit_incidents.py` replays the real failures.
- Validator executes only explicitly approved argv checks. It uses zero model
  tokens by default. Approved programs are trusted execution, not an OS sandbox;
  never disguise installs, migrations, deployment or destructive work as checks.
- Personal and Researcher accept public briefs only, with no repository, private
  context, account or messaging access. Never send private source, credentials,
  customer information or internal URLs to hosted providers.
- Keep public-origin checks, checked redirects, retrieval limits and cache
  eligibility. Unverified hosted text cannot establish current origin facts.
- The LLM derives research requirements, breadth and acceptance from the original
  prompt. Avoid domain-specific price, stock or source-count completion scripts.
- Extended initial work and fresh review share the original model budget.
  Missing reviews do not count as success. Reuse evidence, never replay private
  thinking into later prompts. No recursive worker invocation or permissive
  legacy-agent fallback.
- Local `COMPLETE` is not frontier acceptance. Inspect the real diff, important
  evidence and relevant checks, then record accepted/rejected/takeover through
  `record_review` or `local-worker review`.
- Keep config, tokens, job history, raw traces and benchmark artifacts outside
  public source. Project profiles must be privately reviewed; do not embed a
  user's repositories or automatically discover and authorize their commands.
- Preserve existing history and installation backups. Global agent instructions
  are personal configuration; project docs must not silently replace them. When
  updating the managed shared workflow, keep `~/.codex/AGENTS.md` and
  `~/.claude/CLAUDE.md` identical and preserve guarded backups.

## Validation

Use the actual project commands:

```sh
uv lock --check
.venv/bin/pytest -q
npm --prefix frontend run build
```

`uv sync --frozen` prepares dependencies. The frontend build includes TypeScript
checking. Tests isolate config/state and mock external services; passing tests
do not prove live-model accuracy. Run relevant checks for the changed behavior
and broaden only when needed. Do not rerun passing checks without new changes
or an unresolved concern.

Optional live browser checks run from `frontend/` with the service running:
`node verify-browser.mjs`, or `TRACE_JOB_ID=<recorded-job> node verify-trace.mjs`.
They need Playwright Chromium and create private test evidence. Benchmark scripts
use disposable repositories; inspect their diffs and checks before acceptance.

Keep progress visible with brief updates and bounded waits. Prefer async local
handoffs, poll about every 20–30 seconds, and read compact results before relevant
artifact pages. A quiet check is not necessarily stuck.

## Measurement and reporting

Report actual edits, checks and unresolved limitations. Never infer subscription
dollars saved from local tokens. Net savings require a matched baseline including
frontier orchestration, review, retries and takeover. Keep failures in evaluation
results; avoid declaring model reliability from a single successful run.
