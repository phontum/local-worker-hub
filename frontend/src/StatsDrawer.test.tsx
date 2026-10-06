import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { StatsDrawer } from './StatsDrawer';
import type { Summary } from './api';

const data: Summary = {
  jobs: 49, reviewed: 49, accepted: 44, usage: { input: 65284, output: 7972 }, api_equivalent_usd: .210288,
  estimated_frontier_tokens_avoided: null, estimated_frontier_cost_avoided: null, matched_baselines: 0,
  pricing: { model: 'comparison-model', as_of: '2026-10-02', source: 'https://example.com/pricing' },
  selection: { since: 1791237600, until: null, include_eval: false, include_history: false },
  today: { jobs: 49, usage: { input: 65284, output: 7972 }, api_equivalent_usd: .210288, since: 1791237600, timezone: 'Europe/Budapest' },
  measurement_coverage: { reviewed_jobs: 49, review_effort_jobs: 0, matched_token_baselines: 0, manual_token_baselines: 0 },
  role_stats: { validator: { jobs: 41, reviewed: 41, accepted: 41, completed: 26, takeovers: 0, repair_attempts: 0, checks: { passed: 66, failed: 15 } } },
};
const render = (summary: Summary | null) => renderToStaticMarkup(<StatsDrawer summary={summary} period="since_reset" onPeriod={() => {}} history={[]} onClose={() => {}} />);

describe('statistics meaning', () => {
  it('shows unmeasured savings, daily change and accepted validation failures', () => {
    const html = render(data);
    expect(html).toContain('Not measured—no matched baselines.');
    expect(html).toContain('$0.2103');
    expect(html).toContain('15 failed');
    expect(html).toContain('accepted failure evidence');
    expect(html).toContain('Europe/Budapest');
    expect(html).toContain('Statistics period');
    expect(html).toContain('All history');
  });
  it('preserves negative savings and explains missing rates', () => {
    const html = render({ ...data, estimated_frontier_tokens_avoided: -20, matched_baselines: 1, api_equivalent_usd: null, pricing: {} });
    expect(html).toContain('-20');
    expect(html).toContain('Matched token baselines: 1.');
    expect(html).toContain('Configure dated comparison rates');
  });
  it('renders loading without inventing a zero result', () => {
    expect(render(null)).toContain('—');
  });
});
