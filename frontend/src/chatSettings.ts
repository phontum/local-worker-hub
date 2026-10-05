// What the chat composer can change, how it becomes a Personal job request, and the matching CLI flags.
// The rules mirror hub/models.py (JobRequest validation) and hub/cli.py so a bad combination is explained before sending.

export type Mode = 'small' | 'work' | 'extended';
export type Tri = 'default' | 'on' | 'off';

export type ChatSettings = {
  mode: Mode;
  model: string;            // registry alias, '' = whatever roles.json says
  thinking: Tri;
  review: Tri;
  context: 'auto' | '16384' | '32768';
  verify: boolean;
  agentLoop: boolean;
  board: boolean;
  boardMode: 'lite' | 'full';
};

export type Turn = { role: 'user' | 'assistant'; text: string };

export const defaultSettings: ChatSettings = {
  mode: 'work', model: '', thinking: 'default', review: 'default', context: 'auto', verify: false, agentLoop: false, board: false, boardMode: 'lite',
};

export const modeLabels: Record<Mode, string> = { small: 'Quick (small, 120 s)', work: 'Standard (work, 16K)', extended: 'Extended (32K, thinking, review)' };

// Conversation context sent with a follow-up: short, recent, finished turns only.
export const HISTORY_TURNS = 6;
export const HISTORY_CHARS = 800;

const tri = (value: Tri) => (value === 'default' ? undefined : value === 'on');

/** Reasons the current combination would be rejected by the hub; empty when it is fine to send. */
export function conflicts(s: ChatSettings): string[] {
  const problems: string[] = [];
  if (s.board && (s.mode !== 'work' || s.context === '32768')) problems.push('Board uses fixed 16K contexts: choose Standard mode and 16K or auto context.');
  if (s.mode === 'extended' && s.context === '16384') problems.push('Extended mode is 32K: choose auto or 32K context.');
  if (s.board && s.model) problems.push('Board chooses its own models: set the model to default.');
  return problems;
}

/** Same defaults as hub.models.default_timeout: plain public lookups are quick, anything heavier gets 300 s. */
export function timeoutSeconds(s: ChatSettings): number {
  if (s.mode === 'small') return 120;
  const heavy = s.board || s.verify || s.review === 'on' || s.mode === 'extended';
  return heavy ? 300 : 120;
}

export function recentTurns(turns: Turn[]): Turn[] {
  return turns.filter(t => t.text.trim()).slice(-HISTORY_TURNS).map(t => ({ role: t.role, text: t.text.slice(0, HISTORY_CHARS) }));
}

export function jobBody(s: ChatSettings, task: string, history: Turn[], idempotencyKey: string): Record<string, unknown> {
  const body: Record<string, unknown> = {
    role: 'personal', task, execution_preset: s.mode, timeout: timeoutSeconds(s), caller: 'dashboard', idempotency_key: idempotencyKey,
  };
  if (s.model) body.model = s.model;
  if (s.context !== 'auto') body.model_context = Number(s.context);
  if (tri(s.thinking) !== undefined) body.model_thinking = tri(s.thinking);
  if (tri(s.review) !== undefined) body.review_pass = tri(s.review);
  if (s.verify) body.verify = true;
  if (s.agentLoop) body.agent_loop = true;
  if (s.board) { body.board = true; body.board_mode = s.boardMode; }
  if (history.length) body.history = recentTurns(history);
  return body;
}

/** The terminal command that sends the same request, so a setup found in the UI can be scripted. */
export function cliFlags(s: ChatSettings): string {
  const flags: string[] = [];
  if (s.mode !== 'work') flags.push(`--preset ${s.mode}`);
  if (s.model) flags.push(`--model ${s.model}`);
  if (s.context !== 'auto') flags.push(`--model-context ${s.context}`);
  if (s.thinking !== 'default') flags.push(`--model-thinking ${s.thinking}`);
  if (s.review !== 'default') flags.push(s.review === 'on' ? '--review' : '--no-review');
  if (s.verify) flags.push('--verify');
  if (s.agentLoop) flags.push('--agent-loop');
  if (s.board) flags.push('--board', `--board-mode ${s.boardMode}`);
  return ['local-worker', ...flags].join(' ');
}

/** One line for the collapsed settings summary. */
export function summary(s: ChatSettings): string {
  const parts = [s.mode === 'work' ? 'Standard' : s.mode === 'small' ? 'Quick' : 'Extended', s.model || 'default model'];
  if (s.board) parts.push(`board ${s.boardMode}`);
  if (s.verify) parts.push('verify');
  if (s.agentLoop) parts.push('agent loop');
  if (s.thinking !== 'default') parts.push(`thinking ${s.thinking}`);
  if (s.review !== 'default') parts.push(`review ${s.review}`);
  if (s.context !== 'auto') parts.push(`${Number(s.context) / 1024}K`);
  return parts.join(' · ');
}
