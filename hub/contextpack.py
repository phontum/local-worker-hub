"""Context packing for the Editor prompt: what the model gets to see of the authorized files, within the context window.

Replaces the fixed "whole file up to 400 lines, otherwise regions around six identifiers" rule that hid the strings a task named. The
budget is the context window minus the output cap and the rest of the prompt, so a file that fits is shown whole. A larger file is shown
as regions in priority order: its head, the lines holding the texts the task names (quoted strings and the old side of `old -> new`),
identifiers from the task (protocol words removed), definitions whose names match the task, then neighbouring lines until the budget
is spent. Files listed in `read_paths` but not authorized are added as read-only references with whatever budget is left.
The report says what was shown and which named texts were not found, so a hidden edit site is a visible condition, not a silent one.
"""
import hashlib
import re
from dataclasses import dataclass

from . import mappings
from .codeindex import analyze, subwords, task_terms
from .localize import identifiers

CHARS_PER_TOKEN = 3.2
MARGIN_TOKENS = 700
HEAD = 30
SMALL_FILE = 80
MAX_REFERENCE = 6000
PROTOCOL = frozenset({'SEARCH', 'REPLACE', 'WHOLE', 'DELETE', 'FILE', 'LOCAL_WORKER_REPORT', 'END_LOCAL_WORKER_REPORT'})

@dataclass
class Packed:
    text: str
    report: dict

def budget_chars(context, output_limit, fixed_chars):
    """Characters available for file content: window minus output cap, a margin and the fixed part of the prompt."""
    tokens = context - output_limit - MARGIN_TOKENS - fixed_chars / CHARS_PER_TOKEN
    return max(6000, int(tokens * CHARS_PER_TOKEN))

def quoted(task):
    spans = re.findall(r'`([^`\n]{3,80})`', task) + re.findall(r'"([^"\n]{3,80})"', task) + re.findall(r"(?<![\w])'([^'\n]{3,80})'(?![\w])", task)
    return list(dict.fromkeys(s.strip() for s in spans if s.strip()))

def named_targets(task):
    """(kind, text) for every text the task names: old sides of mappings, quoted strings and identifiers without protocol words."""
    out = [('mapping', old) for old, _ in mappings.extract(task)]
    out += [('quoted', q) for q in quoted(task)]
    out += [('name', n) for n in identifiers(task) if n not in PROTOCOL and n not in quoted(task)]
    seen, unique = set(), []
    for kind, text in out:
        if (kind, text) not in seen:
            seen.add((kind, text))
            unique.append((kind, text))
    return unique

def needle(kind, text, content):
    return mappings.literal(text, [content]) if kind == 'mapping' else (text if text in content else None)

def render_whole(path, content):
    if content is None:
        return f'===== {path} (new file, does not exist yet) ====='
    return f"===== {path} ({len(content.split(chr(10)))} lines) =====\n{content}\n===== end of {path} ====="

def regions(path, content, task, targets):
    """(priority, start, end) line ranges (0-based, end exclusive) worth showing, best first."""
    rows = content.split('\n')
    out = [(0, 0, min(len(rows), HEAD))]
    for kind, text in targets:
        found = needle(kind, text, content)
        if not found:
            continue
        hits = [i for i, row in enumerate(rows) if found in row][:8]
        out += [((1 if kind != 'name' else 2), max(0, i - 10), min(len(rows), i + 11)) for i in hits]
    terms = task_terms(task)
    if terms:
        for name, kind, start, end in analyze(path, content)['defs']:
            if subwords(name) & terms and kind != 'variable':
                out.append((3, max(0, start - 1), min(len(rows), end, start - 1 + 50)))
    return rows, out

def render_partial(path, rows, keep):
    parts, previous = [], -1
    for i in sorted(keep):
        if i != previous + 1:
            parts.append(f'... (lines {previous + 2}-{i} not shown)')
        parts.append(rows[i])
        previous = i
    if previous < len(rows) - 1:
        parts.append(f'... (lines {previous + 2}-{len(rows)} not shown)')
    body = '\n'.join(parts)
    return f'===== {path} ({len(rows)} lines; only parts shown, SEARCH must use shown lines) =====\n{body}\n===== end of {path} ====='

def ranges_of(keep):
    out, start, previous = [], None, None
    for i in sorted(keep):
        if start is None:
            start = previous = i
        elif i == previous + 1:
            previous = i
        else:
            out.append([start + 1, previous + 1])
            start = previous = i
    if start is not None:
        out.append([start + 1, previous + 1])
    return out

