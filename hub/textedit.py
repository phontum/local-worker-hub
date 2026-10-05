"""Text edit protocol for the Editor (aider-style): the host shows the files, the model replies with blocks.

    FILE: path/to/file.py
    <<<<<<< SEARCH
    exact existing lines
    =======
    replacement lines
    >>>>>>> REPLACE

or, for a small or new file, a whole replacement between `<<<<<<< WHOLE` and `>>>>>>> WHOLE`.
No tool calling is involved. Every write still goes through ScopedFiles (exact authorized paths, symlink,
hardlink and secret guards, and a freshness hash taken when the host read the file).
"""
import hashlib
import os
import re
from dataclasses import dataclass

from .scoped import ScopeError

WHOLE_LIMIT = 150

FORMAT = """Reply with edit blocks only, then the line END OF EDITS, then one line starting with "Summary:".
For each change:
FILE: <path exactly as listed>
<<<<<<< SEARCH
<lines copied exactly from the file, including indentation; enough lines to be unique>
=======
<the new lines>
>>>>>>> REPLACE
Several blocks may target the same file. To replace a whole small file (under {limit} lines) or create a listed new file, use:
FILE: <path>
<<<<<<< WHOLE
<complete new file content>
>>>>>>> WHOLE
Every block must be complete: a SEARCH block needs its ======= line and its >>>>>>> REPLACE line, a WHOLE block needs its >>>>>>> WHOLE line. Make several small blocks rather than one huge one.\nEdit only the listed files. Keep unrelated code exactly as it is. Do not use markdown code fences."""

DELETE_FORMAT = """
To delete a file that is listed as deletable, use exactly:
FILE: <path>
<<<<<<< DELETE
>>>>>>> DELETE
Only files named as deletable may be deleted."""

def format_for(delete_paths=()):
    base = FORMAT.format(limit=WHOLE_LIMIT)
    return base + DELETE_FORMAT + ' Deletable: ' + ', '.join(delete_paths) + '.' if delete_paths else base

@dataclass
class Edit:
    path: str
    search: str | None  # None means whole-file replacement
    replace: str | None
    delete: bool = False

SEARCH_OPEN = re.compile(r'<{7} (SEARCH|WHOLE|DELETE)\s*$')
SEPARATOR = re.compile(r'={7}\s*$')
CLOSE = re.compile(r'>{7} (REPLACE|WHOLE|DELETE)\s*$')
HEADER = re.compile(r'\s*(?:FILE|File|file|Path|path)\s*:\s*(\S+)\s*$')
SENTINEL = 'END OF EDITS'

@dataclass
class Parsed:
    edits: list
    problems: list
    summary: str = ''

