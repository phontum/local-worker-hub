"""Reversible user-scoped installation; never modifies AWS credentials or sandbox policy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .settings import PROJECT, CONFIG, STATE, initialize
from .runner import ROLE_SYSTEM

WORKFLOW=r'''# Personal local-agent workflow

Act as primary architect, planner, and final reviewer. Keep architecture, ambiguous
root causes, security decisions, and final acceptance with the frontier agent.
Plan-only work stays with the primary. Do not delegate the helper's own launcher,
security, or configuration diagnosis to it.

Use local-worker for bounded discovery, specified feature slices, evidence-backed
fixes, approved validation and public documentation research. Prefer one complete
handoff once behavior and scope are clear; continue independent frontier work.
Run it like a background subagent: `local-worker delegate [flags] "task"` (Claude Code run_in_background,
Codex background terminal) prints one compact brief when finished while you keep working;
`local-worker watch JOB_ID` prints one line per phase. Over MCP use submit_job, then get_job
(eta_seconds, queue wait) and get_result. For git state use a Validator job with argv checks.
Small tasks that cost more to
delegate than to do directly should stay with the primary. Never blindly trust a
COMPLETE report: inspect important evidence and actual diffs, verify relevant
behavior, then record acceptance/rejection/takeover through record_review.
The hub calls Ollama directly through its native chat API: installed OpenCode V2 does not forward
scoped MCP tools to the model. Do not substitute the permissive legacy agent.

## What to delegate

Delegate these constantly, with exact scope: "find all code involved in X" and "where is this config used"
(Investigator: deterministic code index, host-verified path:line evidence); "read these files and explain how X
works", "compare implementation A and B" and "check whether requirement R is implemented" (Investigator; a COMPLETE
report needs at least one host-verified reference, unverified ones are listed under Risks); "make this mechanical
change in these files", "add this specified guard", "add a regression test for this known bug" and "fix this exact
failing test" (Editor; use --workflow implement with the failing test as the check for a fix); "run these tests and
summarize failures" (Validator: zero tokens, failures parsed into test, file:line and assertion). The Editor works in a
private copy of the repository: read the acceptance packet and patch.diff, then apply_result (it refuses if you
changed those files meanwhile) or discard_result. Pass kind (find_code, explain, config_use, compare, check_requirement,
mechanical, guard, regression_test, run_tests, fix_test) to tune defaults; regression_test verifies the new test fails on the
current code. Architecture, ambiguous root causes and acceptance stay with you.

## Roles and invocation

Investigator (--read-only / --role investigator): scoped file reads and literal searches.
Bare CLI prompts select Personal: public web and general questions, no repository
access. Use explicit roles in frontier tooling. --extended selects opt-in 32K,
thinking and a fresh requirements review by default; otherwise Personal uses
16K and the work preset. --review enables the answer review at 16K; --no-review
opts out. --model-thinking off explicitly overrides thinking. MCP review_pass
controls the same flow; execution_preset=extended enables it by default.
The initial answer and independent review share one model budget (55% reserved
for initial work, the remainder for review). The reviewer checks every original
requirement against saved tool evidence and new scoped reads/fetches, corrects
the answer and records met/unmet/unknown. Met claims require exact supporting
quotes from observed tool evidence or the final answer; common explicit output
counts also receive mechanical checks. Missing reviews never count as success.
Only the original task is assessed, not the runtime transport/report protocol.
Extended work/review use thinking; forced final JSON formatting turns disable
thinking to preserve answer space and record that effective step configuration.
Public questions run a host pipeline: a small decision call, host search (local SearXNG, Exa
fallback), live page reads, then an answer from numbered excerpts with host-written sources in
the user's units (~/.config/local-worker/preferences.json), usually in 10-20 s. Investigators get
a repo map and search hits; editors reply with SEARCH/REPLACE blocks the host applies under the
same scope/freshness guards; --agent-loop keeps the older tool loop.
--verify (MCP verify=true) adds strict origin-proof research: slower and often PARTIAL.
--board (MCP board=true) is a rarely useful slow multi-model deliberation with no measured
benefit; do not use it for specified work, and repository tasks have no board.
Implementation has no automatic review: passing approved checks decide, --review adds an advisory one;
direct Validator checks remain zero-token. Local review is not frontier acceptance.
Public fetch_web defaults to mode=current: bounded direct public origin reads,
with DNS-pinned connections and checked redirects. Blocked origin reads fall
back to explicitly unverified hosted extraction; mode=cached opts into discovery
text. Page with start/characters or use find. WEB_EVIDENCE records retrieval
method, URL, observed time, cache age and eligibility for current evidence.
Server/CDN caching and unrendered JavaScript can still affect origin content.
Before web research the model calls plan_web_task: requirements anchored to the
original prompt, acceptance checks, evidence freshness, breadth, strategy and
output are decided by the LLM. The fresh reviewer checks/corrects that plan
against the original request and investigates gaps. Plans grant no permissions
or extra budget and are visible in the paired dashboard. No domain-specific
source counts, price rules or stock scripts decide semantic task completion.
Current answers require web_claims with exact origin quotes and evidence refs;
unreviewed answers display exact observations. A fresh reviewed answer preserves
the model's synthesis and format when its current claims pass provenance checks.
The LLM assesses meaning, contradictions and sufficiency; missing current proof
remains unresolved without invalidating unrelated confirmed findings.
Reviews cannot establish freshness by rereading cached draft evidence. There is
one bounded verification reminder within the existing model/tool budget.
Use find for a literal term in retrieved webpage text. local-worker web status
shows the search provider; web configure langsearch selects the free API after
private key setup. Default Exa is free keyless MCP, with rate limits. LangSearch
is an explicitly reported fallback on Exa rate limits/transient failures when
its private key is configured. A short per-job cooldown spans both passes;
failure messages never become search results. The default provider is unchanged.
LangSearch full page text is reused in cached mode; hosted fallback uses free keyless Exa.
Editor (--write or --role editor): exact authorized file paths; no shell. It edits a private workspace
(your tree is untouched until apply_result; --in-place / in_place edits directly) and returns a patch plus an
acceptance packet (diff stats, scope, parsed check failures, review focus). For a specified feature slice, --workflow implement --repair-attempts 1 performs
scoped edit and supplied checks, with one targeted repair after a failed check.
--investigate-first adds bounded discovery. The frontier still verifies the diff
and check evidence before acceptance. Two repairs are experimental only.
Validator (--role validator): executes explicitly supplied check argv arrays;
direct recorded results are the default and use zero model tokens. Optional
summary_mode=local or summarize_result analyzes saved evidence without rerunning
checks. Authorized programs are trusted execution,
not an OS sandbox. Never authorize installs, deployments or destructive commands
as routine validation. Production credentials are not automatically inherited.
Researcher (--role researcher): hosted public web search/fetch, no repo access.
Give it only library/version names, generic errors and sanitized public examples.
Never attach raw work source, internal URLs, credentials or customer data.

Run from the intended project root or specify --repo. Substantial tasks use stdin:

```sh
local-worker --read-only --timeout 300 <<'TASK'
Objective: Locate the implementation of this specific behavior.
Known context: Relevant paths and symbols already identified.
Constraints: Read only; no broad inventory or secrets.
Acceptance: Return concise path:line evidence and explicit unknowns.
TASK
```

```sh
local-worker --write --allow-path src/component.tsx --timeout 600 <<'TASK'
Objective: Make this exact small change in the authorized file.
Constraints: Preserve user changes and unrelated behavior.
Acceptance: Describe the actual edit with evidence; do not invent checks.
TASK
```

A complete bounded handoff can use:
local-worker --role editor --workflow implement --repair-attempts 1 \
  --allow-path src/component.tsx --checks checks.json --timeout 600 --async \
  "Make this specified small change."
Use --checks FILE for a JSON array such as
[{"name":"typecheck","argv":["npm","run","typecheck"],"cwd":".","timeout":300}].
Select checks from the project's actual guide. Never run a Next build while its
development server uses the same .next directory. Supply selected project constraints through
--context-file or the MCP context field; do not inject entire large guides.

MCP: submit_job requires role, task, idempotency_key and repo except public web roles.
Use execution_preset=small (120s), work (300s) or extended (32K/300s). timeout
is cumulative model time for presets; approved checks use separate limits. Keep
16K work as the engineering default; extended remains an explicit experiment.
Use read_paths for explicit files/directory roots, exact allowed_paths for writes.
A handoff includes objective, verified context, read/edit scope, expected behavior,
checks and acceptance. Attach evidence_job_ids from finished jobs in the same
canonical repo; reread source before relying on claims or editing. handoff_id
groups follow-ups, reviews and takeovers. Put matched baseline totals on one
review in the group and include all frontier orchestration, review and takeover.
Reviewed profile_ref/check_groups can supply Editor checks as well as Validator
checks; profiles never grant edit scope. For MR work, the frontier first obtains
the exact revision and selected diff; the repository worker has no GitLab access.
Editor additionally requires allowed_paths. Validator requires checks. Reuse a key
only to retry the same transport request. get_job/get_result inspect progress;
cancel_job stops work; record_review records frontier acceptance independently.
For a follow-up provide verified findings and remaining questions explicitly.

Prefer compact results. get_job is bounded progress; get_result defaults to a
2 KiB brief. detail=summary is an 8 KiB overview. wait_job waits at most
30 seconds for progress or completion. read_artifact retrieves only relevant report/diff/log pages; use
detail=full only when needed. get_project_profile inspects reviewed commands and
constraints without executing them. For profile requests submit its short
profile_ref (or full hash),
selected check_groups and explicit nonsecret parameters. A changed profile or
source guide must be inspected again. Profiles do not grant editing permissions.
Use failure_policy=continue_independent for independent checks; depends_on skips
failed prerequisites. Legacy ad hoc requests remain fail-fast. Do not repeat
passing checks without a new change or unresolved failure that warrants it.
Routine results need no model summary. Ask summarize_result only when interpreting
long/failing evidence adds value; original exit codes remain authoritative.

## Bounds and failures

One local job executes at a time; requests queue. Do not overlap edits with worker
changes to its authorized files. Capture user changes before delegation; the hub
also records before/after state. Do not reset, clean or roll back a user's tree.
Keep the default local model (Gemma 4 12B, ahead of Qwen3.5 9B on the junior-task eval in benchmarks/RESULTS.md) at 16K by default and concurrency at one. Explicit extended
32K experiments are allowed; do not promote them without measured evaluation. No recursive local-worker/OpenCode invocation is available.

The final report uses LOCAL_WORKER_REPORT / END_LOCAL_WORKER_REPORT and the labels
Status, Findings, Files, Checks, Risks. Exit 0 means a valid COMPLETE report and
mechanical checks, not frontier acceptance; 2 partial/blocked or argument error;
3 missing report; 4 runtime failure; 124 timeout; 130 cancellation. At most one
tools-disabled recovery summarizes saved evidence; it never repeats edits.
File freshness is checked inside scoped tools; no hash copying is needed in
normal handoffs. Use replace_lines for a freshly read range when exact-text
matching is ambiguous. Whole-file replacement requires a complete fresh read.
Read/search evidence IDs are checked against saved tool results.
After two unsuccessful attempts on an issue, take over. Do not bypass permissions
or inflate limits merely to obtain success. While a worker runs, avoid duplicate
inventories; use wait_job or poll roughly every 20–30 seconds and keep the user informed.

## Monitoring and maintenance

local-worker dashboard prints the localhost URL and one-time browser pairing code.
Read-only MCP/CLI calls never start the service. Use local-worker start if needed.
The paired dashboard shows the initial answer and requirements assessment. The
paired dashboard and read_trace/CLI trace expose the same private local-model
thinking and answer trace, grouped by phase/step, plus tool evidence. Thinking
is not replayed into subsequent prompts or routine frontier responses. Thinking
off phases have no reasoning trace. Partial traces are retained on interruption.
The dashboard shows queue/check/model phases, heartbeat, deadlines and last output;
a quiet check is not necessarily stuck. External approval waits are not observable
by the hub and must not be presented as worker execution. Retry an identical
idempotent transport request once; then inspect CLI status/result and take over.
local-worker history/status/result/cancel/review manage jobs; doctor reports setup.
The shared MCP server is local-worker mcp. Context measures are timestamped;
local tokens and API-equivalent workload are not subscription dollars saved.
Baseline comparisons must include orchestration, review, retries and takeover.

Maintained source: ~/dev/local-worker-hub. Config: ~/.config/local-worker.
Private state/history: ~/.local/state/opencode/local-worker. Raw logs may contain
source. Keep them private and prune deliberately. Existing history is preserved.
Runtime agent/tool policies are generated per job; do not use the legacy global
OpenCode local-worker agent to bypass the hub's boundaries.

Keep ~/.codex/AGENTS.md and ~/.claude/CLAUDE.md identical when updating this workflow.
Codex's uppercase AGENTS.md is required on Linux. If you are a delegated worker,
execute only your bounded assignment and return the report; do not delegate again.
'''

def prepare():
    initialize()
    from .profiles import seed_profiles
    print('Project profiles:',seed_profiles())
    from .history import import_history
    from .store import Store
    print('Imported legacy summaries:',import_history(Store()))
    profiles=CONFIG/'roles.json'
    if not profiles.exists():
        profiles.write_text(json.dumps({name:{'prompt':prompt,'thinking':name=='editor',
            'steps':18 if name=='editor' else 12,'timeout':600 if name=='editor' else 300}
            for name,prompt in ROLE_SYSTEM.items()},indent=2));profiles.chmod(0o600)
    unit=Path.home()/'.config/systemd/user/local-worker-hub.service'
    unit.parent.mkdir(parents=True,exist_ok=True)
    unit.write_text(f'''[Unit]
Description=Local Worker Hub
After=network.target

[Service]
Type=simple
WorkingDirectory={PROJECT}
ExecStart={PROJECT}/.venv/bin/python -m hub.service
UMask=0077
Restart=on-failure
RestartSec=3
KillMode=control-group

[Install]
WantedBy=default.target
''')
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    print('Prepared service and role profiles; active launcher unchanged.')

def activate():
    manifest_path=CONFIG/'installation.json'
    if manifest_path.exists():raise RuntimeError('Already installed; use the maintained source for updates')
    backup=Path((PROJECT/'INSTALL_BACKUP').read_text().strip())
    original=json.loads((backup/'manifest.json').read_text())
    names=[Path.home()/'.local/bin/local-worker',Path.home()/'.codex/AGENTS.md',Path.home()/'.claude/CLAUDE.md',Path.home()/'.codex/config.toml',Path.home()/'.claude.json']
    for path in names:
        if str(path) in original and path.exists() and path.read_bytes()!=Path(original[str(path)]).read_bytes():
            raise RuntimeError('Configuration changed since backup; merge it before activation: '+str(path))
    text=names[3].read_text()
    obj=json.loads(names[4].read_text()) if names[4].exists() else {}
    if '[mcp_servers.local-worker]' in text or 'local-worker' in obj.get('mcpServers',{}):
        raise RuntimeError('An existing local-worker MCP entry must be merged before activation')
    modes={str(path):path.stat().st_mode & 0o777 for path in names if path.exists()}
    launcher=names[0]
    launcher.write_text(f'#!/bin/sh\nexec "{PROJECT}/.venv/bin/python" -m hub.cli "$@"\n')
    launcher.chmod(0o755)
    for path in names[1:3]:path.write_text(WORKFLOW)
    codex=names[3]
    codex.write_text(text+f'\n[mcp_servers.local-worker]\ncommand = "{launcher}"\nargs = ["mcp"]\nstartup_timeout_sec = 30\ntool_timeout_sec = 30\n')
    claude=names[4]
    obj.setdefault('mcpServers',{})['local-worker']={'type':'stdio','command':str(launcher),'args':['mcp']}
    claude.write_text(json.dumps(obj,indent=2)+'\n');claude.chmod(0o600)
    installed={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in names}
    manifest_path.write_text(json.dumps({'backup':str(backup),'installed':installed,'original':original,'modes':modes},indent=2));manifest_path.chmod(0o600)
    subprocess.run(['systemctl','--user','enable','--now','local-worker-hub.service'],check=True)
    print('Installed local-worker and shared Codex/Claude MCP. New frontier sessions load the new tools.')

def uninstall():
    manifest_path=CONFIG/'installation.json';manifest=json.loads(manifest_path.read_text())
    for name,digest in manifest['installed'].items():
        path=Path(name)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Preserve manually changed configuration before uninstalling: '+name)
    subprocess.run(['systemctl','--user','disable','--now','local-worker-hub.service'],check=True)
    for name in manifest['installed']:
        path=Path(name)
        if name in manifest['original']:
            shutil.copy2(manifest['original'][name],path)
            path.chmod(manifest.get('modes',{}).get(name,0o755 if path.name=='local-worker' else 0o600))
        else:path.unlink(missing_ok=True)
    manifest_path.unlink()
    (Path.home()/'.config/systemd/user/local-worker-hub.service').unlink(missing_ok=True)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    print('Restored original launcher and frontier configuration. Project, private history and role settings remain available.')

def update_workflow():
    manifest_path=CONFIG/'installation.json';manifest=json.loads(manifest_path.read_text())
    paths=[Path.home()/'.codex/AGENTS.md',Path.home()/'.claude/CLAUDE.md']
    if paths[0].read_bytes()!=paths[1].read_bytes():raise RuntimeError('Shared workflow files differ; merge before updating')
    for path in paths:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['installed'].get(str(path)):
            raise RuntimeError('Preserve manually changed workflow before managed update: '+str(path))
    backup=CONFIG/'workflow-backups'/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
    for path in paths:
        shutil.copy2(path,backup/(path.parent.name+'-'+path.name))
        path.write_text(WORKFLOW)
        manifest['installed'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest,indent=2));manifest_path.chmod(0o600)
    print('Updated identical Codex/Claude workflow; preserved private backup and uninstall manifest.')

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','activate','uninstall','update-workflow']);args=p.parse_args()
    os.umask(0o077)
    {'prepare':prepare,'activate':activate,'uninstall':uninstall,'update-workflow':update_workflow}[args.action]()

if __name__=='__main__':main()
