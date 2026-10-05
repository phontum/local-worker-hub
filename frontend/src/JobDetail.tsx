import { useEffect, useState } from 'react';
import { Job, Event, active, count, date, duration, seconds } from './api';
import { CheckOutput } from './CheckOutput';
import { ModelTrace } from './ModelTrace';
import { BoardView } from './BoardView';

type Tab = 'answer' | 'evidence' | 'trace' | 'activity' | 'board' | 'review';
const art = (id: string, name: string) => `/api/jobs/${id}/artifacts/${encodeURIComponent(name)}`;

function Progress({ job }: { job: Job }) {
  const p = job.progress;
  if (!p) return <p className="muted">Waiting for the worker…</p>;
  return <div className="progress"><strong>{p.phase}{p.active_check ? ` · ${p.active_check}` : ''}</strong>
    <span>{Math.round(p.elapsed_seconds)}s elapsed{p.eta_seconds ? ` · about ${Math.round(p.eta_seconds)}s left` : ''}
      {p.queue_position ? ` · queue position ${p.queue_position}${p.queue_wait_upper_bound_seconds != null ? ` (at most ~${Math.max(1, Math.round(p.queue_wait_upper_bound_seconds / 60))} min)` : ''}` : ''}</span></div>;
}

function AnswerTab({ job }: { job: Job }) {
  const r = job.result;
  const checks = r?.checks ?? [];
  return <>
    {active(job) && <Progress job={job} />}
    {r?.answer ? <div className="answer">{r.answer}</div> : r?.report ? <pre className="report">{r.report}</pre> : null}
    {r?.error && <p className="error">{r.error}</p>}
    {!active(job) && !r && <p className="muted">No result recorded.</p>}
    {!!r?.changed_files?.length && <p className="line"><b>Changed:</b> {r.changed_files.join(', ')}</p>}
    {!!checks.length && <p className="line"><b>Checks:</b> {checks.map(c => `${c.name} ${c.status ?? (c.exit_code === 0 ? 'passed' : 'failed')}`).join(' · ')}</p>}
    {r?.source_job_id && <p className="line">Analysis of an earlier check job; commands were not rerun.</p>}
  </>;
}

