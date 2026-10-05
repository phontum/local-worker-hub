import { useState } from 'react';
import { api, ArtifactPage, artifactUrl, Check, checkStatus } from './api';

export function CheckOutput({ jobId, check }: { jobId: string; check: Check }) {
  const [text, setText] = useState(''), [offset, setOffset] = useState(0), [more, setMore] = useState(false);
  const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    if (!check.artifact || busy) return;
    setBusy(true); setError('');
    try {
      const page = await api<ArtifactPage>(`/api/jobs/${jobId}/artifact-page/${encodeURIComponent(check.artifact)}?offset=${offset}`);
      setText(t => t + page.text); setOffset(page.next_offset); setMore(page.has_more); setLoaded(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const status = checkStatus(check);
  return <details onToggle={e => { if (e.currentTarget.open && !loaded) void load(); }}>
    <summary>{check.name} · {status === 'passed' ? 'Passed' : status === 'skipped' ? 'Skipped' : status === 'blocked' ? 'Blocked' : `Failed (${check.exit_code ?? status})`}</summary>
    {check.reason && <p>{check.reason}</p>}
    {check.counts && <p>{check.counts.passed}/{check.counts.total} passed · {check.counts.failed} failed · {check.counts.skipped} skipped</p>}
    {text && <pre>{text}</pre>}{error && <p className="error">{error}</p>}
    {check.artifact && <><button disabled={busy || (loaded && !more && !error)} onClick={() => void load()}>{busy ? 'Loading…' : error ? 'Retry output' : more ? 'Load more output' : loaded ? 'Output loaded' : 'Load output'}</button>{' '}
      <a href={artifactUrl(jobId, check.artifact)}>Open full log</a></>}
  </details>;
}
