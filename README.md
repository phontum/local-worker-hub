# Local Worker Hub

A local AI helper for frontier agents and everyday questions. Delegate bounded
file discovery, small edits, checks and public research to Ollama while Codex or
Claude handles architecture, ambiguous decisions and final acceptance.

The hub talks directly to Ollama. It needs no OpenRouter account or cloud model.
Public search uses a hosted provider; inference and repository tools run locally.

## What it does

- CLI and MCP interfaces backed by one persistent job queue.
- Investigator, Editor, Validator, Researcher and Personal roles with separate
  tool permissions. Editors change only explicitly authorized files.
- Deterministic validation with recorded exit codes and zero model tokens by
  default. Implementation combines edits, approved checks and one bounded repair; passing checks decide the outcome, and a local review runs only with `--review` and only advises.
- Personal questions from any directory, answered in plain language in about 10-20 seconds: the host decides whether to search, searches (self-hosted SearXNG, falling back to Exa), reads the best pages live and lets the model answer from numbered excerpts. Sources are added by the host from pages actually read, and units follow `~/.config/local-worker/preferences.json` (metric, 24-hour clock, your timezone): `local-worker "Will it rain in Budapest tomorrow afternoon?"`. Add `--verify` for strict origin-proof research (slower, may end PARTIAL).
- Coding helpers without fragile tool calls: investigators get a repository map and search hits and pick what to read; editors get the files and reply with SEARCH/REPLACE blocks that the host applies under the same path and freshness guards. `--agent-loop` keeps the older tool loop for comparison.
- Background use by Claude/Codex: `local-worker delegate ...` prints one compact brief when finished; `local-worker watch JOB_ID` prints one line per phase.
- Optional extended mode: 32K context, thinking and an independent requirements
  review. The normal personal preset uses 16K.
- A paired localhost dashboard with task history, research plans, local-model
  thinking and answer traces, tool evidence, context, token usage and hardware samples.

This is an experimental assistant. Small-model self-review can miss instructions
or misread evidence. A worker's `COMPLETE` report still needs frontier review.
[Evaluation results](benchmarks/RESULTS.md) include failed and partial runs;
net frontier-token and financial savings have not been established.

## Requirements

- Linux or WSL2. Background setup uses a **systemd user service**;
  native Windows service installation is not provided.
