import { FormEvent, KeyboardEvent, ReactElement, cloneElement, useEffect, useId, useRef, useState } from 'react';
import { active, api, ChatOptions, Job } from './api';
import { ChatSettings, Mode, Tri, Turn, cliFlags, conflicts, defaultSettings, jobBody, modeLabels, summary } from './chatSettings';
import { usePoll } from './usePoll';

type Message = { id: string; role: 'user' | 'assistant'; text: string; jobId?: string; state?: string; status?: string; flags?: string };

const MESSAGES_KEY = 'lw.chat.messages', SETTINGS_KEY = 'lw.chat.settings', KEEP_MESSAGES = 60;
const RUNNING = ['queued', 'running'];

// Browser storage is a per-viewer convenience: it can be blocked or empty, and the chat must still work.
function restore<T>(key: string, fallback: T): T {
  try { const raw = localStorage.getItem(key); return raw ? { ...fallback, ...JSON.parse(raw) } : fallback; } catch { return fallback; }
}
function restoreMessages(): Message[] {
  try { const raw = JSON.parse(localStorage.getItem(MESSAGES_KEY) ?? '[]'); return Array.isArray(raw) ? raw : []; } catch { return []; }
}
function remember(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}

function progressText(job: Job): string {
  const p = job.progress;
  if (!p) return 'Waiting for the worker…';
  const where = p.queue_position ? ` · queue position ${p.queue_position}` : '';
  return `${p.phase}${p.active_check ? ` · ${p.active_check}` : ''} · ${Math.round(p.elapsed_seconds)}s${where}`;
}

function fromJob(job: Job): Partial<Message> {
  const result = job.result;
  const final = result?.answer ?? result?.report ?? result?.error;
  return { state: job.state, status: result?.worker_status, text: active(job) ? progressText(job) : final ?? `Task ${job.state}` };
}

function newId() {
  return globalThis.crypto?.randomUUID?.() ?? Math.random().toString(16).slice(2) + Date.now().toString(16);
}

// The label names the control and nothing else; the hint is attached as its description.
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactElement<Record<string, unknown>> }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label>
    {cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : undefined })}
    {hint && <small id={`${id}-hint`} className="muted">{hint}</small>}</div>;
}