def pack_files(snapshots, task, budget, targets, focus=None):
    texts, shown, rows_by_path = {}, {}, {}
    full = {p: render_whole(p, s[1]) for p, s in snapshots.items()}
    if sum(len(v) for v in full.values()) <= budget:
        for p, s in snapshots.items():
            n = len((s[1] or '').split('\n')) if s[1] is not None else 0
            shown[p] = {'lines': n, 'shown': n, 'whole': True, 'ranges': [[1, n]] if n else []}
        return full, shown
    keep, candidates = {}, []
    for index, (p, (relative, content, digest)) in enumerate(snapshots.items()):
        if content is None:
            texts[p] = full[p]
            shown[p] = {'lines': 0, 'shown': 0, 'whole': True, 'ranges': []}
            continue
        rows, found = regions(p, content, task, targets)
        rows_by_path[p] = rows
        keep[p] = set()
        found += [(-1, max(0, a - 1 - 5), min(len(rows), b + 5)) for a, b in (focus or {}).get(p, [])]  # where the failing test ran: shown before even the head
        if len(rows) <= SMALL_FILE:
            found = [(0, 0, len(rows))]
        candidates += [(prio, index, p, s, e) for prio, s, e in found]
    headers = sum(len(f'===== {p} ({len(r)} lines; only parts shown, SEARCH must use shown lines) =====\n\n===== end of {p} =====') for p, r in rows_by_path.items())
    used = headers + sum(len(v) for v in texts.values())
    for prio, index, p, s, e in sorted(candidates, key=lambda c: (c[0], c[1], c[3])):
        rows = rows_by_path[p]
        fresh = [i for i in range(s, e) if i not in keep[p]]
        cost = sum(len(rows[i]) + 1 for i in fresh)
        if used + cost <= budget or (prio <= 0 and not keep[p]):
            keep[p].update(fresh)
            used += cost
    for _ in range(12):  # spend what is left on neighbouring lines of what is already shown
        grew = False
        for p in keep:
            rows = rows_by_path[p]
            for a, b in ranges_of(keep[p]):
                for i in list(range(max(0, a - 1 - 8), a - 1)) + list(range(b, min(len(rows), b + 8))):
                    if i not in keep[p] and used + len(rows[i]) + 1 <= budget:
                        keep[p].add(i)
                        used += len(rows[i]) + 1
                        grew = True
        if not grew:
            break
    for p in keep:
        rows = rows_by_path[p]
        whole = len(keep[p]) == len(rows)
        texts[p] = render_whole(p, '\n'.join(rows)) if whole else render_partial(p, rows, keep[p])
        shown[p] = {'lines': len(rows), 'shown': len(keep[p]), 'whole': whole, 'ranges': ranges_of(keep[p])}
    return {p: texts[p] for p in snapshots if p in texts}, shown

def signatures(path, text):
    rows = text.split('\n')
    heads = [(start, rows[start - 1].rstrip()) for name, kind, start, end in analyze(path, text)['defs'] if kind != 'property']
    return [f'{start}: {row[:140]}' for start, row in sorted(set(heads))[:60]]

def pack_references(references, room):
    blocks, report = [], []
    for path, text, digest in references or []:
        label = f'===== REFERENCE (read-only, not editable): {path}'
        rows = text.split('\n')
        if len(text) <= min(room, MAX_REFERENCE):
            block, mode = f'{label} ({len(rows)} lines) =====\n{text}\n===== end of {path} =====', 'whole'
        else:
            sigs = signatures(path, text)
            body = '\n'.join(sigs) if sigs else '\n'.join(rows[:40])
            mode = 'signatures' if sigs else 'head'
            block = f'{label} ({len(rows)} lines; {mode} only) =====\n{body[:min(room, MAX_REFERENCE)]}\n===== end of {path} ====='
        if len(block) > room:
            report.append({'path': path, 'mode': 'skipped', 'chars': 0, 'sha256': digest})
            continue
        blocks.append(block)
        room -= len(block)
        report.append({'path': path, 'mode': mode, 'chars': len(block), 'sha256': digest})
    return blocks, report

def coverage(snapshots, targets, texts):
    """For each named text: found in an authorized file, and visible in what the model is shown."""
    contents = {p: s[1] for p, s in snapshots.items() if s[1] is not None}
    shown_text = '\n'.join(texts.values())
    found_any, missing, hidden = [], [], []
    for kind, text in targets:
        located = [needle(kind, text, c) for c in contents.values()]
        located = [n for n in located if n]
        if not located:
            if kind == 'mapping':
                missing.append(text)
            continue
        found_any.append(text)
        if not any(n in shown_text for n in located):
            hidden.append(text)
    return {'named': len(targets), 'found': len(found_any), 'missing_mappings': missing, 'hidden': hidden}

def pack(snapshots, task, budget, references=None, focus=None):
    """The authorized files plus read-only references as prompt text, and a report of what was shown."""
    targets = named_targets(task)
    ref_estimate = min(sum(min(len(t), MAX_REFERENCE) for _, t, _ in references or []), int(budget * 0.25))
    texts, shown = pack_files(snapshots, task, budget - ref_estimate, targets, focus)
    used = sum(len(v) for v in texts.values())
    blocks, ref_report = pack_references(references, max(0, budget - used))
    text = '\n\n'.join(list(texts.values()) + blocks)
    report = {'budget_chars': budget, 'used_chars': len(text), 'files': shown, 'references': ref_report, 'coverage': coverage(snapshots, targets, texts), 'focus': {p: [list(r) for r in rs] for p, rs in (focus or {}).items() if p in snapshots}}
    return Packed(text, report)

def digest_of(text):
    return hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()
