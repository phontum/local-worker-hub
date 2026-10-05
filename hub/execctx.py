"""Execution-guided context: where a failing test actually ran, from the stack frames the host parsed, instead of a broad repository read.

Given the failures of a baseline run, keep the frames inside the repository (library frames are dropped by the parser), resolve each to the function
that contains it with the CodeIndex, and produce (a) focus ranges the context packer shows first and (b) a short failure-evidence block for the prompt.
Deterministic and model-free; when there are no usable frames it returns nothing and the normal packing applies."""
from pathlib import Path

MAX_FOCUS_LINES = 60
MAX_FRAMES_SHOWN = 8
EVIDENCE_CHARS = 2500

def repo_frames(failures, index):
    """Frames of the failures that point at an indexed repository file, in call order (outermost first), without repeats."""
    seen, out = set(), []
    for item in failures:
        frames = item.get('frames') or []
        if frames and frames[0].get('order') == 'innermost_first':
            frames = list(reversed(frames))
        for frame in frames:
            path = frame['file']
            if path not in index.data or (path, frame['line']) in seen:
                continue
            seen.add((path, frame['line']))
            span = index.enclosing_def(path, frame['line'])
            out.append({'path': path, 'line': frame['line'], 'function': span[0] if span else frame.get('function'),
                        'start': span[2] if span else max(1, frame['line'] - 10), 'end': min(span[3], span[2] + MAX_FOCUS_LINES - 1) if span else frame['line'] + 10})
    return out

def focus_ranges(frames):
    """{path: [(start, end)]} line ranges (1-based, inclusive) to show first; the innermost frames come first."""
    out = {}
    for frame in reversed(frames):
        out.setdefault(frame['path'], []).append((frame['start'], frame['end']))
    return out

def evidence_text(failures, frames, limit=EVIDENCE_CHARS):
    """What the test run showed: the failing tests with their message and the repository frames, innermost last (the likeliest place of the cause)."""
    lines = ['Failure evidence (parsed by the host from running the test before your edit; the cause is usually in the innermost frames):']
    for item in failures[:3]:
        where = f"{item.get('file')}:{item.get('line')}" if item.get('file') else ''
        lines.append(f"- {item['test_id']} {where}: {item.get('message', '')[:300]}")
    if frames:
        lines.append('Repository frames, outermost to innermost:')
        for frame in frames[-MAX_FRAMES_SHOWN:]:
            lines.append(f"  {frame['path']}:{frame['line']} in {frame['function'] or '<module>'} (function spans lines {frame['start']}-{frame['end']})")
    return '\n'.join(lines)[:limit]

def build(failures, index):
    """(evidence text, focus ranges) or ('', {}) when the failures have no usable repository frames."""
    frames = repo_frames(failures, index)
    if not frames:
        return '', {}
    return evidence_text(failures, frames), focus_ranges(frames)
