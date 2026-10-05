"""Optional board deliberation for public web jobs: triage, independent proposals, an anonymous arbiter, then one
executor pass that answers in plain language with the board's briefing.

Inference stays sequential on one slot. Qwen phases run before Gemma phases so a job swaps models at most twice.
Nothing here counts votes; the host only checks literal anchors. There is deliberately no critic or repair round:
the pilot showed they cost minutes without a measurable benefit.
"""
import json
import random
import time
from .board_drift import anchored, drift_report, uncovered_spans
from .board_prompts import FULL_ROLES, LITE_ROLES, arbiter_brief, discovery_block, executor_briefing, task_block
from .ledger import WebLedger
from hub.model_registry import model_name
from hub.models import BoardProposal, BoardSynthesis

# Rough phase durations for the progress ETA only (seconds, from live runs).
ESTIMATE = {'triage': 8, 'proposal': 15, 'arbiter': 60, 'answer': 60}

class Board:
    def __init__(self, runner, job, request, directory):
        self.runner, self.job, self.request, self.directory = runner, job, request, directory
        self.ident = job['id']
        self.task = request.task
        self.state = {'state': 'running', 'mode': request.board_mode or 'lite', 'phases': [], 'degraded': None,
                      'needs_web': None, 'needs_current_evidence': None, 'drift_flags': 0}
        self.total = runner.model_remaining[self.ident]
        self.pending = []

    def write(self, name, value):
        path = self.directory / name
        path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))
        path.chmod(0o600)

    def remaining(self):
        return self.runner.model_remaining.get(self.ident, 0)

    def eta(self):
        return sum(ESTIMATE[kind] for kind in self.pending)

    async def phase(self, name, prompt, timeout):
        kind = name.split('-')[0]
        self.runner.phase(self.ident, 'board-' + name, eta_seconds=self.eta(), deadline=time.time() + timeout)
        started = time.monotonic()
        try:
            result = await self.runner.execute_structured(self.job, self.request, self.directory, 'board-' + name, prompt, name, timeout)
        except (TimeoutError, RuntimeError, ValueError, OSError) as error:
            result = {'ok': False, 'error': type(error).__name__ + ': ' + str(error)[:300], 'model': None, 'thinking': None}
        if self.pending and self.pending[0] == kind:
            self.pending.pop(0)
        self.state['phases'].append({'phase': name, 'model': result.get('model'), 'thinking': result.get('thinking'), 'ok': bool(result.get('ok')),
                                     'error': result.get('error'), 'seconds': round(time.monotonic() - started, 1),
                                     'output_tokens': result.get('output_tokens'), 'done_reason': result.get('done_reason')})
        self.runner.store.event(self.ident, 'board-phase', self.state['phases'][-1])
        return result

    def finish(self, outcome, reason=None):
        self.state['state'] = outcome
        self.state['degraded'] = reason
        models = [p['model'] for p in self.state['phases'] if p['model']]
        if outcome == 'completed':
            models.append(model_name())  # the executor uses the default model
        self.state['swaps'] = sum(1 for a, b in zip(models, models[1:]) if a != b)
        self.write('board-state.json', self.state)
        self.runner.store.event(self.ident, 'board', {k: self.state[k] for k in ('state', 'mode', 'degraded', 'swaps', 'drift_flags')})
        return self.state

    async def scout(self, query):
        """One discovery search through the shared web ledger; the host runs it, no model tool loop and no page fetches."""
        from hub.scoped import Research
        from .web_provider import selection

        def audit(kind, data):
            with (self.directory / 'tools.jsonl').open('a') as stream:
                stream.write(json.dumps({'time': time.time(), 'phase': 'scout', 'kind': kind, 'data': data}) + '\n')
        ledger = WebLedger.open(self.directory) or WebLedger.create(self.directory, searches=6, fetches=10)
        research = Research(audit, selection(self.directory)['search_provider'], self.directory / 'web-provider-health.json', '', None, ledger)
        self.runner.phase(self.ident, 'board-scout', eta_seconds=self.eta())
        try:
            text = await research.search_web(query, objective='Discover which public sources, sellers and variants matter. Snippets are discovery only.')
        except Exception as error:  # a failed scout never blocks the board
            self.state['scout'] = {'query': query, 'ok': False, 'error': type(error).__name__ + ': ' + str(error)[:200]}
            self.write('board-scout.json', self.state['scout'])
            return None
        self.state['scout'] = {'query': query, 'ok': True}
        self.write('board-scout.json', {'query': query, 'ok': True, 'excerpt': text[:3500]})
        return text[:3500]

    async def run(self, prompt):
        """Returns {'board': state, 'answer': (report, checks, sessions, None) | None}. None means: answer with the plain single flow."""
        roles = FULL_ROLES if self.state['mode'] == 'full' else LITE_ROLES
        self.pending = ['triage'] + ['proposal'] * len(roles) + ['arbiter', 'answer']
        task_prompt = task_block(self.task)
        triage = await self.phase('triage', task_prompt, max(15, self.total * 0.05))
        if triage['ok']:
            self.write('board-triage.json', triage['value'])
            if triage['value']['skip_board']:
                return {'board': self.finish('skipped', 'Triage: a single quick answer is enough'), 'answer': None}
        discovery = None
        query = triage['value'].get('scout_query', '').strip() if triage['ok'] else ''
        if query and triage['value']['needs_external_info'] and 'skeptic' in roles:
            discovery = await self.scout(query)
        proposals = {}
        for role in roles:
            # Only the skeptic sees discovery snippets; the other proposers stay independent of them.
            prompt_for = task_prompt + discovery_block(discovery) if role == 'skeptic' and discovery else task_prompt
            result = await self.phase('proposal-' + role, prompt_for, max(30, self.total * 0.1))
            if result['ok']:
                self.write(f'board-proposal-{role}.json', result['value'])
                proposals[role] = BoardProposal.model_validate(result['value'])
        usable = {role: p for role, p in proposals.items() if anchored(self.task, p.requirements)}
        if len(usable) < 2:
            return {'board': self.finish('degraded', f'Only {len(usable)} usable proposal(s); at least 2 are required'), 'answer': None}

        order = list(usable)
        random.Random(self.ident).shuffle(order)
        labels = dict(zip('ABCD', order))
        self.write('board-identities.json', {label: {'role': role, 'model': next((p['model'] for p in self.state['phases'] if p['phase'] == 'proposal-' + role), None)}
                                             for label, role in labels.items()})
        candidates = {label: usable[role].model_copy(update={'requirements': anchored(self.task, usable[role].requirements)}) for label, role in labels.items()}
        covered_by_candidates = [r for c in candidates.values() for r in c.requirements]
        brief = arbiter_brief(self.task, candidates, uncovered_spans(self.task, covered_by_candidates))
        arbiter = await self.phase('arbiter', brief, max(60, self.total * 0.25))
        if not arbiter['ok']:
            return {'board': self.finish('degraded', 'Arbiter output unusable: ' + str(arbiter.get('error'))[:200]), 'answer': None}
        synthesis = BoardSynthesis.model_validate(arbiter['value'])
        self.write('board-synthesis.json', arbiter['value'])
        report = drift_report(self.task, {label: usable[role] for label, role in labels.items()}, synthesis)
        self.write('board-drift.json', report)
        self.state['drift_flags'] = len(report['flags'])
        valid = anchored(self.task, synthesis.requirements)
        if not valid:
            return {'board': self.finish('degraded', 'The arbiter produced no requirement anchored to the task'), 'answer': None}
        requirements = [r.model_copy(update={'id': f'Q{i}'}) for i, r in enumerate(valid, 1)]
        self.state.update(needs_web=synthesis.needs_web, needs_current_evidence=synthesis.needs_current_evidence)

        profiles = json.loads((self.directory / 'role-config.json').read_text()) if (self.directory / 'role-config.json').exists() else {}
        work = dict(profiles.get('work', profiles.get(self.request.role, {})))
        if synthesis.needs_web:
            WebLedger.open(self.directory) or WebLedger.create(self.directory, searches=6, fetches=10)
        else:
            work['tool_groups'] = []  # web tools stay unavailable when the board decided no external facts are needed
        profiles['work'] = work
        self.write('role-config.json', profiles)

        self.runner.phase(self.ident, 'board-answer', eta_seconds=self.eta(), deadline=time.time() + self.remaining())
        session, answer = await self.runner.execute_model(self.job, self.request, self.directory, 'work',
                                                          prompt + executor_briefing(synthesis, requirements, report['flags']))
        if not answer:
            return {'board': self.finish('degraded', 'The executor produced no valid answer'), 'answer': None}
        return {'board': self.finish('completed'), 'answer': (answer, [], [session], None)}
