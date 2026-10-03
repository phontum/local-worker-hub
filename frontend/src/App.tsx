import { useEffect, useState } from 'react';
import { ModelTrace } from './ModelTrace';

type Job = { id: string; state: string; created: number; started: number | null; ended: number | null;
  progress?: { phase: string; queue_position?: number; active_check?: string; elapsed_seconds: number; heartbeat?: number; last_output_at?: number; deadline?: number; model_budget_remaining?: number };
  request: { role: string; task: string; repo: string | null; caller: string; summary_mode?: string; workflow?: string; execution_preset?: string; handoff_id?: string; review_pass?: boolean | null };
  review: { decision: string; notes: string; task_outcome?: string | null } | null;
  result: { report?: string; error?: string; worker_status?: string; report_valid?: boolean;
    usage?: Record<string, number>; changed_files?: string[]; truncated?: boolean; report_origin?: string; source_job_id?: string;
    metrics?: { queue_seconds: number; check_seconds: number; analysis_seconds: number; execution_seconds: number; recovery_count: number };
    response_bytes?: number; checks?: Check[]; attempts?: Attempt[];
    research_plans?: string[];
    web_verification?: {artifact: string; verified_observations: number; verified_sources?: number; issues: string[]};
    answer_review?: { state: string; initial_status: string; status: string; requirements?: Array<{requirement: string; status: string; evidence: string; evidence_refs?: Array<{source: string; quote: string}>}> } | null } | null };
type Attempt = { attempt: number; edit_status: string; review_status: string; review_findings: string;
  checks: Array<{name: string; status: string; exit_code: number | null}> };
type Check = { name: string; status?: string; exit_code: number | null; timed_out?: boolean; artifact?: string; reason?: string;
  counts?: { total: number; passed: number; failed: number; skipped: number } };
type ArtifactPage = { text: string; next_offset: number; has_more: boolean };
type Event = { id: number; time: number; kind: string; data: Record<string, any> };
type Sample = { time: number; data: { cpu_percent: number; ram_used: number; ram_total: number; swap_used: number;
  gpu: { name: string; memory_used_mb: number; memory_total_mb: number; utilization_percent: number; temperature_c: number; power_w: number } | null } };
type Summary = { jobs: number; accepted: number; usage: Record<string, number>; api_equivalent_usd: number | null;
  estimated_frontier_tokens_avoided: number | null; estimated_frontier_cost_avoided: number | null; matched_baselines: number;
  pricing: { model?: string; as_of?: string | null };
  role_stats?: Record<string,{jobs:number;accepted:number;completed:number;takeovers:number;repair_attempts:number}>;
  context_stats?: Record<string,{requests:number;peak_tokens:number;seconds:number}>;
  handoff_stats?: Array<{id:string;jobs:number;model_seconds:number;check_seconds:number;review_effort_seconds:number;takeovers:number;local_output_tokens:number}> };

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, signal: options?.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(8000)]) : AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error(response.status === 401 ? 'pair' : `Request failed (${response.status})`);
  return response.json();
}
const count = (n?: number | null) => n == null ? 'Unavailable' : n.toLocaleString(undefined, { maximumFractionDigits: 0 });
const dollars = (n?: number | null) => n == null ? 'Unavailable' : `$${n.toFixed(4)}`;
const gb = (n: number) => `${(n / 1024 ** 3).toFixed(1)} GB`;
const date = (n: number) => new Date(n * 1000).toLocaleString();
const active = (job: Job) => ['queued', 'running'].includes(job.state);

function Meter({ value, max = 100 }: { value: number; max?: number }) {
  return <div className="meter"><span style={{ width: `${Math.min(100, Math.max(0, value / max * 100))}%` }} /></div>;
}

