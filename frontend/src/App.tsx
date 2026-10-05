import { useEffect, useState } from 'react';
import { api, Event, Job, Sample, Summary } from './api';
import { StatusBar } from './StatusBar';
import { JobList } from './JobList';
import { JobDetail } from './JobDetail';
import { StatsDrawer } from './StatsDrawer';

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]), [summary, setSummary] = useState<Summary | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<Event[]>([]), [paired, setPaired] = useState(true);
  const [detail, setDetail] = useState<Job | null>(null), [stats, setStats] = useState(false);
  const [code, setCode] = useState(''), [error, setError] = useState(''), [filter, setFilter] = useState('all'), [busy, setBusy] = useState(false);
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>; const controller = new AbortController();
    const load = async () => {
      try {
        const options = { signal: controller.signal };
        const [j, s, h] = await Promise.all([api<Job[]>('/api/jobs?view=compact', options), api<Summary>('/api/summary', options), api<Sample[]>('/api/hardware', options)]);
        if (stopped) return;
        setJobs(j); setSummary(s); setSamples(h); setPaired(true); setError('');
      } catch (e) { if (!stopped) { const m = (e as Error).message; if (m === 'pair') setPaired(false); else setError(m); } }
      finally { if (!stopped) timer = setTimeout(load, 3000); }
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, []);
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>; let cursor = 0; const controller = new AbortController(); setEvents([]); setDetail(null);
    if (!selected) return;
    const load = async () => {
      try {
        const options = { signal: controller.signal };
        const [j, page] = await Promise.all([api<Job>(`/api/jobs/${selected}?view=compact`, options),
          api<{ events: Event[]; next_cursor: number; has_more: boolean }>(`/api/jobs/${selected}/events?paged=true&after=${cursor}&limit=300`, options)]);
        if (!stopped) { setDetail(j); setEvents(e => [...e, ...page.events].slice(-2000)); cursor = page.next_cursor; }
      } catch (e) { if (!stopped) setError((e as Error).message); }
      finally { if (!stopped) timer = setTimeout(load, 2000); }
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, [selected]);
  async function pair(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { await api('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) }); setPaired(true); setCode(''); setError(''); }
    catch { setError('Pairing code is invalid or expired. Generate another with local-worker dashboard.'); }
    finally { setBusy(false); }
  }
  async function cancel(job: Job) {
    setBusy(true);
    try { const changed = await api<Job>(`/api/jobs/${job.id}/cancel`, { method: 'POST' }); setJobs(j => j.map(x => x.id === changed.id ? changed : x)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const sample = samples.at(-1);
  const history = samples.slice(-60).filter(s => s.data.gpu && Number.isFinite(s.data.gpu.utilization_percent));
  const job = detail?.id === selected ? detail : jobs.find(j => j.id === selected);
  return <div className="app">
    <StatusBar jobs={jobs} sample={sample} error={error} paired={paired} onStats={() => setStats(s => !s)} />
    {error && <div className="error banner" role="alert">{error}</div>}
    {!paired ? <section className="pair card"><h2>Connect this browser</h2><p>Run <code>local-worker dashboard</code> in your terminal, then enter the one-time code.</p>
      <form onSubmit={pair}><label htmlFor="pair-code">Pairing code</label><input id="pair-code" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" required /><button disabled={busy}>Connect</button></form></section> :
      <main className="work"><JobList jobs={jobs} selected={selected} filter={filter} onFilter={setFilter} onSelect={setSelected} />
        <JobDetail job={job} events={events} busy={busy} onCancel={cancel} /></main>}
    {stats && <StatsDrawer summary={summary} sample={sample} history={history} onClose={() => setStats(false)} />}
  </div>;
}
