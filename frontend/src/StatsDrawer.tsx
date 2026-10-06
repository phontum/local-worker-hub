import { Sample, Summary, count, dollars, gb, date } from './api';

export function StatsDrawer({ summary, period, onPeriod, sample, history, onClose }: { summary: Summary | null; period: 'since_reset' | 'today' | 'all'; onPeriod: (period: 'since_reset' | 'today' | 'all') => void; sample?: Sample; history: Sample[]; onClose: () => void }) {
  const gpu = sample?.data.gpu;
  const points = history.map((s, i) => `${i * 300 / Math.max(1, history.length - 1)},${48 - Math.min(100, Math.max(0, s.data.gpu!.utilization_percent)) * 0.44}`).join(' ');
  return <aside className="drawer" aria-label="Statistics">
    <div className="drawer-head"><h2>Stats</h2><button onClick={onClose}>Close</button></div>
    <div className="drawer-body">
      <label>Statistics period <select aria-label="Statistics period" value={period} onChange={e => onPeriod(e.target.value as typeof period)}>
        <option value="since_reset">Since reset</option><option value="today">Today</option><option value="all">All history</option>
      </select></label>
      <p className="muted">Real jobs{summary?.selection.since != null ? ` since ${date(summary.selection.since)}` : ' across all history'}. Benchmarks and imported jobs are excluded.</p>
      <div className="kpis"><div><span>Frontier accepted</span><b>{summary?.accepted ?? '—'} / {summary?.reviewed ?? '—'} reviewed</b></div>
        <div><span>Local output tokens</span><b>{count(summary?.usage.output)}</b></div>
        <div><span>API-equivalent workload</span><b>{dollars(summary?.api_equivalent_usd)}</b></div>
        <div><span>Frontier tokens avoided</span><b>{count(summary?.estimated_frontier_tokens_avoided)}</b></div></div>
      <p className="muted">Review coverage: {summary?.reviewed ?? '—'} / {summary?.jobs ?? '—'} jobs. Recorded review effort: {summary?.measurement_coverage.review_effort_jobs ?? '—'} jobs.</p>
      <p className="muted">Today: {summary?.today.jobs ?? '—'} jobs · {count(summary?.today.usage.input)} input / {count(summary?.today.usage.output)} output tokens · API-equivalent increase {dollars(summary?.today.api_equivalent_usd)} ({summary?.today.timezone ?? '—'}).</p>
      <p className="muted">{summary?.estimated_frontier_tokens_avoided == null ? 'Not measured—no matched baselines.' : `Matched token baselines: ${summary.matched_baselines}.`} Measured cost avoided: {dollars(summary?.estimated_frontier_cost_avoided)}. Manual token estimates: {summary?.measurement_coverage.manual_token_baselines ?? '—'}.</p>
      <p className="muted">Comparison: {summary?.pricing.model ?? 'unset'} · rates dated {summary?.pricing.as_of ?? 'unset'}{summary?.pricing.source && <> · <a href={summary.pricing.source} target="_blank" rel="noreferrer">Rate source</a></>}. API equivalents are not subscription savings.{summary?.api_equivalent_usd == null && ' Configure dated comparison rates to calculate workload.'}</p>
      <h3>Hardware</h3>
      {history.length > 1 ? <svg role="img" aria-label="GPU utilization, last 60 samples" viewBox="0 0 300 52" width="100%" height="52"><polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="2" /></svg> : <p className="muted">GPU history needs two samples.</p>}
      <p className="muted">{gpu ? `${gpu.name} · ${gpu.temperature_c}°C · ${gpu.power_w.toFixed(0)} W` : 'GPU metrics unavailable'} · CPU {sample?.data.cpu_percent ?? '—'}% · swap {sample ? gb(sample.data.swap_used) : '—'}</p>
      <h3>Workflow results</h3>
      {Object.entries(summary?.role_stats ?? {}).map(([role, s]) => <p key={role}>{role}: {s.accepted}/{s.reviewed} reviewed accepted · {s.jobs} jobs · {s.completed} outcomes completed · {s.takeovers} takeovers · {s.repair_attempts} repairs. Checks: {Object.entries(s.checks).map(([status, n]) => `${n} ${status}`).join(', ') || 'none'}{role === 'validator' && ' · zero model tokens by default; accepted failure evidence can have failing checks.'}</p>)}
      {(summary?.handoff_stats ?? []).filter(h => h.jobs > 1).slice(0, 8).length > 0 && <><h3>Related handoffs</h3>
        {(summary?.handoff_stats ?? []).filter(h => h.jobs > 1).slice(0, 8).map(h => <p key={h.id}>{h.id}: {h.jobs} jobs · model {h.model_seconds.toFixed(0)}s · checks {h.check_seconds.toFixed(0)}s · {h.takeovers} takeovers</p>)}</>}
    </div>
  </aside>;
}
