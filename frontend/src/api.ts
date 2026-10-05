import type { Board } from './BoardView';

export type Check = { name: string; status?: string; exit_code: number | null; timed_out?: boolean; artifact?: string; reason?: string;
  counts?: { total: number; passed: number; failed: number; skipped: number } };
export type Attempt = { attempt: number; edit_status: string; review_status: string; review_findings: string;
  checks: Array<{ name: string; status: string; exit_code: number | null }> };
export type ProviderLog = { provider: string; args?: Record<string, unknown>; fallback_reason?: string;
  evidence?: { method: string; requested_url: string; observed_at: string; place?: string; rate_date?: string }; records?: Array<Record<string, unknown>> };
export type ProductRow = { name: string; brand?: string; variant?: string; price: string | null; price_high?: string | null; currency: string; availability: string;
  availability_raw?: string; condition?: string; seller?: string; source: string; url: string; link?: string };
export type Ask = {
  decision?: { needs_web: boolean; queries: string[]; reply_language: string; route?: string; provider?: string } | null;
  queries?: string[]; providers?: (string | null)[]; failures?: string[]; provider?: ProviderLog | null; products?: ProductRow[];
  pages?: Array<{ url: string; kind?: string | null; error?: string | null; observed_at?: string | null; rendered_because?: string; browser_note?: string }>;
  excerpts?: Array<{ n: number; url: string; title: string; kind: string; observed_at: string }>; used?: number[];
};
export type Job = { id: string; state: string; created: number; started: number | null; ended: number | null;
  progress?: { phase: string; queue_position?: number; active_check?: string; elapsed_seconds: number; heartbeat?: number; last_output_at?: number; deadline?: number;
    model_budget_remaining?: number; eta_seconds?: number | null; queue_wait_upper_bound_seconds?: number | null };
  request: { role: string; task: string; repo: string | null; caller: string; summary_mode?: string; workflow?: string; execution_preset?: string;
    handoff_id?: string; review_pass?: boolean | null; board?: boolean; board_mode?: string | null };
  review: { decision: string; notes: string; task_outcome?: string | null } | null;
  result: { report?: string; answer?: string; error?: string; worker_status?: string; report_valid?: boolean;
    usage?: Record<string, number>; changed_files?: string[]; truncated?: boolean; report_origin?: string; source_job_id?: string;
    metrics?: { queue_seconds: number; check_seconds: number; analysis_seconds: number; execution_seconds: number; recovery_count: number };
    ask?: Ask | null;
    response_bytes?: number; checks?: Check[]; attempts?: Attempt[]; research_plans?: string[]; board?: Board | null;
    web_verification?: { artifact: string; verified_observations: number; verified_sources?: number; issues: string[] };
    answer_review?: { state: string; initial_status: string; status: string;
      requirements?: Array<{ requirement: string; status: string; evidence: string; evidence_refs?: Array<{ source: string; quote: string }> }> } | null } | null };
export type ChatOptions = { models: Array<{ alias: string; name: string; installed: boolean | null }> };
export type ArtifactPage = { text: string; next_offset: number; has_more: boolean };
export type Event = { id: number; time: number; kind: string; data: Record<string, any> };
export type Sample = { time: number; data: { cpu_percent: number; ram_used: number; ram_total: number; swap_used: number;
  gpu: { name: string; memory_used_mb: number; memory_total_mb: number; utilization_percent: number; temperature_c: number; power_w: number } | null } };
export type Summary = { jobs: number; accepted: number; usage: Record<string, number>; api_equivalent_usd: number | null;
  estimated_frontier_tokens_avoided: number | null; estimated_frontier_cost_avoided: number | null; matched_baselines: number;
  pricing: { model?: string; as_of?: string | null };
  role_stats?: Record<string, { jobs: number; accepted: number; completed: number; takeovers: number; repair_attempts: number }>;
  handoff_stats?: Array<{ id: string; jobs: number; model_seconds: number; check_seconds: number; review_effort_seconds: number; takeovers: number; local_output_tokens: number }> };

export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...options, signal: options?.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(8000)]) : AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error(response.status === 401 ? 'pair' : `Request failed (${response.status})`);
  return response.json();
}

export const checkStatus = (c: Check) => c.status ?? (c.exit_code === 0 ? 'passed' : 'failed');
export const artifactUrl = (jobId: string, name: string) => `/api/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`;

// 24-hour clock everywhere.
const clock = { hour: '2-digit', minute: '2-digit', hour12: false } as const;
export const count = (n?: number | null) => n == null ? '—' : n.toLocaleString('en-GB', { maximumFractionDigits: 0 });
export const dollars = (n?: number | null) => n == null ? '—' : `$${n.toFixed(4)}`;
export const gb = (n: number) => `${(n / 1024 ** 3).toFixed(1)} GB`;
export const date = (n: number) => new Date(n * 1000).toLocaleString('en-GB', { ...clock, day: '2-digit', month: '2-digit', second: '2-digit' });
export const time = (n: number) => new Date(n * 1000).toLocaleTimeString('en-GB', clock);
export const active = (job: Job) => ['queued', 'running'].includes(job.state);
export const seconds = (job: Job) => job.started ? Math.round((job.ended ?? Date.now() / 1000) - job.started) : 0;
export const duration = (s: number) => s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
