import { Job, Event, artifactUrl } from '../api';
import { CheckOutput } from '../CheckOutput';
import { WebLookup } from './WebLookup';

export function EvidenceTab({ job, events }: { job: Job; events: Event[] }) {
  const r = job.result;
  const plans = events.filter(e => e.kind === 'web_research_plan');
  const empty = !r?.ask && !r?.answer_review && !r?.web_verification && !plans.length && !r?.checks?.length && !r?.attempts?.length && !r?.changed_files?.length;
  return <>
    {empty && <p className="muted">No separate evidence was recorded for this task.</p>}
    {r?.ask && <WebLookup jobId={job.id} ask={r.ask} />}
    {r?.answer_review && <><h3>Requirements review</h3><p>Initial answer: {r.answer_review.initial_status} · Review: {r.answer_review.status} · <a href={artifactUrl(job.id, 'draft-report.txt')}>Initial answer</a>
      {r.answer_review.state === 'completed' && <> · <a href={artifactUrl(job.id, 'answer-review.json')}>Full assessment</a></>}</p>
      <ul>{r.answer_review.requirements?.map((q, i) => <li key={i}><strong>{q.status}: {q.requirement}</strong><p>{q.evidence}</p>
        {!!q.evidence_refs?.length && <details><summary>Supporting quotes</summary>{q.evidence_refs.map((ref, n) => <div key={n}><strong>{ref.source}</strong><pre>{ref.quote}</pre></div>)}</details>}</li>)}</ul></>}
    {r?.web_verification && <><h3>Source verification</h3><p>{r.web_verification.verified_observations} supported origin observations
      {r.web_verification.verified_sources != null ? ` across ${r.web_verification.verified_sources} pages` : ''} · <a href={artifactUrl(job.id, r.web_verification.artifact)}>Retrieval details and exact quotes</a></p>
      <ul>{r.web_verification.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></>}
    {!!plans.length && <><h3>Research plans</h3>{plans.map((e, i) => <details key={e.id}>
      <summary>{e.data.phase ? (String(e.data.phase).startsWith('answer-review') ? 'Reviewer plan' : 'Initial plan') : i === 0 ? 'Initial plan' : 'Reviewer plan'}{e.data.revision > 1 ? ` · revision ${e.data.revision}` : ''}: {String(e.data.objective ?? '')}</summary>
      <p>{e.data.needs_current_evidence ? 'Current evidence required' : 'Freshness chosen for this task'}</p>{e.data.revision_reason && <p>Changed: {String(e.data.revision_reason)}</p>}
      <ul>{(e.data.requirements ?? []).map((q: any) => <li key={q.id}><strong>{q.requirement}</strong><p>Prompt: <q>{q.task_quote}</q></p><p>Check: {q.acceptance}</p></li>)}</ul>
      <p>Strategy</p><ol>{(e.data.strategy ?? []).map((s: string, n: number) => <li key={n}>{s}</li>)}</ol>
      <p>Output: {String(e.data.result_format ?? '')}</p><p>Stop: {String(e.data.stop_when ?? '')}</p></details>)}
      {r?.research_plans?.map(name => <p key={name}><a href={artifactUrl(job.id, name)}>{name === 'work.research-plan.json' ? 'Initial plan JSON' : 'Reviewer plan JSON'}</a></p>)}</>}
    {!!r?.checks?.length && <><h3>Check outcomes</h3>{r.checks.map((c, i) => <CheckOutput key={`${job.id}-${i}`} jobId={r.source_job_id ?? job.id} check={c} />)}</>}
    {!!r?.attempts?.length && <><h3>Local attempts</h3><ol className="timeline">{r.attempts.map(a => <li key={a.attempt}><b>Attempt {a.attempt + 1} · review {a.review_status}</b>
      {a.review_findings && <p>{a.review_findings}</p>}<p>{a.checks.map(c => `${c.name}: ${c.status}`).join(' · ')}</p></li>)}</ol></>}
    {!!r?.changed_files?.length && <p className="line"><a href={artifactUrl(job.id, 'after-diff.txt')}>Download diff</a>{job.request.role === 'editor' && <> · <a href={artifactUrl(job.id, 'scoped-diff.txt')}>Authorized-file diff</a></>}</p>}
  </>;
}
