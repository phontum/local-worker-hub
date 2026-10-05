import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Job, Event, active, date, duration, seconds } from './api';
import { ModelTrace } from './ModelTrace';
import { BoardView } from './BoardView';
import { AnswerTab } from './tabs/AnswerTab';
import { EvidenceTab } from './tabs/EvidenceTab';
import { ActivityTab } from './tabs/ActivityTab';
import { ReviewTab } from './tabs/ReviewTab';

type Tab = 'answer' | 'evidence' | 'trace' | 'activity' | 'board' | 'review';

export function JobDetail({ job, events, busy, onCancel }: { job: Job | undefined; events: Event[]; busy: boolean; onCancel: (job: Job) => void }) {
  const [tab, setTab] = useState<Tab>('answer');
  const buttons = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  useEffect(() => setTab('answer'), [job?.id]);
  if (!job) return <section className="pane empty"><h2>Select a task</h2><p className="muted">Its answer, evidence and trace appear here.</p></section>;
  const direct = job.request.role === 'validator' && (job.request.summary_mode === 'none' || job.result?.report_origin === 'harness');
  const tabs: Array<[Tab, string]> = [['answer', 'Answer'], ['evidence', 'Evidence'], ...(direct ? [] : [['trace', 'Trace'] as [Tab, string]]), ['activity', 'Activity'],
    ...(job.result?.board ? [['board', 'Board'] as [Tab, string]] : []), ['review', 'Review']];
  // Arrow keys, Home and End move between tabs from the focused one (the WAI-ARIA tabs pattern); only the selected tab is in the tab order.
  const onTabKey = (event: KeyboardEvent) => {
    const focused = (event.target as HTMLElement).id.replace('tab-', '');
    const at = Math.max(0, tabs.findIndex(([id]) => id === focused)), last = tabs.length - 1;
    const next = event.key === 'ArrowRight' ? (at + 1) % tabs.length : event.key === 'ArrowLeft' ? (at + last) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? last : -1;
    if (next < 0) return;
    event.preventDefault(); setTab(tabs[next][0]); buttons.current[tabs[next][0]]?.focus();
  };
  return <section className="pane">
    <div className="pane-head"><div className="titleline"><p className="task" title={job.request.task}>{job.request.task}</p>
      {active(job) && <button className="cancel" disabled={busy} onClick={() => onCancel(job)}>Cancel</button>}</div>
      <div className="meta"><span className="role">{job.request.role}</span><span className={`badge ${job.state}`}>{job.state}</span><span>{job.result?.worker_status ?? 'Awaiting report'}</span>
        <span>{duration(seconds(job))}</span><span>{date(job.created)}</span>{job.request.board && <span>board</span>}</div></div>
    <div className="tabs" role="tablist" aria-label="Task details" onKeyDown={onTabKey}>{tabs.map(([id, label]) =>
      <button key={id} ref={el => { buttons.current[id] = el; }} role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls="tab-panel" tabIndex={tab === id ? 0 : -1}
        className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
    <div className="tabbody" role="tabpanel" id="tab-panel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
      {tab === 'answer' && <AnswerTab job={job} />}
      {tab === 'evidence' && <EvidenceTab job={job} events={events} />}
      {tab === 'trace' && <ModelTrace key={job.id} jobId={job.id} running={active(job)} />}
      {tab === 'activity' && <ActivityTab events={events} />}
      {tab === 'board' && job.result?.board && <BoardView jobId={job.id} board={job.result.board} />}
      {tab === 'review' && <ReviewTab job={job} events={events} />}
    </div>
  </section>;
}
