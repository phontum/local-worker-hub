import { Ask, ProductRow, artifactUrl } from '../api';

// How a page or excerpt was obtained decides how far it can be trusted for current facts.
const methods: Record<string, { label: string; note: string; trusted: boolean }> = {
  page: { label: 'origin read', note: 'read directly from the site just now', trusted: true },
  browser: { label: 'browser read', note: 'rendered with JavaScript from the site just now', trusted: true },
  provider: { label: 'structured data', note: 'exact values from a data service, read just now', trusted: true },
  hosted: { label: 'hosted copy', note: 'third-party copy, not verified as current', trusted: false },
  snippet: { label: 'search summary', note: 'may be outdated', trusted: false },
};

export function MethodBadge({ kind }: { kind?: string | null }) {
  const m = methods[kind ?? ''];
  return m ? <span className={`method ${m.trusted ? 'trusted' : 'unverified'}`} title={m.note}>{m.label}</span> : <span className="method unverified">{kind ?? 'unknown'}</span>;
}

/** One sentence: what the pipeline decided and why it may have changed course. */
export function routeSummary(ask: Ask): string {
  const route = ask.decision?.route ?? (ask.decision?.needs_web ? 'web' : 'direct');
  const failed = ask.provider?.fallback_reason;
  if (route.startsWith('provider:')) return `Structured provider “${route.slice(9)}”${ask.provider?.evidence?.place ? ` for ${ask.provider.evidence.place}` : ''}; no web search was needed.`;
  if (failed) return `Provider “${ask.provider?.provider}” could not answer (${failed}); fell back to web search.`;
  if (route === 'web') return `Searched the web: ${(ask.queries ?? ask.decision?.queries ?? []).map(q => `“${q}”`).join(', ') || 'no query recorded'}.`;
  return 'Answered without a lookup (no current facts needed).';
}

function Price({ p }: { p: ProductRow }) {
  return <b>{p.price ?? '—'}{p.price_high ? `–${p.price_high}` : ''} {p.currency || '(currency not stated)'}</b>;
}

export function ProductCards({ products }: { products: ProductRow[] }) {
  return <ul className="products">{products.map((p, i) => <li key={i} className="card product">
    <strong>{p.name || '(unnamed product)'}</strong>
    <span><Price p={p} /> · <span className={`stock ${p.availability}`}>{p.availability.replaceAll('_', ' ')}</span></span>
    <span className="muted">{[p.brand, p.variant, p.condition, p.seller && `sold by ${p.seller}`].filter(Boolean).join(' · ')}</span>
    <span className="muted">markup: {p.source}{p.availability_raw ? ` · availability “${p.availability_raw}”` : ''} · <a href={p.link || p.url}>{new URL(p.link || p.url).hostname}</a></span>
  </li>)}</ul>;
}

export function WebLookup({ jobId, ask }: { jobId: string; ask: Ask }) {
  const provider = ask.provider;
  return <>
    <h3>Lookup</h3>
    <p>{routeSummary(ask)}</p>
    {!!ask.failures?.length && <p className="muted">Fallbacks: {ask.failures.join('; ')}</p>}
    {provider?.evidence && <>
      <p>Provider data ({provider.evidence.method}) · <span className="muted">{new Date(provider.evidence.observed_at).toLocaleTimeString('en-GB', { hour12: false })}</span></p>
      <table className="kv"><tbody>
        {Object.entries(provider.args ?? {}).filter(([, v]) => v !== null && v !== '').map(([k, v]) => <tr key={k}><th>{k.replaceAll('_', ' ')}</th><td>{String(v)}</td></tr>)}
        <tr><th>request</th><td><code>{provider.evidence.requested_url}</code></td></tr>
      </tbody></table></>}
    {!!ask.products?.length && <><h3>Products found in page markup</h3><ProductCards products={ask.products} /></>}
    {!!ask.pages?.length && <><p>Pages opened</p><ul>{ask.pages.map(p => <li key={p.url}>{p.error ? <span className="method failed">failed</span> : <MethodBadge kind={p.kind} />} <a href={p.url}>{p.url}</a>{p.error ? ` · ${p.error}` : ''}{p.rendered_because ? ` · rendered because: ${p.rendered_because}` : ''}{p.browser_note ? ` · ${p.browser_note}` : ''}</li>)}</ul></>}
    {!!ask.excerpts?.length && <><p>Excerpts given to the model (used ones in bold)</p><ol>{ask.excerpts.map(e => <li key={e.n} value={e.n}>
      {(ask.used ?? []).includes(e.n) ? <b>{e.title}</b> : e.title} <MethodBadge kind={e.kind} /> <a href={e.url}>{e.url}</a></li>)}</ol></>}
    <p><a href={artifactUrl(jobId, 'ask.json')}>Full lookup record</a></p>
  </>;
}
