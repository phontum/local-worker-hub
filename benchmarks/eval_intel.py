"""Code-intelligence benchmark: precision, recall and latency of a SymbolProvider against hand-checked answers about this repository.

    .venv/bin/python benchmarks/eval_intel.py [--provider lexical|lsp] [--repeat N]

`lexical` is the CodeIndex provider that ships today. `lsp` uses the language servers the project profile approves (`lsp` in the reviewed profile of this
repository); without an approved server it reports that and exits, so a number is never attributed to a server that did not run. Gold answers were read from
the source by hand; when the code changes, regenerate them deliberately. A semantic provider replaces the lexical one inside the pipelines only if it matches or
beats it here AND on `eval_junior` (correctness and false-COMPLETE), per the plan's promotion rule."""
import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hub.intel.lexical import LexicalProvider  # noqa: E402
from hub.intel.lsp import LspProvider, approved_servers  # noqa: E402
from hub.models import JobRequest  # noqa: E402
from hub.scoped import ScopedFiles  # noqa: E402

# kind, tool method, name, extractor key, gold set
CASES = [
    ('definitions', 'definitions', 'recover', 'path', {'hub/workspace.py'}),
    ('definitions', 'definitions', 'final_diff', 'path', {'hub/outcomes.py'}),
    ('definitions', 'definitions', 'apply', 'path', {'hub/workspace.py', 'hub/textedit.py'}),
    ('callers', 'callers', '_commit', 'in', {'apply', 'revert'}),
    ('callers', 'callers', 'final_diff', 'in', {'review', 'outcome', 'test_final_diff_is_what_the_frontier_left_after_the_snapshot', 'test_final_diff_reports_unavailable_without_a_workspace'}),  # review/outcome are nested in create_app
    ('implementations', 'implementations', 'Strict', 'name', {'Target', 'Mapping', 'Change', 'Criterion', 'Evidence', 'Scope', 'DelegationSpec'}),
    ('implementations', 'implementations', 'LexicalProvider', 'name', {'LspProvider'}),
]

def extract(result, kind, key):
    rows = result.get({'definitions': 'definitions', 'callers': 'callers', 'implementations': 'implementations'}[kind], [])
    return {r.get(key) for r in rows if r.get(key)}

def score(found, gold):
    hit = len(found & gold)
    return (hit / len(found) if found else 0.0), (hit / len(gold) if gold else 1.0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['lexical', 'lsp'], default='lexical')
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    files = ScopedFiles(JobRequest(role='investigator', repo=str(ROOT), task='intel eval', idempotency_key=uuid.uuid4().hex))
    if args.provider == 'lsp':
        servers = approved_servers(str(ROOT))
        if not servers:
            sys.exit('No language server is approved for this repository (add `lsp` to its reviewed project profile); nothing was measured.')
        make = lambda: LspProvider(files, servers)  # noqa: E731
    else:
        make = lambda: LexicalProvider(files)  # noqa: E731
    started = time.monotonic()
    provider = make()
    cold = time.monotonic() - started
    rows = []
    for kind, method, name, key, gold in CASES:
        times, result = [], None
        for _ in range(args.repeat):
            t = time.monotonic()
            result = getattr(provider, method)(name)
            times.append(time.monotonic() - t)
        found = extract(result, kind, key)
        precision, recall = score(found, gold)
        rows.append({'kind': kind, 'name': name, 'precision': round(precision, 2), 'recall': round(recall, 2), 'found': sorted(found)[:10], 'missing': sorted(gold - found),
                     'extra': sorted(found - gold)[:5], 'median_ms': round(statistics.median(times) * 1000, 1), 'provider': result.get('provider'), 'precision_label': result.get('precision'),
                     'fallback': result.get('fallback')})
    for row in rows:
        print(f"{row['kind']:<16}{row['name']:<18}P {row['precision']:<5}R {row['recall']:<5}{row['median_ms']:>8} ms  {row['provider']}/{row['precision_label']}"
              + (f"  missing={row['missing']}" if row['missing'] else '') + (f"  extra={row['extra']}" if row['extra'] else '') + (f"  FALLBACK: {row['fallback']}" if row['fallback'] else ''))
    print(f"\nmean precision {statistics.mean(r['precision'] for r in rows):.2f}  mean recall {statistics.mean(r['recall'] for r in rows):.2f}  cold start {cold:.2f}s  ({len(rows)} cases, {args.repeat} repeats)")

if __name__ == '__main__':
    main()
