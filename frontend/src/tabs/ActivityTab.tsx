import { Event } from '../api';

export function ActivityTab({ events }: { events: Event[] }) {
  const rows = events.filter(e => e.kind !== 'trace').slice(-300);
  return <ol className="timeline">{rows.map(e => <li key={e.id}><span>{new Date(e.time * 1000).toLocaleTimeString('en-GB', { hour12: false })}</span><b>{e.kind.replaceAll('_', ' ')}</b>
    <p>{String(e.data.path ?? e.data.tool ?? e.data.query ?? e.data.url ?? e.data.message ?? e.data.role ?? e.data.phase ?? '').slice(0, 160)}</p></li>)}</ol>;
}
