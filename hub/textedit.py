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

FORMAT = """Reply with edit blocks only, then one line starting with "Summary:".
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
Edit only the listed files. Keep unrelated code exactly as it is. Do not use markdown code fences."""

@dataclass
class Edit:
    path: str
    search: str | None  # None means whole-file replacement
    replace: str

def parse(text):
    """Extract edit blocks; tolerate code fences and a path on the line before the markers."""
    lines = [l for l in text.replace('\r\n', '\n').split('\n') if not re.fullmatch(r'\s*```[\w+-]*\s*', l)]
    edits, path, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        header = re.match(r'\s*(?:FILE|File|file|Path|path)\s*:\s*(\S+)\s*$', line)
        if header:
            path = header.group(1).strip('`"\'')
        elif re.match(r'\s*<{5,9}\s*(SEARCH|WHOLE)\s*$', line):
            kind = re.match(r'\s*<{5,9}\s*(SEARCH|WHOLE)', line).group(1)
            if i and re.fullmatch(r'\s*[\w./-]+\.\w+\s*', lines[i - 1]):  # a bare path line names this block's file
                path = lines[i - 1].strip()
            body, i = [], i + 1
            while i < len(lines) and not re.match(r'\s*(={5,9}|>{5,9})\s*(REPLACE|WHOLE)?\s*$', lines[i]):
                body.append(lines[i]); i += 1
            if kind == 'WHOLE':
                edits.append(Edit(path, None, '\n'.join(body) + '\n'))
            else:
                replace, i = [], i + 1
                while i < len(lines) and not re.match(r'\s*>{5,9}\s*REPLACE\s*$', lines[i]):
                    replace.append(lines[i]); i += 1
                edits.append(Edit(path, '\n'.join(body), '\n'.join(replace)))
        i += 1
    return edits

def summary(text):
    match = re.search(r'(?im)^\s*Summary\s*:\s*(.+)$', text)
    return match.group(1).strip() if match else ''

def indent(line):
    return line[:len(line) - len(line.lstrip())]

def apply(content, search, replace):
    """Exact unique match first, then a whitespace-tolerant line match with the indentation corrected."""
    if search.strip() == '':
        raise ValueError('SEARCH is empty; copy the existing lines you want to change')
    count = content.count(search)
    if count == 1:
        return content.replace(search, replace, 1)
    if count > 1:
        raise ValueError(f'SEARCH matches {count} places; include more surrounding lines so it is unique')
    rows = content.split('\n')
    wanted = [l.strip() for l in search.split('\n')]
    while wanted and not wanted[-1]:
        wanted.pop()
    while wanted and not wanted[0]:
        wanted.pop(0)
    hits = [i for i in range(len(rows) - len(wanted) + 1) if [r.strip() for r in rows[i:i + len(wanted)]] == wanted]
    if not hits:
        raise ValueError('SEARCH does not match the current file; copy the lines exactly as shown')
    if len(hits) > 1:
        raise ValueError(f'SEARCH matches {len(hits)} places; include more surrounding lines so it is unique')
    start = hits[0]
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

def apply_all(files, edits, snapshots):
    """Apply blocks per file; returns (changed paths, errors). A file is written once, only if every block applies."""
    errors, changed, grouped = [], [], {}
    for edit in edits:
        key = re.sub(r'^\./', '', edit.path or '')
        if key not in snapshots:
            errors.append(f"{edit.path or '(no FILE line)'}: not one of the authorized files {sorted(snapshots)}")
            continue
        grouped.setdefault(key, []).append(edit)
    for path, items in grouped.items():
        relative, content, digest = snapshots[path]
        new = content if content is not None else ''
        try:
            for edit in items:
                if edit.search is None:
                    if content is not None and content.count('\n') > WHOLE_LIMIT:
                        raise ValueError(f'WHOLE replacement is only for files under {WHOLE_LIMIT} lines; use SEARCH/REPLACE blocks')
                    new = edit.replace
                else:
                    if content is None:
                        raise ValueError('the file does not exist yet; use a WHOLE block to create it')
                    new = apply(new, edit.search, edit.replace)
        except ValueError as error:
            errors.append(f'{path}: {error}')
            continue
        if new == content:
            errors.append(f'{path}: the blocks did not change anything')
            continue
        try:
            if content is None:
                files.write_file(path, new)
            else:
                files._replace_observed(path, new, digest)
        except (ScopeError, OSError) as error:
            errors.append(f'{path}: {error}')
            continue
        changed.append(path)
    return changed, errors
