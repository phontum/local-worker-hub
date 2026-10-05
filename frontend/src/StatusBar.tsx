import { Job, Sample, gb } from './api';

function Meter({ value, max = 100 }: { value: number; max?: number }) {
  return <span className="meter"><span style={{ width: `${Math.min(100, Math.max(0, value / max * 100))}%` }} /></span>;
}

export type View = 'chat' | 'tasks';

export function StatusBar({ jobs, sample, error, paired, view, onView, onStats }: { jobs: Job[]; sample?: Sample; error: string; paired: boolean; view: View; onView: (view: View) => void; onStats: () => void }) {
  const gpu = sample?.data.gpu;
  const running = jobs.filter(j => j.state === 'running').length, queued = jobs.filter(j => j.state === 'queued').length;
  return <header className="bar">
    <div className="brand"><span className="logo">lw</span><h1>Local Worker</h1></div>
    {paired && <div className="chips" aria-label="Worker status">
      <span className="chip"><b>{running}</b> running · <b>{queued}</b> queued</span>
      <span className="chip">GPU <b>{gpu ? `${gpu.utilization_percent}%` : '—'}</b></span>
      <span className="chip">VRAM <b>{gpu ? `${(gpu.memory_used_mb / 1024).toFixed(1)}/${(gpu.memory_total_mb / 1024).toFixed(1)} GB` : '—'}</b>{gpu && <Meter value={gpu.memory_used_mb} max={gpu.memory_total_mb} />}</span>
      <span className="chip">RAM <b>{sample ? `${gb(sample.data.ram_used)}/${gb(sample.data.ram_total)}` : '—'}</b>{sample && <Meter value={sample.data.ram_used} max={sample.data.ram_total} />}</span>
    </div>}
    <div className="bar-end">
      <span className={`connection ${error ? 'offline' : ''}`}><i />{error ? 'Connection interrupted' : paired ? 'Connected' : 'Pairing required'}</span>
      {paired && <nav className="views" aria-label="View">
        <button aria-pressed={view === 'chat'} onClick={() => onView('chat')}>Chat</button>
        <button aria-pressed={view === 'tasks'} onClick={() => onView('tasks')}>Tasks</button>
      </nav>}
      {paired && <button onClick={onStats}>Stats</button>}
    </div>
  </header>;
}
