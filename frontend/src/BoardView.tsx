import { useEffect, useState } from 'react';
import { api, artifactUrl } from './api';

type Phase = { phase: string; model?: string | null; thinking?: boolean | null; ok: boolean; error?: string | null; seconds: number; output_tokens?: number | null };
export type Board = { state: string; mode: string; degraded?: string | null; swaps?: number; drift_flags?: number; needs_web?: boolean | null;
  needs_current_evidence?: boolean | null; phases: Phase[]; scout?: { query: string; ok: boolean; error?: string };
};
type Requirement = { id: string; kind: string; task_quote: string; requirement: string; acceptance: string; supported_by?: string[] };

const artifact = (jobId: string, name: string) => api<any>(artifactUrl(jobId, name));

const modelLabel = (name?: string | null) => name ? name.split(':')[0] : '—';

export function BoardView({ jobId, board }: { jobId: string; board: Board }) {
  const [data, setData] = useState<Record<string, any>>({});
  const [reveal, setReveal] = useState(false);
  const proposalPhases = board.phases.filter(p => p.ok && p.phase.startsWith('proposal-')).map(p => p.phase);
  const arbiterOk = board.phases.some(p => p.phase === 'arbiter' && p.ok);
  useEffect(() => {
    let stopped = false;
    const names = [...(arbiterOk ? ['board-identities.json', 'board-synthesis.json', 'board-drift.json'] : []), ...proposalPhases.map(p => `board-${p}.json`)];
    Promise.all(names.map(async n => [n, await artifact(jobId, n).catch(() => null)] as const)).then(rows => { if (!stopped) setData(Object.fromEntries(rows)); });
    return () => { stopped = true; };
  }, [jobId, board.state, board.phases.length]);

  const identities: Record<string, { role: string; model?: string }> = data['board-identities.json'] ?? {};
  const synthesis = data['board-synthesis.json'];
  const drift = data['board-drift.json'];
  const labels = Object.keys(identities).sort();
  return <>
    <h3>Board deliberation</h3>
    <p>{board.mode} board · {board.state}{board.degraded ? ` · ${board.degraded}` : ''} · {board.swaps ?? 0} model swaps · {board.drift_flags ?? 0} drift flags
      {board.needs_web != null ? ` · web ${board.needs_web ? 'needed' : 'not needed'}${board.needs_current_evidence ? ' · current evidence required' : ''}` : ''}</p>
    {board.scout && <p>Discovery search: <q>{board.scout.query}</q> · {board.scout.ok ? 'ran (one search, no page fetches)' : `failed: ${board.scout.error}`} · <span className="muted">snippets are unverified and only the skeptic proposer saw them</span></p>}
    <ol className="timeline">{board.phases.map((p, i) => <li key={i}><b>{p.phase}</b> · {modelLabel(p.model)} · thinking {p.thinking ? 'on' : 'off'} · {p.seconds}s
      {p.ok ? '' : <span className="error"> · failed: {p.error}</span>}</li>)}</ol>
    {!!labels.length && <>
      <h3>Anonymous candidates</h3>
      <p className="muted">The arbiter saw only these labels, never the source model or role. <button onClick={() => setReveal(!reveal)}>{reveal ? 'Hide sources' : 'Reveal sources'}</button></p>
      {labels.map(label => {
        const role = identities[label].role;
        const proposal = data[`board-proposal-${role}.json`];
        return <details key={label}><summary>Candidate {label}{reveal ? ` · ${role} · ${modelLabel(identities[label].model)}` : ''}</summary>
          {proposal ? <>
            <ul>{(proposal.requirements as Requirement[]).map(r => <li key={r.id}><b>{r.kind}</b>: {r.requirement}<p>Task quote: <q>{r.task_quote}</q></p></li>)}</ul>
            {!!proposal.assumptions?.length && <><p>Assumptions to check</p><ul>{proposal.assumptions.map((a: any, i: number) => <li key={i}>{a.text}<p>If wrong: {a.risk_if_wrong}</p></li>)}</ul></>}
            {!!proposal.hypotheses_to_verify?.length && <><p>Hypotheses to verify</p><ul>{proposal.hypotheses_to_verify.map((h: string, i: number) => <li key={i}>{h}</li>)}</ul></>}
            <p className="muted">{proposal.rationale}</p></> : <p>Proposal unavailable.</p>}
        </details>;
      })}
    </>}
    {synthesis && <>
      <h3>Arbiter synthesis</h3>
      <p>{synthesis.objective}</p>
      <ul>{(synthesis.requirements as Requirement[]).map(r => <li key={r.id}><b>{r.id} · {r.kind}</b>: {r.requirement}
        <p>Task quote: <q>{r.task_quote}</q> · supported by {(r.supported_by ?? []).join(', ') || 'none'}</p><p>Accept when: {r.acceptance}</p></li>)}</ul>
      {!!synthesis.decisions?.length && <><p>Decisions</p><ul>{synthesis.decisions.map((d: any, i: number) => <li key={i}>{d.topic}: {d.chosen} <span className="muted">({d.basis})</span></li>)}</ul></>}
      {!!synthesis.dissent?.length && <><p>Minority views kept for the critic</p><ul>{synthesis.dissent.map((d: any, i: number) => <li key={i}>Candidate {d.candidate}: {d.point}<p>Not adopted: {d.why_not_adopted}</p></li>)}</ul></>}
    </>}
    {drift && <>
      <h3>Drift against the original task</h3>
      <p className="muted">Computed by the host from literal quotes; no model decides this.</p>
      {drift.flags?.length ? <ul>{drift.flags.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul> : <p>No dropped, unanchored or uncovered requirements.</p>}
    </>}
  </>;
}