- Python 3.11+, [uv](https://docs.astral.sh/uv/) and Node.js 20.19+ with npm.
- [Ollama](https://ollama.com/) listening at `127.0.0.1:11434` with
  `gemma4:12b-it-qat` (default, 7.2 GB) and `qwen3.5:9b` downloaded; the board uses both,
  one at a time. Hardware must fit the model and context; GPU telemetry is optional
  and depends on available system tools.
- Internet access for dependencies and public web questions. Repository-only
  tasks can run offline after dependencies and the model are installed.

On a host with about 8 GB of RAM (for example a default WSL2 VM), set `LLAMA_ARG_CACHE_RAM=512` in the
Ollama service environment: llama.cpp's prompt cache lives in host RAM and otherwise grows until the OOM killer
stops Ollama. `local-worker doctor` reports whether it is configured.

Keep inference concurrency at one and use 16K until measurements justify
larger contexts. Extended mode increases memory use and does not guarantee better answers.

## Install from source

Use an editable source checkout: the service serves the dashboard build from
that checkout. Standalone wheel deployment is not the documented installation.

```sh
git clone https://github.com/phontum/local-worker-hub.git
cd local-worker-hub
uv sync --frozen
ollama pull gemma4:12b-it-qat
ollama pull qwen3.5:9b
npm --prefix frontend ci
npm --prefix frontend run build

# Creates private state, role defaults and the systemd user unit.
.venv/bin/python -m hub.install prepare
systemctl --user enable --now local-worker-hub.service

# For this terminal; persist this PATH entry if desired.
export PATH="$PWD/.venv/bin:$PATH"
local-worker doctor
local-worker dashboard
```

Ensure Ollama is running before model tasks (`ollama serve` if your installation
does not start it automatically). Open `http://127.0.0.1:8765` and enter the
one-time pairing code printed by `dashboard`. Browser sessions expire after eight
hours or a service restart; generate another code to reconnect.

If systemd user services are unavailable, run `.venv/bin/local-worker serve` in
the foreground from the checkout. Keep that process running when using the CLI.
On WSL, availability also depends on the WSL instance being running.

Preparation creates `~/.config/systemd/user/local-worker-hub.service` and private
defaults, and imports any existing legacy summaries. It preserves existing role
files and project profiles. It does **not** register frontier integrations or
replace global agent instructions. `hub.install activate` is an older managed
migration path requiring an existing private backup; it is not a fresh installer.

## Use it

Optional, recommended: `local-worker web setup-searxng` starts a private SearXNG search container (Docker) on 127.0.0.1:8888 and selects it.


```sh
# Personal: general questions and public search, no repository access.
local-worker "Find the official Python documentation for itertools.batched."
local-worker --extended "Compare these public sources and cite your evidence."
local-worker --review "Answer and check every requirement using 16K context."
local-worker --verify "Is the RTX 5070 in stock at gigatron.rs right now?"   # strict proof, slower
local-worker --board "..."   # rarely useful: slower multi-model deliberation

# Background helper for a frontier agent: run in the background, continue working, read the brief on exit.
local-worker delegate --read-only "Find where the retry timeout is set; return path:line evidence."
local-worker watch JOB_ID   # optional: one line per phase change, then DONE <state> <status>

# Run engineering commands from the intended repository root.
local-worker --role investigator --read-path src --preset work \
  "Find the timeout default in src; return concise path:line evidence."
local-worker --role editor --allow-path src/config.ts --timeout 300 \
  "Change only the timeout default from 30 to 45. Preserve other behavior."
local-worker --role validator --checks checks.json \
  "Run these approved checks and report their actual results."
```

Example `checks.json` (use commands that actually exist in your project):

```json
[{"name":"typecheck","argv":["npm","run","typecheck"],"cwd":".","timeout":120}]
```

For a complete bounded handoff, supply the objective, verified context, exact
edit/read scope, checks and acceptance criteria:

```sh
local-worker --role editor --workflow implement --repair-attempts 1 \
  --read-path src --allow-path src/config.ts --checks checks.json \
  --preset work --async \
  "Change the documented timeout default to 45 and preserve unrelated behavior."
local-worker status JOB_ID
local-worker result JOB_ID
local-worker review JOB_ID accepted --notes "Inspected diff and check evidence."
```

Supply longer tasks through quoted stdin. Use `--repo /path/to/project` when not
running from the project root. `--extended` enables 32K, thinking and answer
review; `--no-review` and `--model-thinking off` override those options.
Both answer passes share one model-time budget; checks have separate timeouts.
Cancellation preserves edits already made and recorded evidence.

## Connect Codex or Claude

Register the same local stdio MCP server with either client using the absolute
path to the checkout's `.venv/bin/local-worker` executable and argument `mcp`.
Merge these entries into existing configuration; preserve other settings.

Codex's `~/.codex/config.toml` entry:

```toml
[mcp_servers.local-worker]
command = "/absolute/path/local-worker-hub/.venv/bin/local-worker"
args = ["mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 30
```

Claude MCP configuration entry:

```json
{
  "mcpServers": {
    "local-worker": {
      "type": "stdio",
      "command": "/absolute/path/local-worker-hub/.venv/bin/local-worker",
      "args": ["mcp"]
    }
  }
}
```

Restart the client session after registration. Tools are `submit_job`, `get_job`,
`get_result`, `wait_job`, `read_artifact`, `read_trace`, `get_project_profile`,
`summarize_result`, `cancel_job` and `record_review`. Use async submission and
compact results, then inspect relevant artifacts. Frontier acceptance is
recorded separately from the local report.

[CLAUDE.md](CLAUDE.md) contains this project's development instructions;
[AGENTS.md](AGENTS.md) points to that same guide. Copy only relevant workflow
rules into personal frontier instructions rather than replacing them.

## Web access and privacy

```sh
local-worker web status
local-worker web configure langsearch  # interactively requests a hidden API key
local-worker web configure exa        # selects keyless Exa MCP
```

Default search uses keyless Exa MCP. LangSearch is an optional keyed provider and
an explicitly reported fallback for Exa transient failures when its key is
configured. Providers have their own limits and terms; a free endpoint does not
guarantee unlimited access. Search briefs and requested public URLs go to hosted
providers. Never submit credentials, customer data, private source or internal
URLs through public roles.

Public fetches read bounded origin text with checked redirects and public-address
validation. Blocked origins can fall back to explicitly unverified hosted text.
There is no authenticated browser or JavaScript rendering, so some pages cannot
be confirmed. The model authors research requirements and stopping conditions;
scripts check provenance and boundaries rather than prescribing product,
price or stock rules. Required facts without supporting evidence remain unknown.

The API listens on loopback, authenticates callers and requires browser pairing
for private details. Private state lives outside the checkout:

| Location | Contents |
| --- | --- |
| `~/.config/local-worker` | API token, role settings, private search key, reviewed project profiles and optional comparison pricing |
| `~/.local/state/opencode/local-worker` | Job history, source snapshots, checks, traces and benchmark evidence |

Logs and traces can contain repository source. Keep them private. Override these
locations with `LOCAL_WORKER_CONFIG` and `LOCAL_WORKER_STATE` for isolated runs.
Scoped model tools are application permission boundaries; approved validation
programs are trusted execution, **not an OS sandbox**.

## Development and maintenance

```sh
uv sync --frozen
.venv/bin/pytest -q
npm --prefix frontend run build
systemctl --user restart local-worker-hub.service
```

The frontend build includes TypeScript checking. Tests use temporary private
config/state and mocked external services; live model quality needs separate
evaluation. Optional browser smoke checks and benchmark commands are documented
in [advanced workflows](docs/WORKFLOWS.md).

| Directory | Purpose |
| --- | --- |
| `hub/` | CLI, MCP, queue, Ollama runtime, scoped tools, validation and evidence |
| `frontend/` | React/TypeScript dashboard and browser smoke scripts |
| `tests/` | Runtime, permission, evidence and workflow regression tests |
| `examples/` | Generic private-project profile template |
| `benchmarks/` | Disposable evaluation fixtures and aggregate results |
| `legacy/` | Archived OpenCode launcher; not the active runtime |

Existing managed migrations have a guarded restore command; fresh manual setup
can be removed by disabling the user service and removing its unit and MCP
entries. Preserve private state/history when retaining past evidence.

Local token counts describe actual Ollama workload. Optional API-equivalent cost
estimates require dated comparison rates and are not subscription dollars saved.
Any net savings claim needs a matched frontier baseline including delegation,
review, retries and takeover.