def parse(text, require_sentinel=False):
    """Strict parse of a whole reply. Every block must be properly terminated, markers are exactly seven characters, and a WHOLE block
    may contain any text (including lines of `=======`). Any problem voids the entire reply: nothing is applied from a malformed one."""
    lines = [l.rstrip('\r') for l in text.replace('\r\n', '\n').split('\n') if not re.fullmatch(r'\s*```[\w+-]*\s*', l)]
    edits, problems, outside, path, i = [], [], [], None, 0
    while i < len(lines):
        line = lines[i]
        header = HEADER.match(line)
        opened = SEARCH_OPEN.match(line.strip())
        if header:
            path = header.group(1).strip('`"\'')
        elif opened:
            kind = opened.group(1)
            where = f'block {len(edits) + 1}'
            if i and re.fullmatch(r'\s*[\w./-]+\.\w+\s*', lines[i - 1]):  # a bare path line names this block's file
                path = lines[i - 1].strip()
            if path is None:
                problems.append(f'{where} has no FILE line before it')
            body, i = [], i + 1
            end = CLOSE if kind in ('WHOLE', 'DELETE') else SEPARATOR
            while i < len(lines) and not end.match(lines[i].strip()) and not (kind == 'SEARCH' and (SEARCH_OPEN.match(lines[i].strip()) or CLOSE.match(lines[i].strip()))):
                body.append(lines[i]); i += 1
            closed = i < len(lines) and end.match(lines[i].strip())
            if kind in ('WHOLE', 'DELETE') and closed and closed.group(1) != kind:
                closed = None
            if not closed:
                problems.append(f'{where} ({path}) is not terminated: ' + (f'missing >>>>>>> {kind}' if kind in ('WHOLE', 'DELETE') else 'missing =======') + '; the output was cut off or malformed')
                break
            if kind == 'DELETE':
                edits.append(Edit(path, None, None, delete=True))
            elif kind == 'WHOLE':
                edits.append(Edit(path, None, '\n'.join(body) + '\n'))
            else:
                replace, i = [], i + 1
                while i < len(lines) and not CLOSE.match(lines[i].strip()) and not SEARCH_OPEN.match(lines[i].strip()):
                    replace.append(lines[i]); i += 1
                if i >= len(lines) or not CLOSE.match(lines[i].strip()) or CLOSE.match(lines[i].strip()).group(1) != 'REPLACE':
                    problems.append(f'{where} ({path}) is not terminated: missing >>>>>>> REPLACE; the output was cut off or malformed')
                    break
                edits.append(Edit(path, '\n'.join(body), '\n'.join(replace)))
        else:
            outside.append(line)
        i += 1
    tail = [l.strip() for l in outside if l.strip()]
    sentinel = SENTINEL in tail
    if require_sentinel and edits and not problems and not sentinel:
        problems.append(f'the reply does not end with "{SENTINEL}", so it may be incomplete')
    match = re.search(r'(?im)^\s*Summary\s*:\s*(.+)$', '\n'.join(outside))  # only text outside the blocks
    return Parsed(edits, problems, match.group(1).strip() if match else '')

def summary(text):
    return parse(text).summary

def indent(line):
    return line[:len(line) - len(line.lstrip())]

def occurrences(content, search):
    found, i = [], content.find(search)
    while i >= 0:
        found.append(i)
        i = content.find(search, i + len(search))
    return found

def alignment(content, i, search):
    """How an exact match sits in its lines: 'line' (whole lines), 'word' (starts and ends on word boundaries) or 'inside' (cuts through a word)."""
    j = i + len(search)
    start = content.rfind('\n', 0, i) + 1
    end = content.find('\n', j)
    end = len(content) if end < 0 else end
    if content[start:i].strip() == '' and content[j:end].strip() == '':
        return 'line'
    word = lambda c: c.isalnum() or c == '_'
    cut = (i > 0 and word(content[i - 1]) and word(search[0])) or (j < len(content) and word(content[j]) and word(search[-1]))
    return 'inside' if cut else 'word'

def apply(content, search, replace, mode='substring', stats=None):
    """Exact unique match first, then a whitespace-tolerant line match with the indentation corrected.
    `mode` decides which exact matches count: 'substring' (any), 'word' (not through a word) or 'line' (whole lines only); `stats` counts how each match sat."""
    if search.strip() == '':
        raise ValueError('SEARCH is empty; copy the existing lines you want to change')
    hits = occurrences(content, search)
    if stats is not None:
        for i in hits:
            stats[alignment(content, i, search)] = stats.get(alignment(content, i, search), 0) + 1
    allowed = {'substring': ('line', 'word', 'inside'), 'word': ('line', 'word'), 'line': ('line',)}[mode]
    good = [i for i in hits if alignment(content, i, search) in allowed]
    if len(good) == 1:
        return content[:good[0]] + replace + content[good[0] + len(search):]
    if len(good) > 1:
        raise ValueError(f'SEARCH matches {len(good)} places; include more surrounding lines so it is unique')
    if hits:
        raise ValueError('SEARCH matches only part of a line' if mode == 'line' else 'SEARCH starts or ends inside a word'
                         + '; copy complete lines exactly as shown, from the start of the first line to the end of the last')
    rows = content.split('\n')
    wanted = [l.strip() for l in search.split('\n')]
    while wanted and not wanted[-1]:
        wanted.pop()
    while wanted and not wanted[0]:
        wanted.pop(0)
    found = [i for i in range(len(rows) - len(wanted) + 1) if [r.strip() for r in rows[i:i + len(wanted)]] == wanted]
    if not found:
        raise ValueError('SEARCH does not match the current file; copy the lines exactly as shown')
    if len(found) > 1:
        raise ValueError(f'SEARCH matches {len(found)} places; include more surrounding lines so it is unique')
    if stats is not None:
        stats['tolerant'] = stats.get('tolerant', 0) + 1
    start = found[0]
    search_first = next(l for l in search.split('\n') if l.strip())
    shift = indent(rows[start])[:max(0, len(indent(rows[start])) - len(indent(search_first)))] if indent(rows[start]).endswith(indent(search_first)) else ''
    new = [(shift + l) if l.strip() else l for l in replace.split('\n')]
    return '\n'.join(rows[:start] + new + rows[start + len(wanted):])

