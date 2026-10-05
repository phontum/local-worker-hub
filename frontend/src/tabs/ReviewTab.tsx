import { Job, Event, count, duration, seconds } from '../api';

export function ReviewTab({ job, events }: { job: Job; events: Event[] }) {
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
