"""The ask pipeline for Personal/Researcher: decide -> host retrieval -> answer from numbered excerpts.

Two schema-constrained model calls, no tool calling. Small models skipped searches, trusted stale snippets,
converted units in their heads and invented citations when they drove the procedure themselves; the host now
does those steps and the model only decides what to look up and reads what was found.
"""
import json
import time
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field
from .answers import envelope, render
from .calls import Caller, load_profiles, write_session
from .ledger import BudgetExhausted, WebLedger
from .models import JobRequest
from .preferences import load as load_preferences, local_now
from .retrieval import gather
from .trace import append_trace

ASK_PROFILES = {
    'ask-decide': {'thinking': False, 'output': 300, 'temperature': 0.0},
    'ask-answer': {'thinking': False, 'output': 2048, 'temperature': 0.3},
}

class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    needs_web: bool
    queries: list[str] = Field(max_length=2)
    reply_language: str = Field(max_length=40)

class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(max_length=6000)
    used: list[int] = Field(max_length=12)
    answered: bool
    follow_up_query: str = Field(default='', max_length=200)

DECIDE = (
    "You route a user's message. needs_web is true when a good answer needs facts from the internet: anything current or "
    "changing (weather, prices, availability, news, schedules, software versions, recent events, who currently holds a role) or "
    "specific facts you are not sure of. It is false for greetings, chit-chat, opinions, writing or coding help without lookups, "
    "math, and stable general knowledge. "
    "queries: up to two short web search queries, written in the language most likely to find the answer (often the language of "
    "the place or topic) and phrased the way a person would search, for example aiming at comparison or listing pages when the "
    "user wants the cheapest or best option. Do not put dates or years in queries about current things; words like today, now "
    "or hourly find current pages better. Leave queries empty when needs_web is false. "
    "reply_language: the language the user's message is written in (an English question about Serbia gets English), never the "
    "language of the place or of the queries. "
    "If the message is missing an essential detail (for example a city for a weather question), set needs_web false.")
ANSWER_WEB = ("Answer the user's message in {language} using the numbered excerpts. They were read just now unless marked "
    "'snippet' (a search-result summary that may be outdated) or 'hosted' (a third-party copy). Prefer page excerpts over snippets and "
    "newer over older. Check the date each excerpt is about and use only data for the day and time the user asked about; ignore excerpts about other days. Use only facts that appear in the excerpts, and limit words like cheapest, best or all to the sources you read, saying so. Copy numbers and their units exactly as written; they are converted "
    "automatically. Give the answer first, then the details that matter for the user's plan; for a time window, describe that window. "
    "Plain text, no markdown, no links and no source lines: sources are added automatically from 'used', the excerpt numbers you relied on. "
    "If the excerpts do not contain the answer, say so in one sentence, give your best guidance, set answered=false and suggest a better follow_up_query.")
ANSWER_PLAIN = ("Answer the user's message in {language}, naturally and concisely, from your own knowledge. Plain text, no markdown, no links. "
    "If the message lacks an essential detail, ask one short question. If it needs current information you do not have, say so. "
    "answered is true unless you could not answer; used is empty.")

def excerpt_block(excerpts):
    rows = []
    for e in excerpts:
        rows.append(f"[{e['n']}] {e['title'][:100]} | {e['url']} | {e['kind']} | read {e['observed_at'][:16].replace('T', ' ')} UTC\n{e['text']}")
    return '\n\n'.join(rows)

async def run_ask(directory, label, prompt, phase='work'):
    request = JobRequest.model_validate_json((directory / 'request.json').read_text())
    prefs = load_preferences()
    blocks = []
    question = request.task
    context = prompt if prompt.strip() != question.strip() else ''
    note, excerpts, log = '', [], {}
    when = 'Current local time: ' + local_now(prefs) + f" ({prefs['timezone']}). Current UTC time: " + datetime.now(timezone.utc).isoformat()[:16]
    async with Caller(directory, label, request, load_profiles(directory, ASK_PROFILES), 'ask') as caller:
        def audit(kind, data):
            with (directory / 'tools.jsonl').open('a') as stream:
                stream.write(json.dumps({'time': time.time(), 'phase': 'ask', 'kind': kind, 'data': data}) + '\n')
            caller.event({'type': 'tool', 'tool': {'web_search': 'search_web', 'web_fetch': 'fetch_web'}.get(kind, kind), 'state': {'status': 'completed'}})

        decision = await caller.json('ask-decide', 0, DECIDE + '\n' + when, question, Decision, thinking=False)
        if decision is None:
            decision = Decision(needs_web=True, queries=[question[:200]], reply_language='the language of the user')
        if decision.needs_web:
            queries = [q for q in decision.queries if q.strip()] or [question[:200]]
            try:
                excerpts, log = await gather(question, queries, audit, ledger=WebLedger.open(directory))
            except (BudgetExhausted, OSError, ValueError) as error:
                log = {'error': str(error)[:200]}
            blocks.append({'type': 'tool', 'name': 'search_web', 'arguments': {'queries': queries}, 'state': {'status': 'completed', 'content': [{'type': 'text', 'text': json.dumps(log)}]}})
            append_trace(directory, 'ask', 0, 'retrieval', json.dumps(log) + '\n\n' + excerpt_block(excerpts))
            if not excerpts:
                note = "I couldn't get anything useful from the web just now, so this is from general knowledge and may be out of date."
        system = (ANSWER_WEB if excerpts else ANSWER_PLAIN).format(language=decision.reply_language or 'the language of the user') + '\n' + when
        user = question + (('\n\nContext:\n' + context) if context else '') + (('\n\nExcerpts:\n' + excerpt_block(excerpts)) if excerpts else '')
        thinking = request.execution_preset == 'extended' or bool(request.model_thinking)
        answer = await caller.json('ask-answer', 1, system, user, Answer, thinking)
        if answer and excerpts and not answer.answered and answer.follow_up_query.strip():
            try:
                more, more_log = await gather(question, [answer.follow_up_query], audit, ledger=WebLedger.open(directory), pages_limit=2)
            except (BudgetExhausted, OSError, ValueError):
                more, more_log = [], {}
            for extra in more:
                extra['n'] += len(excerpts)
            if more:
                excerpts += more
                log['follow_up'] = more_log
                append_trace(directory, 'ask', 3, 'retrieval', json.dumps(more_log) + '\n\n' + excerpt_block(more))
                user = question + (('\n\nContext:\n' + context) if context else '') + '\n\nExcerpts:\n' + excerpt_block(excerpts)
                answer = await caller.json('ask-answer', 4, system, user, Answer, thinking) or answer
        if answer is None or not answer.answer.strip():
            content = envelope('PARTIAL', 'I could not produce an answer this time.', 'The local model returned no usable answer.')
        else:
            content = render(answer.answer, excerpts, answer.used, answer.answered or (not excerpts and not decision.needs_web), question, note, prefs)
        record = {'decision': decision.model_dump(), 'retrieval': log, 'excerpts': [{k: e[k] for k in ('n', 'url', 'title', 'kind', 'observed_at')} for e in excerpts],
                  'answer': answer.model_dump() if answer else None}
        (directory / 'ask.json').write_text(json.dumps(record, indent=2, ensure_ascii=False))
        (directory / 'ask.json').chmod(0o600)
        write_session(directory, label, caller, prompt, content, blocks)