export function Chat({ onOpenJob }: { onOpenJob: (id: string) => void }) {
  const [messages, setMessages] = useState<Message[]>(restoreMessages);
  const [settings, setSettings] = useState<ChatSettings>(() => restore(SETTINGS_KEY, defaultSettings));
  const [options, setOptions] = useState<ChatOptions | null>(null);
  const [draft, setDraft] = useState(''), [sending, setSending] = useState(false), [error, setError] = useState('');
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { remember(MESSAGES_KEY, messages.slice(-KEEP_MESSAGES)); }, [messages]);
  useEffect(() => { remember(SETTINGS_KEY, settings); }, [settings]);
  useEffect(() => { void api<ChatOptions>('/api/chat-options').then(setOptions).catch(() => setOptions(null)); }, []);
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [messages]);

  const pending = [...messages].reverse().find(m => m.role === 'assistant' && m.jobId && RUNNING.includes(m.state ?? ''));
  const problems = conflicts(settings);
  const change = (patch: Partial<ChatSettings>) => setSettings(s => ({ ...s, ...patch }));
  const update = (id: string, patch: Partial<Message>) => setMessages(all => all.map(m => m.id === id ? { ...m, ...patch } : m));

  usePoll(async signal => {
    if (!pending?.jobId) return;
    try {
      const job = await api<Job>(`/api/jobs/${pending.jobId}?view=compact`, { signal });
      if (!signal.aborted) update(pending.id, fromJob(job));
    } catch (e) { if (!signal.aborted && (e as Error).message !== 'pair') setError((e as Error).message); }
  }, 1500, [pending?.id], !!pending);

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || sending || pending || problems.length) return;
    const history: Turn[] = messages.filter(m => m.role === 'user' || (m.state === 'completed' && m.text)).map(m => ({ role: m.role, text: m.text }));
    const user: Message = { id: newId(), role: 'user', text };
    const reply: Message = { id: newId(), role: 'assistant', text: 'Sending…', state: 'sending', flags: cliFlags(settings) };
    setMessages(all => [...all, user, reply]); setDraft(''); setError(''); setSending(true);
    try {
      const job = await api<Job>('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(jobBody(settings, text, history, newId())) });
      update(reply.id, { jobId: job.id, ...fromJob(job) });
    } catch (e) {
      update(reply.id, { state: 'failed', text: (e as Error).message === 'pair' ? 'This browser is not paired. Reload the page to connect.' : `Could not start the task: ${(e as Error).message}` });
    } finally { setSending(false); }
  }

  async function stop() {
    if (!pending?.jobId) return;
    try { update(pending.id, fromJob(await api<Job>(`/api/jobs/${pending.jobId}/cancel`, { method: 'POST' }))); } catch (e) { setError((e as Error).message); }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
  }

  const models = options?.models ?? [];
  const command = cliFlags(settings);
  return <section className="pane chat" aria-label="Chat with the personal model">
    <div className="chat-log" role="log" aria-live="polite" aria-relevant="additions text">
      {!messages.length && <div className="chat-empty"><h2>Ask the personal model</h2>
        <p className="muted">Public web questions and general chat. It searches only when it needs current facts, and never sees your repositories. Each reply is a normal task you can inspect under Tasks.</p></div>}
      {messages.map(m => <article key={m.id} className={`bubble ${m.role}`}>
        <div className="bubble-text">{m.text}</div>
        {m.role === 'assistant' && <footer className="bubble-meta">
          {m.state && <span className={`badge ${m.state}`}>{m.state}</span>}
          {m.status && <span>{m.status}</span>}
          {m.jobId && <button className="link" onClick={() => onOpenJob(m.jobId!)}>Details</button>}
          {m.flags && m.flags !== 'local-worker' && <code title="Equivalent command">{m.flags}</code>}
        </footer>}
      </article>)}
      <div ref={end} />
    </div>
    {error && <div className="error" role="alert">{error}</div>}
    <form className="composer" onSubmit={send}>
      <details className="chat-settings">
        <summary>Settings · {summary(settings)}</summary>
        <div className="settings-grid">
          <Field label="Mode" hint="Context size, thinking and review follow the mode unless overridden below.">
            <select value={settings.mode} onChange={e => change({ mode: e.target.value as Mode })}>
              {(Object.keys(modeLabels) as Mode[]).map(m => <option key={m} value={m}>{modeLabels[m]}</option>)}
            </select>
          </Field>
          <Field label="Model" hint={models.length ? undefined : 'Model list unavailable; the configured default is used.'}>
            <select value={settings.model} onChange={e => change({ model: e.target.value })} disabled={settings.board}>
              <option value="">Default (roles.json)</option>
              {models.map(m => <option key={m.alias} value={m.alias} disabled={m.installed === false}>{m.alias} · {m.name}{m.installed === false ? ' (not installed)' : ''}</option>)}
            </select>
          </Field>
          <Field label="Context">
            <select value={settings.context} onChange={e => change({ context: e.target.value as ChatSettings['context'] })}>
              <option value="auto">Auto</option><option value="16384">16K</option><option value="32768">32K</option>
            </select>
          </Field>
          <Field label="Thinking">
            <select value={settings.thinking} onChange={e => change({ thinking: e.target.value as Tri })}>
              <option value="default">Default</option><option value="on">On</option><option value="off">Off</option>
            </select>
          </Field>
          <Field label="Answer review" hint="A fresh pass checks the answer against every requirement.">
            <select value={settings.review} onChange={e => change({ review: e.target.value as Tri })}>
              <option value="default">Default</option><option value="on">On</option><option value="off">Off</option>
            </select>
          </Field>
          <fieldset className="field toggles"><legend>Experiments</legend>
            <label><input type="checkbox" checked={settings.verify} onChange={e => change({ verify: e.target.checked })} /> Strict verify <small className="muted">slow, often PARTIAL</small></label>
            <label><input type="checkbox" checked={settings.agentLoop} onChange={e => change({ agentLoop: e.target.checked })} /> Older tool loop</label>
            <label><input type="checkbox" checked={settings.board} onChange={e => change({ board: e.target.checked })} /> Board <small className="muted">rarely useful</small></label>
            {settings.board && <select aria-label="Board mode" value={settings.boardMode} onChange={e => change({ boardMode: e.target.value as ChatSettings['boardMode'] })}>
              <option value="lite">Lite</option><option value="full">Full</option></select>}
          </fieldset>
        </div>
        {!!problems.length && <ul className="error" role="alert">{problems.map(p => <li key={p}>{p}</li>)}</ul>}
        <p className="muted">Same request from the terminal: <code>{command} "…"</code>{' '}
          <button type="button" className="link" onClick={() => void navigator.clipboard?.writeText(`${command} "`).catch(() => undefined)}>Copy</button>{' '}
          <button type="button" className="link" onClick={() => setSettings(defaultSettings)}>Reset</button></p>
      </details>
      <div className="composer-row">
        <label htmlFor="chat-input" className="sr-only">Message</label>
        <textarea id="chat-input" value={draft} rows={2} placeholder="Ask anything…  Enter to send, Shift+Enter for a new line" onChange={e => setDraft(e.target.value)} onKeyDown={onKeyDown} maxLength={12000} />
        {pending ? <button type="button" className="cancel" onClick={() => void stop()}>Stop</button>
          : <button className="primary" disabled={!draft.trim() || sending || !!problems.length}>Send</button>}
        <button type="button" disabled={!messages.length || !!pending} onClick={() => setMessages([])}>New chat</button>
      </div>
    </form>
  </section>;
}
