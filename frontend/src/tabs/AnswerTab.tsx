import { Job, active, checkStatus } from '../api';

export function Progress({ job }: { job: Job }) {
  const p = job.progress;
  if (!p) return <p className="muted">Waiting for the worker…</p>;
  return <div className="progress"><strong>{p.phase}{p.active_check ? ` · ${p.active_check}` : ''}</strong>
    <span>{Math.round(p.elapsed_seconds)}s elapsed{p.eta_seconds ? ` · about ${Math.round(p.eta_seconds)}s left` : ''}
      {p.queue_position ? ` · queue position ${p.queue_position}${p.queue_wait_upper_bound_seconds != null ? ` (at most ~${Math.max(1, Math.round(p.queue_wait_upper_bound_seconds / 60))} min)` : ''}` : ''}</span></div>;
}

export function AnswerTab({ job }: { job: Job }) {
  const r = job.result;
  const checks = r?.checks ?? [];
  return <>
    {active(job) && <Progress job={job} />}
    {r?.answer ? <div className="answer">{r.answer}</div> : r?.report ? <pre className="report">{r.report}</pre> : null}
    {r?.error && <p className="error">{r.error}</p>}
    {!active(job) && !r && <p className="muted">No result recorded.</p>}
    {!!r?.changed_files?.length && <p className="line"><b>Changed:</b> {r.changed_files.join(', ')}</p>}
    {!!checks.length && <p className="line"><b>Checks:</b> {checks.map(c => `${c.name} ${checkStatus(c)}`).join(' · ')}</p>}
    {r?.source_job_id && <p className="line">Analysis of an earlier check job; commands were not rerun.</p>}
  </>;
}
