import json
import sqlite3
import time
import uuid
from .settings import STATE, initialize, pricing

TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted'}

class Store:
    def __init__(self, path=None):
        initialize()
        self.path = path or STATE / 'hub.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, key TEXT UNIQUE, request TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, started REAL, ended REAL, result TEXT, review TEXT);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, job TEXT, time REAL, kind TEXT, data TEXT);
                CREATE INDEX IF NOT EXISTS events_job ON events(job,id);
                CREATE TABLE IF NOT EXISTS hardware (time REAL PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS imports (path TEXT PRIMARY KEY);
            ''')
            if 'progress' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN progress TEXT')
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def submit(self, request):
        payload = request.model_dump_json()
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE key=?', (request.idempotency_key,)).fetchone()
            if row:
                from .models import JobRequest
                if JobRequest.model_validate_json(row['request']).model_dump() != request.model_dump(): raise ValueError('Idempotency key was reused with different task data')
                return self.decode(row)
            if db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] >= 32:
                raise OverflowError('Worker queue is full')
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO jobs (id,key,request,state,created,started,ended,result,review) VALUES (?,?,?,?,?,?,?,?,?)',
                (ident, request.idempotency_key, payload, 'queued', time.time(), None, None, None, None))
        return self.get(ident)

    @staticmethod
    def decode(row):
        if not row: return None
        obj = dict(row)
        for k in ('request', 'result', 'review', 'progress'): obj[k] = json.loads(obj[k]) if obj.get(k) else None
        return obj

    def get(self, ident):
        with self.connect() as db: return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())

    def list(self, limit=100):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ?', (limit,))]

    def next(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row: return None
            n = db.execute("UPDATE jobs SET state='running',started=? WHERE id=? AND state='queued'", (time.time(),row['id'])).rowcount
        return self.get(row['id']) if n else None

    def finish(self, ident, state, result):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=CASE WHEN state IN ('cancelled','interrupted') THEN state ELSE ? END,ended=COALESCE(ended,?),result=? WHERE id=?",
                (state,time.time(),json.dumps(result),ident))

    def checkpoint(self, ident, result):
        with self.connect() as db:
            db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result),ident))

    def progress(self, ident, data):
        with self.connect() as db:
            db.execute('UPDATE jobs SET progress=? WHERE id=?', (json.dumps(data),ident))

    def queue_position(self, ident):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created,id").fetchall()
        return next((i+1 for i,r in enumerate(rows) if r['id']==ident),None)

    def cancel(self, ident):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='cancelled',ended=? WHERE id=? AND state IN ('queued','running')", (time.time(),ident))
        return self.get(ident)

    def interrupt_running(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='interrupted',ended=?,progress=json_set(COALESCE(progress,'{}'),'$.phase','interrupted','$.deadline',NULL,'$.active_check',NULL) WHERE state='running'", (time.time(),))

    def review(self, ident, review):
        job = self.get(ident)
        if not job: raise KeyError(ident)
        if job['state'] not in TERMINAL: raise ValueError('Review requires a finished job')
        handoff=job['request'].get('handoff_id')
        if handoff and (review.baseline_frontier_tokens is not None or review.baseline_frontier_cost is not None):
            for other in self.list(10000):
                prior=other.get('review') or {}
                if other['id']!=ident and other['request'].get('handoff_id')==handoff and any(prior.get(k) is not None for k in ('baseline_frontier_tokens','baseline_frontier_cost')):
                    raise ValueError('Record the matched baseline once per handoff, including all follow-ups and takeover')
        with self.connect() as db: db.execute('UPDATE jobs SET review=? WHERE id=?', (review.model_dump_json(),ident))
        return self.get(ident)

    def event(self, ident, kind, data):
        with self.connect() as db:
            db.execute('INSERT INTO events(job,time,kind,data) VALUES (?,?,?,?)', (ident,time.time(),kind,json.dumps(data)))

    def events(self, ident, after=0, limit=300):
        with self.connect() as db:
            return [dict(r) | {'data':json.loads(r['data'])} for r in db.execute(
                'SELECT * FROM events WHERE job=? AND id>? ORDER BY id LIMIT ?', (ident,after,max(1,min(limit,1001))))]

    def recent_events(self, ident, limit=8):
        with self.connect() as db:
            return [dict(r) | {'data':json.loads(r['data'])} for r in db.execute(
                'SELECT * FROM (SELECT * FROM events WHERE job=? ORDER BY id DESC LIMIT ?) ORDER BY id', (ident,limit))]

    def all_events(self, ident):
        after = 0
        while page := self.events(ident, after, 1000):
            yield from page
            after = page[-1]['id']

    def hardware(self, data):
        with self.connect() as db:
            db.execute('INSERT INTO hardware VALUES (?,?)',(time.time(),json.dumps(data)))
            db.execute('DELETE FROM hardware WHERE time<?',(time.time()-7*86400,))

    def samples(self, limit=720):
        with self.connect() as db:
            return [dict(r) | {'data':json.loads(r['data'])} for r in db.execute(
                'SELECT * FROM (SELECT * FROM hardware ORDER BY time DESC LIMIT ?) ORDER BY time', (limit,))]

    def summary(self):
        jobs = self.list(10000)
        usage = {'input':0,'output':0,'reasoning':0,'cache_read':0,'cache_write':0}
        role_stats={}
        handoffs={}
        for j in jobs:
            group=j['request'].get('handoff_id') or j['id']
            h=handoffs.setdefault(group,{'id':group,'jobs':0,'model_seconds':0,'check_seconds':0,'review_effort_seconds':0,'takeovers':0,'local_output_tokens':0})
            h['jobs']+=1
            h['model_seconds']+=((j.get('result') or {}).get('metrics') or {}).get('analysis_seconds',0) or 0
            h['check_seconds']+=((j.get('result') or {}).get('metrics') or {}).get('check_seconds',0) or 0
            h['local_output_tokens']+=((j.get('result') or {}).get('usage') or {}).get('output',0) or 0
            h['review_effort_seconds']+=(j.get('review') or {}).get('review_effort_seconds',0) or 0
            h['takeovers']+=int((j.get('review') or {}).get('decision')=='takeover')
            name=j['request'].get('role','unknown');stats=role_stats.setdefault(name,{'jobs':0,'accepted':0,'completed':0,'takeovers':0,'repair_attempts':0})
            stats['jobs']+=1
            if j['review'] and j['review']['decision']=='accepted':stats['accepted']+=1
            if j['review'] and j['review'].get('task_outcome')=='completed':stats['completed']+=1
            if j['review'] and j['review']['decision']=='takeover':stats['takeovers']+=1
            stats['repair_attempts']+=max(0,len((j['result'] or {}).get('attempts',[]))-1)
            for k,v in (j['result'] or {}).get('usage',{}).items():
                if k in usage: usage[k] += v or 0
        review_reasons,review_by_kind={},{}
        for j in jobs:
            r=j.get('review')
            if not r:continue
            row=review_by_kind.setdefault(j['request'].get('kind') or j['request'].get('role','unknown'),{'accepted':0,'rejected':0,'takeover':0})
            row[r['decision']]=row.get(r['decision'],0)+1
            if r['decision']!='accepted':review_reasons[r.get('reason') or 'unspecified']=review_reasons.get(r.get('reason') or 'unspecified',0)+1
        context_stats={}
        with self.connect() as db:
            for row in db.execute("SELECT data FROM events WHERE kind='inference'"):
                entry=json.loads(row['data']);limit=entry.get('context_limit')
                if limit:
                    stats=context_stats.setdefault(str(limit),{'requests':0,'peak_tokens':0,'seconds':0})
                    stats['requests']+=1;stats['peak_tokens']=max(stats['peak_tokens'],entry.get('context_tokens') or 0)
                    stats['seconds']+=entry.get('seconds') or 0
        rates = pricing()
        rate_keys = ('input_per_million','cached_input_per_million','output_per_million')
        equivalent = None
        if all(isinstance(rates.get(k),(float,int)) for k in rate_keys):
            equivalent = (usage['input']*rates[rate_keys[0]] + usage['cache_read']*rates[rate_keys[1]] + usage['output']*rates[rate_keys[2]])/1e6
        matched = [j for j in jobs if j['review'] and j['review'].get('baseline_frontier_tokens') is not None and j['review'].get('delegated_frontier_tokens') is not None]
        measured = [j for j in matched if j['review'].get('measurement_source','measured')=='measured']
        savings = sum(j['review']['baseline_frontier_tokens']-j['review']['delegated_frontier_tokens'] for j in measured)
        money = [j for j in jobs if j['review'] and j['review'].get('measurement_source','measured')=='measured' and j['review'].get('baseline_frontier_cost') is not None and j['review'].get('delegated_frontier_cost') is not None]
        return {'jobs':len(jobs),'accepted':sum(bool(j['review'] and j['review']['decision']=='accepted') for j in jobs),
            'role_stats':role_stats,'review_stats':{'by_kind':review_by_kind,'reasons':review_reasons},'context_stats':context_stats,'handoff_stats':list(handoffs.values())[:100],
            'usage':usage,'pricing':rates,'api_equivalent_usd':equivalent,'matched_baselines':len(measured),
            'manual_estimate_baselines':len(matched)-len(measured),
            'estimated_frontier_tokens_avoided':savings if measured else None,
            'estimated_frontier_cost_avoided':sum(j['review']['baseline_frontier_cost']-j['review']['delegated_frontier_cost'] for j in money) if money else None,
            'subscription_dollars_saved':None}