def snapshot(files, path):
    """Read an authorized file for the prompt and remember its hash for the freshness check at write time."""
    target = files.path(path, exists=False)
    relative = str(target.relative_to(files.root))
    if not target.is_file():
        return relative, None, None
    if target.stat().st_size > 500_000:
        raise ScopeError(f'{relative} is too large for a text edit')
    with os.fdopen(files.open_fd(path, os.O_RDONLY), 'rb') as stream:
        data = stream.read(500_001)
    digest = hashlib.sha256(data).hexdigest()
    files.observed[relative] = digest
    files.audit('read', {'path': relative, 'start': 1, 'lines': data.count(b'\n') + 1, 'sha256': digest, 'evidence_id': None, 'excerpt': ''})
    return relative, data.decode('utf-8'), digest

DEF = re.compile(r'^\s*(?:async\s+)?(?:def|class|function)\s+\w+|^\s*(?:it|test|describe)\s*\(|^\s*(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s*)?(?:\(|function)')
SHRINK = {'block_min_search_lines': 20, 'block_keep_ratio': 0.25, 'file_min_lines': 40, 'file_keep_ratio': 0.6, 'definitions_lost': 2}

def lines_of(text):
    return text.split('\n') if text else []

def suspicious(path, before, after, search=None, replace=None, limits=SHRINK):
    """Reasons a change looks destructive: a large SEARCH replaced by almost nothing, a file that loses most of its lines,
    or definitions removed without any being added. Returns [] for ordinary edits."""
    reasons = []
    if search is not None:
        old, new = lines_of(search), [l for l in lines_of(replace or '') if l.strip()]
        if len(old) >= limits['block_min_search_lines'] and len(new) < len(old) * limits['block_keep_ratio']:
            reasons.append(f'{path}: one block replaces {len(old)} lines with {len(new)}; this removes existing code. Narrow the SEARCH to the lines that change, or reply with the full replacement')
    b, a = lines_of(before), lines_of(after)
    if len(b) >= limits['file_min_lines'] and len(a) < len(b) * limits['file_keep_ratio']:
        reasons.append(f'{path}: the file would shrink from {len(b)} to {len(a)} lines; the task did not ask to delete most of it')
    lost = sum(1 for l in b if DEF.match(l)) - sum(1 for l in a if DEF.match(l))
    if lost >= limits['definitions_lost'] and not any(DEF.match(l) for l in a if l not in set(b)):
        reasons.append(f'{path}: {lost} definitions would be removed and none added')
    return reasons

class Deleted:
    def __repr__(self):
        return 'DELETED'

DELETED = Deleted()

@dataclass
class Plan:
    """The outcome of planning one reply, in memory only: new content per file that planned cleanly, and every error."""
    contents: dict
    errors: list
    changes: dict  # path -> (lines removed, lines added)
    stats: dict = None  # how exact matches sat in their lines, and how many needed the tolerant path

def already_there(content, replace):
    """True when a substantial replacement already stands in the file as whole lines, so the change was evidently made by an earlier unit."""
    if not replace or len(replace.strip()) < 12:
        return False
    i = content.find(replace)
    return i >= 0 and alignment(content, i, replace) == 'line'

