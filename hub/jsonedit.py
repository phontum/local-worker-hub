"""JSON edit format (experiment): the same edits as the text protocol, as one schema-constrained JSON object.

The model replies {"edits": [{"path", "op", "search", "replace"}...], "summary": "..."} under Ollama's `format` constraint. Edits become the same
`textedit.Edit` objects and go through the same plan, guards, staging, continuation and commit. A reply that is not valid JSON voids everything,
like a malformed text reply; `salvage` recovers the complete edit objects from a cut-off reply so continuation works the same way.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .textedit import Edit, Parsed, WHOLE_LIMIT

class JsonEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=300)
    op: Literal['replace', 'whole', 'delete']
    search: str | None = None
    replace: str | None = None

class JsonReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edits: list[JsonEdit] = Field(max_length=60)
    summary: str = Field(default='', max_length=300)

FORMAT = """Reply with one JSON object: {{"edits": [...], "summary": "<one line>"}}. Each edit is one of:
{{"path": "<path exactly as listed>", "op": "replace", "search": "<lines copied exactly from the file, with their indentation; enough lines to be unique>", "replace": "<the new lines>"}}
{{"path": "<path>", "op": "whole", "replace": "<complete new file content>"}}  (only for a file under {limit} lines or a new listed file)
{{"path": "<path>", "op": "delete"}}  (only for files named as deletable)
Write line breaks inside strings as \\n. Edit only the listed files. Keep unrelated code exactly as it is. Make several small edits rather than one huge one."""

def format_for(delete_paths=()):
    base = FORMAT.format(limit=WHOLE_LIMIT)
    return base + ' Deletable: ' + ', '.join(delete_paths) + '.' if delete_paths else base

def convert(edit, number):
    """(Edit, problem) for one JSON edit."""
    where = f'edit {number} ({edit.path})'
    if edit.op == 'delete':
        return Edit(edit.path, None, None, delete=True), None
    if edit.op == 'whole':
        if edit.replace is None:
            return None, f'{where}: a whole-file edit needs "replace"'
        return Edit(edit.path, None, edit.replace if edit.replace.endswith('\n') else edit.replace + '\n'), None
    if not edit.search or edit.replace is None:
        return None, f'{where}: a replace edit needs both "search" and "replace"'
    return Edit(edit.path, edit.search, edit.replace), None

def parse(text):
    """Strict parse of a whole reply; any problem voids it."""
    try:
        reply = JsonReply.model_validate_json(text)
    except ValueError as error:
        first = str(error).splitlines()[0][:160]
        return Parsed([], [f'the reply is not a valid JSON edit set ({first}); the output may have been cut off or malformed'])
    edits, problems = [], []
    for number, item in enumerate(reply.edits, 1):
        edit, problem = convert(item, number)
        (problems if problem else edits).append(problem or edit)
    return Parsed([] if problems else edits, problems, reply.summary.strip())

def salvage(text):
    """The complete, valid edit objects at the start of a cut-off reply, as Edit objects."""
    match = re.search(r'"edits"\s*:\s*\[', text)
    if not match:
        return []
    decoder, position, out = json.JSONDecoder(), match.end(), []
    while True:
        while position < len(text) and text[position] in ' \n\r\t,':
            position += 1
        if position >= len(text) or text[position] != '{':
            break
        try:
            raw, position = decoder.raw_decode(text, position)
            item = JsonEdit.model_validate(raw)
        except ValueError:
            break
        edit, problem = convert(item, len(out) + 1)
        if problem:
            break
        out.append(edit)
    return out
