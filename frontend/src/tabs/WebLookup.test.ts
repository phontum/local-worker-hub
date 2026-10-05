import { describe, expect, it } from 'vitest';
import { routeSummary } from './WebLookup';

const decision = (patch: object) => ({ needs_web: false, queries: [], reply_language: 'English', ...patch });

describe('routeSummary', () => {
  it('describes a provider route with the place it resolved', () => {
    const ask = { decision: decision({ route: 'provider:weather' }), provider: { provider: 'weather', evidence: { method: 'provider-api', requested_url: 'u', observed_at: 't', place: 'Oslo, Norway' } } };
    expect(routeSummary(ask)).toBe('Structured provider “weather” for Oslo, Norway; no web search was needed.');
  });
  it('explains a fallback from a provider to web search', () => {
    const ask = { decision: decision({ route: 'web', needs_web: true }), provider: { provider: 'fx', fallback_reason: 'HTTP 404' } };
    expect(routeSummary(ask)).toBe('Provider “fx” could not answer (HTTP 404); fell back to web search.');
  });
  it('lists the queries of a web search and understands older records without a route', () => {
    expect(routeSummary({ decision: decision({ route: 'web', queries: ['a'] }), queries: ['rtx 5070 cena'] })).toBe('Searched the web: “rtx 5070 cena”.');
    expect(routeSummary({ decision: decision({ needs_web: true, queries: ['x'] }) })).toBe('Searched the web: “x”.');
    expect(routeSummary({ decision: decision({}) })).toBe('Answered without a lookup (no current facts needed).');
  });
});