def plan(edits, snapshots, allow_shrink=False, deletable=(), mode='substring', lenient=False):
    """Compute every file's new content without writing anything. A file with any failing block contributes errors and no content."""
    errors, contents, changes, grouped, stats = [], {}, {}, {}, {}
    for edit in edits:
        key = re.sub(r'^\./', '', edit.path or '')
        if key not in snapshots:
            errors.append(f"{edit.path or '(no FILE line)'}: not one of the authorized files {sorted(snapshots)}")
            continue
        grouped.setdefault(key, []).append(edit)
    for path, items in grouped.items():
        relative, content, digest = snapshots[path]
        new, problems = content if content is not None else '', []
        if any(e.delete for e in items):
            if len(items) > 1:
                errors.append(f'{path}: a file cannot be both edited and deleted in one reply')
            elif path not in deletable:
                errors.append(f'{path}: deleting it was not declared (delete_paths); no deletion was applied')
            elif content is None:
                errors.append(f'{path}: the file does not exist, so it cannot be deleted')
            else:
                contents[path] = DELETED
                changes[path] = (len(lines_of(content)), 0)
            continue
        try:
            for edit in items:
                if lenient and edit.search is not None and edit.search not in new and already_there(new, edit.replace):
                    stats['already_applied'] = stats.get('already_applied', 0) + 1  # an earlier unit already made this change
                    continue
                if edit.search is None:
                    if content is not None and content.count('\n') > WHOLE_LIMIT:
                        raise ValueError(f'WHOLE replacement is only for files under {WHOLE_LIMIT} lines; use SEARCH/REPLACE blocks')
                    problems += [] if allow_shrink else suspicious(path, new, edit.replace)
                    new = edit.replace
                else:
                    if content is None:
                        raise ValueError('the file does not exist yet; use a WHOLE block to create it')
                    updated = apply(new, edit.search, edit.replace, mode, stats)
                    problems += [] if allow_shrink else suspicious(path, new, updated, edit.search, edit.replace)
                    new = updated
        except ValueError as error:
            errors.append(f'{path}: {error}')
            continue
        if new == content:
            if not lenient:  # a unit with nothing left to change (an earlier unit did it, or it restated the text) is fine; the final checks catch real omissions
                errors.append(f'{path}: the blocks did not change anything')
            continue
        if problems:
            errors.extend(dict.fromkeys(problems))
            continue
        before_rows, after_rows = lines_of(content or ''), lines_of(new)
        contents[path] = new
        changes[path] = (max(0, len(before_rows) - len(after_rows)), max(0, len(after_rows) - len(before_rows)))
    return Plan(contents, errors, changes, stats)

def commit(files, contents, snapshots):
    """Write every planned file (and perform every declared deletion) or none. Freshness is re-checked for all files first; if a write still
    fails midway, what was already done is restored from the snapshots. Returns (changed paths, errors)."""
    for path in contents:
        relative, content, digest = snapshots[path]
        target = files.path(path, exists=False)
        if content is not None and (not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest):
            return [], [f'{path}: the file changed since it was read; nothing was applied']
        if content is None and target.exists():
            return [], [f'{path}: the file appeared since it was read; nothing was applied']
    written = []
    try:
        for path, new in contents.items():
            relative, content, digest = snapshots[path]
            if new is DELETED:
                files.delete_file(path, digest)
            elif content is None:
                files.write_file(path, new)
            else:
                files._replace_observed(path, new, digest)
            written.append(path)
    except (ScopeError, OSError) as error:
        for path in reversed(written):
            relative, content, digest = snapshots[path]
            try:
                if contents[path] is DELETED:
                    files.write_file(path, content)
                elif content is None:
                    files.path(path, exists=False).unlink(missing_ok=True)
                else:
                    files._replace_observed(path, content, hashlib.sha256(contents[path].encode()).hexdigest())
            except (ScopeError, OSError):
                pass
        return [], [f'{error}; the files written so far were restored and nothing was applied']
    return written, []

def apply_all(files, edits, snapshots, allow_shrink=False, deletable=()):
    """All-or-nothing apply of one reply's blocks; returns (changed paths, errors)."""
    planned = plan(edits, snapshots, allow_shrink, deletable)
    if planned.errors:
        return [], planned.errors
    return commit(files, planned.contents, snapshots)
