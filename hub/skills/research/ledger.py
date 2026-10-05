"""Job-level web budget and page cache shared by every phase subprocess of one job.

Only jobs that create web-ledger.json (board jobs) use it; other flows keep per-phase budgets.
A reused page keeps its original observed_at and WEB_EVIDENCE metadata, so freshness is never upgraded.
"""
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

NAME = 'web-ledger.json'

class BudgetExhausted(ValueError):
    pass

class WebLedger:
    def __init__(self, path, max_age=600):
        self.path = Path(path)
        self.max_age = max_age

    @classmethod
    def create(cls, directory, searches=6, fetches=10, max_age=600):
        path = Path(directory) / NAME
        path.write_text(json.dumps({'limits': {'search': searches, 'fetch': fetches},
                                    'used': {'search': 0, 'fetch': 0}, 'pages': {}, 'order': []}))
        path.chmod(0o600)
        return cls(path, max_age)

    @classmethod
    def open(cls, directory):
        path = Path(directory) / NAME
        return cls(path) if path.is_file() else None

    def update(self, change):
        """Apply `change(state)` under an exclusive lock and persist the new state."""
        with self.path.open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            state = json.loads(stream.read())
            result = change(state)
            stream.seek(0)
            stream.write(json.dumps(state))
            stream.truncate()
            return result

    def read(self):
        with self.path.open() as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return json.loads(stream.read())

    def charge(self, kind):
        def change(state):
            if state['used'][kind] >= state['limits'][kind]:
                raise BudgetExhausted(f"Job-wide {kind} budget exhausted ({state['limits'][kind]}); report available findings")
            state['used'][kind] += 1
            return state['limits'][kind] - state['used'][kind]
        return self.update(change)

    def wid(self, url):
        def change(state):
            if url not in state['order']:
                state['order'].append(url)
            return 'W' + str(state['order'].index(url) + 1)
        return self.update(change)

    def store_page(self, url, text, metadata, provider, observed_at):
        def change(state):
            state['pages'][url] = {'text': text, 'metadata': metadata, 'provider': provider, 'observed_at': observed_at}
        self.update(change)

    def load_page(self, url):
        entry = self.read()['pages'].get(url)
        if not entry:
            return None
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry['observed_at'])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return None
        return entry if 0 <= age <= self.max_age else None
