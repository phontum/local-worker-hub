import { Job, active, duration, seconds, time } from './api';

const ROLES = ['all', 'active', 'personal', 'researcher', 'investigator', 'editor', 'validator'];

export function JobList({ jobs, selected, filter, onFilter, onSelect }: { jobs: Job[]; selected: string | null; filter: string; onFilter: (v: string) => void; onSelect: (id: string) => void }) {
  const visible = jobs.filter(j => filter === 'all' || (filter === 'active' ? active(j) : j.request.role === filter));
  return <nav className="list" aria-label="Tasks">
    <div className="list-head"><h2>Tasks</h2>
      <select aria-label="Filter tasks" value={filter} onChange={e => onFilter(e.target.value)}>{ROLES.map(v => <option key={v} value={v}>{v === 'all' ? 'All roles' : v}</option>)}</select></div>
    <select className="job-select" aria-label="Select task" value={selected ?? ''} onChange={e => onSelect(e.target.value)}>
      <option value="" disabled>Select a task</option>{visible.map(j => <option key={j.id} value={j.id}>{time(j.created)} · {j.request.task.slice(0, 60)}</option>)}</select>
    <div className="list-body">{visible.length === 0 ? <p className="muted pad">No tasks yet.</p> : visible.map(j =>
      <button key={j.id} data-job-id={j.id} className={`job ${selected === j.id ? 'selected' : ''}`} onClick={() => onSelect(j.id)}>
        <span className="job-task">{j.request.task}</span>
        <span className="job-meta"><span className={`badge ${j.state}`}>{j.state}</span><span>{j.request.role}</span><span>{time(j.created)}</span>
          {seconds(j) > 0 && <span>{duration(seconds(j))}</span>}{j.review && <span>{j.review.decision}</span>}</span>
      </button>)}</div>
  </nav>;
}