function EvidenceTab({ job, events }: { job: Job; events: Event[] }) {
  const r = job.result;
  const plans = events.filter(e => e.kind === 'web_research_plan');
  const empty = !r?.ask && !r?.answer_review && !r?.web_verification && !plans.length && !r?.checks?.length && !r?.attempts?.length && !r?.changed_files?.length;
  return <>
    {empty && <p className="muted">No separate evidence was recorded for this task.</p>}
    {r?.ask && <><h3>Web lookup</h3>
      <p>{r.ask.decision?.needs_web ? `Searched: ${(r.ask.queries ?? r.ask.decision.queries).map(q => `“${q}”`).join(', ')} via ${(r.ask.providers ?? []).filter(Boolean).join(', ') || 'no provider'}` : 'Answered without the web (no current facts needed).'}</p>
      {!!r.ask.failures?.length && <p className="muted">Fallbacks: {r.ask.failures.join('; ')}</p>}
      {!!r.ask.pages?.length && <><p>Pages opened</p><ul>{r.ask.pages.map(p => <li key={p.url}>{p.url} · {p.error ? `failed: ${p.error}` : p.kind === 'page' ? 'read live' : p.kind}</li>)}</ul></>}
      {!!r.ask.excerpts?.length && <><p>Excerpts given to the model (used ones in bold)</p><ol>{r.ask.excerpts.map(e => <li key={e.n} value={e.n}>{(r.ask!.used ?? []).includes(e.n) ? <b>{e.title}</b> : e.title} · {e.kind} · <a href={e.url}>{e.url}</a></li>)}</ol></>}
      <p><a href={art(job.id, 'ask.json')}>Full lookup record</a></p></>}
    {r?.answer_review && <><h3>Requirements review</h3><p>Initial answer: {r.answer_review.initial_status} · Review: {r.answer_review.status} · <a href={art(job.id, 'draft-report.txt')}>Initial answer</a>
      {r.answer_review.state === 'completed' && <> · <a href={art(job.id, 'answer-review.json')}>Full assessment</a></>}</p>
      <ul>{r.answer_review.requirements?.map((q, i) => <li key={i}><strong>{q.status}: {q.requirement}</strong><p>{q.evidence}</p>
        {!!q.evidence_refs?.length && <details><summary>Supporting quotes</summary>{q.evidence_refs.map((ref, n) => <div key={n}><strong>{ref.source}</strong><pre>{ref.quote}</pre></div>)}</details>}</li>)}</ul></>}
    {r?.web_verification && <><h3>Source verification</h3><p>{r.web_verification.verified_observations} supported origin observations
      {r.web_verification.verified_sources != null ? ` across ${r.web_verification.verified_sources} pages` : ''} · <a href={art(job.id, r.web_verification.artifact)}>Retrieval details and exact quotes</a></p>
      <ul>{r.web_verification.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></>}
    {!!plans.length && <><h3>Research plans</h3>{plans.map((e, i) => <details key={e.id}>
      <summary>{e.data.phase ? (String(e.data.phase).startsWith('answer-review') ? 'Reviewer plan' : 'Initial plan') : i === 0 ? 'Initial plan' : 'Reviewer plan'}{e.data.revision > 1 ? ` · revision ${e.data.revision}` : ''}: {String(e.data.objective ?? '')}</summary>
      <p>{e.data.needs_current_evidence ? 'Current evidence required' : 'Freshness chosen for this task'}</p>{e.data.revision_reason && <p>Changed: {String(e.data.revision_reason)}</p>}
      <ul>{(e.data.requirements ?? []).map((q: any) => <li key={q.id}><strong>{q.requirement}</strong><p>Prompt: <q>{q.task_quote}</q></p><p>Check: {q.acceptance}</p></li>)}</ul>
      <p>Strategy</p><ol>{(e.data.strategy ?? []).map((s: string, n: number) => <li key={n}>{s}</li>)}</ol>
      <p>Output: {String(e.data.result_format ?? '')}</p><p>Stop: {String(e.data.stop_when ?? '')}</p></details>)}
      {r?.research_plans?.map(name => <p key={name}><a href={art(job.id, name)}>{name === 'work.research-plan.json' ? 'Initial plan JSON' : 'Reviewer plan JSON'}</a></p>)}</>}
    {!!r?.checks?.length && <><h3>Check outcomes</h3>{r.checks.map((c, i) => <CheckOutput key={`${job.id}-${i}`} jobId={r.source_job_id ?? job.id} check={c} />)}</>}
    {!!r?.attempts?.length && <><h3>Local attempts</h3><ol className="timeline">{r.attempts.map(a => <li key={a.attempt}><b>Attempt {a.attempt + 1} · review {a.review_status}</b>
      {a.review_findings && <p>{a.review_findings}</p>}<p>{a.checks.map(c => `${c.name}: ${c.status}`).join(' · ')}</p></li>)}</ol></>}
    {!!r?.changed_files?.length && <p className="line"><a href={art(job.id, 'after-diff.txt')}>Download diff</a>{job.request.role === 'editor' && <> · <a href={art(job.id, 'scoped-diff.txt')}>Authorized-file diff</a></>}</p>}
  </>;
}

function ActivityTab({ events }: { events: Event[] }) {
  const rows = events.filter(e => e.kind !== 'trace').slice(-300);
  return <ol className="timeline">{rows.map(e => <li key={e.id}><span>{new Date(e.time * 1000).toLocaleTimeString('en-GB', { hour12: false })}</span><b>{e.kind.replaceAll('_', ' ')}</b>
    <p>{String(e.data.path ?? e.data.tool ?? e.data.query ?? e.data.url ?? e.data.message ?? e.data.role ?? e.data.phase ?? '').slice(0, 160)}</p></li>)}</ol>;
}

function ReviewTab({ job, events }: { job: Job; events: Event[] }) {
  const r = job.result, inference = events.filter(e => e.kind === 'inference').at(-1);
  const configs = events.filter(e => e.kind === 'effective-config');
  return <>
    <h3>Frontier review</h3>
    <p>{job.review ? `${job.review.decision}${job.review.task_outcome ? ` · task ${job.review.task_outcome}` : ''}${job.review.notes ? ` · ${job.review.notes}` : ''}` : 'Unreviewed. A worker completion is not final acceptance.'}</p>
    <h3>Run</h3>
    <p>Output tokens {count(r?.usage?.output)} · speed {inference?.data.tokens_per_second ? `${inference.data.tokens_per_second.toFixed(1)} t/s` : '—'} · duration {duration(seconds(job))}
      · context {inference ? `${count(inference.data.context_tokens)}/${count(inference.data.context_limit ?? 16384)}` : '—'}</p>
    {r?.metrics && <p className="muted">Queue {r.metrics.queue_seconds.toFixed(1)}s · checks {r.metrics.check_seconds.toFixed(1)}s · model {r.metrics.analysis_seconds.toFixed(1)}s · {r.metrics.recovery_count} recoveries</p>}
    <p className="muted">Preset {job.request.execution_preset ?? 'legacy'}{job.request.handoff_id ? ` · handoff ${job.request.handoff_id}` : ''}{job.progress?.model_budget_remaining != null ? ` · model budget left ${Math.round(job.progress.model_budget_remaining)}s` : ''}</p>
    {configs.map(e => <p key={e.id} className="muted">{e.data.phase} · {e.data.model ?? 'model'} · {count(e.data.context)} context · thinking {e.data.thinking ? 'on' : 'off'}</p>)}
  </>;
}

export function JobDetail({ job, events, busy, onCancel }: { job: Job | undefined; events: Event[]; busy: boolean; onCancel: (job: Job) => void }) {
  const [tab, setTab] = useState<Tab>('answer');
  useEffect(() => setTab('answer'), [job?.id]);
  if (!job) return <section className="pane empty"><h2>Select a task</h2><p className="muted">Its answer, evidence and trace appear here.</p></section>;
  const direct = job.request.role === 'validator' && (job.request.summary_mode === 'none' || job.result?.report_origin === 'harness');
  const tabs: Array<[Tab, string]> = [['answer', 'Answer'], ['evidence', 'Evidence'], ...(direct ? [] : [['trace', 'Trace'] as [Tab, string]]), ['activity', 'Activity'],
    ...(job.result?.board ? [['board', 'Board'] as [Tab, string]] : []), ['review', 'Review']];
  return <section className="pane">
    <div className="pane-head"><div className="titleline"><p className="task" title={job.request.task}>{job.request.task}</p>
      {active(job) && <button className="cancel" disabled={busy} onClick={() => onCancel(job)}>Cancel</button>}</div>
      <div className="meta"><span className="role">{job.request.role}</span><span className={`badge ${job.state}`}>{job.state}</span><span>{job.result?.worker_status ?? 'Awaiting report'}</span>
        <span>{duration(seconds(job))}</span><span>{date(job.created)}</span>{job.request.board && <span>board</span>}</div></div>
    <div className="tabs" role="tablist">{tabs.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
    <div className="tabbody">
      {tab === 'answer' && <AnswerTab job={job} />}
      {tab === 'evidence' && <EvidenceTab job={job} events={events} />}
      {tab === 'trace' && <ModelTrace key={job.id} jobId={job.id} running={active(job)} />}
      {tab === 'activity' && <ActivityTab events={events} />}
      {tab === 'board' && job.result?.board && <BoardView jobId={job.id} board={job.result.board} />}
      {tab === 'review' && <ReviewTab job={job} events={events} />}
    </div>
  </section>;
}
