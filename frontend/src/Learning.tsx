import { useState } from 'react';
import { api } from './api';
import { usePoll } from './usePoll';

type Task = { id: string; state: string; sampled: boolean; goal?: string; repo?: string; decision?: string };
type Comparison = { available: boolean; reason?: string; shadow_state?: string; comparison?: { exact_match: boolean; ast_equivalent: boolean; structural_coverage: number | null; files: { matching: string[]; missing: string[]; extra: string[] }; edits: { matching: unknown[]; missing: unknown[]; extra: unknown[] } }; reviews?: { labels: string[]; notes: string }[] };
type Recipe = { id: string; state: string; recipe: { name: string; operations: unknown[]; parameters: string[]; evidence: string[] } };
const labels = ['unknown', 'completely_wrong', 'nearly_correct', 'right_idea_wrong_implementation', 'correct_with_extras', 'partial_solution', 'equivalent_solution'];
export function Learning() {
  const [tasks, setTasks] = useState<Task[]>([]), [selected, setSelected] = useState<Task | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null), [recipes, setRecipes] = useState<Recipe[]>([]);
  const [error, setError] = useState(''), [label, setLabel] = useState('unknown'), [notes, setNotes] = useState(''), [busy, setBusy] = useState(false);
  usePoll(async signal => { try { const rows = await api<Task[]>('/api/tasks', { signal }); if (!signal.aborted) setTasks(rows.reverse().slice(0, 100)); } catch (e) { if (!signal.aborted) setError((e as Error).message); } }, 5000, []);
  async function open(task: Task) {
    setSelected(task); setComparison(null); setRecipes([]); setNotes(''); setError('');
    try {
      if (task.state === 'completed') setComparison(await api<Comparison>(`/api/tasks/${task.id}/comparison`));
      if (task.repo) setRecipes(await api<Recipe[]>(`/api/recipes?repo=${encodeURIComponent(task.repo)}`));
    } catch (e) { setError((e as Error).message); }
  }
  async function post(path: string, body: unknown) {
    setBusy(true); setError('');
    try { await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); if (selected) await open(selected); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const c = comparison?.comparison;
  return <main className="work"><section className="card"><h2>Learning</h2><p>Shadow results remain sealed until the frontier finishes. Comparisons describe patch structure; semantic assessments come from review.</p>
    {tasks.length === 0 && <p>No observations yet. Enable experiments in a reviewed project profile and install the lifecycle hooks.</p>}
    {tasks.map(task => <p key={task.id}><button onClick={() => void open(task)} aria-pressed={selected?.id === task.id}>{task.goal || task.id.slice(0, 12)}</button> · {task.state} · {task.sampled ? 'shadow sampled' : 'observed'}</p>)}
  </section><section className="card"><h2>{selected?.goal || 'Select an observation'}</h2>{error && <p role="alert">{error}</p>}
    {selected && selected.state !== 'completed' && <p>Shadow content is sealed.</p>}
    {comparison && !comparison.available && <p>{comparison.reason}</p>}
    {c && <><p>Shadow: {comparison?.shadow_state}. Exact patch match: {String(c.exact_match)}. AST equivalence: {String(c.ast_equivalent)}.</p>
      <p>Matching edits: {c.edits.matching.length}; missing edits: {c.edits.missing.length}; extra edits: {c.edits.extra.length}.</p>
      <p>Structural overlap: {c.structural_coverage === null ? 'unavailable' : `${Math.round(c.structural_coverage * 100)}%`}. This percentage is not a correctness score.</p>
      <p>Missing files: {c.files.missing.join(', ') || 'none'}. Extra files: {c.files.extra.join(', ') || 'none'}.</p>
      {comparison?.reviews?.map((r, i) => <p key={i}>{r.labels.join(', ')}: {r.notes}</p>)}
      <label>Assessment <select value={label} onChange={e => setLabel(e.target.value)}>{labels.map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
      <label>Review evidence and notes <textarea value={notes} onChange={e => setNotes(e.target.value)} /></label>
      <button disabled={busy || notes.trim().length < 10} onClick={() => void post(`/api/tasks/${selected!.id}/review`, { labels: [label], notes })}>Record review</button>
    </>}
    {recipes.length > 0 && <><h3>Deterministic recipes</h3>{recipes.map(recipe => <article key={recipe.id}><h4>{recipe.recipe.name} · {recipe.state}</h4><p>Version {recipe.id}; {recipe.recipe.evidence.length} examples; parameters: {recipe.recipe.parameters.join(', ')}.</p><pre>{JSON.stringify(recipe.recipe.operations, null, 2)}</pre>
      <button disabled={busy || notes.trim().length < 10} onClick={() => void post('/api/recipes/review', { repo: selected?.repo, id: recipe.id, decision: recipe.state === 'approved' ? 'revoke' : 'approve', notes })}>{recipe.state === 'approved' ? 'Revoke' : 'Approve this version'}</button></article>)}<p>Recipe approval uses the review notes above and permits this exact version. Running a recipe still produces a private patch for review.</p></>}
  </section></main>;
}
