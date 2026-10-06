"""Host-side check of the `A -> B` mappings a task states: after the edit, the old text should be gone from the authorized files.

Deterministic and conservative. A mapping is only judged when its old text is actually found in the files before the edit (leading words
that belong to the sentence rather than the text, such as "progress strings", are trimmed until it is). Findings make a COMPLETE job
PARTIAL for the frontier to look at; they never edit anything.
"""
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

class LiteralMapping(BaseModel):
    """Explicit replacements; file/count are required for the model-free edit path."""
    model_config = ConfigDict(extra='forbid')
    old: str = Field(min_length=1, max_length=4000)
    new: str = Field(min_length=1, max_length=4000)
    path: str | None = Field(default=None, min_length=1, max_length=300)
    expected_count: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode='after')
    def single_line(self):
        if '\n' in self.old or '\n' in self.new or '\r' in self.old or '\r' in self.new:
            raise ValueError('A mapping side must be one line')
        if self.old == self.new:
            raise ValueError('A mapping must change its old text')
        return self

def verify_literals(items, before, after):
    """Exact typed mapping verification, including new text containing old text."""
    unmet, checked = [], 0
    for item in items:
        item = LiteralMapping.model_validate(item)
        paths = [item.path] if item.path else list(before)
        old = {p:before.get(p, '') for p in paths}
        new = {p:after.get(p, '') for p in paths}
        was=count(item.old, old.values())
        expected=item.expected_count if item.expected_count is not None else was
        checked+=1
        reason=None
        if not was or was != expected:
            reason=f'expected {expected} source occurrences, observed {was}'
        elif count(item.old, new.values()) != was-expected+item.new.count(item.old)*expected:
            reason='the expected old occurrences were not replaced'
        elif count(item.new, new.values()) < count(item.new, old.values()) + expected:
            reason='the expected new occurrences are missing'
        if reason:
            unmet.append({'old':item.old,'new':item.new,'path':item.path,'remaining':count(item.old,new.values()),'reason':reason})
    return {'checked':checked,'unverifiable':0,'unmet':unmet}

def literal_contents(items, before):
    """Plan all replacements before a scoped transactional commit. No fuzzy matching."""
    output=dict(before)
    for raw in items:
        item=LiteralMapping.model_validate(raw)
        if item.path not in output or item.expected_count is None:
            raise ValueError('Literal edits require an authorized path and expected_count')
        text=output[item.path]
        observed=text.count(item.old)
        if observed != item.expected_count:
            raise ValueError(f'{item.path}: expected {item.expected_count} occurrences, observed {observed}')
        output[item.path]=text.replace(item.old,item.new)
    verified=verify_literals(items,before,output)
    if verified['unmet']:
        raise ValueError('Overlapping or conflicting literal mappings: '+verified['unmet'][0]['reason'])
    return {p:t for p,t in output.items() if t != before[p]}

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

def verify(task, before, after, pairs=None):
    """`before` and `after` map each authorized path to its text; `pairs` (typed mappings from a DelegationSpec) replace the ones parsed from the task text.
    Returns {'checked', 'unverifiable', 'unmet': [...]}."""
    before_texts, after_texts = list(before.values()), list(after.values())
    unmet, checked, unverifiable = [], 0, 0
    for old, new in (extract(task) if pairs is None else pairs):
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
