"""Host-side check of the `A -> B` mappings a task states: after the edit, the old text should be gone from the authorized files.

Deterministic and conservative. A mapping is only judged when its old text is actually found in the files before the edit (leading words
that belong to the sentence rather than the text, such as "progress strings", are trimmed until it is). Findings make a COMPLETE job
PARTIAL for the frontier to look at; they never edit anything.
"""
import re

ARROW = re.compile(r'\s*(?:->|→|=>)\s*')
CLAUSES = re.compile(r'[;\n]|,\s|\.\s')
STRIP = ' `"\'.'

def extract(task):
    """Candidate (old, new) pairs from clauses of the form `old -> new`; chains with several arrows are ambiguous and skipped."""
    pairs = []
    for clause in CLAUSES.split(task):
        parts = ARROW.split(clause)
        if len(parts) == 2:
            old, new = parts[0].strip(STRIP), parts[1].strip(STRIP)
            if old and new:
                pairs.append((old, new))
    return pairs

def literal(old, texts):
    """The longest trailing run of `old`'s words that really occurs in the files, or None."""
    words = old.split()
    for i in range(len(words)):
        candidate = ' '.join(words[i:])
        if len(candidate) >= 3 and any(candidate in text for text in texts):
            return candidate
    return None

def count(needle, texts):
    return sum(text.count(needle) for text in texts)

def verify(task, before, after):
    """`before` and `after` map each authorized path to its text. Returns {'checked', 'unverifiable', 'unmet': [...]}."""
    before_texts, after_texts = list(before.values()), list(after.values())
    unmet, checked, unverifiable = [], 0, 0
    for old, new in extract(task):
        found = literal(old, before_texts)
        if found is None:
            unverifiable += 1
            continue
        checked += 1
        remaining, was = count(found, after_texts), count(found, before_texts)
        short_new = len(new.split()) <= 6 and not re.search(r'["`{}]', new)
        if remaining and (len(found.split()) >= 2 or remaining >= was):
            unmet.append({'old': found, 'new': new, 'remaining': remaining, 'reason': 'the old text is still present'})
        elif short_new and new not in ' '.join(after_texts) and not count(new, after_texts):
            unmet.append({'old': found, 'new': new, 'remaining': remaining, 'reason': 'the new text is missing'})
    return {'checked': checked, 'unverifiable': unverifiable, 'unmet': unmet}
