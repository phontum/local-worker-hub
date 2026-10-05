import { describe, expect, it } from 'vitest';
import { HISTORY_CHARS, HISTORY_TURNS, cliFlags, conflicts, defaultSettings, jobBody, recentTurns, summary, timeoutSeconds } from './chatSettings';

const s = (patch: Partial<typeof defaultSettings> = {}) => ({ ...defaultSettings, ...patch });

describe('jobBody', () => {
  it('sends only what differs from the defaults', () => {
    expect(jobBody(s(), 'hello', [], 'k1')).toEqual({ role: 'personal', task: 'hello', execution_preset: 'work', timeout: 120, caller: 'dashboard', idempotency_key: 'k1' });
  });
  it('maps every override to its JobRequest field', () => {
    const body = jobBody(s({ mode: 'extended', model: 'qwen', context: '32768', thinking: 'off', review: 'on', verify: true, agentLoop: true }), 'x', [], 'k');
    expect(body).toMatchObject({ execution_preset: 'extended', model: 'qwen', model_context: 32768, model_thinking: false, review_pass: true, verify: true, agent_loop: true, timeout: 300 });
    expect(body).not.toHaveProperty('board');
  });
  it('sends a board with its mode and no model', () => {
    expect(jobBody(s({ board: true, boardMode: 'full' }), 'x', [], 'k')).toMatchObject({ board: true, board_mode: 'full', timeout: 300 });
  });
  it('omits empty history and bounds long history', () => {
    expect(jobBody(s(), 'x', [], 'k')).not.toHaveProperty('history');
    const long = Array.from({ length: 20 }, (_, i) => ({ role: i % 2 ? 'assistant' as const : 'user' as const, text: 'a'.repeat(5000) }));
    const sent = (jobBody(s(), 'x', long, 'k').history as typeof long);
    expect(sent).toHaveLength(HISTORY_TURNS);
    expect(sent.every(t => t.text.length === HISTORY_CHARS)).toBe(true);
  });
});

describe('recentTurns', () => {
  it('drops blank turns and keeps the latest ones in order', () => {
    const turns = [{ role: 'user' as const, text: '  ' }, ...Array.from({ length: 8 }, (_, i) => ({ role: 'user' as const, text: `m${i}` }))];
    expect(recentTurns(turns).map(t => t.text)).toEqual(['m2', 'm3', 'm4', 'm5', 'm6', 'm7']);
  });
});

describe('conflicts (mirrors hub.models validation)', () => {
  it('accepts the defaults and ordinary overrides', () => {
    expect(conflicts(s())).toEqual([]);
    expect(conflicts(s({ mode: 'extended', context: '32768', verify: true }))).toEqual([]);
  });
  it('rejects a board with a non-work mode, 32K context or a chosen model', () => {
    expect(conflicts(s({ board: true, mode: 'small' }))).toHaveLength(1);
    expect(conflicts(s({ board: true, mode: 'extended' }))).toHaveLength(1);
    expect(conflicts(s({ board: true, context: '32768' }))).toHaveLength(1);
    expect(conflicts(s({ board: true, model: 'gemma' }))).toHaveLength(1);
  });
  it('rejects extended mode with 16K context', () => {
    expect(conflicts(s({ mode: 'extended', context: '16384' }))).toHaveLength(1);
  });
});

describe('timeoutSeconds (mirrors hub.models.default_timeout)', () => {
  it('is quick for plain lookups and longer for heavier modes', () => {
    expect(timeoutSeconds(s())).toBe(120);
    expect(timeoutSeconds(s({ mode: 'small', verify: true }))).toBe(120);
    expect(timeoutSeconds(s({ verify: true }))).toBe(300);
    expect(timeoutSeconds(s({ review: 'on' }))).toBe(300);
    expect(timeoutSeconds(s({ review: 'off' }))).toBe(120);
    expect(timeoutSeconds(s({ board: true }))).toBe(300);
  });
});

describe('cliFlags and summary', () => {
  it('prints the equivalent command', () => {
    expect(cliFlags(s())).toBe('local-worker');
    expect(cliFlags(s({ mode: 'small', model: 'qwen', context: '16384', thinking: 'on', review: 'off', agentLoop: true }))).toBe(
      'local-worker --preset small --model qwen --model-context 16384 --model-thinking on --no-review --agent-loop');
    expect(cliFlags(s({ board: true, boardMode: 'lite' }))).toBe('local-worker --board --board-mode lite');
  });
  it('summarises a non-default setup in one line', () => {
    expect(summary(s())).toBe('Standard · default model');
    expect(summary(s({ mode: 'extended', model: 'gemma', context: '32768', verify: true }))).toBe('Extended · gemma · verify · 32K');
  });
});
