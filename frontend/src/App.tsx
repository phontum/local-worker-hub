import { useEffect, useRef, useState } from 'react';
import { api, Event, Job, Sample, Summary } from './api';
import { StatusBar, View } from './StatusBar';
import { Chat } from './Chat';
import { JobList } from './JobList';
import { JobDetail } from './JobDetail';
import { StatsDrawer } from './StatsDrawer';
import { usePoll } from './usePoll';

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]), [summary, setSummary] = useState<Summary | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<Event[]>([]), [paired, setPaired] = useState(true);
  const [detail, setDetail] = useState<Job | null>(null), [stats, setStats] = useState(false);
  const [statsPeriod, setStatsPeriod] = useState<'since_reset' | 'today' | 'all'>('since_reset');
  const [view, setViewState] = useState<View>(() => location.hash === '#chat' ? 'chat' : 'tasks');
  const [code, setCode] = useState(''), [error, setError] = useState(''), [filter, setFilter] = useState('all'), [busy, setBusy] = useState(false);
  usePoll(async signal => {
    try {
      const options = { signal };
      const [j, s, h] = await Promise.all([api<Job[]>('/api/jobs?view=compact', options), api<Summary>(`/api/summary?period=${statsPeriod}&include_eval=false`, options), api<Sample[]>('/api/hardware', options)]);
      if (signal.aborted) return;
      setJobs(j); setSummary(s); setSamples(h); setPaired(true); setError('');
    } catch (e) { if (!signal.aborted) { const m = (e as Error).message; if (m === 'pair') setPaired(false); else setError(m); } }
  }, 3000, [statsPeriod]);
  const cursor = useRef(0);
  useEffect(() => { cursor.current = 0; setEvents([]); setDetail(null); }, [selected]);  // declared before the poll below so it resets first
  usePoll(async signal => {
    try {
      const options = { signal };
      const [j, page] = await Promise.all([api<Job>(`/api/jobs/${selected}?view=compact`, options),
        api<{ events: Event[]; next_cursor: number; has_more: boolean }>(`/api/jobs/${selected}/events?paged=true&after=${cursor.current}&limit=300`, options)]);
      if (signal.aborted) return;
      setDetail(j); setEvents(e => [...e, ...page.events].slice(-2000)); cursor.current = page.next_cursor;
    } catch (e) { if (!signal.aborted) setError((e as Error).message); }
  }, 2000, [selected], !!selected);
  const setView = (next: View) => { setViewState(next); window.history.replaceState(null, '', next === 'chat' ? '#chat' : location.pathname + location.search); };
  const openJob = (id: string) => { setSelected(id); setView('tasks'); };
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
    <StatusBar jobs={jobs} sample={sample} error={error} paired={paired} view={view} onView={setView} onStats={() => setStats(s => !s)} />
    {error && <div className="error banner" role="alert">{error}</div>}
    {!paired ? <section className="pair card"><h2>Connect this browser</h2><p>Run <code>local-worker dashboard</code> in your terminal, then enter the one-time code.</p>
      <form onSubmit={pair}><label htmlFor="pair-code">Pairing code</label><input id="pair-code" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" required /><button disabled={busy}>Connect</button></form></section> :
      view === 'chat' ? <main className="work chat-view"><Chat onOpenJob={openJob} /></main> :
      <main className="work"><JobList jobs={jobs} selected={selected} filter={filter} onFilter={setFilter} onSelect={setSelected} />
        <JobDetail job={job} events={events} busy={busy} onCancel={cancel} /></main>}
    {stats && <StatsDrawer summary={summary} period={statsPeriod} onPeriod={p => { setSummary(null); setStatsPeriod(p); }} sample={sample} history={history} onClose={() => setStats(false)} />}
  </div>;
}
