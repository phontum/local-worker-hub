import { Sample, Summary, count, dollars, gb } from './api';

export function StatsDrawer({ summary, sample, history, onClose }: { summary: Summary | null; sample?: Sample; history: Sample[]; onClose: () => void }) {
  const gpu = sample?.data.gpu;
  const points = history.map((s, i) => `${i * 300 / Math.max(1, history.length - 1)},${48 - Math.min(100, Math.max(0, s.data.gpu!.utilization_percent)) * 0.44}`).join(' ');
  return <aside className="drawer" aria-label="Statistics">
    <div className="drawer-head"><h2>Stats</h2><button onClick={onClose}>Close</button></div>
    <div className="drawer-body">
      <div className="kpis"><div><span>Frontier accepted</span><b>{summary?.accepted ?? 0} / {summary?.jobs ?? 0}</b></div>
        <div><span>Local output tokens</span><b>{count(summary?.usage.output)}</b></div>
        <div><span>API-equivalent workload</span><b>{dollars(summary?.api_equivalent_usd)}</b></div>
        <div><span>Frontier tokens avoided</span><b>{count(summary?.estimated_frontier_tokens_avoided)}</b></div></div>
      <p className="muted">Matched baselines: {summary?.matched_baselines ?? 0} · avoided {dollars(summary?.estimated_frontier_cost_avoided)}. API equivalents are not subscription savings.</p>
      <h3>Hardware</h3>
      {history.length > 1 ? <svg role="img" aria-label="GPU utilization, last 60 samples" viewBox="0 0 300 52" width="100%" height="52"><polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="2" /></svg> : <p className="muted">GPU history needs two samples.</p>}
      <p className="muted">{gpu ? `${gpu.name} · ${gpu.temperature_c}°C · ${gpu.power_w.toFixed(0)} W` : 'GPU metrics unavailable'} · CPU {sample?.data.cpu_percent ?? '—'}% · swap {sample ? gb(sample.data.swap_used) : '—'}</p>
      <h3>Workflow results</h3>
      {Object.entries(summary?.role_stats ?? {}).map(([role, s]) => <p key={role}>{role}: {s.accepted}/{s.jobs} accepted · {s.completed} complete · {s.takeovers} takeovers · {s.repair_attempts} repairs</p>)}
      {(summary?.handoff_stats ?? []).filter(h => h.jobs > 1).slice(0, 8).length > 0 && <><h3>Related handoffs</h3>
        {(summary?.handoff_stats ?? []).filter(h => h.jobs > 1).slice(0, 8).map(h => <p key={h.id}>{h.id}: {h.jobs} jobs · model {h.model_seconds.toFixed(0)}s · checks {h.check_seconds.toFixed(0)}s · {h.takeovers} takeovers</p>)}</>}
    </div>
  </aside>;
}