function CheckOutput({ jobId, check }: { jobId: string; check: Check }) {
  const [text,setText] = useState(''), [offset,setOffset] = useState(0), [more,setMore] = useState(false);
  const [loaded,setLoaded] = useState(false), [busy,setBusy] = useState(false), [error,setError] = useState('');
  async function load() {
    if (!check.artifact || busy) return;
    setBusy(true);setError('');
    try {
      const page=await api<ArtifactPage>(`/api/jobs/${jobId}/artifact-page/${encodeURIComponent(check.artifact)}?offset=${offset}`);
      setText(t=>t+page.text);setOffset(page.next_offset);setMore(page.has_more);setLoaded(true);
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  const status=check.status ?? (check.exit_code===0 ? 'passed' : 'failed');
  return <details onToggle={e=>{if(e.currentTarget.open && !loaded)void load();}}>
    <summary>{check.name} · {status==='passed' ? 'Passed' : status==='skipped' ? 'Skipped' : status==='blocked' ? 'Blocked' : `Failed (${check.exit_code ?? status})`}</summary>
    {check.reason && <p>{check.reason}</p>}
    {check.counts && <p>{check.counts.passed}/{check.counts.total} passed · {check.counts.failed} failed · {check.counts.skipped} skipped</p>}
    {text && <pre>{text}</pre>}{error && <p className="error">{error}</p>}
    {check.artifact && <><button disabled={busy || (loaded && !more && !error)} onClick={()=>void load()}>{busy ? 'Loading…' : error ? 'Retry output' : more ? 'Load more output' : loaded ? 'Output loaded' : 'Load output'}</button> <a href={`/api/jobs/${jobId}/artifacts/${check.artifact}`}>Download output</a></>}
  </details>;
}

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]), [summary, setSummary] = useState<Summary | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<Event[]>([]), [paired, setPaired] = useState(true);
  const [detail,setDetail] = useState<Job | null>(null);
  const [code, setCode] = useState(''), [error, setError] = useState(''), [filter, setFilter] = useState('all');
  const [updated, setUpdated] = useState<number | null>(null), [busy, setBusy] = useState(false);
  useEffect(() => {
    let stopped = false;let timer: ReturnType<typeof setTimeout>;const controller=new AbortController();
    const load = async () => {
      try {
        const options={signal:controller.signal};
        const [j, s, h] = await Promise.all([api<Job[]>('/api/jobs?view=compact',options), api<Summary>('/api/summary',options), api<Sample[]>('/api/hardware',options)]);
        if (stopped) return;
        setJobs(j); setSummary(s); setSamples(h); setPaired(true); setError(''); setUpdated(Date.now());
      } catch (e) { if (!stopped) { const m = (e as Error).message; if (m === 'pair') setPaired(false); else setError(m); } }
      finally {if(!stopped)timer=setTimeout(load,3000);}
    };
    void load();
    return () => { stopped = true; clearTimeout(timer);controller.abort(); };
  }, []);
  useEffect(() => {
    let stopped = false;let timer: ReturnType<typeof setTimeout>;let cursor=0;const controller=new AbortController();setEvents([]);setDetail(null);
    if (!selected) return;
    const load = async () => {
      try {
        const options={signal:controller.signal};
        const [j,page]=await Promise.all([api<Job>(`/api/jobs/${selected}?view=compact`,options),api<{events:Event[];next_cursor:number;has_more:boolean}>(`/api/jobs/${selected}/events?paged=true&after=${cursor}&limit=300`,options)]);
        if(!stopped){setDetail(j);setEvents(e=>[...e,...page.events].slice(-2000));cursor=page.next_cursor;}
      } catch (e) { if (!stopped) setError((e as Error).message); }
      finally {if(!stopped)timer=setTimeout(load,2000);}
    };
    void load();
    return () => { stopped = true; clearTimeout(timer);controller.abort(); };
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
  const hardware = samples.at(-1), gpu = hardware?.data.gpu;
  const gpuHistory = samples.slice(-60).filter(s => s.data.gpu && Number.isFinite(s.data.gpu.utilization_percent));
  const gpuPoints = gpuHistory.map((s, i) => `${i * 300 / Math.max(1, gpuHistory.length - 1)},${48 - Math.min(100, Math.max(0, s.data.gpu!.utilization_percent)) * 0.44}`).join(' ');
  const job = detail?.id===selected ? detail : jobs.find(j => j.id === selected);
  const directValidation=job?.request.role==='validator' && (job.request.summary_mode==='none' || job.result?.report_origin==='harness');
  const inference = events.filter(e => e.kind === 'inference').at(-1);
  const visible = jobs.filter(j => filter === 'all' || (filter === 'active' ? active(j) : j.request.role === filter));
  return <div className="app">
    <header><div className="brand"><span className="logo">lw</span><div><h1>Local Worker</h1><p>Your local engineering companion</p></div></div>
      <span className={`connection ${error ? 'offline' : ''}`}><i />{error ? 'Connection interrupted' : paired ? 'Connected locally' : 'Browser pairing required'}</span></header>
    {error && <div className="error" role="alert">{error}</div>}
    {!paired ? <section className="pair card"><h2>Connect this browser</h2><p>Run <code>local-worker dashboard</code> in your terminal, then enter the one-time code.</p><form onSubmit={pair}><label htmlFor="pair-code">Pairing code</label><input id="pair-code" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" required /><button disabled={busy}>Connect</button></form></section> : <>
      <section className="stats" aria-label="Worker summary">
        <div className="card stat"><span>Active / queued</span><strong>{jobs.filter(j => j.state === 'running').length} <small>/ {jobs.filter(j => j.state === 'queued').length}</small></strong><p>One inference slot</p></div>
        <div className="card stat"><span>Frontier accepted</span><strong>{summary?.accepted ?? 0} <small>/ {summary?.jobs ?? 0}</small></strong><p>{Object.values(summary?.role_stats ?? {}).reduce((n,r)=>n+r.completed,0)} tasks recorded complete</p></div>
        <div className="card stat"><span>Local output tokens</span><strong>{count(summary?.usage.output)}</strong><p>Completed responses, including failures and recovery</p></div>
        <div className="card stat"><span>API-equivalent workload</span><strong className="money">{dollars(summary?.api_equivalent_usd)}</strong><p>{summary?.pricing.as_of ? `${summary.pricing.model} · rates ${summary.pricing.as_of}` : 'Configure dated comparison rates'}</p></div>
      </section>
      <section className="hardware card"><div className="section-title"><h2>Hardware</h2><span>{gpu?.name ?? 'GPU readings unavailable'} · device / WSL</span></div>
        <div className="hardware-grid"><div><span>GPU utilization</span><strong>{gpu ? `${gpu.utilization_percent}%` : '—'}</strong><Meter value={gpu?.utilization_percent ?? 0} /></div>
          <div><span>VRAM</span><strong>{gpu ? `${(gpu.memory_used_mb / 1024).toFixed(1)} / ${(gpu.memory_total_mb / 1024).toFixed(1)} GB` : '—'}</strong><Meter value={gpu?.memory_used_mb ?? 0} max={gpu?.memory_total_mb || 1} /></div>
          <div><span>CPU</span><strong>{hardware ? `${hardware.data.cpu_percent}%` : '—'}</strong><Meter value={hardware?.data.cpu_percent ?? 0} /></div>
          <div><span>WSL memory</span><strong>{hardware ? `${gb(hardware.data.ram_used)} / ${gb(hardware.data.ram_total)}` : '—'}</strong><Meter value={hardware?.data.ram_used ?? 0} max={hardware?.data.ram_total || 1} /></div></div>
        {gpuHistory.length > 1 ? <svg role="img" aria-labelledby="gpu-history-title" viewBox="0 0 300 52" width="100%" height="52"><title id="gpu-history-title">GPU utilization history, last 60 hardware samples</title><polyline points={gpuPoints} fill="none" stroke="#247456" strokeWidth="2" /></svg> : <p className="muted">GPU history needs two valid samples.</p>}
        <p className="hardware-foot">{gpu ? `${gpu.temperature_c}°C · ${gpu.power_w.toFixed(1)} W` : 'GPU metrics unavailable'} · Swap {hardware ? gb(hardware.data.swap_used) : '—'} · sampled every 5s · {hardware ? `Last sample ${date(hardware.time)}` : 'Awaiting hardware sample'}</p></section>
      <main><section className="jobs card"><div className="section-title"><h2>Task history</h2><select aria-label="Filter tasks" value={filter} onChange={e => setFilter(e.target.value)}>{['all', 'active', 'investigator', 'editor', 'validator', 'researcher', 'personal'].map(v => <option key={v} value={v}>{v === 'all' ? 'All roles' : v}</option>)}</select></div>
        {visible.length === 0 ? <div className="empty"><h3>No tasks here yet</h3><p>Delegate a bounded task from Codex, Claude, or <code>local-worker</code>.</p></div> : <div className="job-list">{visible.map(j => <button data-job-id={j.id} className={`job ${j.id === selected ? 'selected' : ''}`} key={j.id} onClick={() => setSelected(j.id)}>
          <div><span className="role">{j.request.role}</span><span className={`badge ${j.state}`}>{j.state}</span></div><h3>{j.request.task.slice(0, 130)}</h3><p>{j.request.repo?.split('/').at(-1) ?? 'Public web research'} · {j.request.caller}</p><div className="job-foot"><time>{date(j.created)}</time><span>{j.review?.decision ?? 'Unreviewed'}</span></div></button>)}</div>}</section>
        <section className="detail card">{!job ? <div className="empty"><h3>Select a task</h3><p>Inspect its evidence, checks, context, and frontier review.</p></div> : <>
          <div className="section-title"><h2>Task details</h2>{active(job) && <button className="cancel" disabled={busy} onClick={() => cancel(job)}>Cancel task</button>}</div>
          <div className="detail-meta"><span className="role">{job.request.role}</span><span className={`badge ${job.state}`}>{job.state}</span><span>{job.result?.worker_status ?? 'Awaiting report'}</span><span>{job.request.workflow === 'implement' ? 'Investigate · Edit · Validate · Review' : (job.request.review_pass ?? job.request.execution_preset === 'extended') && !directValidation ? 'Initial answer · Requirements review' : ''}</span></div><p className="task-brief">{job.request.task}</p><p className="muted">{job.request.repo ?? 'Public research · no repository access'}</p>
          {job.progress && <div className="context"><strong>{job.progress.phase}{job.progress.active_check ? ` · ${job.progress.active_check}` : ''}</strong><p>{job.progress.queue_position ? `Queue position ${job.progress.queue_position} · ` : ''}{Math.round(job.progress.elapsed_seconds)}s elapsed{job.progress.deadline ? ` · deadline ${date(job.progress.deadline)}` : ''}</p><p>{job.progress.heartbeat ? `Heartbeat ${date(job.progress.heartbeat)}` : 'No heartbeat recorded'}{job.progress.last_output_at ? ` · Last output ${date(job.progress.last_output_at)}` : ' · No new output recorded'}</p></div>}
          <div className="context"><div><span>Latest prompt context</span><strong>{directValidation ? 'No model inference' : inference ? `${count(inference.data.context_tokens)} / ${count(inference.data.context_limit ?? 16384)} tokens` : 'Awaiting measurement'}</strong></div><Meter value={inference?.data.context_tokens ?? 0} max={inference?.data.context_limit ?? 16384} /><p>{directValidation ? 'Recorded command results · zero local model tokens' : `Measured ${inference ? date(inference.time) : 'after a provider response'}. Each phase starts with fresh context.`}</p></div>
          <div className="detail-numbers"><span>Output <b>{count(job.result?.usage?.output)}</b></span><span>Speed <b>{inference?.data.tokens_per_second ? `${inference.data.tokens_per_second.toFixed(1)} t/s` : '—'}</b></span><span>Duration <b>{job.started ? `${Math.round((job.ended ?? Date.now() / 1000) - job.started)}s` : 'Queued'}</b></span></div>
          {job.result?.report && <><h3>Report</h3><pre className="report">{job.result.report}</pre></>}{job.result?.error && <p className="error">{job.result.error}</p>}
          {job.result?.truncated && <p>Summary shortened. <a href={`/api/jobs/${job.id}/artifacts/report.txt`}>Read the saved report</a> and check logs for complete evidence.</p>}
          {job.result?.answer_review && <><h3>Requirements review</h3><p>Initial answer: {job.result.answer_review.initial_status} · Review: {job.result.answer_review.status}</p><p><a href={`/api/jobs/${job.id}/artifacts/draft-report.txt`}>Initial answer</a> {job.result.answer_review.state === 'completed' && <> · <a href={`/api/jobs/${job.id}/artifacts/answer-review.json`}>Full requirements assessment</a></>}</p><ul>{job.result.answer_review.requirements?.map((r,i)=><li key={i}><strong>{r.status}: {r.requirement}</strong><p>{r.evidence}</p>{!!r.evidence_refs?.length && <details><summary>Supporting quotes</summary>{r.evidence_refs.map((ref,n)=><div key={n}><strong>{ref.source}</strong><pre>{ref.quote}</pre></div>)}</details>}</li>)}</ul></>}
          {job.result?.source_job_id && <p>Analysis of <button onClick={()=>setSelected(job.result!.source_job_id!)}>original check job</button>; commands were not rerun.</p>}
          {job.result?.metrics && <p className="muted">Queue {job.result.metrics.queue_seconds.toFixed(1)}s · Checks {job.result.metrics.check_seconds.toFixed(1)}s · Model {job.result.metrics.analysis_seconds.toFixed(1)}s · Execution {job.result.metrics.execution_seconds.toFixed(1)}s · {job.result.metrics.recovery_count} recoveries · Summary {job.result.response_bytes ?? '—'} bytes</p>}
          {!!job.result?.changed_files?.length && <><h3>Changed files</h3><ul>{job.result.changed_files.map(p => <li key={p}>{p}</li>)}</ul><a href={`/api/jobs/${job.id}/artifacts/after-diff.txt`}>Download diff</a></>}
          {events.some(e=>e.kind==='web_research_plan') && <><h3>Research plans</h3><p>The model chooses the requirements, evidence checks and research strategy for this request.</p>{events.filter(e=>e.kind==='web_research_plan').map((e,i)=><details key={e.id}><summary>{e.data.phase ? (String(e.data.phase).startsWith('answer-review') ? 'Reviewer plan' : 'Initial plan') : i===0 ? 'Initial plan' : 'Reviewer plan'}{e.data.revision>1 ? ` · revision ${e.data.revision}` : ''}: {String(e.data.objective ?? '')}</summary><p>{e.data.needs_current_evidence ? 'Current evidence required' : 'Freshness chosen for this task'}</p>{e.data.revision_reason && <p>Changed: {String(e.data.revision_reason)}</p>}<ul>{(e.data.requirements ?? []).map((r:any)=><li key={r.id}><strong>{r.requirement}</strong><p>Prompt: <q>{r.task_quote}</q></p><p>Check: {r.acceptance}</p></li>)}</ul><p>Strategy</p><ol>{(e.data.strategy ?? []).map((s:string,n:number)=><li key={n}>{s}</li>)}</ol><p>Output: {String(e.data.result_format ?? '')}</p><p>Stop: {String(e.data.stop_when ?? '')}</p></details>)}{job.result?.research_plans?.map(name=><p key={name}><a href={`/api/jobs/${job.id}/artifacts/${encodeURIComponent(name)}`}>{name==='work.research-plan.json' ? 'Initial plan JSON' : 'Reviewer plan JSON'}</a></p>)}</>}
          {job.result?.web_verification && <><h3>Source verification</h3><p>{job.result.web_verification.verified_observations} supported origin observations{job.result.web_verification.verified_sources!=null ? ` across ${job.result.web_verification.verified_sources} pages` : ''} · <a href={`/api/jobs/${job.id}/artifacts/${encodeURIComponent(job.result.web_verification.artifact)}`}>Retrieval details and exact quotes</a></p><ul>{job.result.web_verification.issues.map((issue,i)=><li key={i}>{issue}</li>)}</ul></>}
          {job.request.role==='editor' && <p><a href={`/api/jobs/${job.id}/artifacts/scoped-diff.txt`}>Authorized-file diff</a></p>}
          {job.progress?.model_budget_remaining != null && <p>Model budget remaining: {Math.round(job.progress.model_budget_remaining)}s · checks use their own approved limits</p>}
          <p className="muted">Preset: {job.request.execution_preset ?? 'Legacy timing'}{job.request.handoff_id ? ` · Handoff ${job.request.handoff_id}` : ''}</p>
          {events.filter(e=>e.kind==='effective-config').map(e=><p key={e.id} className="muted">{e.data.phase} · {count(e.data.context)} context · thinking {e.data.thinking ? 'on' : 'off'}</p>)}
          {!directValidation && <ModelTrace key={job.id} jobId={job.id} running={active(job)} />}
          {!!job.result?.checks?.length && <><h3>Check outcomes</h3>{job.result.checks.map((c,i) => <CheckOutput key={`${job.id}-${i}`} jobId={job.result!.source_job_id ?? job.id} check={c} />)}</>}
          {!!job.result?.attempts?.length && <><h3>Local attempts</h3><ol className="timeline">{job.result.attempts.map(a=><li key={a.attempt}><b>Attempt {a.attempt+1} · Review {a.review_status}</b><p>{a.review_findings}</p><p>{a.checks.map(c=>`${c.name}: ${c.status}`).join(' · ')}</p></li>)}</ol></>}
          <h3>Frontier review</h3><p>{job.review ? `${job.review.decision}${job.review.task_outcome ? ` · Task ${job.review.task_outcome}` : ''}${job.review.notes ? ` · ${job.review.notes}` : ''}` : 'Unreviewed. A worker completion is not final acceptance.'}</p>
          <h3>Activity</h3><ol className="timeline">{events.filter(e=>e.kind!=='trace').slice(-300).map(e => <li key={e.id}><span>{new Date(e.time * 1000).toLocaleTimeString()}</span><b>{e.kind.replaceAll('_', ' ')}</b><p>{String(e.data.path ?? e.data.tool ?? e.data.query ?? e.data.url ?? e.data.message ?? e.data.role ?? e.data.state ?? '')}</p>{e.kind !== 'trace' && <details><summary>Recorded evidence</summary><pre>{JSON.stringify(e.data,null,2).slice(0,12000)}</pre></details>}</li>)}</ol>
        </>}</section></main>
      <section className="savings card"><div><h2>Estimated frontier usage avoided</h2><p>Matched baselines include orchestration, review, retries, and takeover. API equivalents do not imply subscription billing savings.</p></div><div><strong>{count(summary?.estimated_frontier_tokens_avoided)} <small>tokens</small></strong><span>{summary?.matched_baselines ?? 0} matched baselines · {dollars(summary?.estimated_frontier_cost_avoided)}</span></div></section>
      <section className="savings card"><div><h2>Workflow results</h2><p>Frontier decisions and task outcomes are recorded separately.</p></div><div>{Object.entries(summary?.role_stats ?? {}).map(([role,stats])=><p key={role}>{role}: {stats.accepted}/{stats.jobs} reports accepted · {stats.completed} tasks complete · {stats.takeovers} takeovers · {stats.repair_attempts} repairs</p>)}</div></section>
      <section className="savings card"><div><h2>Related handoffs</h2><p>Group follow-ups with handoff_id. Record a matched baseline once, including all frontier review, retries and takeover.</p></div><div>{(summary?.handoff_stats ?? []).filter(h=>h.jobs>1).slice(0,10).map(h=><p key={h.id}>{h.id}: {h.jobs} jobs · model {h.model_seconds.toFixed(1)}s · checks {h.check_seconds.toFixed(1)}s · recorded review {h.review_effort_seconds}s · {h.takeovers} takeovers</p>)}</div></section>
      <footer>Qwen3.5 9B · local inference · {updated ? `Updated ${new Date(updated).toLocaleTimeString()}` : 'Waiting for service'}</footer>
    </>}
  </div>;
}
