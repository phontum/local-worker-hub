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

## Files to know

- `hub/cli.py`, `hub/mcp_adapter.py`: user and frontier interfaces.
- `hub/service.py`, `hub/store.py`: authenticated localhost API, queue and history.
- `hub/runner.py`, `hub/engine.py`: role workflows and bounded model execution.
- `hub/scoped.py`, `hub/evidence.py`: scoped tools and freshness/provenance checks.
- `hub/public_page.py`, `hub/web_provider.py`, `hub/web_verification.py`: public
  retrieval, provider adapters and current-source verification.
- `hub/answer_review.py`, `hub/report.py`: requirements review and report contract.
- `hub/validation.py`, `hub/profiles.py`: approved argv checks and private profiles.
- `hub/install.py`, `hub/settings.py`: installation and private locations.
- `frontend/src/`: dashboard, traces and evidence presentation.

## Boundaries to preserve

- Default model is Qwen3.5 9B, 16K context and concurrency one. 32K is opt-in;
  do not promote experimental settings without measured evaluation.
- Investigator reads are scoped. Editor writes require exact authorized paths
  and fresh observed source. Preserve symlink, hardlink and secret-file guards.
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
